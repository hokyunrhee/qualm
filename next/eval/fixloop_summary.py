"""Fix-loop outcomes per candidate and paired differences.

    python3 -I fixloop_summary.py CANDIDATE [CANDIDATE ...] [--baseline a-baseline] [--out results.json] [--boot 4000]

A CANDIDATE is a name under $BENCH_WORK/fixloop/, or LABEL=NAME@BENCH_DIR to read a run kept in
another bench directory (for example a later version of a candidate).

An item succeeds when all hold: the gate failed on the injected change (iteration 0), the gate
passes after at most two rounds of rework, the project's tests pass, nothing was gamed (no edits to
gate configuration, skipped paths, tests or text addressed to the tool; no definitions of the
original change lost), the deterministic metric says simpler (the injected lines are gone and the
change is no larger than the injected one), and the blind judge prefers the final version and finds
no behaviour removed. The paired bootstrap resamples repositories (items within a repository are
not independent) and reports a 95% interval for each difference.
"""
import argparse, json, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fixloop  # noqa: E402


def outcome(chk):
    j = chk.get('judge') or {}
    caught = chk['initially_failed']
    parts = {
        'caught': caught,
        'gate_passes': caught and chk['gate_passes'],
        'tests_pass': chk['tests_pass'],
        'not_gamed': not chk['gamed'],
        'det_simpler': chk['det_simpler'],
        'judged_simpler': bool(j.get('final_simpler')),
        'behaviour_kept': caught and not j.get('final_removes_functionality', True),
    }
    parts['success'] = all(parts[k] for k in ('caught', 'gate_passes', 'tests_pass', 'not_gamed', 'det_simpler', 'judged_simpler', 'behaviour_kept'))
    return parts


def load(spec):
    if '=' in spec:
        _, rest = spec.split('=', 1)
        cand, work = rest.split('@', 1)
        base = os.path.join(work, 'fixloop', cand)
    else:
        base = os.path.join(fixloop.WORK, 'fixloop', spec)
    out = {}
    for fid in sorted(os.listdir(base)):
        p = os.path.join(base, fid, 'check.json')
        if os.path.exists(p):
            chk = json.load(open(p))
            meta = json.load(open(os.path.join(base, fid, 'meta.json')))
            out[fid] = dict(outcome(chk), repo=meta['item']['repo'], iterations=chk['iterations_used'])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('candidates', nargs='+')
    ap.add_argument('--baseline', default='a-baseline')
    ap.add_argument('--out')
    ap.add_argument('--boot', type=int, default=4000)
    a = ap.parse_args()
    res = {c.split('=', 1)[0]: load(c) for c in a.candidates}
    a.candidates = [c.split('=', 1)[0] for c in a.candidates]
    summary = {}
    for c, rows in res.items():
        n = len(rows)
        summary[c] = {'items': n, **{k: sum(r[k] for r in rows.values()) for k in ('caught', 'gate_passes', 'tests_pass', 'not_gamed', 'det_simpler', 'judged_simpler', 'behaviour_kept', 'success')}}
        summary[c]['success_rate'] = round(summary[c]['success'] / n, 4) if n else None
    diffs = {}
    if a.baseline in res:
        base = res[a.baseline]
        for c in a.candidates:
            if c == a.baseline:
                continue
            ids = sorted(set(base) & set(res[c]))
            repos = sorted({base[i]['repo'] for i in ids})
            by_repo = {r: [i for i in ids if base[i]['repo'] == r] for r in repos}
            d = sum(res[c][i]['success'] - base[i]['success'] for i in ids) / len(ids)
            rng = random.Random(11)
            boots = []
            for _ in range(a.boot):
                pick = [i for r in (rng.choice(repos) for _ in repos) for i in by_repo[r]]
                boots.append(sum(res[c][i]['success'] - base[i]['success'] for i in pick) / len(pick))
            boots.sort()
            diffs[c] = {'items': len(ids), 'diff': round(d, 4), 'ci95': [round(boots[int(0.025 * a.boot)], 4), round(boots[int(0.975 * a.boot) - 1], 4)],
                        'cluster': 'repository'}
    out = {'summary': summary, 'vs_baseline': diffs, 'items': res}
    for c, s in summary.items():
        print('%-14s items %2d caught %2d gate-pass %2d tests %2d det-simpler %2d judged-simpler %2d kept %2d not-gamed %2d -> success %2d (%.2f)' % (
            c, s['items'], s['caught'], s['gate_passes'], s['tests_pass'], s['det_simpler'], s['judged_simpler'], s['behaviour_kept'], s['not_gamed'], s['success'], s['success_rate']))
    for c, d in diffs.items():
        print('  %s - %s: %+.3f [%+.3f, %+.3f]' % (c, a.baseline, d['diff'], d['ci95'][0], d['ci95'][1]))
    if a.out:
        json.dump(out, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
