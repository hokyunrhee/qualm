"""b-offline's verdict: a logistic model over the change's shape and the detectors' findings.

The model (model.json) is trained on the dev split only (train.py), with folds grouped by
repository, and its threshold is chosen on out-of-fold predictions. Scoring is plain arithmetic.
"""
import os

from gatecore.model import LogisticModel

VERSION = '0.1.0'
HERE = os.path.dirname(os.path.realpath(__file__))
ADVICE_MIN = 0.6    # findings shown on a passing file
FAILING_MIN = 0.3   # findings shown on a failing file


class Scorer:
    def __init__(self, args, policy, model_path=None):
        self.model = LogisticModel.load(model_path or os.path.join(HERE, 'model.json'))
        self.threshold = args.threshold or policy.threshold or self.model.threshold
        self.warnings = []

    def usage(self):
        return {'llm_cost_estimated': False}

    def score(self, ctx, found, feats):
        out = {}
        for f in ctx.files:
            x = feats[f.path]
            p = self.model.predict(x)
            failed = p >= self.threshold
            keep = FAILING_MIN if failed else ADVICE_MIN
            out[f.path] = {
                'score': p, 'threshold': self.threshold, 'failed': failed, 'abstained': False,
                'findings': [d for d in found[f.path] if d['confidence'] >= keep],
                'signals': {'top_features': self.model.contributions(x)},
            }
        return out
