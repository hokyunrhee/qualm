"""Cost and wall time per pull request, from recorded runs.

    python3 -I cost_latency.py NAME=run.jsonl [...] [--out out.json]

Cost is what each report's usage says: Jev's own figures, plus LLM costs estimated from token counts
at list price (marked estimated). Wall time is the run's seconds; for a candidate whose LLM answers came
from subagents through --llm-dir, the recorded time is the final pass with the answers already there,
so it leaves out the time a real API call would take.
"""
import argparse, json


def pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs', nargs='+')
    ap.add_argument('--out')
    a = ap.parse_args()
    out = {}
    for spec in a.runs:
        name, path = spec.split('=', 1)
        recs = {}
        for line in open(path):
            r = json.loads(line)
            if r.get('exit') in (0, 1) or r['case_id'] not in recs:
                recs[r['case_id']] = r
        cost, secs, llm = [], [], 0
        for r in recs.values():
            u = (r.get('report') or {}).get('usage') or {}
            c = float(u.get('jev_cost_usd') or 0) + float(u.get('llm_cost_usd') or 0)
            llm += 1 if u.get('llm_requests') else 0
            cost.append(c)
            secs.append(float(r.get('seconds') or 0))
        out[name] = {'pull_requests': len(recs), 'cost_mean': round(sum(cost) / len(cost), 6), 'cost_median': round(pct(cost, 0.5), 6),
                     'cost_p95': round(pct(cost, 0.95), 6), 'cost_max': round(max(cost), 6), 'seconds_mean': round(sum(secs) / len(secs), 2),
                     'seconds_median': round(pct(secs, 0.5), 2), 'seconds_p95': round(pct(secs, 0.95), 2), 'seconds_max': round(max(secs), 2),
                     'llm_cost_estimated': llm > 0}
        print(name, json.dumps(out[name]))
    if a.out:
        json.dump(out, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
