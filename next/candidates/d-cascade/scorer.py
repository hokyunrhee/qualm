"""d-cascade's verdict.

For each judged file: Jev reads the raw diff (qualm's ten questions) and the masked diff (comments
removed, prose strings replaced; eight of qualm's questions and four reviewer verbs). A logistic
model trained on dev combines the answers with the detectors' findings into a calibrated
probability. A file whose probability lands near the threshold is asked twice more and the median
probability decides, so a close call reads the same on every run.

A failing file is localized: each of its larger hunks is asked again on its own, and the hunks
reviewers would push back on become findings with the verb that fits, next to the detectors'
exact-line findings.

With --llm-dir and a band in band.json, files whose probability falls inside the abstain band are
settled by an LLM reviewer instead (the cascade); without it, the model's threshold decides.
"""
import json, math, os, statistics
from concurrent.futures import ThreadPoolExecutor

from gatecore import cli, gitio, jev, llmdir, perimeter
from gatecore.units import added_ranges as units_ranges
from gatecore.model import LogisticModel

import asks
import review

VERSION = '0.1.0'
HERE = os.path.dirname(os.path.realpath(__file__))
CLOSE = 0.05          # a probability this near the threshold is a close call
MAX_HUNKS = 6         # places (functions or runs of added code) asked about when localizing a failing file
DET_FAILING_MIN = 0.3
DET_ADVICE_MIN = 0.6

RAW_Q = [q for q in asks.QUALM if q != 'direction']
MASKED_Q = list(asks.MASKED_QUESTIONS)

VERB_TEXT = {
    'added_copies': ('repeat logic that is already in this file',
                     'Find the code these lines duplicate, move the shared steps into one function and call it from both places. Done when the logic is written once.'),
    'added_impossible_guards': ('add checks or fallbacks for states the surrounding code already rules out',
                                'Delete the checks for cases that cannot happen, or turn a case that can happen into an error where it starts. Done when every remaining check guards a state a caller can produce.'),
    'added_placeholders': ('add placeholder, debugging or unfinished code',
                           'Remove the debugging output and stubs, or finish them. Done when nothing in these lines is temporary.'),
    'added_unused_flexibility': ('add an option, hook or layer of indirection with a single user',
                                 'Inline it into its one caller and drop what is not used yet. Done when every new abstraction has two real users or is gone.'),
    'indirection': ('add a wrapper, interface, class or parameter that has only one user or one implementation',
                    'Inline it into its one caller. Done when the extra layer is gone or has a second real user.'),
    'remove': ('hold code that can be deleted without changing behaviour (unused values, redundant checks, dead branches or needless conversions)',
               'Delete the lines that do not change the result, then run the tests. Done when every added line is needed.'),
    'reuse': ('re-implement something the language, the standard library or nearby code already provides',
              'Replace them with a call to the existing function or built-in. Done when the hand-written version is gone.'),
    'shorten': ('can be written more directly',
                'Rewrite them with the simplest construct the language offers (a built-in, a comprehension, an early return) without changing behaviour. Done when the same logic takes noticeably fewer lines.'),
}


