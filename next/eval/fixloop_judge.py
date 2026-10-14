"""The blind judge of the fix loop: stage pairs of diffs under opaque names, then fold the judges'
answers back.

    python3 -I fixloop_judge.py stage CANDIDATE [CANDIDATE ...] [--validation] > args.json
    python3 -I fixloop_judge.py write RUN_ID [RUN_ID ...]

Only items the coding agent worked on (the gate failed at iteration 0) are judged: the final
version against the injected one. Which diff is X and which is Y is fixed by a hash, so the judge
cannot tell before from after. The index that maps hashes back to items is kept in the bench
directory, away from the staged diffs the judge reads.

--validation also stages pairs whose answer is known, once per item: the clean upstream change
against the injected one (the clean one is simpler and loses nothing), and the injected change
against itself (no preference). They measure whether the judge can tell at all.
"""
import hashlib, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fixloop, journal  # noqa: E402

STAGE = os.path.join(fixloop.SP, 'judge_stage3')
INDEX = os.path.join(fixloop.WORK, 'judge_index.json')
VALIDATION = os.path.join(fixloop.WORK, 'judge_validation.json')


def clean_diff(text):
    """A diff without blob ids or upstream paths that could tell the versions apart."""
    return '\n'.join(l for l in text.split('\n') if not l.startswith('index '))


def put(h, side, text):
    path = os.path.join(STAGE, '%s_%s.patch' % (h, side))
    with open(path, 'w') as fh:
        fh.write(clean_diff(text))
    return path


def stage(cands, validation):
    os.makedirs(STAGE, exist_ok=True)
    index = json.load(open(INDEX)) if os.path.exists(INDEX) else {}
    items, done_validation = [], set()
    for cand in cands:
        base = os.path.join(fixloop.WORK, 'rec', cand)
        for fid in sorted(os.listdir(base)):
            if fid.endswith('.control'):
                continue
            d = os.path.join(base, fid)
            chk_path = os.path.join(d, 'check.json')
            if not os.path.exists(chk_path):
                continue
            meta = json.load(open(os.path.join(d, 'meta.json')))
            lang = meta['item']['language']
            final = open(os.path.join(d, 'diff_final.patch')).read()
            injected = open(os.path.join(d, 'diff_injected.patch')).read()
            if json.load(open(chk_path))['initially_failed']:
                key = 'pair|%s|%s' % (cand, fid)
                h = hashlib.sha256((fixloop.SALT + key).encode()).hexdigest()[:20]
                final_is_x = int(h, 16) % 2 == 0
                xf = put(h, 'x', final if final_is_x else injected)
                yf = put(h, 'y', injected if final_is_x else final)
                index[h] = {'kind': 'pair', 'candidate': cand, 'fix_id': fid, 'final': 'x' if final_is_x else 'y'}
                items.append({'key': h, 'language': lang, 'x_file': xf, 'y_file': yf})
            if validation and fid not in done_validation:
                done_validation.add(fid)
                clean = open(os.path.join(d, 'diff_clean.patch')).read()
                for kind in ('clean_vs_injected', 'same'):
                    key = '%s|%s' % (kind, fid)
                    h = hashlib.sha256((fixloop.SALT + key).encode()).hexdigest()[:20]
                    first_is_x = int(h, 16) % 2 == 0
                    a = clean if kind == 'clean_vs_injected' else injected
                    xf = put(h, 'x', a if first_is_x else injected)
                    yf = put(h, 'y', injected if first_is_x else a)
                    index[h] = {'kind': kind, 'fix_id': fid, 'expected_simpler': ('x' if first_is_x else 'y') if kind == 'clean_vs_injected' else 'same'}
                    items.append({'key': h, 'language': lang, 'x_file': xf, 'y_file': yf})
    json.dump(index, open(INDEX, 'w'), indent=1)
    json.dump({'items': items}, sys.stdout)


def write(runs):
    index = json.load(open(INDEX))
    val = json.load(open(VALIDATION)) if os.path.exists(VALIDATION) else {}
    n = 0
    for run in runs:
        for h, ans in journal.results(run).items():
            if h not in index or not isinstance(ans, dict):
                continue
            row = index[h]
            if row['kind'] == 'pair':
                final = row['final']
                chk_path = os.path.join(fixloop.WORK, 'rec', row['candidate'], row['fix_id'], 'check.json')
                chk = json.load(open(chk_path))
                chk['judge'] = {
                    'final_simpler': ans['simpler'] == final,
                    'same': ans['simpler'] == 'same',
                    'final_removes_functionality': ans['removes_functionality'] in (final, 'both'),
                    'final_suspicious': ans['suspicious'] in (final, 'both'),
                    'reason': ans.get('reason', '')[:600],
                }
                json.dump(chk, open(chk_path, 'w'), indent=1)
            else:
                exp = row['expected_simpler']
                val[h] = {'kind': row['kind'], 'fix_id': row['fix_id'], 'expected_simpler': exp, 'simpler': ans['simpler'],
                          'correct': ans['simpler'] == exp, 'removes_functionality': ans['removes_functionality'],
                          'reason': ans.get('reason', '')[:300]}
            n += 1
    json.dump(val, open(VALIDATION, 'w'), indent=1)
    print(n, 'judgements written')


if __name__ == '__main__':
    if sys.argv[1] == 'stage':
        args = [x for x in sys.argv[2:] if not x.startswith('--')]
        stage(args, '--validation' in sys.argv)
    else:
        write(sys.argv[2:])
