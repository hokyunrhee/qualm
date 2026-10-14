"""d-cascade's verdict.

For each judged file: Jev reads the raw diff (qualm's ten questions) and the masked diff (comments
removed, prose strings replaced; eight of qualm's questions and four reviewer verbs). A logistic
model trained on dev combines the answers into a calibrated probability. A file whose probability
lands near the threshold is read again (3, then 7, then at most 11 readings, stopping once the
median is clearly on one side) and the median decides, so a close call reads the same more often.

A failing file is localized one function at a time: every function the change wrote and every
existing function it changed is asked about on its own (with the lines it removed shown), and
each one a reviewer would push back on becomes a finding. The verb is the question whose answer
stands out most against its usual level on dev. The detectors' exact-line findings come first.

No LLM runs inside it: the model's threshold decides every file. (The study's LLM band was left
out: on dev it could not keep its precision unless the LLM decided every file above 0.26, and the
LLM's answers flip between runs.)
"""
import json, math, os, re, statistics
from concurrent.futures import ThreadPoolExecutor

from gatecore import gitio, jev, perimeter
from gatecore.units import added_ranges as units_ranges
from gatecore.model import LogisticModel

import asks

VERSION = '1.0.0'
HERE = os.path.dirname(os.path.realpath(__file__))
CLOSE = 0.05          # a probability this near the threshold is a close call
# a close call is read again until its median is clearly on one side: after 3 readings stop unless
# the median is within 0.02 of the threshold, after 7 unless within 0.01; at most 11 readings
READINGS = ((3, 0.02), (7, 0.01), (11, None))
MAX_PLACES = 10       # functions (or runs of added code) asked about when localizing a failing file
PLACE_BYTES = 60000   # and at most this much text across them
PLACE_LINES = 150     # a longer function is shown from 10 lines before its first change to 10 after its last
LOOSE_LINES = 60      # added code outside any function is cut into runs of at most this many lines
PRECISE = 8           # a detector finding this short points at the spot itself
DET_ADVICE_MIN = 0.6
LEFTOVERS = ('debug_output', 'placeholder', 'stub', 'commented_out_code', 'unused_code', 'defensive_guard', 'change_narration')

RAW_Q = [q for q in asks.QUALM if q != 'direction']
MASKED_Q = list(asks.MASKED_QUESTIONS)

VERB_TEXT = {
    'added_copies': ('repeat logic that already exists in this file',
                     'Find the code these lines repeat, keep one copy and call it from both places with what differs as a parameter. Done when the repeated steps exist once.'),
    'added_impossible_guards': ('add checks or fallbacks for states the surrounding code already rules out',
                                'Delete each check for a case its callers cannot produce. Keep checks for input that can really arrive. Done when every remaining check guards a state a caller can produce.'),
    'added_placeholders': ('add placeholder, debugging or unfinished code',
                           'Remove the debugging output and stubs, or finish them. Done when nothing in these lines is temporary.'),
    'added_unused_flexibility': ('add an option, hook or layer of indirection that nothing here needs yet',
                                 'Inline the layer into its one caller, or drop the parameter or hook no caller sets. Keep options the change is meant to offer. Done when each new abstraction has a second real user or is gone.'),
    'indirection': ('add a wrapper, interface, class or parameter with only one user or one implementation',
                    'Inline it into its one caller. Done when the extra layer is gone or has a second real user.'),
    'remove': ('hold steps that do not change the result (an unused value, a check repeated, a branch that cannot run, a needless conversion)',
               'Delete the steps that do not change the result; keep everything a caller or test observes. Done when each remaining step changes the result.'),
    'reuse': ('re-implement something the language, the standard library or nearby code already provides',
              'Replace the hand-written version with a call to the existing function or built-in. Done when the hand-written version is gone.'),
    'shorten': ('take a longer route than needed for what they compute',
                'Rewrite the steps with the direct construct (a built-in, one loop instead of two, an early return instead of nesting) and keep the behaviour. Done when the construct named here is replaced, not when lines are merged.'),
}
# detector findings that already say, line by line, what a verb would say about a whole function
VERB_DETECTORS = {'added_placeholders': ('debug_output', 'placeholder', 'stub', 'commented_out_code'), 'added_copies': ('near_copy',),
                  'added_impossible_guards': ('defensive_guard',), 'remove': ('unused_code',), 'indirection': ('pass_through',),
                  'added_unused_flexibility': ('pass_through', 'unused_code')}
