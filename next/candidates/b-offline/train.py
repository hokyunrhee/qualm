"""Train b-offline's model on the dev split only.

    python3 -I train.py DEV.jsonl.gz [--features CACHE.json] [--out model.json] [--report cv.json]
                        [--precision-floor P]

Features come from gatecore.offline (what `run` computes, without git). Folds are grouped by
repository, so no repository is in a model's training and test folds at once. The reported
numbers are out-of-fold. The final model is fitted on all of dev, with a Platt step fitted on the
out-of-fold logits, and a threshold chosen on the out-of-fold probabilities: the highest recall
whose precision stays at or above the floor (or the best F1 when no floor is given).
"""
import argparse, gzip, json, math, os, sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '_core'))

from gatecore import features, offline  # noqa: E402


def featurize_case(line):
    c = json.loads(line)
    want = {it['path']: it for it in c['items'] if it.get('label') is not None}
    if not want:
        return []
    res = offline.case_features(c, set(want))
    rows = []
    for path, it in want.items():
        if path not in res:
            continue
        x, found = res[path]
        rows.append({'item_id': it['item_id'], 'case_id': c['case_id'], 'repo': c['repo'], 'origin': c['source'].split(':')[0],
                     'language': it['language'], 'label_source': it['label_source'], 'label': it['label'], 'x': x,
                     'detectors': sorted({d['id'] for d in found})})
    return rows


def featurize(dev, jobs=4):
    with gzip.open(dev, 'rt') as fh:
        lines = fh.readlines()
    rows = []
    with ProcessPoolExecutor(jobs) as ex:
        for part in ex.map(featurize_case, lines, chunksize=8):
            rows.extend(part)
    rows.sort(key=lambda r: r['item_id'])
    return rows


def matrix(rows, names):
    import numpy as np
    X = np.array([[r['x'].get(n, 0.0) for n in names] for r in rows], dtype=float)
    y = np.array([r['label'] for r in rows], dtype=int)
    return X, y


def metrics(y, p):
    from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss
    return {'pr_auc': round(float(average_precision_score(y, p)), 4), 'roc_auc': round(float(roc_auc_score(y, p)), 4),
            'brier': round(float(brier_score_loss(y, p)), 4), 'n': int(len(y)), 'positives': int(y.sum())}


def pick_threshold(y, p, floor=None):
    import numpy as np
    best = None
    for t in np.unique(np.round(p, 3)):
        f = p >= t
        tp = int((f & (y == 1)).sum())
        fp = int((f & (y == 0)).sum())
        fn = int((~f & (y == 1)).sum())
        if tp == 0:
            continue
        prec, rec = tp / (tp + fp), tp / (tp + fn)
        f1 = 2 * prec * rec / (prec + rec)
        key = (rec, prec) if floor is not None else (f1,)
        if floor is not None and prec < floor:
            continue
        if best is None or key > best[0]:
            best = (key, float(t), prec, rec, f1)
    if best is None:
        return 0.99, {}
    return best[1], {'precision': round(best[2], 4), 'recall': round(best[3], 4), 'f1': round(best[4], 4)}


def fit_lr(X, y, C, w=None):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(X)
    lr = LogisticRegression(C=C, max_iter=5000).fit(sc.transform(X), y, sample_weight=w)
    return sc, lr


def oof(rows, X, y, C, w=None, folds=5):
    import numpy as np
    from sklearn.model_selection import GroupKFold
    groups = np.array([r['repo'] for r in rows])
    z = np.zeros(len(y))
    for tr, te in GroupKFold(n_splits=folds).split(X, y, groups):
        sc, lr = fit_lr(X[tr], y[tr], C, None if w is None else w[tr])
        z[te] = lr.decision_function(sc.transform(X[te]))
    return z


def platt(z, y):
    from sklearn.linear_model import LogisticRegression
    m = LogisticRegression(C=1e6, max_iter=1000).fit(z.reshape(-1, 1), y)
    return float(m.coef_[0][0]), float(m.intercept_[0])


def main():
    import numpy as np
    ap = argparse.ArgumentParser()
    ap.add_argument('dev')
    ap.add_argument('--features', default=None)
    ap.add_argument('--out', default=os.path.join(HERE, 'model.json'))
    ap.add_argument('--report', default=None)
    ap.add_argument('--precision-floor', type=float, default=None)
    ap.add_argument('--jobs', type=int, default=4)
    a = ap.parse_args()
    if a.features and os.path.exists(a.features):
        rows = json.load(open(a.features))
    else:
        rows = featurize(a.dev, a.jobs)
        if a.features:
            json.dump(rows, open(a.features, 'w'))
    names = features.names()
    X, y = matrix(rows, names)
    print(len(rows), 'items,', int(y.sum()), 'positives', flush=True)
    sigmoid = lambda z: 1 / (1 + np.exp(-z))
    grid = {}
    for C in (0.01, 0.03, 0.1, 0.3, 1.0):
        z = oof(rows, X, y, C)
        grid[C] = (z, metrics(y, sigmoid(z)))
        print('C=%g' % C, grid[C][1], flush=True)
    C = max(grid, key=lambda c: grid[c][1]['pr_auc'])
    z = grid[C][0]
    a_, b_ = platt(z, y)
    p = sigmoid(a_ * z + b_)
    thr, op = pick_threshold(y, p, a.precision_floor)
    sc, lr = fit_lr(X, y, C)
    model = {'names': names, 'mean': [float(v) for v in sc.mean_], 'scale': [float(v) for v in sc.scale_],
             'coef': [float(v) for v in lr.coef_[0]], 'intercept': float(lr.intercept_[0]), 'platt': [a_, b_], 'threshold': round(thr, 3),
             'meta': {'trained_on': 'dev split only', 'items': len(rows), 'positives': int(y.sum()), 'C': C,
                      'threshold_rule': 'highest out-of-fold recall with precision >= %s' % a.precision_floor if a.precision_floor else 'best out-of-fold F1'}}
    json.dump(model, open(a.out, 'w'), indent=1)
    by = {}
    for key in ('language', 'label_source', 'origin'):
        by[key] = {}
        for v in sorted({r[key] for r in rows}):
            idx = np.array([r[key] == v for r in rows])
            if idx.sum() and 0 < y[idx].sum() < idx.sum():
                by[key][v] = metrics(y[idx], p[idx])
            else:
                by[key][v] = {'n': int(idx.sum()), 'positives': int(y[idx].sum()), 'flagged': int((p[idx] >= thr).sum())}
    rep = {'oof': metrics(y, p), 'C': C, 'grid': {str(c): grid[c][1] for c in grid}, 'threshold': thr, 'operating_point_oof': op,
           'by': by, 'weights': sorted(zip(names, [round(float(v), 3) for v in lr.coef_[0]]), key=lambda t: -abs(t[1]))}
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        json.dump(rep, open(a.report, 'w'), indent=1)
    print(json.dumps({k: rep[k] for k in ('oof', 'C', 'threshold', 'operating_point_oof')}, indent=1))
    print('top weights', rep['weights'][:12])


if __name__ == '__main__':
    main()
