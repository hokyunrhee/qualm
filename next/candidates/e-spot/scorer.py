"""e-spot's verdict: Jev asked about each changed unit, the answers folded into a calibrated model.

For every judged file, `spots.select` picks the changed units (and the module-level code) worth a
question. Each spot is one Jev request with six questions (questions.py). The answers are folded
per file (max and size-weighted mean per question, plus a noisy-or of push_back) and joined to the
core's features; a logistic model trained on dev (train.py, model.json) turns them into the
probability that a reviewer would ask for the file's change to be simplified.

A file whose score lands close to the threshold has each of its spots asked four more times and is
judged on the mean of the five answers, so a fresh draw from Jev rarely flips the verdict. A file that cannot be asked about (no key, Jev
failing after retries, the budget spent) is an error, and the run exits 2: it never passes.
"""
import hashlib, math, os
from concurrent.futures import ThreadPoolExecutor

from gatecore import gitio, jev, perimeter, units as unitmod
from gatecore.model import LogisticModel

import questions
import spots

VERSION = '0.1.0'
HERE = os.path.dirname(os.path.realpath(__file__))
DEFAULT_BUDGET = 0.25   # dollars per run when --budget is not given
JOBS = 6                # requests in flight at once
CLOSE = 0.05            # a file this near its threshold is a close call
DRAWS = 5               # answers per spot a close call is judged on (their mean)
VERB_MIN = 0.5          # a verb answer this high makes a finding on a failing file
ADVICE_MIN = 0.8        # ... and on a passing file, as advice
DETECTOR_FAILING_MIN = 0.3
DETECTOR_ADVICE_MIN = 0.6
MAX_UNIT_FINDINGS = 6


# ------------------------------------------------------------------ folding answers into features

def aggregate(spot_list, answers, left_out=()):
    """Per-file features from the answers about each spot (answers[i] belongs to spot_list[i])."""
    x = {}
    weights = [max(1, s.n_added) for s in spot_list]
    total = float(sum(weights)) or 1.0
    for q in questions.IDS:
        vals = [a[q] for a in answers]
        x['jev.max.' + q] = max(vals) if vals else 0.0
        x['jev.mean.' + q] = sum(v * w for v, w in zip(vals, weights)) / total if vals else 0.0
    pb = [a['push_back'] for a in answers]
    x['jev.noisy_or.push_back'] = 1.0 - math.prod(1.0 - v for v in pb) if pb else 0.0
    x['jev.max_verb'] = max([a[v] for a in answers for v in questions.VERBS] or [0.0])
    x['jev.n_high'] = math.log1p(sum(1 for v in pb if v >= 0.5))
    x['spots.n'] = math.log1p(len(spot_list))
    x['spots.none'] = 0.0 if spot_list else 1.0
    x['spots.left_out'] = math.log1p(len(left_out))
    return x


def names():
    out = []
    for q in questions.IDS:
        out += ['jev.max.' + q, 'jev.mean.' + q]
    return out + ['jev.noisy_or.push_back', 'jev.max_verb', 'jev.n_high', 'spots.n', 'spots.none', 'spots.left_out']


# ------------------------------------------------------------------ findings

def finding_lines(spot):
    """Where a finding about a spot points: its added code lines, or for module-level code the
    run of added lines that holds the most of them."""
    if spot.kind == 'unit':
        return spot.added[0], spot.added[-1]
    runs = unitmod.added_ranges(spot.added, gap=3)
    lo, hi = max(runs, key=lambda r: (sum(1 for n in spot.added if r[0] <= n <= r[1]), -r[0]))
    return lo, hi


def unit_finding(f, spot, verb, value, sim=None):
    lo, hi = finding_lines(spot)
    rng = 'line %d' % lo if lo == hi else 'lines %d-%d' % (lo, hi)
    if spot.kind == 'module':
        span, where, unit = '%s (module level)' % rng, 'the added module-level lines', 'the module-level code'
    else:
        span, where, unit = '%s (in %s)' % (rng, spot.label), 'the added lines of %s' % spot.label, spot.label
    related, rel = '', []
    if verb == 'merge' and sim is not None:
        u = sim[1]
        related = ' (compare %s, lines %d-%d)' % (spots.safe_name(u.name), u.start, u.end)
        rel = [{'path': f.path, 'lines': [u.start, u.end]}]
    t = questions.FINDINGS[verb]
    return {'id': 'spot_' + verb, 'lines': [lo, hi], 'message': t['message'].format(span=span, where=where, unit=unit, related=related),
            'fix': t['fix'].format(span=span, where=where, unit=unit, related=related), 'confidence': round(float(value), 2), 'related': rel}


