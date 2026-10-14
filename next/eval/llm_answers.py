"""Move LLM requests and answers between a candidate's --llm-dir folders and the answering workflow.

    python3 -I llm_answers.py pending ROOT [--limit N]      -> JSON list of unanswered request files under ROOT
    python3 -I llm_answers.py write RUN_ID [RUN_ID ...]      -> writes each agent's answer to responses/<key>.json

A request lives at <dir>/requests/<key>.json and its answer at <dir>/responses/<key>.json. Each answer
comes from a fresh-context subagent that saw only its request file (workflows/answer_llm.js).
"""
import glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import journal  # noqa: E402


def pending(root, limit=None):
    out = []
    for req in sorted(glob.glob(os.path.join(root, '**', 'requests', '*.json'), recursive=True)):
        resp = os.path.join(os.path.dirname(os.path.dirname(req)), 'responses', os.path.basename(req))
        if not os.path.exists(resp):
            out.append(req)
    return out[:limit] if limit else out


def write(run_ids):
    n = 0
    for run in run_ids:
        for path, ans in journal.results(run).items():
            if not isinstance(ans, dict) or not path.endswith('.json') or '/requests/' not in path:
                continue
            resp_dir = os.path.join(os.path.dirname(os.path.dirname(path)), 'responses')
            os.makedirs(resp_dir, exist_ok=True)
            with open(os.path.join(resp_dir, os.path.basename(path)), 'w') as fh:
                json.dump(ans, fh)
            n += 1
    return n


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'pending':
        lim = int(sys.argv[sys.argv.index('--limit') + 1]) if '--limit' in sys.argv else None
        json.dump(pending(sys.argv[2], lim), sys.stdout)
    elif cmd == 'write':
        print(write(sys.argv[2:]), 'answers written')
