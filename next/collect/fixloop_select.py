"""Choose the commits and smells for LLM-written injections in repositories whose tests run.

    python3 -I fixloop_select.py fixrepos.json OUT.json [--per-split 40] [--seed 11]

Each item: a commit whose tests pass, the one source file to inject into (the file the commit
adds the most lines to), a smell, and the split (fixed by repository; assemble.py's tables).
Smells cycle through the five types so every split gets about the same number of each.
"""
import argparse, json, os, random, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from assemble import FIXLOOP_HELDOUT, FIXLOOP_DEV  # noqa: E402

SMELLS = ['near_copy', 'impossible_guard', 'leftover', 'single_use_indirection', 'long_unit_growth']


SP = '/tmp/claude-0/-home-user-qualm/2ede26c6-6287-5d82-9ba2-8868fcdef917/scratchpad'


def biggest(key, c):
    import subprocess
    out = subprocess.run(['git', '-C', os.path.join(SP, 'fixrepos', key), 'diff', '--numstat', c['sha'] + '^', c['sha'], '--', *c['files']],
                         capture_output=True, text=True).stdout
    rows = [(int(l.split('\t')[0]), l.split('\t')[2]) for l in out.splitlines() if l.split('\t')[0].isdigit()]
    return max(rows)[1] if rows else c['files'][0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('fixrepos')
    ap.add_argument('out')
    ap.add_argument('--per-split', type=int, default=40)
    ap.add_argument('--seed', type=int, default=11)
    a = ap.parse_args()
    rng = random.Random(a.seed)
    cfg = {}
    for path in a.fixrepos.split(','):
        if os.path.exists(path):
            cfg.update(json.load(open(path))['repos'])
    pools = {'dev': [], 'heldout': []}
    for key, r in sorted(cfg.items()):
        name = key.replace('_', '/', 1)
        split = 'heldout' if name in FIXLOOP_HELDOUT else 'dev' if name in FIXLOOP_DEV else None
        if not split:
            continue
        good = [c for c in r.get('commits', []) if c.get('tests_pass')]
        for c in good:
            pools[split].append((key, r, c))
    items = []
    for split, pool in pools.items():
        rng.shuffle(pool)
        # spread across repositories: round-robin by repository
        by_repo = {}
        for k, r, c in pool:
            by_repo.setdefault(k, []).append((k, r, c))
        order = []
        while any(by_repo.values()):
            for k in sorted(by_repo):
                if by_repo[k]:
                    order.append(by_repo[k].pop())
        for i, (k, r, c) in enumerate(order[:a.per_split]):
            target = biggest(k, c)
            items.append({'fix_id': 'fx-%s-%s-%s' % (split[:4], k, c['sha'][:8]), 'split': split, 'repo_key': k,
                          'repo': k.replace('_', '/', 1), 'language': r['language'], 'sha': c['sha'], 'file': target,
                          'all_files': c['files'], 'smell': SMELLS[i % len(SMELLS)], 'setup': r['setup'], 'test': r['test']})
    json.dump({'items': items}, open(a.out, 'w'), indent=1)
    for split in pools:
        print(split, sum(1 for it in items if it['split'] == split), 'items from', len({it['repo_key'] for it in items if it['split'] == split}), 'repositories')


if __name__ == '__main__':
    main()
