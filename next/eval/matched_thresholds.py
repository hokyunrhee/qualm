"""Thresholds that give each candidate the baseline's false-alarm rate on natural negatives, fixed on dev.

    python3 -I matched_thresholds.py DEV.jsonl.gz --baseline NAME=run.jsonl --run NAME=run.jsonl [...]
                                     [--subset-run NAME=ids.txt] --out matched.json

The target is the share of natural negatives (approved, post-fix and surviving files) the baseline
fails at its own gate on dev. For each candidate, the threshold is the lowest score at which its
own false-alarm rate on dev's natural negatives is at most the target. Applied unchanged to the
held-out split, it compares recall and precision at an equal false-alarm rate, which each
candidate's own gate (set by different rules) does not. A candidate scored only on a subset
(--subset-run) has its items weighted back to the full split's label-source mix.
"""
import argparse, collections, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from metrics import load_labels, load_run, join  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('split')
    ap.add_argument('--baseline', required=True)
    ap.add_argument('--run', action='append', default=[])
    ap.add_argument('--subset-run', action='append', default=[])
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    items = load_labels(a.split)
    subsets = {k: set(l.strip() for l in open(v) if l.strip()) for k, v in (x.split('=', 1) for x in a.subset_run)}
    full_src = collections.Counter(i['source'] for i in items)
    bname, bpath = a.baseline.split('=', 1)
    rows, _ = join(items, load_run(bpath))
    neg = [r for r in rows if r['natural'] and not r['label']]
    target = sum(r['failed'] for r in neg) / len(neg)
    out = {'target_fpr_natural_negatives': round(target, 4), 'baseline': bname, 'thresholds': {}}
    for spec in a.run:
        name, path = spec.split('=', 1)
        its = [i for i in items if i['item_id'] in subsets[name]] if name in subsets else items
        sub_src = collections.Counter(i['source'] for i in its)
        w = {k: full_src[k] / sub_src[k] for k in sub_src} if name in subsets else collections.defaultdict(lambda: 1.0)
        rows, _ = join(its, load_run(path))
        neg = [r for r in rows if r['natural'] and not r['label']]
        pos = [r for r in rows if r['label']]
        wn = sum(w[r['source']] for r in neg)
        best = None
        for t in sorted({r['score'] for r in rows}):
            fpr = sum(w[r['source']] for r in neg if r['score'] >= t) / wn
            if fpr <= target:
                best = t
                break
        rec = sum(w[r['source']] for r in pos if r['score'] >= best) / sum(w[r['source']] for r in pos) if best is not None else None
        out['thresholds'][name] = {'threshold': best, 'dev_recall_at_it': round(rec, 4) if rec is not None else None,
                                   'weighted_subset': name in subsets}
        print(name, out['thresholds'][name])
    print('target', out['target_fpr_natural_negatives'])
    json.dump(out, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
