"""Read a workflow's journal into {label: result}: what each fresh subagent returned.

    python3 -I journal.py RUN_ID [RUN_ID ...] > out.json
"""
import json, os, sys

BASE = '/root/.claude/projects/-home-user-qualm/2ede26c6-6287-5d82-9ba2-8868fcdef917/subagents/workflows'


def results(run_id):
    label, out = {}, {}
    for line in open(os.path.join(BASE, run_id, 'journal.jsonl')):
        r = json.loads(line)
        if r.get('type') == 'started':
            label[r['agentId']] = r.get('label')
        elif r.get('type') == 'result':
            out[label.get(r['agentId'], r['agentId'])] = r.get('result')
    return out


if __name__ == '__main__':
    merged = {}
    for run in sys.argv[1:]:
        merged.update(results(run))
    json.dump(merged, sys.stdout, indent=1)
