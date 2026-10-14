"""The study's Jev spend by part, from the ledger (results/spend.jsonl), by fixed rules on each entry's
description.

    python3 -I eval/spend_summary.py > eval/results/spend_summary.json
"""
import json, os

LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'results', 'spend.jsonl')
RULES = [  # first match wins
    ('fix loop (gate runs, recorded after the fact)', ('fix-loop gate runs',)),
    ('held-out: accuracy and determinism runs', ('heldout', 'held-out')),
    ('attacks', ('attack suite',)),
    ('winner checks: equivalence and self-run', ('equiv-', 'self-run')),
    ('dev determinism', ('(det-', 'det30 subset', 'det100 subset')),
    ('dev training data: Jev answers for the models', ('masked-view', 'per-function', 'unit asks')),
    ('smoke tests', ('smoke test',)),
    ('dev accuracy runs and pilots', ('',)),
]


def main():
    parts, rows = {}, [json.loads(l) for l in open(LEDGER)]
    for r in rows:
        name = next(n for n, keys in RULES if any(k in r['what'] for k in keys))
        p = parts.setdefault(name, {'cost_usd': 0.0, 'entries': 0})
        p['cost_usd'] += r.get('cost_usd', 0)
        p['entries'] += 1
    for p in parts.values():
        p['cost_usd'] = round(p['cost_usd'], 4)
    whens = sorted(r['when'] for r in rows)
    json.dump({'total_usd': round(sum(r.get('cost_usd', 0) for r in rows), 4), 'entries': len(rows), 'budget_usd': 20,
               'first_entry': whens[0], 'parts': dict(sorted(parts.items(), key=lambda kv: -kv[1]['cost_usd'])),
               'rules': [[n, list(k)] for n, k in RULES],
               'note': 'One entry is stamped 05:10Z but sits between 03:26Z and 03:47Z in the file: it records held-out runs stopped at about 03:30Z and was mis-stamped; its amount is unaffected.'},
              __import__('sys').stdout, indent=1)


if __name__ == '__main__':
    main()
