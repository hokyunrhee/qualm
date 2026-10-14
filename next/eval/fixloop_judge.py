"""The blind judge of the fix loop: stage each reworked item's two diffs under opaque names, then
fold the judges' answers back into each item's check.json.

    python3 -I fixloop_judge.py stage CANDIDATE [CANDIDATE ...] > args.json
    python3 -I fixloop_judge.py write RUN_ID [RUN_ID ...]

Only items the coding agent worked on (the gate failed at iteration 0) are judged. Which diff is
X and which is Y is fixed by a hash of the item, so the judge cannot tell before from after.
"""
import hashlib, json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fixloop, journal  # noqa: E402

STAGE = os.path.join(fixloop.SP, 'judge_stage')


def stage(cands):
    os.makedirs(STAGE, exist_ok=True)
    index_path = os.path.join(STAGE, 'index.json')
    index = json.load(open(index_path)) if os.path.exists(index_path) else {}
    items = []
    for cand in cands:
        base = os.path.join(fixloop.WORK, 'fixloop', cand)
        for fid in sorted(os.listdir(base)):
            d = os.path.join(base, fid)
            chk = os.path.join(d, 'check.json')
            if not os.path.exists(chk) or not json.load(open(chk))['initially_failed']:
                continue
            key = '%s/%s' % (cand, fid)
            h = hashlib.sha256(key.encode()).hexdigest()[:20]
            final_is_x = int(h, 16) % 2 == 0
            x_src, y_src = ('diff_final.patch', 'diff_injected.patch') if final_is_x else ('diff_injected.patch', 'diff_final.patch')
            xf, yf = os.path.join(STAGE, h + '_x.patch'), os.path.join(STAGE, h + '_y.patch')
            shutil.copyfile(os.path.join(d, x_src), xf)
            shutil.copyfile(os.path.join(d, y_src), yf)
            meta = json.load(open(os.path.join(d, 'meta.json')))
            index[h] = {'key': key, 'final': 'x' if final_is_x else 'y'}
            items.append({'key': h, 'language': meta['item']['language'], 'x_file': xf, 'y_file': yf})
    json.dump(index, open(index_path, 'w'), indent=1)
    json.dump({'items': items}, sys.stdout)


def write(runs):
    index = json.load(open(os.path.join(STAGE, 'index.json')))
    n = 0
    for run in runs:
        for h, ans in journal.results(run).items():
            if h not in index or not isinstance(ans, dict):
                continue
            cand, fid = index[h]['key'].split('/', 1)
            final = index[h]['final']
            other = 'y' if final == 'x' else 'x'
            chk_path = os.path.join(fixloop.WORK, 'fixloop', cand, fid, 'check.json')
            chk = json.load(open(chk_path))
            chk['judge'] = {
                'final_simpler': ans['simpler'] == final,
                'same': ans['simpler'] == 'same',
                'final_removes_functionality': ans['removes_functionality'] in (final, 'both'),
                'final_suspicious': ans['suspicious'] in (final, 'both'),
                'reason': ans.get('reason', '')[:600],
            }
            json.dump(chk, open(chk_path, 'w'), indent=1)
            n += 1
    print(n, 'judgements written')


if __name__ == '__main__':
    stage(sys.argv[2:]) if sys.argv[1] == 'stage' else write(sys.argv[2:])