with open(os.path.join(HERE, 'verbs.json')) as _fh:
    VERB_STATS = json.load(_fh)['questions']


def logit(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


PIECE_Q = list(asks.HUNK_QUESTIONS)


def piece_features(answers, sizes):
    """Spot-level signal: for each question, the strongest answer over the file's changed functions,
    and the push_back answer weighted by how much code each function added."""
    x = {}
    if not answers:
        for q in PIECE_Q:
            x['piece_max_' + q] = 0.0
        x['piece_wmean_push_back'] = 0.0
        x['piece_n'] = 0.0
        x['piece_missing'] = 1.0
        return x
    for q in PIECE_Q:
        x['piece_max_' + q] = max(logit(a[q]) for a in answers)
    total = float(sum(sizes)) or 1.0
    x['piece_wmean_push_back'] = sum(logit(a['push_back']) * w for a, w in zip(answers, sizes)) / total
    x['piece_n'] = math.log1p(len(answers))
    x['piece_missing'] = 0.0
    return x


def feature_vector(raw, masked, feats, pieces_answers=None, pieces_sizes=None):
    x = dict(feats)
    x.update(piece_features(pieces_answers, pieces_sizes or []))
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


IMPORT_LINE = re.compile(r'^\s*(import\b|from\s+\S+\s+import\b|package\b|require(_relative)?\b|use\s|using\s|#include|export\s+\*\s+from|'
                         r'(const|let|var)\s+\w+\s*=\s*require\(|\)$|"[^"]*"$|\w+\s+"[^"]*"$)')


def pieces(f, limit=MAX_PLACES):
    """The places a reviewer would comment on, one function each: every function the change wrote
    (small helpers included), then every existing function it changed, then runs of added code
    outside any function, as (lo, hi, name, added code lines). Returns them with the masked text of
    the head and base."""
    masked = asks.masked_text(f.head, f.language).split('\n')
    base_masked = asks.masked_text(f.base, f.language).split('\n') if f.base is not None else []
    added_code = set(f.added_code_lines())
    new, changed, inside = [], [], set()
    for u in sorted(f.new_units(), key=lambda u: (u.start, -u.end)):
        if any(o.start <= u.start and u.end <= o.end for o in new):
            continue  # nested in a new function already listed
        rows = [n for n in range(u.start, u.end + 1) if n in added_code]
        if rows:
            new.append(u)
    for u in f.top_units:
        inside.update(range(u.start, u.end + 1))
        if any(o.start >= u.start and o.end <= u.end for o in new) and not [n for n in range(u.start, u.end + 1) if n in added_code
                                                                             and not any(o.start <= n <= o.end for o in new)]:
            continue  # its added lines all sit in new functions listed already
        if any(o.start <= u.start and u.end <= o.end for o in new):
            continue
        rows = [n for n in range(u.start, u.end + 1) if n in added_code and not any(o.start <= n <= o.end for o in new)]
        if len(rows) >= 1:
            changed.append((u, rows))
    out = [(u.start, u.end, u.name, [n for n in range(u.start, u.end + 1) if n in added_code]) for u in new]
    out += [(u.start, u.end, u.name, rows) for u, rows in sorted(changed, key=lambda r: (-len(r[1]), r[0].start))]
    # added code outside any function, leaving out import lines: an import block is not a place to simplify
    loose = sorted(n for n in added_code if n not in inside and not IMPORT_LINE.match(masked[n - 1]))
    for lo, hi in units_ranges(loose, gap=2):
        rows = [n for n in loose if lo <= n <= hi]
        for k in range(0, len(rows), LOOSE_LINES):
            part = rows[k:k + LOOSE_LINES]
            if len(part) >= 2:
                out.append((max(1, part[0] - 3), min(len(masked), part[-1] + 3), None, part))
    places, total = [], 0
    for lo, hi, name, rows in out:
        if hi - lo + 1 > PLACE_LINES:
            lo, hi = max(lo, rows[0] - 10), min(hi, rows[-1] + 10)
        size = sum(len(masked[n - 1]) + 2 for n in range(lo, hi + 1))
        if len(places) >= limit or total + size > PLACE_BYTES:
            break
        places.append((lo, hi, name, rows))
        total += size
    return places, (masked, base_masked)


def piece_state(f, lo, hi, name, masked):
    """One place as a unified diff: head lines lo..hi, with the base lines the change removed there."""
    head_m, base_m = masked if isinstance(masked, tuple) else (masked, [])
    removed_before = {}  # head line -> base lines removed just before it
    offset = 0           # base line = head line - offset, for unchanged lines before lo
    for blo, bhi, hlo, hhi in f.diff.hunks:
        if hlo <= lo and hhi < lo:
            offset += (hhi - hlo + 1) - (bhi - blo + 1)
        if bhi >= blo and lo <= max(hlo, 1) <= hi + 1:
            removed_before.setdefault(max(hlo, 1), []).extend(range(blo, bhi + 1))
    rows, nb, nh = [], 0, 0
    for n in range(lo, hi + 1):
        for b in removed_before.get(n, ()):
            rows.append('-' + (base_m[b - 1] if b - 1 < len(base_m) else ''))
            nb += 1
        if n in f.added:
            rows.append('+' + head_m[n - 1])
        else:
            rows.append(' ' + head_m[n - 1])
            nb += 1
        nh += 1
    for b in removed_before.get(hi + 1, ()):
        rows.append('-' + (base_m[b - 1] if b - 1 < len(base_m) else ''))
        nb += 1
    bstart = max(0, lo - offset) if nb else 0
    piece = '--- a/%s\n+++ b/%s\n@@ -%d,%d +%d,%d @@%s\n%s\n' % (f.path, f.path, bstart, nb, lo, nh, (' ' + name) if name else '', '\n'.join(rows))
    return asks.state(piece, asks.language(f.path), asks.MASKED_FORMAT)


def verb_of(ans):
    """The question whose answer stands out most against its usual level on dev: the largest gap
    between its log-odds and that question's mean log-odds over dev functions. (Dividing by each
    question's spread as well picked "reuse" too often; on dev's injected smells the plain gap named
    the right kind for 112 of 164 against 100.)"""
    def gap(q):
        st = VERB_STATS.get(q)
        if not st or q not in ans:
            return -99.0
        return logit(ans[q]) - st['mean']
    return max(VERB_TEXT, key=gap)


class Scorer:
    def __init__(self, args, policy, model_path=None):
        self.args = args
        self.model = LogisticModel.load(model_path or os.path.join(HERE, 'model.json'))
        self.threshold = args.threshold if args.threshold is not None else (policy.threshold or self.model.threshold)
        self.uses_pieces = any(n.startswith('piece_') for n in self.model.names)
        self.warnings = []
        cache = None
        if not args.no_cache:
            cache, warn = perimeter.cache_dir(args.repo)
            if warn:
                self.warnings.append(warn)
            if cache:
                cache = os.path.join(cache, 'qualm-next')
        self.client = jev.Client(budget=args.budget, cache_dir=cache)

    def usage(self):
        u = {'jev_requests': self.client.requests, 'jev_input_tokens': self.client.input_tokens, 'jev_cost_usd': round(self.client.cost, 6)}
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

    def ask_places(self, f, use_cache=True):
        """Jev's answers for each place (asked once per run; close-call re-readings reuse them)."""
        places, masked = pieces(f)
        answers = [self.client.ask(piece_state(f, lo, hi, name, masked), asks.HUNK_QUESTIONS, use_cache=use_cache) for lo, hi, name, _ in places]
        return places, answers

    def ask_file(self, ctx, f, feats, use_cache=True, reading=0, places=None):
        raw_d, masked_d = self.views(ctx, f)
        lang = asks.language(f.path)
        raw = asks.ask_pieces(self.client, raw_d, lang, asks.QUALM, asks.DIFF_FORMAT, use_cache, reading) if raw_d.strip() else None
        masked = asks.ask_pieces(self.client, masked_d, lang, asks.MASKED_QUESTIONS, asks.MASKED_FORMAT, use_cache, reading) if masked_d.strip() else None
        if places is None:
            places = self.ask_places(f, use_cache) if self.uses_pieces else ([], [])
        x = feature_vector(raw, masked, feats, places[1], [len(r[3]) for r in places[0]])
        return self.model.predict(x), raw, masked, x, masked_d, places

    def judge(self, ctx, f, feats):
        use_cache = not self.args.no_cache
        p, raw, masked, x, masked_d, places = self.ask_file(ctx, f, feats, use_cache=use_cache)
        ps = [p]
        if abs(p - self.threshold) <= CLOSE:
            # more readings, and the median decides; each reading has its own cache entry, so a
            # second run with the cache on reaches the same verdict
            for target, margin in READINGS:
                while len(ps) < target:
                    ps.append(self.ask_file(ctx, f, feats, use_cache=use_cache, reading=len(ps), places=places)[0])
                if margin is None or abs(statistics.median(ps) - self.threshold) > margin:
                    break
            p = statistics.median(ps)
        return {'p': p, 'readings': ps, 'raw': raw, 'masked': masked, 'x': x, 'masked_diff': masked_d, 'places': places}

    # ---------------------------------------------------------------- findings
    def localize(self, f, places, found):
        """Findings for the places a reviewer would push back on, each no wider than one function.
        A place whose added lines precise detector findings already cover is left to them. If no
        place reaches 0.5 and no precise finding exists, the strongest place is still named, so a
        failing file always says where to start."""
        if not places or not places[0]:
            places = self.ask_places(f, use_cache=not self.args.no_cache)
        precise = {n for d in found if d['confidence'] >= 0.6 and d['lines'][1] - d['lines'][0] + 1 <= PRECISE
                   for n in range(d['lines'][0], d['lines'][1] + 1)}
        precise_ids = [(d['id'], d['lines']) for d in found if d['confidence'] >= 0.6 and d['lines'][1] - d['lines'][0] + 1 <= PRECISE]
        asked = []
        for (lo, hi, name, rows), ans in zip(*places):
            if sum(1 for n in rows if n in precise) >= 0.5 * len(rows):
                continue
            verb = verb_of(ans)
            # the detectors already name the exact lines of this kind of problem inside the place
            if any(i in VERB_DETECTORS.get(verb, ()) and lo <= ls[0] and ls[1] <= hi for i, ls in precise_ids):
                continue
            asked.append((ans['push_back'], lo, hi, name, ans, rows))
        asked.sort(key=lambda r: (-r[0], r[1]))
        chosen = [r for r in asked if r[0] >= 0.5]
        if not chosen and asked and not precise:
            chosen = asked[:1]
        findings = []
        for pb, plo, phi, name, ans, rows in chosen:
            verb = verb_of(ans)
            what, fix = VERB_TEXT[verb]
            lo, hi = min(rows), max(rows)
            span = 'Line %d' % lo if lo == hi else 'Lines %d-%d' % (lo, hi)
            where = ' (in %s)' % name if name else ''
            findings.append({'id': 'review.' + verb, 'lines': [lo, hi], 'message': '%s%s %s.' % (span, where, what), 'fix': fix,
                             'confidence': round(pb, 2), 'related': [], 'place': [plo, phi]})
        return findings

    # ---------------------------------------------------------------- verdict
    def score(self, ctx, found, feats):
        if not ctx.files:
            return {}
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
        out = {}
        for f in ctx.files:
            r = results[f.path]
            if 'error' in r:
                out[f.path] = {'error': r['error']}
                continue
            p = r['p']
            failed = p >= self.threshold
            score = p
            dets = [d for d in found[f.path] if d['confidence'] >= DET_ADVICE_MIN]
            findings, advice = [], []
            if failed:
                try:
                    if not r['places'] or not r['places'][0]:
                        r['places'] = self.ask_places(f, use_cache=not self.args.no_cache)
                    located = self.localize(f, r['places'], dets)
                except jev.JevError as e:
                    located = []
                    self.warnings.append('%s: findings could not be localized (%s)' % (f.path, e))
                # a detector finding is part of the verdict when it is an exact leftover or sits in a
                # place the reviewer questions flagged; anything else is advice
                spans = [d.get('place') or d['lines'] for d in located]
                for d in dets:
                    lo, hi = d['lines']
                    if d['id'] in LEFTOVERS and hi - lo + 1 <= PRECISE * 3 or any(s[0] <= lo and hi <= s[1] for s in spans):
                        findings.append(d)
                    else:
                        advice.append(d)
                findings += located
                findings.sort(key=lambda d: (-d['confidence'], d['lines'][0]))
            else:
                advice = dets
            signals = {'p_model': round(p, 4), 'readings': [round(v, 4) for v in r['readings']],
                       'raw': r['raw'], 'masked': r['masked'], 'top_features': self.model.contributions(r['x'])}
            if failed and r.get('places') and r['places'][0]:
                signals['places'] = [{'lines': [lo, hi], 'name': name, 'answers': a} for (lo, hi, name, _), a in zip(*r['places'])]
            out[f.path] = {'score': score, 'threshold': self.threshold, 'failed': failed, 'abstained': False,
                           'findings': findings, 'advice': advice, 'signals': signals}
        return out
