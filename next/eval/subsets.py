"""Choose the fixed dev subsets, once, with a seed, before any candidate runs on them.

    python3 -I subsets.py DEV.jsonl.gz OUTDIR

- dev150: 150 scored items for LLM-based candidates: 10 positives and 18 negatives for each of Go, Python,
  Ruby, TypeScript and Java and 4/6 for JavaScript, spread round-robin over label sources, from cases with
  at most five judged files (so a per-file LLM judges at most five files per case).
  Written as item ids (dev150_items.txt) and their cases (dev150_cases.txt).
- det100: 100 cases for the determinism runs of non-LLM candidates (5 runs, caches off).
- det30: 30 cases from dev150 for LLM determinism (3 runs, fresh subagents).
"""
import collections, gzip, json, os, random, sys


def main():
    dev, out = sys.argv[1], sys.argv[2]
    rng = random.Random(41)
    cases = [json.loads(l) for l in gzip.open(dev, 'rt')]
    small = [c for c in cases if len(c['files']) <= 5]
    quota = {('Go', 1): 10, ('Go', 0): 18, ('Python', 1): 10, ('Python', 0): 18, ('Ruby', 1): 10, ('Ruby', 0): 18,
             ('TypeScript', 1): 10, ('TypeScript', 0): 18, ('Java', 1): 10, ('Java', 0): 18, ('JavaScript', 1): 4, ('JavaScript', 0): 6}
    pools = collections.defaultdict(list)
    for c in small:
        for it in c['items']:
            if it.get('label') is not None and (it['language'], it['label']) in quota:
                pools[(it['language'], it['label'])].append((c['case_id'], it['item_id'], (it['language'], it['label_source'], it['label'])))
    picked, used_cases = [], set()
    for k in sorted(quota):
        pool = sorted(pools[k])
        rng.shuffle(pool)
        # spread across label sources: round-robin by source
        by_src = collections.defaultdict(list)
        for x in pool:
            by_src[x[2][1]].append(x)
        n = 0
        while n < quota[k] and any(by_src.values()):
            for src in sorted(by_src):
                if n >= quota[k] or not by_src[src]:
                    continue
                cid, iid, key = by_src[src].pop()
                if cid in used_cases:
                    continue
                picked.append((cid, iid, key))
                used_cases.add(cid)
                n += 1
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
