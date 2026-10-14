"""A logistic model stored as plain JSON: feature names, means, scales, weights, an intercept,
and an optional Platt step. Scoring needs no third-party library."""
import json, math


class LogisticModel:
    def __init__(self, data):
        self.names = data['names']
        self.mean = data['mean']
        self.scale = data['scale']
        self.coef = data['coef']
        self.intercept = data['intercept']
        self.platt = data.get('platt')  # [a, b]: p = sigmoid(a * logit + b)
        self.threshold = data.get('threshold', 0.5)
        self.meta = data.get('meta', {})

    @classmethod
    def load(cls, path):
        with open(path) as fh:
            return cls(json.load(fh))

    def logit(self, x):
        z = self.intercept
        for k, name in enumerate(self.names):
            v = float(x.get(name, 0.0))
            s = self.scale[k] or 1.0
            z += self.coef[k] * (v - self.mean[k]) / s
        return z

    def predict(self, x):
        z = self.logit(x)
        if self.platt:
            z = self.platt[0] * z + self.platt[1]
        return 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))

    def contributions(self, x, top=4):
        rows = []
        for k, name in enumerate(self.names):
            v = float(x.get(name, 0.0))
            s = self.scale[k] or 1.0
            rows.append((self.coef[k] * (v - self.mean[k]) / s, name))
        rows.sort(reverse=True)
        return [(n, round(c, 3)) for c, n in rows[:top]]