def spot_findings(f, spot_list, answers, minimum):
    out = []
    for s, a in zip(spot_list, answers):
        verbs = sorted(((a[v], v) for v in questions.VERBS if a[v] >= minimum and not (v == 'split' and s.kind == 'module')), reverse=True)[:2]
        if not verbs and a['push_back'] >= minimum:
            verbs = [(a['push_back'], 'push_back')]
        for value, verb in verbs:
            out.append(unit_finding(f, s, verb, value, spots.similar(f, s) if verb == 'merge' else None))
    out.sort(key=lambda d: (-d['confidence'], d['lines'][0]))
    return out[:MAX_UNIT_FINDINGS]


def strongest(f, spot_list, answers):
    """The one finding a failing file always carries: its most suspicious spot, by its top verb."""
    if not spot_list:
        added = sorted(f.added) or [h[2] for h in f.diff.hunks] or [1]
        s = spots.Spot('module', 'module', added[0], added[-1], added)
        return unit_finding(f, s, 'push_back', 0.5)
    k = max(range(len(spot_list)), key=lambda i: (answers[i]['push_back'] + max(answers[i][v] for v in questions.VERBS), -i))
    a, s = answers[k], spot_list[k]
    value, verb = max(((a[v], v) for v in questions.VERBS if not (v == 'split' and s.kind == 'module')), default=(a['push_back'], 'push_back'))
    if value < 0.5 and a['push_back'] >= value:
        value, verb = a['push_back'], 'push_back'
    return unit_finding(f, s, verb, value, spots.similar(f, s) if verb == 'merge' else None)


def merge_findings(unit_found, detector_found):
    """Detector findings are exact-line and come first when they cover the same lines."""
    out = list(detector_found)
    for d in unit_found:
        out.append(d)
    out.sort(key=lambda d: (-d['confidence'], d['lines'][0]))
    return out


# ------------------------------------------------------------------ the scorer