def logit(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def feature_vector(raw, masked, feats):
    x = dict(feats)
    for q in RAW_Q:
        x['raw_' + q] = logit(raw[q]) if raw and q in raw else 0.0
    x['raw_missing'] = 0.0 if raw else 1.0
    for q in MASKED_Q:
        x['masked_' + q] = logit(masked[q]) if masked and q in masked else 0.0
    x['masked_missing'] = 0.0 if masked else 1.0
    if raw and masked:
        x['push_back_mean'] = (x['raw_push_back'] + x['masked_push_back']) / 2
        x['push_back_gap'] = x['raw_push_back'] - x['masked_push_back']
    else:
        x['push_back_mean'] = x['raw_push_back'] if raw else x['masked_push_back']
        x['push_back_gap'] = 0.0
    return x


def pieces(f, limit=MAX_HUNKS):
    """The places a reviewer would comment on: each changed top-level function, and each run of
    added code outside any function, as (lo, hi, name, added lines), most added code first, with
    the masked text of the file (comments removed, prose strings replaced)."""
    masked = asks.masked_text(f.head, f.language).split('\n')
    added_code = set(f.added_code_lines())
    out, covered = [], set()
    for u in f.top_units:
        rows = [n for n in range(u.start, u.end + 1) if n in added_code]
        covered.update(range(u.start, u.end + 1))
        if len(rows) >= 2:
            out.append((u.start, u.end, u.name, rows))
    loose = sorted(n for n in added_code if n not in covered)
    for lo, hi in units_ranges(loose):
        rows = [n for n in loose if lo <= n <= hi]
        if len(rows) >= 2:
            out.append((max(1, lo - 3), min(len(masked), hi + 3), None, rows))
    out.sort(key=lambda r: (-len(r[3]), r[0]))
    return out[:limit], masked


def piece_state(f, lo, hi, name, masked):
    body = ''.join(('+' if n in f.added else ' ') + masked[n - 1] + '\n' for n in range(lo, hi + 1))
    piece = '--- a/%s\n+++ b/%s\n@@ -%d +%d,%d @@%s\n%s' % (f.path, f.path, lo, lo, hi - lo + 1, (' ' + name) if name else '', body)
    return asks.state(piece, asks.language(f.path), asks.MASKED_FORMAT)


class Scorer:
    def __init__(self, args, policy, model_path=None):
        self.args = args
        self.model = LogisticModel.load(model_path or os.path.join(HERE, 'model.json'))
        self.threshold = args.threshold or policy.threshold or self.model.threshold
        band_path = os.path.join(HERE, 'band.json')
        self.band = None
        if os.path.exists(band_path):
            with open(band_path) as fh:
                self.band = json.load(fh)
        self.warnings = []
        cache = None
        if not args.no_cache:
            cache, warn = perimeter.cache_dir(args.repo)
            if warn:
                self.warnings.append(warn)
            if cache:
                cache = os.path.join(cache, 'd-cascade')
        self.client = jev.Client(budget=args.budget, cache_dir=cache)
        self.llm = llmdir.LLM(args.llm_dir) if args.llm_dir else None

    def usage(self):
        u = {'jev_requests': self.client.requests, 'jev_input_tokens': self.client.input_tokens, 'jev_cost_usd': round(self.client.cost, 6)}
        if self.llm:
            u.update({'llm_requests': self.llm.requests, 'llm_input_tokens': self.llm.input_tokens, 'llm_output_tokens': self.llm.output_tokens,
                      'llm_cost_usd': round(self.llm.cost, 6), 'llm_cost_estimated': True})
        return u

    # ---------------------------------------------------------------- asking
    def views(self, ctx, f):
        change = next((c for c in ctx.all_changes if c.path == f.path), None)
        raw = None
        if ctx.repo and change is not None and not change.undecodable:
            raw = asks.git_diff(ctx.repo, ctx.base, change)
        if raw is None:
            raw = asks.py_diff(f.path, f.base, f.head, old_path=f.old_path)
        masked = asks.masked_diff(f.path, f.base, f.head, f.language, f.old_path)
        return raw, masked

    def ask_file(self, ctx, f, feats, use_cache=True, reading=0):
        raw_d, masked_d = self.views(ctx, f)
        lang = asks.language(f.path)
        raw = asks.ask_pieces(self.client, raw_d, lang, asks.QUALM, asks.DIFF_FORMAT, use_cache, reading) if raw_d.strip() else None
        masked = asks.ask_pieces(self.client, masked_d, lang, asks.MASKED_QUESTIONS, asks.MASKED_FORMAT, use_cache, reading) if masked_d.strip() else None
        x = feature_vector(raw, masked, feats)
        return self.model.predict(x), raw, masked, x, masked_d

    def judge(self, ctx, f, feats):
        use_cache = not self.args.no_cache
        p, raw, masked, x, masked_d = self.ask_file(ctx, f, feats, use_cache=use_cache)
        ps = [p]
        if abs(p - self.threshold) <= CLOSE:
            # two more readings, and the median decides; each reading has its own cache entry,
            # so a second run with the cache on reaches the same verdict
            for k in (1, 2):
                ps.append(self.ask_file(ctx, f, feats, use_cache=use_cache, reading=k)[0])
            p = statistics.median(ps)
        return {'p': p, 'readings': ps, 'raw': raw, 'masked': masked, 'x': x, 'masked_diff': masked_d}

    # ---------------------------------------------------------------- findings
    def localize(self, f, masked_d, found):
        places, masked = pieces(f)
        asked = []
        for lo, hi, name, rows in places:
            ans = self.client.ask(piece_state(f, lo, hi, name, masked), asks.HUNK_QUESTIONS, use_cache=not self.args.no_cache)
            asked.append((ans['push_back'], min(rows), max(rows), name, ans, rows))
        asked.sort(key=lambda r: -r[0])
        findings = []
        for k, (pb, lo, hi, name, ans, rows) in enumerate(asked):
            if pb < 0.5 and k > 0:
                continue
            covered = {n for d in found if d['confidence'] >= 0.6 for n in range(d['lines'][0], d['lines'][1] + 1)}
            if sum(1 for n in rows if n in covered) >= 0.5 * len(rows):
                continue  # exact detector findings already cover most of this place
            verb = max(VERB_TEXT, key=lambda q: ans.get(q, 0.0))
            what, fix = VERB_TEXT[verb]
            span = 'Line %d' % lo if lo == hi else 'Lines %d-%d' % (lo, hi)
            where = ' (in %s)' % name if name else ''
            findings.append({'id': 'review.' + verb, 'lines': [lo, hi], 'message': '%s%s %s.' % (span, where, what), 'fix': fix,
                             'confidence': round(pb, 2), 'related': []})
        return findings

    # ---------------------------------------------------------------- verdict
    def score(self, ctx, found, feats):
        if not self.client.available():
            raise gitio.GateError('no API key: set %s' % jev.KEY_VAR)
        results = {}
        with ThreadPoolExecutor(8) as ex:
            futs = {f.path: ex.submit(self.judge, ctx, f, feats[f.path]) for f in ctx.files}
            for f in ctx.files:
                try:
                    results[f.path] = futs[f.path].result()
                except jev.BudgetSpent as e:
                    results[f.path] = {'error': 'budget spent: %s' % e}
                except jev.JevError as e:
                    results[f.path] = {'error': str(e)}
        # the cascade: an LLM reviewer settles the files the model is unsure of
        pending = []
        if self.llm and self.band:
            for f in ctx.files:
                r = results[f.path]
                if 'p' in r and self.band['lo'] <= r['p'] < self.band['hi']:
                    ans = self.llm.ask(*review.request(f, self.band.get('model', review.MODEL)))
                    if ans is None:
                        pending.append(f.path)
                    else:
                        r['llm'] = review.read(ans, f)
        if pending:
            raise cli.NeedsLLM('%d request(s) for the abstain band in %s' % (len(pending), self.args.llm_dir))
        out = {}
        for f in ctx.files:
            r = results[f.path]
            if 'error' in r:
                out[f.path] = {'error': r['error']}
                continue
            p = r['p']
            abstained = 'llm' in r
            failed = r['llm']['blocking'] if abstained else p >= self.threshold
            score = r['llm']['probability'] if abstained else p
            dets = [d for d in found[f.path] if d['confidence'] >= (DET_FAILING_MIN if failed else DET_ADVICE_MIN)]
            findings = list(dets)
            if failed:
                try:
                    findings += self.localize(f, r['masked_diff'], dets)
                except jev.JevError as e:
                    self.warnings.append('%s: findings could not be localized (%s)' % (f.path, e))
                if abstained:
                    findings += [c for c in r['llm']['findings'] if not any(c['lines'] == d['lines'] for d in findings)]
                findings.sort(key=lambda d: (-d['confidence'], d['lines'][0]))
            signals = {'p_model': round(p, 4), 'readings': [round(v, 4) for v in r['readings']],
                       'raw': r['raw'], 'masked': r['masked'], 'top_features': self.model.contributions(r['x'])}
            if abstained:
                signals['llm'] = {k: r['llm'][k] for k in ('blocking', 'probability')}
            out[f.path] = {'score': score, 'threshold': self.threshold, 'failed': failed, 'abstained': abstained,
                           'findings': findings[:8], 'signals': signals}
        return out
