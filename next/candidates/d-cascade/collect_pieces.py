"""Ask Jev about each changed function (the localization pieces) of every scored dev file, so the
model can learn from spot-level answers too.

    GATE_CACHE_DIR=... python3 -I collect_pieces.py DEV.jsonl.gz OUT.json --budget 0.6 [--ledger spend.jsonl]

Pieces and questions are exactly what scorer.py asks at run time (scorer.pieces, scorer.piece_state).
"""
import argparse, gzip, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '_core'))
sys.path.insert(0, HERE)

from gatecore import analyze, jev, lexer  # noqa: E402
import asks, scorer  # noqa: E402


def jobs_of(dev):
    out = []
    with gzip.open(dev, 'rt') as fh:
        for line in fh:
            c = json.loads(line)
            files = {f['path']: f for f in c['files']}
            for it in c['items']:
                f = files.get(it['path'])
                if it.get('label') is None or not f or f.get('head') is None:
                    continue
                lang = lexer.language_of(f['path'])
                if lang not in lexer.JUDGED:
                    continue
                ctx = analyze.FileCtx(f['path'], lang, f['head'], f.get('base'))
                places, masked = scorer.pieces(ctx)
                out.append((it['item_id'], [scorer.piece_state(ctx, lo, hi, name, masked) for lo, hi, name, _ in places],
                            [len(rows) for _, _, _, rows in places]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dev')
    ap.add_argument('out')
    ap.add_argument('--budget', type=float, required=True)
    ap.add_argument('--ledger')
    ap.add_argument('--jobs', type=int, default=8)
    a = ap.parse_args()
    client = jev.Client(budget=a.budget, cache_dir=os.environ.get('GATE_CACHE_DIR'))
    if not client.available():
        sys.exit('no API key: set %s' % jev.KEY_VAR)
    todo = jobs_of(a.dev)
    print(len(todo), 'files,', sum(len(s) for _, s, _ in todo), 'pieces', flush=True)
    done = json.load(open(a.out)) if os.path.exists(a.out) else {}
    todo = [t for t in todo if t[0] not in done]

    def one(t):
        iid, states, sizes = t
        try:
            return iid, {'pieces': [client.ask(s, asks.HUNK_QUESTIONS) for s in states], 'sizes': sizes}
        except jev.BudgetSpent:
            return iid, None
        except jev.JevError as e:
            return iid, {'error': str(e)[:200]}

    n = 0
    try:
        with ThreadPoolExecutor(a.jobs) as ex:
            for iid, ans in ex.map(one, todo):
                if ans is not None:
                    done[iid] = ans
                n += 1
                if n % 500 == 0:
                    print('  %d/%d $%.4f' % (n, len(todo), client.cost), flush=True)
    finally:
        json.dump(done, open(a.out, 'w'))
        if a.ledger and client.cost > 0:
            with open(a.ledger, 'a') as fh:
                fh.write(json.dumps({'when': time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime()), 'what': 'd-cascade: per-function Jev answers for dev scored files',
                                     'cases': n, 'cost_usd': round(client.cost, 6)}) + '\n')
    print('%d files, $%.4f, %d requests' % (len(done), client.cost, client.requests))


if __name__ == '__main__':
    main()
