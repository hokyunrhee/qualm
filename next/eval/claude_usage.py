"""Claude usage by subagents and workflows in this study, in tokens (not dollars: subagent use is not
billed per call here). Read from the session transcript's task notifications, which report each
finished agent's or workflow's total tokens.

    python3 -I claude_usage.py TRANSCRIPT.jsonl > results/claude_usage.json
"""
import json, re, sys


def main():
    seen = {}
    for line in open(sys.argv[1]):
        text = line.replace('\\n', '\n')
        for m in re.finditer(r'<task-id>([^<]+)</task-id>.*?<summary>(.*?)</summary>.*?<subagent_tokens>(\d+)</subagent_tokens>', text, re.S):
            seen[m.group(1)] = {'task': m.group(1), 'what': m.group(2).replace('\\"', '"')[:160], 'tokens': int(m.group(3))}
    rows = sorted(seen.values(), key=lambda r: r['task'])
    json.dump({'source': 'task notifications in the session transcript', 'total_tokens': sum(r['tokens'] for r in rows),
               'runs': rows}, sys.stdout, indent=1)


if __name__ == '__main__':
    main()
