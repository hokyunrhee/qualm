"""Choose the fixed dev subsets, once, with a seed, before any candidate runs on them.

    python3 -I subsets.py DEV.jsonl.gz OUTDIR

- dev150: 150 scored items for LLM-based candidates, stratified by language x label source x label,
  drawn from cases with at most three judged files (so a per-file LLM judges at most ~3 files per case).
  Written as item ids (dev150_items.txt) and their cases (dev150_cases.txt).
- det100: 100 cases for the determinism runs of non-LLM candidates (5 runs, caches off).
- det30: 30 cases from dev150 for LLM determinism (3 runs, fresh subagents).
"""
import collections, gzip, json, os, random, sys


def main():
    dev, out = sys.argv[1], sys.argv[2]
    rng = random.Random(41)
    cases = [json.loads(l) for l in gzip.open(dev, 'rt')]
    small = [c for c in cases if len(c['files']) <= 3]
    strata = collections.defaultdict(list)
    for c in small:
        for it in c['items']:
            if it.get('label') is not None:
                strata[(it['language'], it['label_source'], it['label'])].append((c['case_id'], it['item_id']))
    total = sum(len(v) for v in strata.values())
    picked, used_cases = [], set()
    keys = sorted(strata)
    for k in keys:
        strata[k].sort()
        rng.shuffle(strata[k])
    quota = {k: max(1, round(150 * len(strata[k]) / total)) for k in keys}
    for k in keys:
        for cid, iid in strata[k]:
            if sum(1 for p in picked if p[2] == k) >= quota[k] or len(picked) >= 150:
                break
            if cid in used_cases:
                continue
            picked.append((cid, iid, k))
            used_cases.add(cid)
    rest = [(cid, iid, k) for k in keys for cid, iid in strata[k] if cid not in used_cases]
    rng.shuffle(rest)
    for cid, iid, k in rest:
        if len(picked) >= 150:
            break
        if cid not in used_cases:
            picked.append((cid, iid, k))
            used_cases.add(cid)
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, 'dev150_items.txt'), 'w').write('\n'.join(sorted(p[1] for p in picked)) + '\n')
    open(os.path.join(out, 'dev150_cases.txt'), 'w').write('\n'.join(sorted(used_cases)) + '\n')
    scored_cases = sorted({c['case_id'] for c in cases if any(it.get('label') is not None for it in c['items'])})
    rng2 = random.Random(43)
    det100 = rng2.sample(scored_cases, 100)
    open(os.path.join(out, 'det100_cases.txt'), 'w').write('\n'.join(sorted(det100)) + '\n')
    det30 = random.Random(47).sample(sorted(used_cases), 30)
    open(os.path.join(out, 'det30_cases.txt'), 'w').write('\n'.join(sorted(det30)) + '\n')
    c = collections.Counter((k[0], k[2]) for _, _, k in picked)
    print('dev150:', len(picked), 'items in', len(used_cases), 'cases;', 'positives', sum(1 for p in picked if p[2][2] == 1))
    print('  by language/label:', dict(sorted(c.items())))
    print('det100:', len(det100), 'cases; det30:', len(det30), 'cases')


if __name__ == '__main__':
    main()
