"""Score candidates' recorded runs against the benchmark labels.

    python3 -I metrics.py SPLIT_FILE --run NAME=results.jsonl [--run ...] [--baseline NAME]
                          [--threshold NAME=0.5] [--out metrics.json] [--boot 2000] [--subset ids.txt]

Per candidate: PR-AUC (average precision), ROC-AUC, Brier score, ECE (10 equal-width bins),
precision / recall / F1 at the candidate's own gate (or a threshold given here), localization hit
rate, and could-not-judge count, overall and by language, label source, smell and agent-written.
Against the baseline: paired bootstrap 95% intervals over cases (a case is resampled with all its
items, since files of one change are not independent).

A skipped or kept file counts as a pass with score 0. A case the candidate could not judge (exit 2,
no report) is counted and its items are left out of the accuracy metrics; the count is reported.
"""
import argparse, collections, gzip, json, random, sys


NATURAL_POS = {'reviewer_comment', 'followup_cleanup'}
NATURAL_NEG = {'approved_uncommented', 'approved_clean', 'post_fix', 'survived_90d'}


def load_labels(path, subset=None):
    opener = gzip.open if path.endswith('.gz') else open
    items = []
    with opener(path, 'rt') as fh:
        for line in fh:
            c = json.loads(line)
            for it in c['items']:
                if it.get('label') is None:
                    continue
                if subset is not None and it['item_id'] not in subset and c['case_id'] not in subset:
                    continue
                origin = c.get('origin_case') or c['case_id']
                origin = origin.rsplit('-ps', 1)[0] if origin.startswith('gerrit-') else origin
                natural = it['label_source'] in NATURAL_POS if it['label'] == 1 else it['label_source'] in NATURAL_NEG
                items.append({'item_id': it['item_id'], 'case_id': c['case_id'], 'path': it['path'], 'label': it['label'],
                              'repo': c.get('repo_key') or c.get('repo'), 'origin_change': origin, 'natural': natural,
                              'nit': bool(it.get('nit')), 'tier': it.get('tier') or '-',
                              'language': it['language'], 'source': it['label_source'], 'smell': it.get('smell') or '-',
                              'location': it.get('location'), 'agent_written': bool(c.get('agent_written')),
                              'origin': c['source'].split(':')[0]})
    return items


def load_run(path):
    out = {}
    for line in open(path):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        prev = out.get(r['case_id'])
        if prev is None or prev['exit'] not in (0, 1):
            out[r['case_id']] = r
    return out


def file_view(rec, path, threshold=None):
    """(score, failed, findings) for one file of one case, or None if the case wasn't judged."""
    if rec is None or rec['exit'] not in (0, 1) or not rec.get('report'):
        return None
    for f in rec['report'].get('files') or []:
        if f['path'] == path:
            if f.get('status') != 'judged':
                return (0.0, False, [])
            s = f.get('score')
            if s is None:
                return None
            failed = (s >= threshold) if threshold is not None else bool(f.get('failed'))
            return (float(s), failed, f.get('findings') or [])
    return (0.0, False, [])  # not in the report: the candidate didn't consider it a judged change


def average_precision(ys, ss):
    pairs = sorted(zip(ss, ys), key=lambda p: -p[0])
    pos = sum(ys)
    if pos == 0:
        return float('nan')
    ap, tp, i = 0.0, 0, 0
    n = len(pairs)
    while i < n:  # handle ties as one step
        j = i
        while j < n and pairs[j][0] == pairs[i][0]:
            j += 1
        block_tp = sum(y for _, y in pairs[i:j])
        prev_tp = tp
        tp += block_tp
        if block_tp:
            ap += (tp - prev_tp) / pos * (tp / j)
        i = j
    return ap


def roc_auc(ys, ss):
    pos = [s for s, y in zip(ss, ys) if y == 1]
    neg = [s for s, y in zip(ss, ys) if y == 0]
    if not pos or not neg:
        return float('nan')
    allv = sorted([(s, 1) for s in pos] + [(s, 0) for s in neg])
    ranks, i = {}, 0
    rank_sum = 0.0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        r = (i + j + 1) / 2
        rank_sum += r * sum(1 for k in range(i, j) if allv[k][1] == 1)
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def brier(ys, ss):
    return sum((s - y) ** 2 for s, y in zip(ss, ys)) / len(ys)


