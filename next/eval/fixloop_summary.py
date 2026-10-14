"""Fix-loop outcomes per candidate, and paired differences against the baseline.

    python3 -I fixloop_summary.py CANDIDATE [CANDIDATE ...] [--baseline a-baseline] [--out results.json] [--boot 4000]

Reads $BENCH_WORK/rec/<candidate>/. An item succeeds when all hold: the gate failed on the
injected change (iteration 0, "caught"), the gate passes after at most two rounds of rework, the
project's tests pass, nothing was gamed (no edits to gate configuration, skipped paths or tests,
no text addressed to the tool, no definitions of the original change lost), the deterministic
metric says simpler (the injected lines are gone and the change is no larger than the injected
one), and the blind judge prefers the final version and finds no behaviour removed.

Reported separately, because success folds them together:
- caught, and success among the caught items (how well the report lets an agent fix what it flags);
- controls: the clean upstream change, judged once; a gate that blocks it (exit 1 or 2) raised a
  false alarm, so a gate cannot buy "caught" by failing everything;
- the blind judge's accuracy on pairs with a known answer (fixloop_judge.py --validation);
- success without the judge's vote, in case the judge cannot tell.

Paired comparisons: an exact McNemar test on the discordant items, and a bootstrap that resamples
repositories (items of one repository are not independent; with few repositories it is rough).
"""
import argparse, json, math, os, random, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fixloop  # noqa: E402

PARTS = ('caught', 'gate_passes', 'tests_pass', 'not_gamed', 'det_simpler', 'judged_simpler', 'behaviour_kept')


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
        'judged': bool(j),
    }
    parts['success'] = all(parts[k] for k in PARTS)
    parts['success_without_judge'] = all(parts[k] for k in ('caught', 'gate_passes', 'tests_pass', 'not_gamed', 'det_simpler'))
    return parts


def load(cand):
    base = os.path.join(fixloop.WORK, 'rec', cand)
    items, controls = {}, {}
    for fid in sorted(os.listdir(base)):
        d = os.path.join(base, fid)
        if fid.endswith('.control'):
            p = os.path.join(d, 'iter0.json')
            if os.path.exists(p):
                controls[fid[:-len('.control')]] = json.load(open(p))['exit']
            continue
        p = os.path.join(d, 'check.json')
        if os.path.exists(p):
            meta = json.load(open(os.path.join(d, 'meta.json')))
            items[fid] = dict(outcome(json.load(open(p))), repo=meta['item']['repo'], iterations=json.load(open(p))['iterations_used'])
    return items, controls


def mcnemar(a, b):
    """Exact two-sided McNemar: b01 items only the candidate got, b10 only the baseline got."""
    n01 = sum(1 for x, y in zip(a, b) if x and not y)
    n10 = sum(1 for x, y in zip(a, b) if y and not x)
    n = n01 + n10
    if n == 0:
        return {'only_candidate': 0, 'only_baseline': 0, 'p_two_sided': 1.0}
    k = min(n01, n10)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return {'only_candidate': n01, 'only_baseline': n10, 'p_two_sided': round(min(1.0, 2 * p), 4)}


def boot_diff(cand, base, ids, field, n_boot, seed=11):
    repos = sorted({base[i]['repo'] for i in ids})
    by_repo = {r: [i for i in ids if base[i]['repo'] == r] for r in repos}
    d = sum(cand[i][field] - base[i][field] for i in ids) / len(ids)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        pick = [i for r in (rng.choice(repos) for _ in repos) for i in by_repo[r]]
        boots.append(sum(cand[i][field] - base[i][field] for i in pick) / len(pick))
    boots.sort()
    return {'diff': round(d, 4), 'ci95': [round(boots[int(0.025 * n_boot)], 4), round(boots[int(0.975 * n_boot) - 1], 4)],
            'clusters': len(repos)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('candidates', nargs='+')
    ap.add_argument('--baseline', default='a-baseline')
    ap.add_argument('--out')
    ap.add_argument('--boot', type=int, default=4000)
    a = ap.parse_args()
    res, ctl = {}, {}
    for c in a.candidates:
        res[c], ctl[c] = load(c)
    summary = {}
    for c, rows in res.items():
        n = len(rows)
        s = {'items': n, **{k: sum(r[k] for r in rows.values()) for k in PARTS + ('success', 'success_without_judge', 'judged')}}
        s['success_rate'] = round(s['success'] / n, 4) if n else None
        s['success_given_caught'] = round(s['success'] / s['caught'], 4) if s['caught'] else None
        s['controls'] = len(ctl[c])
        s['controls_blocked'] = sum(1 for e in ctl[c].values() if e != 0)
        s['controls_failed'] = sum(1 for e in ctl[c].values() if e == 1)
        summary[c] = s
    val_path = os.path.join(fixloop.WORK, 'judge_validation.json')
    validation = None
    if os.path.exists(val_path):
        v = json.load(open(val_path))
        validation = {}
        for kind in ('clean_vs_injected', 'same'):
            rows = [r for r in v.values() if r['kind'] == kind]
            validation[kind] = {'pairs': len(rows), 'correct': sum(r['correct'] for r in rows),
                                'answered_same': sum(r['simpler'] == 'same' for r in rows)}
    diffs = {}
    if a.baseline in res:
        base = res[a.baseline]
        for c in a.candidates:
            if c == a.baseline:
                continue
            ids = sorted(set(base) & set(res[c]))
            diffs[c] = {'items': len(ids)}
            for field in ('success', 'success_without_judge', 'caught'):
                diffs[c][field] = boot_diff(res[c], base, ids, field, a.boot)
                diffs[c][field]['mcnemar'] = mcnemar([res[c][i][field] for i in ids], [base[i][field] for i in ids])
            both = [i for i in ids if res[c][i]['caught'] and base[i]['caught']]
            diffs[c]['caught_by_both'] = len(both)
            if both:
                diffs[c]['success_on_items_both_caught'] = {'candidate': sum(res[c][i]['success'] for i in both),
                                                            'baseline': sum(base[i]['success'] for i in both)}
    out = {'summary': summary, 'judge_validation': validation, 'vs_baseline': diffs, 'items': res, 'controls': ctl}
    for c, s in summary.items():
        print('%-12s items %2d caught %2d gate-pass %2d tests %2d det-simpler %2d judged-simpler %2d kept %2d not-gamed %2d -> success %2d '
              '(%.2f; %s given caught); without judge %2d; controls blocked %d/%d' % (
                  c, s['items'], s['caught'], s['gate_passes'], s['tests_pass'], s['det_simpler'], s['judged_simpler'], s['behaviour_kept'],
                  s['not_gamed'], s['success'], s['success_rate'] or 0, s['success_given_caught'], s['success_without_judge'],
                  s['controls_blocked'], s['controls']))
    if validation:
        print('judge validation:', json.dumps(validation))
    for c, d in diffs.items():
        for field in ('success', 'success_without_judge', 'caught'):
            x = d[field]
            print('  %s - %s %s: %+.3f [%+.3f, %+.3f] (%d repositories); McNemar %s' % (
                c, a.baseline, field, x['diff'], x['ci95'][0], x['ci95'][1], x['clusters'], json.dumps(x['mcnemar'])))
    if a.out:
        json.dump(out, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
