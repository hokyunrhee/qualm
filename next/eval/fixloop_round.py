"""One round of the fix loop for one candidate: which items go to a coding agent next, and the gate after.

    python3 -I fixloop_round.py next CANDIDATE ITER > args.json   items whose gate failed at ITER-1 (ITER is 1 or 2)
    python3 -I fixloop_round.py gate CANDIDATE ITER               run the gate on every item the agents worked on

Each report is copied to an opaquely named file (a hash) that the coding agent reads, so the
agent sees the report and its repository and nothing about the item or the other candidates.
Control workspaces (the clean change) never go to an agent.
"""
import hashlib, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fixloop  # noqa: E402

REPORTS = os.path.join(fixloop.SP, 'fx_reports3')


def items_for(cand, k):
    base = os.path.join(fixloop.WORK, 'rec', cand)
    out = []
    for fid in sorted(os.listdir(base)):
        if fid.endswith('.control'):
            continue
        d = os.path.join(base, fid)
        prev = os.path.join(d, 'iter%d.json' % (k - 1))
        if not os.path.exists(prev) or os.path.exists(os.path.join(d, 'iter%d.json' % k)):
            continue
        if json.load(open(prev))['exit'] != 1:
            continue
        out.append((fid, d))
    return out


def next_round(cand, k):
    os.makedirs(REPORTS, exist_ok=True)
    rows = []
    for fid, d in items_for(cand, k):
        meta = json.load(open(os.path.join(d, 'meta.json')))
        text = open(os.path.join(d, 'iter%d.txt' % (k - 1))).read()
        name = hashlib.sha256(('%s|%s/%s/%d' % (fixloop.SALT, cand, fid, k)).encode()).hexdigest()[:20] + '.txt'
        with open(os.path.join(REPORTS, name), 'w') as fh:
            fh.write(text)
        ws = meta['ws']
        rows.append({'key': hashlib.sha256(('%s|key|%s/%s/%d' % (fixloop.SALT, cand, fid, k)).encode()).hexdigest()[:16],
                     'ws': ws, 'base': meta['base'], 'report_file': os.path.join(REPORTS, name),
                     'test': fixloop.subst(meta['item']['test'], ws), 'iteration': k})
        index_path = os.path.join(fixloop.WORK, 'agent_index.json')
        index = json.load(open(index_path)) if os.path.exists(index_path) else {}
        index[rows[-1]['key']] = {'candidate': cand, 'fix_id': fid, 'iteration': k}
        json.dump(index, open(index_path, 'w'), indent=1)
    json.dump({'items': rows}, sys.stdout)


def gate_round(cand, k, llm_root=None):
    for fid, d in items_for(cand, k):
        fixloop.gate(cand, fid, k, llm_root)


if __name__ == '__main__':
    cmd, cand, k = sys.argv[1], sys.argv[2], int(sys.argv[3])
    next_round(cand, k) if cmd == 'next' else gate_round(cand, k, os.environ.get('FIX_LLM_ROOT'))
