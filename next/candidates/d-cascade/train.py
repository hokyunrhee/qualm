"""Train d-cascade's model and choose its operating point and abstain band, on the dev split only.

    python3 -I train.py --features B_FEATURES.json --raw BASELINE_DEV_RUN.jsonl --masked MASKED.json
                        [--out model.json] [--band band.json] [--report cv.json] [--oof oof.json]
                        [--precision-floor P]

Inputs: the detectors' features per scored dev file (b-offline's train.py --features cache; the
same gatecore code `run` uses), the raw view's answers (qualm 0.1.1's own dev run: its request is
the raw view), and the masked view's answers (collect_dev.py).

Folds are grouped by repository. Feature sets are compared on out-of-fold PR-AUC; the chosen set
is refitted on all of dev with a Platt step fitted on its out-of-fold logits. The threshold is the
highest out-of-fold recall whose precision stays at or above the floor. The abstain band is chosen
by Learn-then-Test (fixed-sequence binomial tests): the narrowest band around the threshold such
that, outside it, the out-of-fold precision of fails is at least the fail target and the share of
positives among passes is at most the miss target, each with 95% confidence.
"""
import argparse, json, math, os, sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '_core'))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402
from gatecore import features  # noqa: E402
import scorer as S  # noqa: E402

NATURAL = ('reviewer_comment', 'followup_cleanup', 'approved_uncommented', 'approved_clean', 'approved_agent', 'post_fix', 'survived_90d')
DET = [n for n in features.names() if n.startswith(('n_', 'max_')) and n not in ('n_new_units', 'n_changed_units', 'max_new_unit', 'max_depth')] + ['copy_share']
RAW = ['raw_' + q for q in S.RAW_Q] + ['raw_missing']
MASKED = ['masked_' + q for q in S.MASKED_Q] + ['masked_missing']
BOTH = ['push_back_mean', 'push_back_gap']
SETS = {
    'raw': RAW,
    'masked': MASKED,
    'raw+masked': RAW + MASKED + BOTH,
    'raw+masked+det': RAW + MASKED + BOTH + DET,
    'raw+masked+det+shape': RAW + MASKED + BOTH + DET + ['is_script', 'comment_share'],
}


def load(a):
    rows = json.load(open(a.features))
    raw = {}
    for line in open(a.raw):
        r = json.loads(line)
        if r['exit'] not in (0, 1) or not r.get('report'):
            continue
        for f in r['report']['files']:
            if f.get('status') == 'judged':
                raw[r['case_id'] + '::' + f['path']] = f['signals']
    masked = json.load(open(a.masked))
    for r in rows:
        m = masked.get(r['item_id'])
        if m is not None and ('error' in m or 'empty' in m):
            m = None
        r['x'] = S.feature_vector(raw.get(r['item_id']), m, r['x'])
    return rows


def matrix(rows, names):
    return np.array([[r['x'].get(n, 0.0) for n in names] for r in rows], dtype=float), np.array([r['label'] for r in rows])


def metrics(y, p):
    from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
    return {'pr_auc': round(float(average_precision_score(y, p)), 4), 'roc_auc': round(float(roc_auc_score(y, p)), 4),
            'brier': round(float(brier_score_loss(y, p)), 4), 'n': int(len(y)), 'positives': int(y.sum())}


def fit(X, y, C):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(X)
    return sc, LogisticRegression(C=C, max_iter=5000).fit(sc.transform(X), y)


def oof(rows, X, y, C):
    from sklearn.model_selection import GroupKFold
    g = np.array([r['repo'] for r in rows])
    z = np.zeros(len(y))
    for tr, te in GroupKFold(n_splits=5).split(X, y, g):
        sc, lr = fit(X[tr], y[tr], C)
        z[te] = lr.decision_function(sc.transform(X[te]))
    return z


def platt(z, y):
    from sklearn.linear_model import LogisticRegression
    m = LogisticRegression(C=1e6, max_iter=1000).fit(z.reshape(-1, 1), y)
    return float(m.coef_[0][0]), float(m.intercept_[0])


