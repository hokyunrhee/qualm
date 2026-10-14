"""Build inputs for the LLM comment classifier (dev only) and the human adjudication page.

  comments  DEV.jsonl.gz OUTDIR --shards 3
      One job per dev reviewer-comment positive: the comment text and 13 lines of code around it.
  adjudicate DEV.jsonl.gz HELDOUT.jsonl.gz OUT_ITEMS.json OUT_KEYMAP.json [--classified dev_comment_labels.json]
      60 items, held-out first: every held-out tier-B positive waiting for a person (up to 28),
      8 tier-A reviewer positives shown with their comment, and 9 held-out items shown blind
      (4 positives, 5 negatives); then 15 dev items: comments the LLM classifier disagreed with,
      and blind items. Keys are opaque; the key map stays out of the page.
"""
import argparse, difflib, gzip, json, os, random


def load(path):
    with gzip.open(path, 'rt') as fh:
        return [json.loads(l) for l in fh]


def file_of(case, path):
    return next(f for f in case['files'] if f['path'] == path)


def code_around(case, item, radius=6):
    f = file_of(case, item['path'])
    lines = f['head'].splitlines()
    lo, hi = item.get('location') or [1, 1]
    a, b = max(1, lo - radius), min(len(lines), hi + radius)
    return '\n'.join('%5d  %s' % (n, lines[n - 1]) for n in range(a, b + 1))


def diff_rows(case, item, radius=10, max_rows=70):
    f = file_of(case, item['path'])
    a = (f.get('base') or '').splitlines()
    b = f['head'].splitlines()
    rows = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for group in sm.get_grouped_opcodes(4):
        i1, j1 = group[0][1], group[0][3]
        block = [['@', None, None, '@@ -%d +%d @@' % (i1 + 1, j1 + 1)]]
        for tag, i1, i2, j1, j2 in group:
            if tag == 'equal':
                block += [[' ', i + 1, j + 1, a[i]] for i, j in zip(range(i1, i2), range(j1, j2))]
            else:
                block += [['-', i + 1, None, a[i]] for i in range(i1, i2)]
                block += [['+', None, j + 1, b[j]] for j in range(j1, j2)]
        rows.append(block)
    loc = item.get('location')
    if loc:
        def near(block):
            ns = [r[2] for r in block if r[2]]
            return ns and min(ns) - radius <= loc[1] and loc[0] <= max(ns) + radius
        chosen = [blk for blk in rows if near(blk)] or rows[:1]
    else:
        chosen = sorted(rows, key=lambda blk: -sum(1 for r in blk if r[0] in '+-'))[:1]
    out = [r for blk in chosen for r in blk]
    if loc and len(out) > max_rows:
        idx = [k for k, r in enumerate(out) if r[2] and loc[0] - radius <= r[2] <= loc[1] + radius]
        if idx:
            out = out[max(0, idx[0] - 5):idx[-1] + 6]
    return out[:max_rows]


def comments(a):
    cases = load(a.dev)
    jobs = []
    for c in cases:
        for it in c['items']:
            if it.get('label_source') == 'reviewer_comment' and it.get('label') == 1:
                jobs.append({'item_id': it['item_id'], 'language': it['language'], 'comments': [e[:1200] for e in it.get('evidence') or []][:3],
                             'code': code_around(c, it)})
    os.makedirs(a.outdir, exist_ok=True)
    for k in range(a.shards):
        json.dump(jobs[k::a.shards], open(os.path.join(a.outdir, 'comments_shard%d.json' % k), 'w'))
    print(len(jobs), 'comment jobs in', a.shards, 'shards')


def adjudicate(a):
    rng = random.Random(29)
    held = load(a.heldout)
    dev = load(a.dev)
    picks = []

    def pool(cases, pred):
        out = [(c, it) for c in cases for it in c['items'] if pred(it)]
        rng.shuffle(out)
        return out

    tier_b = pool(held, lambda it: it.get('unscored') == 'tier_b_pending_adjudication')[:28]
    picks += [('heldout', 'comment', c, it) for c, it in tier_b]
    tier_a = pool(held, lambda it: it.get('label') == 1 and it.get('tier') == 'A')
    n_a = 30 - len(tier_b)
    picks += [('heldout', 'comment', c, it) for c, it in tier_a[:n_a]]
    blind_pos = [x for x in tier_a[n_a:] if x[1]['added'] <= 150][:6]
    picks += [('heldout', 'blind', c, it) for c, it in blind_pos]
    negs = pool(held, lambda it: it.get('label') == 0 and it['label_source'] in ('approved_uncommented', 'approved_clean', 'survived_90d', 'post_fix', 'approved_agent') and it['added'] <= 150)
    picks += [('heldout', 'blind', c, it) for c, it in negs[:9]]
    need_dev = 60 - len(picks)
    disagree = set()
    if a.classified and os.path.exists(a.classified):
        data = json.load(open(a.classified))
        disagree = {r['item_id'] for r in (data['results'] if isinstance(data, dict) else data) if r.get('verdict') == 'other'}
    dev_comment = pool(dev, lambda it: it['item_id'] in disagree)[:8]
    picks += [('dev', 'comment', c, it) for c, it in dev_comment]
    dev_blind = pool(dev, lambda it: it.get('label') is not None and it['label_source'] in ('reviewer_comment', 'approved_uncommented', 'post_fix', 'approved_clean') and it['added'] <= 150)
    picks += [('dev', 'blind', c, it) for c, it in dev_blind[:60 - len(picks)]]
    items, keymap = [], {}
    order = list(range(len(picks)))
    for n, k in enumerate(order, 1):
        split, kind, c, it = picks[k]
        key = 'A%02d' % n
        items.append({'key': key, 'kind': kind, 'language': it['language'], 'path': it['path'],
                      'comment': ' / '.join(e.strip() for e in (it.get('evidence') or [])[:2]) if kind == 'comment' else None,
                      'rows': diff_rows(c, it), 'focus': it.get('location')})
        keymap[key] = {'item_id': it['item_id'], 'split': split, 'kind': kind, 'label': it.get('label'), 'label_was': it.get('label_was'),
                       'tier': it.get('tier'), 'label_source': it['label_source']}
    json.dump({'items': items}, open(a.out_items, 'w'))
    json.dump(keymap, open(a.out_keymap, 'w'), indent=1)
    print(len(items), 'items:', {s: sum(1 for v in keymap.values() if v['split'] == s) for s in ('heldout', 'dev')})


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('comments'); c.add_argument('dev'); c.add_argument('outdir'); c.add_argument('--shards', type=int, default=3)
    d = sub.add_parser('adjudicate'); d.add_argument('dev'); d.add_argument('heldout'); d.add_argument('out_items'); d.add_argument('out_keymap')
    d.add_argument('--classified')
    a = ap.parse_args()
    comments(a) if a.cmd == 'comments' else adjudicate(a)


if __name__ == '__main__':
    main()
