"""a-recal's run file from a recorded baseline run: the same reports with each judged file's score
mapped through a-recal's single Platt step (platt.json), exactly what `candidates/a-recal/run` does
to a live report. Verdicts stay qualm's.

    python3 -I recal_run.py BASELINE_RUN.jsonl OUT.jsonl [--platt candidates/a-recal/platt.json]

On dev the Platt step was fitted on these same scores (two parameters), so its calibration
numbers there are in-sample; its ranking is the baseline's, since the map is monotone.
"""
import argparse, json, math, os

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('run')
    ap.add_argument('out')
    ap.add_argument('--platt', default=os.path.join(HERE, '..', 'candidates', 'a-recal', 'platt.json'))
    a = ap.parse_args()
    platt = json.load(open(a.platt))
    with open(a.out, 'w') as out:
        for line in open(a.run):
            r = json.loads(line)
            rep = r.get('report')
            if rep:
                for f in rep.get('files') or []:
                    if f.get('status') == 'judged' and f.get('score') is not None:
                        q = min(max(f['score'], 1e-3), 1 - 1e-3)
                        f['score'] = round(1 / (1 + math.exp(-(platt['a'] * math.log(q / (1 - q)) + platt['b']))), 4)
                        f['threshold'] = platt['threshold']
                rep['tool'] = 'a-recal'
            out.write(json.dumps(r) + '\n')


if __name__ == '__main__':
    main()