class Scorer:
    def __init__(self, args, policy, model_path=None, client=None):
        self.model = LogisticModel.load(model_path or os.path.join(HERE, 'model.json'))
        self.threshold = getattr(args, 'threshold', None) or getattr(policy, 'threshold', None) or self.model.threshold
        self.budget = args.budget if getattr(args, 'budget', None) is not None else DEFAULT_BUDGET
        self.use_cache = not getattr(args, 'no_cache', False)
        self.client = client
        self.warnings = []

    def usage(self):
        c = self.client
        if c is None:
            return {'llm_cost_estimated': False}
        return {'jev_requests': c.requests, 'jev_input_tokens': c.input_tokens, 'jev_cost_usd': round(c.cost, 6), 'llm_cost_estimated': False}

    def _client(self, repo):
        if self.client is not None:
            return self.client
        cache = None
        if self.use_cache:
            cache, warning = perimeter.cache_dir(repo or '.')
            if warning:
                self.warnings.append(warning)
        self.client = jev.Client(budget=self.budget, cache_dir=cache)
        return self.client

    def _ask_one(self, st, use_cache):
        for attempt in range(2):
            try:
                return self.client.ask(st, questions.QUESTIONS, use_cache=use_cache)
            except jev.JevError as e:
                # OpenRouter sometimes answers 499 (the request was dropped upstream); the core
                # client does not retry it, so it is tried once more here
                if attempt or 'HTTP 499' not in str(e):
                    return e

    def _ask_all(self, jobs, use_cache=True):
        """jobs: [(key, state)] -> {key: answers or JevError}, asked JOBS at a time."""
        use_cache = use_cache and self.use_cache
        with ThreadPoolExecutor(JOBS) as ex:
            return dict(zip([k for k, _ in jobs], ex.map(lambda job: self._ask_one(job[1], use_cache), jobs)))

    def _settled_key(self, st):
        req = self.client.request(st, questions.QUESTIONS)
        return hashlib.sha256(('e-spot-settled-mean%d:' % DRAWS + jev.Client.key_of(req)).encode()).hexdigest()

    def _settle(self, close, plan, got):
        """Close calls: each spot's answers become the mean of DRAWS answers (the first plus fresh
        ones, asked concurrently). Settled answers are cached under their own key, so a cached run
        does not ask again. Returns {(path, i): answers or JevError}."""
        out, fresh = {}, []
        for path in close:
            for i, st in enumerate(plan[path][2]):
                hit = self.client.cache_get(self._settled_key(st)) if self.use_cache else None
                if hit is not None and all(q in hit.get('answers', {}) for q in questions.IDS):
                    out[(path, i)] = hit['answers']
                else:
                    fresh += [((path, i, k), st) for k in range(DRAWS - 1)]
        more = self._ask_all(fresh, use_cache=False)
        for path in close:
            for i, st in enumerate(plan[path][2]):
                if (path, i) in out:
                    continue
                draws = [got[(path, i)]] + [more[(path, i, k)] for k in range(DRAWS - 1)]
                err = next((d for d in draws if isinstance(d, jev.JevError)), None)
                if err is not None:
                    out[(path, i)] = err
                    continue
                mean = {q: sum(d[q] for d in draws) / len(draws) for q in questions.IDS}
                if self.use_cache:
                    self.client.cache_put(self._settled_key(st), {'answers': mean})
                out[(path, i)] = mean
        return out

    def score(self, ctx, found, feats):
        if not ctx.files:
            return {}
        client = self._client(ctx.repo)
        if not client.available():
            raise gitio.GateError('no API key: set %s to judge the change with Jev; a change that cannot be judged never passes' % jev.KEY_VAR)
        plan = {}
        jobs = []
        for f in ctx.files:
            asked, left = spots.select(f)
            ol = spots.outline(f)
            states = [spots.state(f, s, ol) for s in asked]
            plan[f.path] = (asked, left, states)
            jobs += [((f.path, i), st) for i, st in enumerate(states)]
        got = self._ask_all(jobs)
        out, first = {}, {}
        for f in ctx.files:
            asked, left, states = plan[f.path]
            answers = [got[(f.path, i)] for i in range(len(asked))]
            errs = [a for a in answers if isinstance(a, jev.JevError)]
            if errs:
                out[f.path] = {'error': 'could not judge: %s' % errs[0]}
                continue
            x = dict(feats[f.path])
            x.update(aggregate(asked, answers, left))
            first[f.path] = (x, answers, self.model.predict(x))
        close = [path for path, (_, _, p) in first.items() if plan[path][0] and abs(p - self.threshold) <= CLOSE]
        settled = self._settle(close, plan, got) if close else {}
        for f in ctx.files:
            if f.path not in first:
                continue
            asked, left, states = plan[f.path]
            x, answers, p = first[f.path]
            if f.path in close:
                answers = [settled[(f.path, i)] for i in range(len(asked))]
                errs = [a for a in answers if isinstance(a, jev.JevError)]
                if errs:
                    out[f.path] = {'error': 'could not judge: %s' % errs[0]}
                    continue
                x.update(aggregate(asked, answers, left))
                p = self.model.predict(x)
            out[f.path] = self._verdict(f, p, x, asked, left, answers, found[f.path], f.path in close)
        errors = [r for r in out.values() if r.get('error')]
        if errors:
            self.warnings.append('%d file%s could not be judged by Jev, so the change does not pass.' % (len(errors), '' if len(errors) == 1 else 's'))
        return out

    def _verdict(self, f, p, x, asked, left, answers, detector_found, settled):
        failed = p >= self.threshold
        if failed:
            unit_found = spot_findings(f, asked, answers, VERB_MIN)
            det = [d for d in detector_found if d['confidence'] >= DETECTOR_FAILING_MIN]
            if not unit_found and not det:
                unit_found = [strongest(f, asked, answers)]
        else:
            unit_found = spot_findings(f, asked, answers, ADVICE_MIN)
            det = [d for d in detector_found if d['confidence'] >= DETECTOR_ADVICE_MIN]
        signals = {k: round(v, 4) for k, v in x.items() if k.startswith(('jev.', 'spots.'))}
        signals['spots'] = [{'unit': s.label, 'lines': [s.start, s.end], 'added': s.n_added,
                             'answers': {q: round(a[q], 3) for q in questions.IDS}} for s, a in zip(asked, answers)]
        if left:
            signals['spots_left_out'] = [{'unit': s.label, 'lines': [s.start, s.end], 'added': s.n_added} for s in left]
        signals['settled'] = settled
        signals['top_features'] = self.model.contributions(x)
        return {'score': p, 'threshold': self.threshold, 'failed': failed, 'abstained': False,
                'findings': merge_findings(unit_found, det), 'signals': signals}
