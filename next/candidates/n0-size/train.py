"""The size-only yardstick: a logistic regression on log added lines, log removed lines and
language, trained on dev with folds grouped by repository. A candidate that does not beat it on
natural labels has learned the size of the change, not its shape.

    python3 -I train.py FEATURES.json [--out model.json] [--oof oof.json]

FEATURES.json is b-offline's feature cache (train.py --features), the same gatecore features `run` computes.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '_core'))
import numpy as np  # noqa: E402
from gatecore import features  # noqa: E402

NAMES = ['log_added', 'log_removed', 'new_file'] + ['lang_' + l for l in features.LANGS]


def main():
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import GroupKFold
    from sklearn.preprocessing import StandardScaler
    ap = argparse.ArgumentParser()
    ap.add_argument('features')
    ap.add_argument('--out', default=os.path.join(HERE, 'model.json'))
    ap.add_argument('--oof')
    a = ap.parse_args()
    rows = json.load(open(a.features))
    X = np.array([[r['x'][n] for n in NAMES] for r in rows])
    y = np.array([r['label'] for r in rows])
    g = np.array([r['repo'] for r in rows])
    p = np.zeros(len(y))
    for tr, te in GroupKFold(5).split(X, y, g):
        sc = StandardScaler().fit(X[tr])
        lr = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(X[tr]), y[tr])
        p[te] = lr.predict_proba(sc.transform(X[te]))[:, 1]
    sc = StandardScaler().fit(X)
    lr = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(X), y)
    # the operating point: best out-of-fold F1 (a yardstick, not a gate anyone ships)
    best = max(((2 * ((p >= t) & (y == 1)).sum() / ((p >= t).sum() + (y == 1).sum()), t) for t in np.unique(np.round(p, 3))))
    json.dump({'names': NAMES, 'mean': list(map(float, sc.mean_)), 'scale': [float(s) or 1.0 for s in sc.scale_], 'coef': list(map(float, lr.coef_[0])),
               'intercept': float(lr.intercept_[0]), 'threshold': round(float(best[1]), 3),
               'meta': {'trained_on': 'dev split only', 'threshold_rule': 'best out-of-fold F1'}}, open(a.out, 'w'), indent=1)
    if a.oof:
        json.dump({r['item_id']: round(float(v), 5) for r, v in zip(rows, p)}, open(a.oof, 'w'))
    print('n0-size out-of-fold: PR-AUC %.4f ROC %.4f, threshold %.3f' % (average_precision_score(y, p), roc_auc_score(y, p), best[1]))


if __name__ == '__main__':
    main()
