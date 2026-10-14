"""Ask Jev the masked-view questions about every scored file of the dev split, for training.

    python3 -I collect_dev.py DEV.jsonl.gz OUT.json --budget 1.2 [--ledger spend.jsonl] [--jobs 8]

The raw view's answers for training come from qualm 0.1.1's own run over the same cases (its
request is the raw view). Answers are cached (GATE_CACHE_DIR, signed), so a rerun costs nothing.
One line with what Jev reported is appended to the study ledger.
"""
import argparse, gzip, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '_core'))
sys.path.insert(0, HERE)

from gatecore import jev, lexer  # noqa: E402
import asks  # noqa: E402


def jobs_of(dev):
    out = []
    with gzip.open(dev, 'rt') as fh:
        for line in fh:
            c = json.loads(line)
            files = {f['path']: f for f in c['files']}
            for it in c['items']:
                if it.get('label') is None:
                    continue
                f = files.get(it['path'])
                if not f or f.get('head') is None:
                    continue
                lang = lexer.language_of(f['path'])
                d = asks.masked_diff(f['path'], f.get('base'), f['head'], lang, f.get('old_path'))
                out.append((it['item_id'], d, asks.language(f['path'])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dev')
    ap.add_argument('out')
    ap.add_argument('--budget', type=float, required=True)
    ap.add_argument('--ledger', default=None)
    ap.add_argument('--jobs', type=int, default=8)
    a = ap.parse_args()
    cache = os.environ.get('GATE_CACHE_DIR')
    if not cache:
        sys.exit('set GATE_CACHE_DIR so answers are kept')
    client = jev.Client(budget=a.budget, cache_dir=cache)
    if not client.available():
        sys.exit('no API key: set %s' % jev.KEY_VAR)
    todo = jobs_of(a.dev)
    print(len(todo), 'files', flush=True)
    done = json.load(open(a.out)) if os.path.exists(a.out) else {}
    todo = [t for t in todo if t[0] not in done]

    def one(t):
        iid, d, lang = t
        if not d.strip():
            return iid, {'empty': True}
        try:
            return iid, asks.ask_pieces(client, d, lang, asks.MASKED_QUESTIONS, asks.MASKED_FORMAT)
        except jev.BudgetSpent:
            return iid, None
        except jev.JevError as e:
            return iid, {'error': str(e)[:200]}

    t0 = time.time()
    n = 0
    try:
        with ThreadPoolExecutor(a.jobs) as ex:
            for iid, ans in ex.map(one, todo):
                if ans is not None:
                    done[iid] = ans
                n += 1
                if n % 250 == 0:
                    print('  %d/%d  $%.4f  cache hits %d' % (n, len(todo), client.cost, client.cache_hits), flush=True)
                    json.dump(done, open(a.out, 'w'))
    finally:
        json.dump(done, open(a.out, 'w'))
        if a.ledger and client.cost > 0:
            with open(a.ledger, 'a') as fh:
                fh.write(json.dumps({'when': time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime()), 'what': 'd-cascade: masked-view Jev answers for dev scored files',
                                     'cases': n, 'cost_usd': round(client.cost, 6)}) + '\n')
    print('%d answered in %.0fs, $%.4f, %d requests, %d cache hits' % (len(done), time.time() - t0, client.cost, client.requests, client.cache_hits))


if __name__ == '__main__':
    main()
