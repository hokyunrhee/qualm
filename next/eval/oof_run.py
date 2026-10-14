"""Turn out-of-fold dev probabilities into a run file metrics.py reads, so a candidate trained on dev
is compared on dev without scoring its own training data.

    python3 -I oof_run.py DEV.jsonl.gz OOF.json THRESHOLD OUT.jsonl [--tool NAME]

Each case gets a report holding its scored files with the out-of-fold score and the verdict at
THRESHOLD. Findings are not part of out-of-fold scores, so localization is not measured from these
files (it is measured from real runs).
"""
import argparse, gzip, json


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dev')
    ap.add_argument('oof')
    ap.add_argument('threshold', type=float)
    ap.add_argument('out')
    ap.add_argument('--tool', default='oof')
    a = ap.parse_args()
    oof = json.load(open(a.oof))
    n = 0
    with open(a.out, 'w') as out, gzip.open(a.dev, 'rt') as fh:
        for line in fh:
            c = json.loads(line)
            files = []
            for it in c['items']:
                if it.get('label') is None:
                    continue
                if it['item_id'] not in oof:
                    files.append({'path': it['path'], 'status': 'error', 'reason': 'no out-of-fold score'})
                    continue
                p = oof[it['item_id']]
                files.append({'path': it['path'], 'status': 'judged', 'score': p, 'threshold': a.threshold, 'failed': p >= a.threshold, 'findings': []})
            if not files:
                continue
            errors = any(f['status'] == 'error' for f in files)
            failed = any(f.get('failed') for f in files)
            out.write(json.dumps({'case_id': c['case_id'], 'exit': 2 if errors else (1 if failed else 0), 'seconds': 0,
                                  'report': {'tool': a.tool, 'out_of_fold': True, 'passed': not errors and not failed, 'files': files}}) + '\n')
            n += 1
    print(n, 'cases written')


if __name__ == '__main__':
    main()
