"""The recalibrated-baseline yardstick: qualm 0.1.1's push_back answer, mapped to a probability by
a Platt step fitted on dev (folds grouped by repository for the out-of-fold numbers).

    python3 -I train.py DEV_BASELINE_RUN.jsonl DEV.jsonl.gz [--out platt.json] [--oof oof.json]
"""
import argparse, gzip, json, math, os

HERE = os.path.dirname(os.path.realpath(__file__))


def logit(p):
    p = min(max(p, 1e-3), 1 - 1e-3)
    return math.log(p / (1 - p))


def main():
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, brier_score_loss
    from sklearn.model_selection import GroupKFold
    ap = argparse.ArgumentParser()
    ap.add_argument('run')
    ap.add_argument('dev')
    ap.add_argument('--out', default=os.path.join(HERE, 'platt.json'))
    ap.add_argument('--oof')
    a = ap.parse_args()
    pb = {}
    for line in open(a.run):
        r = json.loads(line)
        if r['exit'] in (0, 1) and r.get('report'):
            for f in r['report']['files']:
                if f.get('status') == 'judged':
                    pb[r['case_id'] + '::' + f['path']] = f['signals']['push_back']
    rows = []
    for line in gzip.open(a.dev, 'rt'):
        c = json.loads(line)
        for it in c['items']:
            if it.get('label') is not None and it['item_id'] in pb:
                rows.append((it['item_id'], c['repo'], logit(pb[it['item_id']]), it['label']))
    z = np.array([[r[2]] for r in rows])
    y = np.array([r[3] for r in rows])
    g = np.array([r[1] for r in rows])
    p = np.zeros(len(y))
    for tr, te in GroupKFold(5).split(z, y, g):
        m = LogisticRegression(C=1e6).fit(z[tr], y[tr])
        p[te] = m.predict_proba(z[te])[:, 1]
    m = LogisticRegression(C=1e6).fit(z, y)
    a_, b_ = float(m.coef_[0][0]), float(m.intercept_[0])
    # the threshold that keeps qualm's own operating point: push_back >= 0.6 mapped through the step
    thr = 1 / (1 + math.exp(-(a_ * logit(0.6) + b_)))
    json.dump({'a': a_, 'b': b_, 'threshold': round(thr, 4), 'meta': {'trained_on': 'dev split only', 'items': len(rows),
               'threshold_rule': "qualm's push_back >= 0.6, mapped through the Platt step"}}, open(a.out, 'w'), indent=1)
    if a.oof:
        json.dump({r[0]: round(float(v), 5) for r, v in zip(rows, p)}, open(a.oof, 'w'))
    print('a-recal: %d items, out-of-fold PR-AUC %.4f Brier %.4f (raw push_back Brier %.4f), threshold %.4f' % (
        len(rows), average_precision_score(y, p), brier_score_loss(y, p), brier_score_loss(y, 1 / (1 + np.exp(-z[:, 0]))), thr))


if __name__ == '__main__':
    main()