def pr_at(y, p, t):
    f = p >= t
    tp, fp, fn = int((f & (y == 1)).sum()), int((f & (y == 0)).sum()), int((~f & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return prec, rec, (2 * prec * rec / (prec + rec) if prec + rec else 0.0), int(f.sum())


def pick_threshold(y, p, floor):
    best = None
    for t in np.unique(np.round(p, 3)):
        prec, rec, f1, k = pr_at(y, p, t)
        if k < 20 or prec < floor:
            continue
        if best is None or (rec, prec) > best[1:3]:
            best = (float(t), rec, prec, f1)
    return best


def binom_upper_tail(k, n, p0):
    """P(X >= k) for X ~ Binomial(n, p0)."""
    from scipy.stats import binom
    return float(binom.sf(k - 1, n, p0))


def ltt_band(y, p, thr, fail_precision, miss_rate, delta=0.05):
    """The abstain band [lo, hi) by Learn-then-Test with fixed-sequence testing on each edge.

    hi is walked down from 0.90 (fixed before looking: above it there are too few files for any test) in steps of 0.01 while the files at or above it keep a fail precision
    whose one-sided Clopper-Pearson lower bound is at least fail_precision; lo is walked up from 0
    while the files below it keep a positive share whose upper bound is at most miss_rate. Each
    edge gets delta/2, so both guarantees hold together with probability 1 - delta. The band never
    excludes the threshold."""
    hi = None
    for t in np.round(np.arange(0.90, thr - 1e-9, -0.01), 3):
        fails = p >= t
        nf, tp = int(fails.sum()), int((fails & (y == 1)).sum())
        if nf == 0:
            hi = float(t)
            continue
        if _lower_bound(tp, nf, delta / 2) >= fail_precision:
            hi = float(t)
        else:
            break
    lo = None
    for t in np.round(np.arange(0.0, thr + 1e-9, 0.01), 3):
        passes = p < t
        n, fn = int(passes.sum()), int((passes & (y == 1)).sum())
        if n == 0 or _upper_bound(fn, n, delta / 2) <= miss_rate:
            lo = float(t)
        else:
            break
    if hi is None:
        hi = 1.01  # nothing fails without the reviewer
    lo = 0.0 if lo is None else lo
    nf, npass = int((p >= hi).sum()), int((p < lo).sum())
    return lo, hi, None, nf, npass, int(len(y) - nf - npass)


def _lower_bound(k, n, delta):
    """Clopper-Pearson one-sided lower confidence bound on a proportion."""
    from scipy.stats import beta
    return 0.0 if k == 0 else float(beta.ppf(delta, k, n - k + 1))


def _upper_bound(k, n, delta):
    """Clopper-Pearson one-sided upper confidence bound on a proportion."""
    from scipy.stats import beta
    return 1.0 if k == n else float(beta.ppf(1 - delta, k + 1, n - k))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--features', required=True)
    ap.add_argument('--raw', required=True)
    ap.add_argument('--masked', required=True)
    ap.add_argument('--out', default=os.path.join(HERE, 'model.json'))
    ap.add_argument('--band', default=os.path.join(HERE, 'band.json'))
    ap.add_argument('--report')
    ap.add_argument('--oof')
    ap.add_argument('--precision-floor', type=float, required=True)
    ap.add_argument('--fail-precision', type=float, default=0.75)
    ap.add_argument('--miss-rate', type=float, default=0.10)
    ap.add_argument('--set', default=None, help='force a feature set')
    a = ap.parse_args()
    rows = load(a)
    y = np.array([r['label'] for r in rows])
    nat = np.array([r['label_source'] in NATURAL for r in rows])
    sig = lambda z: 1 / (1 + np.exp(-z))
    results = {}
    for name, cols in SETS.items():
        X, _ = matrix(rows, cols)
        best = None
        for C in (0.003, 0.01, 0.03, 0.1):
            z = oof(rows, X, y, C)
            m = metrics(y, sig(z))
            if best is None or m['pr_auc'] > best[1]['pr_auc']:
                best = (C, m, metrics(y[nat], sig(z[nat])), z)
        results[name] = best
        print('%-24s C=%-6s all %s | natural PR-AUC %.4f ROC %.4f' % (name, best[0], best[1], best[2]['pr_auc'], best[2]['roc_auc']), flush=True)
    name = a.set or max(results, key=lambda k: results[k][1]['pr_auc'])
    C, m_all, m_nat, z = results[name]
    cols = SETS[name]
    pa, pb = platt(z, y)
    p = sig(pa * z + pb)
    thr, rec, prec, f1 = pick_threshold(y, p, a.precision_floor)
    X, _ = matrix(rows, cols)
    sc, lr = fit(X, y, C)
    model = {'names': cols, 'mean': [float(v) for v in sc.mean_], 'scale': [float(v) if v else 1.0 for v in sc.scale_],
             'coef': [float(v) for v in lr.coef_[0]], 'intercept': float(lr.intercept_[0]), 'platt': [pa, pb], 'threshold': round(thr, 3),
             'meta': {'trained_on': 'dev split only', 'feature_set': name, 'C': C, 'items': len(rows), 'positives': int(y.sum()),
                      'threshold_rule': 'highest out-of-fold recall with precision >= %.3f' % a.precision_floor}}
    json.dump(model, open(a.out, 'w'), indent=1)
    band = ltt_band(y, p, thr, a.fail_precision, a.miss_rate)
    band_doc = None
    if band:
        lo, hi, w, nf, npass, nband = band
        band_doc = {'lo': round(lo, 3), 'hi': round(hi, 3), 'model': 'claude-haiku-5-5',
                    'rule': 'Learn-then-Test on out-of-fold dev probabilities, fixed-sequence on each edge: outside the band, fail precision >= %.2f and positives among passes <= %.2f, jointly at 95%% confidence' % (a.fail_precision, a.miss_rate),
                    'dev_out_of_fold': {'auto_fail': nf, 'auto_pass': npass, 'in_band': nband}}
        json.dump(band_doc, open(a.band, 'w'), indent=1)
    rep = {'feature_sets': {k: {'C': v[0], 'all': v[1], 'natural': v[2]} for k, v in results.items()}, 'chosen': name,
           'oof_calibrated': metrics(y, p), 'oof_natural': metrics(y[nat], p[nat]), 'threshold': thr,
           'operating_point_oof': {'precision': round(prec, 4), 'recall': round(rec, 4), 'f1': round(f1, 4)}, 'band': band_doc,
           'weights': sorted(zip(cols, [round(float(v), 3) for v in lr.coef_[0]]), key=lambda t: -abs(t[1]))}
    if a.report:
        json.dump(rep, open(a.report, 'w'), indent=1)
    if a.oof:
        json.dump({r['item_id']: round(float(v), 5) for r, v in zip(rows, p)}, open(a.oof, 'w'))
    print(json.dumps({k: rep[k] for k in ('chosen', 'oof_calibrated', 'oof_natural', 'threshold', 'operating_point_oof', 'band')}, indent=1))
    print('weights', rep['weights'][:14])


if __name__ == '__main__':
    main()
