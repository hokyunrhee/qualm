"""c-llm's verdict: one Claude Haiku 5.5 reviewer per judged file.

Each judged file gets a prompt (prompt.py) with an outline and a numbered view of its change. The
answer's probability is the file's score; the file fails when the score reaches the threshold.
The answer's comments become findings only after verify.py has checked their lines and kept
the file's comments and strings out of them. Changes that write no new code are settled by rule
(mechanical.py) and cost no call.

In this study the calls go through the file protocol of gatecore.llmdir: every request a run
needs is written in one pass, and the run exits 2 with "needs llm" until all are answered.
"""
import hashlib, json, os

from gatecore import cli, gitio, llmdir
import mechanical, prompt, verify

VERSION = '0.1.0'
HERE = os.path.dirname(os.path.realpath(__file__))
SETTINGS = os.path.join(HERE, 'settings.json')
NO_LLM = ('no LLM is configured, and every judged file needs one. In this study pass --llm-dir DIR: the run writes its '
          'requests to DIR/requests and judges once DIR/responses holds an answer for each. (A shipped version calls the '
          'Anthropic Messages API with ANTHROPIC_API_KEY instead.)')


def load_settings(path=SETTINGS):
    with open(path) as fh:
        s = json.load(fh)
    return {'threshold': float(s.get('threshold', 0.5)), 'samples': max(1, int(s.get('samples', 1))),
            'mechanical_score': float(s.get('mechanical_score', 0.02))}


def request_key(model, system, text, schema):
    """The key gatecore.llmdir.LLM files a request under (same recipe), so an unreadable answer can be
    reported instead of asked again."""
    req = {'model': model, 'system': system, 'prompt': text, 'schema': schema}
    return hashlib.sha256(json.dumps(req, sort_keys=True).encode()).hexdigest()[:32]


def sample_text(text, i):
    """The prompt for sample i. Sample 0 is the plain prompt, so raising `samples` reuses its answer;
    later samples differ by one line so their keys differ."""
    return text if i == 0 else '%s\n\nReview pass %d.' % (text, i + 1)


class Scorer:
    def __init__(self, args, policy, settings=None):
        s = settings or load_settings()
        self.threshold = args.threshold or policy.threshold or s['threshold']
        self.samples = s['samples']
        self.mechanical_score = s['mechanical_score']
        self.dir = os.path.abspath(args.llm_dir) if args.llm_dir else None
        self.llm = llmdir.LLM(self.dir) if self.dir else None
        self.warnings = []

    def usage(self):
        if not self.llm:
            return {'llm_cost_estimated': True}
        return {'llm_requests': self.llm.requests, 'llm_input_tokens': self.llm.input_tokens, 'llm_output_tokens': self.llm.output_tokens,
                'llm_cost_usd': round(self.llm.cost, 6), 'llm_cost_estimated': True}

    def score(self, ctx, found, feats):
        out, ask = {}, []
        for f in ctx.files:
            reason = mechanical.settled(f)
            if reason:
                s = self.mechanical_score
                out[f.path] = {'score': s, 'threshold': self.threshold, 'failed': s >= self.threshold, 'abstained': False,
                               'findings': [], 'signals': {'settled_by_rule': reason}}
            else:
                ask.append(f)
        if ask and not self.llm:
            raise gitio.GateError(NO_LLM)
        waiting = 0
        for f in ask:
            req = prompt.build(f)
            answers, keys, unreadable = [], [], []
            for i in range(self.samples):
                text = sample_text(req.text, i)
                key = request_key(prompt.MODEL, prompt.SYSTEM, text, prompt.SCHEMA)
                keys.append(key)
                if self.unreadable(key):
                    unreadable.append(key)
                    continue
                answers.append(self.llm.ask(prompt.MODEL, prompt.SYSTEM, text, prompt.SCHEMA))
            if unreadable:
                out[f.path] = self.error('the reviewer\'s answer in responses/%s.json is not a JSON object' % unreadable[0])
            elif any(a is None for a in answers):
                waiting += 1
            else:
                out[f.path] = self.judge(f, req, answers, keys)
        if self.llm and self.llm.pending:
            raise cli.NeedsLLM('%d request%s for %d file%s written to %s; answer each in %s and run again' % (
                self.llm.pending, '' if self.llm.pending == 1 else 's', waiting, '' if waiting == 1 else 's',
                os.path.join(self.dir, 'requests'), os.path.join(self.dir, 'responses')))
        return out

    def unreadable(self, key):
        path = os.path.join(self.dir, 'responses', key + '.json')
        if not os.path.exists(path):
            return False
        try:
            with open(path) as fh:
                return not isinstance(json.load(fh), dict)
        except (OSError, ValueError):
            return True

    def error(self, reason):
        # the core reads score and threshold before it looks at the error
        return {'score': 1.0, 'threshold': self.threshold, 'failed': True, 'abstained': False, 'findings': [], 'signals': {}, 'error': reason}

    def judge(self, f, req, answers, keys):
        parsed = [verify.parse(a) for a in answers]
        for p, key in zip(parsed, keys):
            if p is None:
                return self.error('the reviewer\'s answer in responses/%s.json has no probability from 0 to 1' % key)
        taint = verify.Taint(f)
        n_lines = len(f.lines)
        per_sample, records = [], []
        for p in parsed:
            kept, record = verify.check(f, p.comments, taint, n_lines)
            per_sample.append(kept)
            records.append(record)
        n = len(parsed)
        score = sum(p.probability for p in parsed) / n
        votes = sum(1 for p in parsed if p.blocking)
        failed = score >= self.threshold
        findings = merge(per_sample, score)
        if failed and not findings:
            findings = [verify.generic(f, score)]
        signals = {
            'llm.probability': round(score, 4),
            'llm.blocking': votes * 2 > n,
            'llm.samples': n,
            'llm.probabilities': [round(p.probability, 4) for p in parsed],
            'llm.blocking_votes': votes,
            'llm.comments': records,
            'llm.keys': keys,
            'prompt.tokens_est': req.tokens,
            'view.added_lines_left_out': len(req.view.left_out),
        }
        notes = [note for p in parsed for note in p.notes]
        if notes:
            signals['llm.notes'] = notes
        return {'score': score, 'threshold': self.threshold, 'failed': failed, 'abstained': False, 'findings': findings, 'signals': signals}


def same_spot(a, b):
    """Two comments about the same thing: same kind, overlapping lines."""
    return a['id'] == b['id'] and a['lines'][0] <= b['lines'][1] and b['lines'][0] <= a['lines'][1]


def merge(per_sample, score):
    """At most three findings across samples, the ones most samples agree on first. A comment
    another sample already made at the same spot is not repeated; comments of one sample are
    never merged with each other. Confidence is the score times the share of samples that agree."""
    n = len(per_sample)
    pool = []
    for i, kept in enumerate(per_sample):
        for j, c in enumerate(kept):
            support = sum(1 for other in per_sample if any(same_spot(c, d) for d in other)) / n
            pool.append((-support, i, j, c))
    out, origin = [], []
    for neg_support, i, _, c in sorted(pool, key=lambda t: t[:3]):
        if len(out) >= verify.MAX_COMMENTS or any(same_spot(c, d) and k != i for d, k in zip(out, origin)):
            continue
        out.append(dict(c, confidence=round(score * -neg_support, 2)))
        origin.append(i)
    return out
