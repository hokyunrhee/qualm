"""Verdict flip rate across repeated runs of one candidate on the same cases.

    python3 -I determinism.py SPLIT_FILE RUN1.jsonl RUN2.jsonl ... [--subset ids.txt]

For every scored item judged in all runs: it flips if its verdict (failed or not) is not the same
in every run. Also reports the spread of scores (max - min) and how many items sit within 0.05 of
the threshold. Each run must have been made with caches off.
"""
import argparse, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import load_labels, load_run, file_view  # noqa: E402


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
    flips, spreads, n, case_flips = 0, [], 0, set()
    for it in items:
        views = [file_view(r.get(it['case_id']), it['path']) for r in runs]
        if any(v is None for v in views):
            continue
        n += 1
        verdicts = {v[1] for v in views}
        if len(verdicts) > 1:
            flips += 1
            case_flips.add(it['case_id'])
        spreads.append(max(v[0] for v in views) - min(v[0] for v in views))
    cases = {it['case_id'] for it in items}
    out = {'runs': len(runs), 'items': n, 'flipped_items': flips, 'flip_rate': flips / n if n else None,
           'cases': len(cases), 'flipped_cases': len(case_flips),
           'max_score_spread': max(spreads) if spreads else None,
           'mean_score_spread': sum(spreads) / len(spreads) if spreads else None}
    print(json.dumps(out))
    if a.out:
        json.dump(out, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
