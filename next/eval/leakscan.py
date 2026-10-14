"""Which agents' tool calls touched held-out material, from the session's subagent transcripts.

    python3 -I leakscan.py TRANSCRIPT_DIR --out results/leakscan.json

Every tool call (command, file path, edit) an agent made is matched against the held-out paths:
the assembled held-out files, the held-out fix-loop items, the adjudication key maps, the key and
the sealed archive. The result lists each agent that matched with the first line of its task, so
a reader can tell the agents that built the held-out split (expected) from any other (a leak).
"""
import argparse, collections, json, os, re

PAT = re.compile(r'assembled_(v1|test|final)/heldout|heldout(_reserve)?\.jsonl|fx-held|adjudication_keymap|adj_test_keymap|heldout\.key|heldout\.v1\.tar|inject_(args|results)_\w+_heldout')


def task_text(first):
    c = first.get('content')
    c = c if isinstance(c, str) else json.dumps(c)
    i = c.find('The computed task text follows:')
    return ' '.join((c[i + 31:] if i >= 0 else c).split())[:160]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('--out')
    a = ap.parse_args()
    agents, scanned = [], 0
    for dp, _dn, fn in os.walk(a.root):
        for name in sorted(fn):
            if not (name.startswith('agent-') and name.endswith('.jsonl')):
                continue
            scanned += 1
            first, hits = None, collections.Counter()
            for line in open(os.path.join(dp, name), errors='replace'):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                m = d.get('message') or {}
                if first is None and m.get('role') == 'user':
                    first = m
                if m.get('role') == 'assistant' and isinstance(m.get('content'), list):
                    for c in m['content']:
                        if c.get('type') == 'tool_use':
                            for mm in PAT.finditer(json.dumps(c.get('input', {}))):
                                hits[mm.group(0)] += 1
            if hits:
                agents.append({'transcript': os.path.relpath(os.path.join(dp, name), a.root), 'task': task_text(first or {}), 'matches': dict(hits)})
    out = {'agents_scanned': scanned, 'agents_matching': len(agents), 'agents': agents}
    if a.out:
        json.dump(out, open(a.out, 'w'), indent=1)
    print(json.dumps({'agents_scanned': scanned, 'agents_matching': len(agents)}))
    for g in agents:
        print(g['transcript'], '|', g['task'][:100])


if __name__ == '__main__':
    main()