def ece(ys, ss, bins=10):
    tot = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, s in enumerate(ss) if (lo <= s < hi) or (b == bins - 1 and s == 1.0)]
        if idx:
            conf = sum(ss[i] for i in idx) / len(idx)
            acc = sum(ys[i] for i in idx) / len(idx)
            tot += len(idx) / len(ss) * abs(conf - acc)
    return tot


def ece_mass(ys, ss, bins=10):
    """ECE with equal-count bins, which suits scores piled up near 0 and 1."""
    order = sorted(range(len(ss)), key=lambda i: ss[i])
    tot = 0.0
    for b in range(bins):
        idx = order[b * len(order) // bins:(b + 1) * len(order) // bins]
        if idx:
            conf = sum(ss[i] for i in idx) / len(idx)
            acc = sum(ys[i] for i in idx) / len(idx)
            tot += len(idx) / len(ss) * abs(conf - acc)
    return tot


def prf(ys, fs):
    tp = sum(1 for y, f in zip(ys, fs) if y and f)
    fp = sum(1 for y, f in zip(ys, fs) if not y and f)
    fn = sum(1 for y, f in zip(ys, fs) if y and not f)
    p = tp / (tp + fp) if tp + fp else float('nan')
    r = tp / (tp + fn) if tp + fn else float('nan')
    f1 = 2 * p * r / (p + r) if tp else 0.0
    return p, r, f1


def iou(a, b, slack=2):
    lo, hi = max(a[0], b[0] - slack), min(a[1], b[1] + slack)
    inter = max(0, hi - lo + 1)
    union = (a[1] - a[0] + 1) + (b[1] - b[0] + 1 + 2 * slack) - inter
    return inter / union if union > 0 else 0.0


def overlaps(a, b, slack=2):
    return a and b and a[0] - slack <= b[1] and b[0] - slack <= a[1]


def compute(rows):
    """rows: list of dicts with label, score, failed, findings, location."""
    ys = [r['label'] for r in rows]
    ss = [r['score'] for r in rows]
    fs = [r['failed'] for r in rows]
    p, rcl, f1 = prf(ys, fs)
    loc_rows = [r for r in rows if r['label'] == 1 and r['failed'] and r.get('location')]
    hits = [any(overlaps(fd.get('lines'), r['location']) for fd in r['findings'] if fd.get('lines')) for r in loc_rows]
    tight = [any(overlaps(fd.get('lines'), r['location']) and fd['lines'][1] - fd['lines'][0] < 15 for fd in r['findings'] if fd.get('lines'))
             for r in loc_rows]
    ious = [max([iou(fd['lines'], r['location']) for fd in r['findings'] if fd.get('lines')] or [0.0]) for r in loc_rows]
    widths = [fd['lines'][1] - fd['lines'][0] + 1 for r in loc_rows for fd in r['findings'] if fd.get('lines')]
    return {'n': len(rows), 'positives': sum(ys), 'pr_auc': average_precision(ys, ss), 'roc_auc': roc_auc(ys, ss),
            'brier': brier(ys, ss) if rows else float('nan'), 'ece': ece(ys, ss) if rows else float('nan'),
            'ece_mass': ece_mass(ys, ss) if rows else float('nan'),
            'precision': p, 'recall': rcl, 'f1': f1, 'fail_rate': sum(fs) / len(fs) if fs else float('nan'),
            'loc_n': len(loc_rows), 'loc_hit': sum(hits) / len(hits) if hits else float('nan'),
            'loc_hit_tight': sum(tight) / len(tight) if tight else float('nan'),
            'loc_iou20': sum(1 for v in ious if v >= 0.2) / len(ious) if ious else float('nan'),
            'loc_median_width': sorted(widths)[len(widths) // 2] if widths else None}


def pr_level(rows):
    """A change fails if any scored file fails; it is positive if any scored file is positive.
    Reports how often a clean change is blocked, which compounds per-file false alarms."""
    by_case = collections.defaultdict(list)
    for r in rows:
        by_case[r['case_id']].append(r)
    ys = [int(any(r['label'] for r in v)) for v in by_case.values()]
    fs = [any(r['failed'] for r in v) for v in by_case.values()]
    ss = [max(r['score'] for r in v) for v in by_case.values()]
    p, rcl, f1 = prf(ys, fs)
    clean = [f for y, f in zip(ys, fs) if not y]
    return {'cases': len(ys), 'positive_cases': sum(ys), 'pr_auc': average_precision(ys, ss), 'precision': p, 'recall': rcl,
            'f1': f1, 'clean_change_block_rate': sum(clean) / len(clean) if clean else float('nan')}


def join(items, run, threshold=None):
    rows, cnj = [], 0
    for it in items:
        v = file_view(run.get(it['case_id']), it['path'], threshold)
        if v is None:
            # A file the candidate could not judge fails closed: it counts as a fail at score 1.
            cnj += 1
            rows.append(dict(it, score=1.0, failed=True, findings=[], could_not_judge=True))
            continue
        rows.append(dict(it, score=v[0], failed=v[1], findings=v[2]))
    return rows, cnj


def breakdown(rows, key):
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r[key]].append(r)
    return {k: compute(v) for k, v in sorted(groups.items()) if len(v) >= 5}


def paired_bootstrap(items, run_a, run_b, thr_a, thr_b, boot=2000, seed=7, cluster='repo'):
    """Paired intervals for (a - b) of PR-AUC, precision, recall, F1, Brier, resampling whole clusters:
    repositories by default (the split unit, and it keeps injected/original and positive/post-fix pairs
    together), or origin changes."""
    ra, _ = join(items, run_a, thr_a)
    rb, _ = join(items, run_b, thr_b)
    ka = {r['item_id']: r for r in ra}
    kb = {r['item_id']: r for r in rb}
    common = [i for i in ka if i in kb]
    by_case = collections.defaultdict(list)
    for i in common:
        by_case[ka[i][cluster]].append(i)
    cases = sorted(by_case)
    rng = random.Random(seed)
    names = ['pr_auc', 'precision', 'recall', 'f1', 'brier', 'roc_auc']
    point_a = compute([ka[i] for i in common])
    point_b = compute([kb[i] for i in common])
    diffs = {n: [] for n in names}
    for _ in range(boot):
        pick = [rng.choice(cases) for _ in cases]
        ids = [i for c in pick for i in by_case[c]]
        ma, mb = compute([ka[i] for i in ids]), compute([kb[i] for i in ids])
        for n in names:
            d = ma[n] - mb[n]
            if d == d:  # not nan
                diffs[n].append(d)
    out = {'n_items': len(common), 'n_clusters': len(cases), 'cluster': cluster}
    for n in names:
        ds = sorted(diffs[n])
        if ds:
            out[n] = {'a': point_a[n], 'b': point_b[n], 'diff': point_a[n] - point_b[n],
                      'ci95': [ds[int(0.025 * len(ds))], ds[min(len(ds) - 1, int(0.975 * len(ds)))]]}
    return out


def matched_pairs(split_file, run, threshold=None):
    """P(score(positive) > score(its clean partner)): injected vs original, reviewer positive vs post_fix."""
    opener = gzip.open if split_file.endswith('.gz') else open
    cases = {}
    with opener(split_file, 'rt') as fh:
        for line in fh:
            c = json.loads(line)
            cases[c['case_id']] = c
    by_item = {it['item_id']: (c, it) for c in cases.values() for it in c['items']}
    out = {}
    inj = []
    for c, it in by_item.values():
        if it.get('paired_with') and it.get('label') == 1 and it['paired_with'] in by_item:
            oc, oit = by_item[it['paired_with']]
            inj.append(((c, it), (oc, oit)))
    post = []
    finals = collections.defaultdict(list)
    for c, it in by_item.values():
        if it.get('label_source') == 'post_fix':
            finals[(c['case_id'].rsplit('-ps', 1)[0], it['path'])].append((c, it))
    for c, it in by_item.values():
        if it.get('label_source') == 'reviewer_comment' and it.get('label') == 1:
            key = (c['case_id'].rsplit('-ps', 1)[0], it['path'])
            for pair in finals.get(key, []):
                post.append(((c, it), pair))
    for name, pairs in (('injected_vs_original', inj), ('positive_vs_post_fix', post)):
        wins, n = 0.0, 0
        for (pc, pit), (nc, nit) in pairs:
            vp = file_view(run.get(pc['case_id']), pit['path'], threshold)
            vn = file_view(run.get(nc['case_id']), nit['path'], threshold)
            if vp is None or vn is None:
                continue
            n += 1
            wins += 1.0 if vp[0] > vn[0] else 0.5 if vp[0] == vn[0] else 0.0
        out[name] = {'pairs': n, 'p_positive_higher': wins / n if n else None}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('split_file')
    ap.add_argument('--run', action='append', required=True, help='NAME=results.jsonl')
    ap.add_argument('--threshold', action='append', default=[], help='NAME=0.5 overrides the gate for scoring')
    ap.add_argument('--baseline')
    ap.add_argument('--subset')
    ap.add_argument('--boot', type=int, default=2000)
    ap.add_argument('--out')
    a = ap.parse_args()
    subset = set(l.strip() for l in open(a.subset) if l.strip()) if a.subset else None
    items = load_labels(a.split_file, subset)
    runs = {k: load_run(v) for k, v in (x.split('=', 1) for x in a.run)}
    thr = {k: float(v) for k, v in (x.split('=', 1) for x in a.threshold)}
    result = {'split': a.split_file, 'subset': a.subset, 'items': len(items), 'positives': sum(i['label'] for i in items), 'candidates': {}}
    for name, run in runs.items():
        rows, cnj = join(items, run, thr.get(name))
        m = compute(rows)
        m['could_not_judge_items'] = cnj
        m['by_language'] = breakdown(rows, 'language')
        m['by_source'] = breakdown(rows, 'source')
        m['by_smell'] = breakdown(rows, 'smell')
        m['by_agent_written'] = breakdown(rows, 'agent_written')
        m['by_origin'] = breakdown(rows, 'origin')
        m['by_nit'] = breakdown(rows, 'nit')
        m['threshold_override'] = thr.get(name)
        m['pr_level'] = pr_level(rows)
        result['candidates'][name] = m
    natural_items = [it for it in items if it['natural']]
    for name, run in runs.items():
        rows, _ = join(natural_items, run, thr.get(name))
        result['candidates'][name]['natural'] = compute(rows)
        result['candidates'][name]['pairs'] = matched_pairs(a.split_file, run, thr.get(name))
    if a.baseline:
        result['vs_baseline'] = {}
        result['vs_baseline_natural'] = {}
        result['vs_baseline_by_origin'] = {}
        for name in runs:
            if name != a.baseline:
                result['vs_baseline'][name] = paired_bootstrap(items, runs[name], runs[a.baseline], thr.get(name), thr.get(a.baseline), a.boot)
                result['vs_baseline_natural'][name] = paired_bootstrap(natural_items, runs[name], runs[a.baseline], thr.get(name), thr.get(a.baseline), a.boot)
                result['vs_baseline_by_origin'][name] = paired_bootstrap(items, runs[name], runs[a.baseline], thr.get(name), thr.get(a.baseline), a.boot, cluster='origin_change')
    print('%-22s %5s %4s %7s %7s %6s %6s %6s %6s %6s %6s %5s' % ('candidate', 'n', 'pos', 'PR-AUC', 'ROC', 'Brier', 'ECE', 'P', 'R', 'F1', 'loc', 'cnj'))
    for name, m in result['candidates'].items():
        print('%-22s %5d %4d %7.3f %7.3f %6.3f %6.3f %6.3f %6.3f %6.3f %6.3f %5d' % (
            name, m['n'], m['positives'], m['pr_auc'], m['roc_auc'], m['brier'], m['ece'], m['precision'], m['recall'], m['f1'],
            m['loc_hit'], m['could_not_judge_items']))
    for name, m in result['candidates'].items():
        nat = m['natural']
        print('  %-20s natural: n %d pos %d PR-AUC %.3f P %.3f R %.3f | pairs %s' % (name, nat['n'], nat['positives'], nat['pr_auc'], nat['precision'], nat['recall'],
              {k: (v['pairs'], None if v['p_positive_higher'] is None else round(v['p_positive_higher'], 3)) for k, v in m['pairs'].items()}))
    for name, d in (result.get('vs_baseline_natural') or {}).items():
        print('  natural %s - %s: ' % (name, a.baseline) + ', '.join('%s %+.3f [%+.3f, %+.3f]' % (k, v['diff'], v['ci95'][0], v['ci95'][1])
                                                           for k, v in d.items() if isinstance(v, dict)))
    for name, d in (result.get('vs_baseline') or {}).items():
        print('  %s - %s: ' % (name, a.baseline) + ', '.join('%s %+.3f [%+.3f, %+.3f]' % (k, v['diff'], v['ci95'][0], v['ci95'][1])
                                                           for k, v in d.items() if isinstance(v, dict)))
    if a.out:
        json.dump(result, open(a.out, 'w'), indent=1, sort_keys=True, default=str)


if __name__ == '__main__':
    main()
