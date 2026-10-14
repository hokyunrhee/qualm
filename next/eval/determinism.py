"""Verdict flip rate across repeated runs of one candidate on the same cases.

    python3 -I determinism.py SPLIT_FILE RUN1.jsonl RUN2.jsonl ... [--subset ids.txt] [--out out.json]

Each run must have been made with every cache off. Three rates, each with an exact (Clopper-Pearson)
95% upper bound:
- per file: every file any run judged (scored items and the rest), flipped if its verdict is not
  the same in every run. A file a run could not judge (an error, a missing report) counts as a fail
  in that run, the way the gate treats it;
- per pull request: the run's exit code (pass, or anything else) is not the same in every run;
- pairwise: the share of files whose verdict differs between two runs, averaged over pairs of runs
  (this does not grow with the number of runs, as "not unanimous" does).
The scored-items rate of earlier versions is kept for comparison, with the spread of scores.
"""
import argparse, itertools, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import load_labels, load_run, file_view  # noqa: E402


def upper95(k, n):
    """Exact one-sided 95% upper bound for a binomial rate (Clopper-Pearson)."""
    if n == 0:
        return None
    if k >= n:
        return 1.0
    lo, hi = k / n, 1.0
    for _ in range(60):  # bisection on P(X <= k | p) = 0.05
        mid = (lo + hi) / 2
        cdf, term = 0.0, (1 - mid) ** n
        for i in range(k + 1):
            if i > 0:
                term *= (n - i + 1) / i * mid / (1 - mid)
            cdf += term
        if cdf > 0.05:
            lo = mid
        else:
            hi = mid
    return round(hi, 5)


def verdicts(rec):
    """{path: failed} for every file a run judged or could not judge; None if the case has no report."""
    if rec is None or not rec.get('report'):
        return None
    out = {}
    for f in rec['report'].get('files') or []:
        if f.get('status') == 'judged':
            out[f['path']] = bool(f.get('failed'))
        elif f.get('status') == 'error':
            out[f['path']] = True
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('split_file')
    ap.add_argument('runs', nargs='+')
    ap.add_argument('--subset')
    ap.add_argument('--out')
    a = ap.parse_args()
    subset = set(l.strip() for l in open(a.subset) if l.strip()) if a.subset else None
    items = load_labels(a.split_file, subset)
    runs = [load_run(r) for r in a.runs]
    cases = sorted({it['case_id'] for it in items})
    # per file, over every file any run judged; a case with no report fails all its files
    files, flips, pr_flips, pair_diff, pair_n = 0, 0, 0, 0, 0
    flipped = []
    for c in cases:
        vs = [verdicts(r.get(c)) for r in runs]
        exits = [(r.get(c) or {}).get('exit') for r in runs]
        if len({e == 0 for e in exits}) > 1:
            pr_flips += 1
        paths = set().union(*[v for v in vs if v])
        for p in sorted(paths):
            row = [True if v is None else v.get(p, True) for v in vs]
            files += 1
            if len(set(row)) > 1:
                flips += 1
                flipped.append([c, p, row])
            for i, j in itertools.combinations(range(len(row)), 2):
                pair_n += 1
                pair_diff += row[i] != row[j]
    # the scored-items view of earlier versions
    n, sflips, spreads = 0, 0, []
    for it in items:
        views = [file_view(r.get(it['case_id']), it['path']) for r in runs]
        if any(v is None for v in views):
            continue
        n += 1
        if len({v[1] for v in views}) > 1:
            sflips += 1
        spreads.append(max(v[0] for v in views) - min(v[0] for v in views))
    out = {'runs': len(runs), 'cases': len(cases),
           'per_file': {'files': files, 'flipped': flips, 'rate': flips / files if files else None, 'upper95': upper95(flips, files)},
           'per_pull_request': {'cases': len(cases), 'flipped': pr_flips, 'rate': pr_flips / len(cases) if cases else None,
                                'upper95': upper95(pr_flips, len(cases))},
           'pairwise_per_file': {'pairs': pair_n, 'differ': pair_diff, 'rate': pair_diff / pair_n if pair_n else None},
           'scored_items': {'items': n, 'flipped': sflips, 'rate': sflips / n if n else None, 'upper95': upper95(sflips, n),
                            'max_score_spread': max(spreads) if spreads else None,
                            'mean_score_spread': sum(spreads) / len(spreads) if spreads else None},
           'flipped_files': flipped}
    print(json.dumps({k: v for k, v in out.items() if k != 'flipped_files'}))
    if a.out:
        json.dump(out, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
