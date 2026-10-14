"""Train e-spot's model on the dev split only.

    python3 -I train.py estimate DEV.jsonl.gz --rows ROWS.pkl
        Featurize every scored dev file in memory (what `run` computes, without git), pick its
        spots and build its Jev requests, and print what asking would cost. No network.
    python3 -I train.py ask --rows ROWS.pkl --answers ANSWERS.jsonl --budget 1.50 [--sample-cost 1.40]
                            [--ledger PATH] [--limit N]
        Ask Jev about the spots of the scored files (all of them, or a sample stratified by
        language and label source with a fixed seed when --sample-cost is below the full cost).
        Needs OPENROUTER_API_KEY; uses the signed cache in GATE_CACHE_DIR; appends one line to the
        study ledger with what Jev reported.
    python3 -I train.py fit --rows ROWS.pkl --answers ANSWERS.jsonl [--out model.json] [--report cv.json]
        Fit the logistic model with folds grouped by repository; Platt step on the out-of-fold
        logits; threshold chosen on the out-of-fold probabilities.

The threshold rule: the highest out-of-fold recall whose precision stays at or above qualm 0.1.1's
precision on dev at its own gate (0.61; --precision-floor), because the study's definition of done
asks for precision not lower than the baseline's.
"""
import argparse, collections, gzip, json, math, os, pickle, random, sys, time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', '_core'))
sys.path.insert(0, HERE)

from gatecore import analyze, detectors, features, jev, lexer, offline  # noqa: E402
import questions  # noqa: E402
import scorer  # noqa: E402
import spots  # noqa: E402

SEED = 20261014
BASELINE_DEV_PRECISION = 0.61   # qualm 0.1.1 at push_back >= 0.6 on dev (1133 positives, 5139 items)


# ------------------------------------------------------------------ featurizing

def file_ctxs(case, paths):
    """FileCtx for the judged files of a case, plus the scored files the generic rules would skip
    (as gatecore.offline.case_features builds them)."""
    files = []
    for fl, lang in offline.judged_case_files(case):
        try:
            files.append(analyze.FileCtx(fl['path'], lang, fl['head'], fl.get('base'), 'A' if fl.get('base') is None else 'M', fl.get('old_path')))
        except (RecursionError, ValueError):
            continue
    have = {f.path for f in files}
    for fl in case['files']:
        if fl['path'] in paths and fl['path'] not in have and fl.get('head') is not None:
            lang = lexer.language_of(fl['path'])
            if lang in lexer.JUDGED:
                files.append(analyze.FileCtx(fl['path'], lang, fl['head'], fl.get('base'), 'M', fl.get('old_path')))
    return files


def request_bytes(st):
    return len(json.dumps({'model': jev.MODEL, 'state': st, 'questions': {
        q: {'type': 'noul', 'instructions': v['instructions'] + ' ' + jev.GUARD} for q, v in questions.QUESTIONS.items()}}))


def featurize_case(line):
    c = json.loads(line)
    want = {it['path']: it for it in c['items'] if it.get('label') is not None}
    files = file_ctxs(c, set(want))
    ctx = offline.MemoryCtx(files, [fl.get('base') for fl in c['files']])
    rows, case_bytes, case_requests = [], 0, 0
    for f in files:
        try:
            asked, left = spots.select(f)
            ol = spots.outline(f)
            states = [spots.state(f, s, ol) for s in asked]
        except (RecursionError, ValueError):
            continue
        case_bytes += sum(request_bytes(st) for st in states)
        case_requests += len(states)
        if f.path not in want:
            continue
        it = want[f.path]
        found = detectors.run_all(f, ctx)
        rows.append({'item_id': it['item_id'], 'case_id': c['case_id'], 'repo': c['repo'], 'origin': c['source'].split(':')[0],
                     'language': it['language'], 'label_source': it['label_source'], 'label': it['label'],
                     'location': it.get('location'), 'smell': it.get('smell'), 'x': features.file_features(f, found),
                     'spots': [{'unit': s.label, 'kind': s.kind, 'start': s.start, 'end': s.end, 'lo': s.added[0], 'hi': s.added[-1],
                                'added': s.n_added} for s in asked],
                     'left_out': len(left), 'states': states, 'bytes': [request_bytes(st) for st in states],
                     'detectors': [{'id': d['id'], 'lines': d['lines'], 'confidence': d['confidence']} for d in found]})
    return rows, {'case_id': c['case_id'], 'bytes': case_bytes, 'requests': case_requests, 'judged_files': len(files)}


def featurize(dev, jobs=4):
    with gzip.open(dev, 'rt') as fh:
        lines = fh.readlines()
    rows, cases = [], []
    with ProcessPoolExecutor(jobs) as ex:
        for part, case in ex.map(featurize_case, lines, chunksize=8):
            rows.extend(part)
            cases.append(case)
    rows.sort(key=lambda r: r['item_id'])
    return rows, cases


def est_cost(nbytes):
    return jev.cost_of(nbytes // 3)


def cmd_estimate(a):
    rows, cases = featurize(a.dev, a.jobs)
    with open(a.rows, 'wb') as fh:
        pickle.dump({'rows': rows, 'cases': cases}, fh)
    report_estimate(rows, cases)


def report_estimate(rows, cases):
    tot = sum(sum(r['bytes']) for r in rows)
    req = sum(len(r['bytes']) for r in rows)
    print('%d scored files, %d requests, %.1fM bytes, estimated $%.3f (bytes/3 tokens at $%.3f/M)' % (
        len(rows), req, tot / 1e6, est_cost(tot), jev.PRICE_PER_M))
    by = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        k = (r['language'], r['label_source'])
        by[k][0] += 1
        by[k][1] += len(r['bytes'])
        by[k][2] += sum(r['bytes'])
    for k in sorted(by):
        n, q, b = by[k]
        print('  %-10s %-22s %5d files %6d requests  $%.4f' % (k[0], k[1], n, q, est_cost(b)))
    none = sum(1 for r in rows if not r['spots'])
    print('files with no spot: %d; spots per file: mean %.2f; left out: %d files' % (
        none, req / max(1, len(rows)), sum(1 for r in rows if r['left_out'])))
    pr = sorted(est_cost(c['bytes']) for c in cases)
    if pr:
        print('per PR (all judged files): median $%.5f, p90 $%.5f, max $%.4f; requests median %d' % (
            pr[len(pr) // 2], pr[int(len(pr) * 0.9)], pr[-1], sorted(c['requests'] for c in cases)[len(cases) // 2]))


# ------------------------------------------------------------------ asking

def sample(rows, target):
    """Rows to ask about: all when they fit in `target` dollars, else a fixed-seed sample
    stratified by language and label source (the same share of each stratum)."""
    full = sum(est_cost(sum(r['bytes'])) for r in rows)
    if target is None or full <= target:
        return rows, 1.0
    share = target / full
    rng = random.Random(SEED)
    strata = collections.defaultdict(list)
    for r in rows:
        strata[(r['language'], r['label_source'])].append(r)
    out = []
    for k in sorted(strata):
        group = sorted(strata[k], key=lambda r: r['item_id'])
        rng.shuffle(group)
        out.extend(group[:max(1, round(share * len(group)))])
    out.sort(key=lambda r: r['item_id'])
    return out, share


def ledger_total(path):
    total = 0.0
    if path and os.path.exists(path):
        for line in open(path):
            if line.strip():
                total += float(json.loads(line).get('cost_usd', 0) or 0)
    return total


def cmd_ask(a):
    data = pickle.load(open(a.rows, 'rb'))
    rows = data['rows']
    todo, share = sample(rows, a.sample_cost)
    if a.limit:
        todo = todo[:a.limit]
    est = sum(est_cost(sum(r['bytes'])) for r in todo)
    print('asking about %d of %d scored files (share %.3f), %d requests, estimated $%.4f' % (
        len(todo), len(rows), share, sum(len(r['states']) for r in todo), est), flush=True)
    if a.ledger:
        already = ledger_total(a.ledger)
        if already + min(est * 1.3, a.budget) > a.study_cap:
            sys.exit('refusing: the study ledger is at $%.4f and this batch could add $%.4f, past the $%.2f cap' % (already, min(est * 1.3, a.budget), a.study_cap))
    client = jev.Client(budget=a.budget, cache_dir=os.environ.get('GATE_CACHE_DIR') or None)
    if not client.available():
        sys.exit('no API key: set %s' % jev.KEY_VAR)
    done = {}
    if os.path.exists(a.answers):
        for line in open(a.answers):
            rec = json.loads(line)
            done[rec['item_id']] = rec
    jobs = [(r['item_id'], i, st) for r in todo if r['item_id'] not in done for i, st in enumerate(r['states'])]
    got = collections.defaultdict(dict)
    failures = []
    t0 = time.time()

    def one(job):
        item, i, st = job
        try:
            return item, i, client.ask(st, questions.QUESTIONS)
        except jev.JevError as e:
            return item, i, e
    need = collections.Counter(item for item, _, _ in jobs)
    n = 0
    with open(a.answers, 'a') as out, ThreadPoolExecutor(a.threads) as ex:
        for item, i, res in ex.map(one, jobs):
            n += 1
            if isinstance(res, jev.JevError):
                failures.append((item, str(res)))
                if isinstance(res, jev.BudgetSpent):
                    continue
            else:
                got[item][i] = res
            if len(got[item]) == need[item]:
                out.write(json.dumps({'item_id': item, 'answers': [got[item][k] for k in range(need[item])]}) + '\n')
                out.flush()
            if n % 500 == 0:
                print('  %d/%d requests, %d live, $%.4f, %.0fs' % (n, len(jobs), client.requests, client.cost, time.time() - t0), flush=True)
    # files whose every spot was answered but which needed no request (no spots)
    with open(a.answers, 'a') as out:
        for r in todo:
            if not r['states'] and r['item_id'] not in done:
                out.write(json.dumps({'item_id': r['item_id'], 'answers': []}) + '\n')
    print('done: %d requests (%d live, %d from cache), $%.6f reported, %d failures, %.0fs' % (
        len(jobs), client.requests, client.cache_hits, client.cost, len(failures), time.time() - t0), flush=True)
    for item, msg in failures[:5]:
        print('  failure', item, msg)
    if a.ledger and client.requests:
        with open(a.ledger, 'a') as led:
            led.write(json.dumps({'when': time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime()), 'what': 'e-spot: %s' % a.what,
                                  'cases': len({r['case_id'] for r in todo}), 'cost_usd': round(client.cost, 6)}) + '\n')
        print('ledger total now $%.4f' % ledger_total(a.ledger))


# ------------------------------------------------------------------ fitting

def load(a):
    data = pickle.load(open(a.rows, 'rb'))
    answers = {}
    for line in open(a.answers):
        rec = json.loads(line)
        answers[rec['item_id']] = rec['answers']
    rows = []
    for r in data['rows']:
        if r['item_id'] not in answers or len(answers[r['item_id']]) != len(r['states']):
            continue
        # aggregate() weighs each spot by its number of added lines, which is all it reads of a spot
        sp = [spots.Spot(s['kind'], s['unit'], s['start'], s['end'], [s['lo']] * s['added']) for s in r['spots']]
        x = dict(r['x'])
        x.update(scorer.aggregate(sp, answers[r['item_id']], [None] * r['left_out']))
        r = dict(r)
        r['x'] = x
        r['answers'] = answers[r['item_id']]
        del r['states']
        rows.append(r)
    return rows, data['cases']


def matrix(rows, names):
    import numpy as np
    X = np.array([[r['x'].get(n, 0.0) for n in names] for r in rows], dtype=float)
    y = np.array([r['label'] for r in rows], dtype=int)
    return X, y


def metrics(y, p):
    from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss
    out = {'n': int(len(y)), 'positives': int(y.sum())}
    if 0 < y.sum() < len(y):
        out.update(pr_auc=round(float(average_precision_score(y, p)), 4), roc_auc=round(float(roc_auc_score(y, p)), 4))
    if p.min() >= 0 and p.max() <= 1:
        out['brier'] = round(float(brier_score_loss(y, p)), 4)
    return out


def at_threshold(y, p, t):
    f = p >= t
    tp, fp, fn = int((f & (y == 1)).sum()), int((f & (y == 0)).sum()), int((~f & (y == 1)).sum())
    return {'flagged': int(f.sum()), 'precision': round(tp / (tp + fp), 4) if tp + fp else None,
            'recall': round(tp / (tp + fn), 4) if tp + fn else None, 'fail_rate': round(float(f.mean()), 4)}


def pick_threshold(y, p, floor=None):
    import numpy as np
    best = None
    for t in np.unique(np.round(p, 3)):
        f = p >= t
        tp = int((f & (y == 1)).sum())
        fp = int((f & (y == 0)).sum())
        fn = int((~f & (y == 1)).sum())
        if tp == 0:
            continue
        prec, rec = tp / (tp + fp), tp / (tp + fn)
        f1 = 2 * prec * rec / (prec + rec)
        if floor is not None and prec < floor:
            continue
        key = (rec, prec) if floor is not None else (f1,)
        if best is None or key > best[0]:
            best = (key, float(t), prec, rec, f1)
    if best is None:
        return 0.99, {}
    return best[1], {'precision': round(best[2], 4), 'recall': round(best[3], 4), 'f1': round(best[4], 4)}


def fit_lr(X, y, C):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    sc = StandardScaler().fit(X)
    lr = LogisticRegression(C=C, max_iter=5000).fit(sc.transform(X), y)
    return sc, lr


def oof(rows, X, y, C, folds=5):
    import numpy as np
    from sklearn.model_selection import GroupKFold
    groups = np.array([r['repo'] for r in rows])
    z = np.zeros(len(y))
    for tr, te in GroupKFold(n_splits=folds).split(X, y, groups):
        sc, lr = fit_lr(X[tr], y[tr], C)
        z[te] = lr.decision_function(sc.transform(X[te]))
    return z


def platt(z, y):
    from sklearn.linear_model import LogisticRegression
    m = LogisticRegression(C=1e6, max_iter=1000).fit(z.reshape(-1, 1), y)
    return float(m.coef_[0][0]), float(m.intercept_[0])


NATURAL_POS = {'reviewer_comment', 'followup_cleanup'}
NATURAL_NEG = {'approved_uncommented', 'approved_clean', 'approved_agent', 'post_fix', 'survived_90d'}


def strata(rows, y, p, thr):
    import numpy as np
    out = {}
    for key in ('language', 'label_source', 'origin'):
        out[key] = {}
        for v in sorted({r[key] for r in rows}):
            idx = np.array([r[key] == v for r in rows])
            m = metrics(y[idx], p[idx])
            m.update(at_threshold(y[idx], p[idx], thr))
            out[key][v] = m
    nat = np.array([r['label_source'] in NATURAL_POS | NATURAL_NEG for r in rows])
    rev = np.array([r['label_source'] in {'reviewer_comment', 'approved_uncommented', 'approved_clean', 'approved_agent'} for r in rows])
    for name, idx in (('natural', nat), ('reviewer_vs_approved', rev)):
        m = metrics(y[idx], p[idx])
        m.update(at_threshold(y[idx], p[idx], thr))
        out[name] = m
    return out


def location_hits(rows, p, thr):
    """Of the failing reviewer positives, how many carry a spot finding within 3 lines of the
    reviewer's location (the spots that would become findings at VERB_MIN)."""
    hit = n = 0
    for r, s in zip(rows, p):
        if r['label_source'] != 'reviewer_comment' or s < thr or not r.get('location'):
            continue
        lo, hi = r['location']
        n += 1
        for meta, a in zip(r['spots'], r['answers']):
            if max(a[v] for v in questions.IDS) >= scorer.VERB_MIN and meta['lo'] - 3 <= hi and lo <= meta['hi'] + 3:
                hit += 1
                break
    return {'failing_reviewer_positives': n, 'with_a_spot_finding_at_the_location': hit}


def cmd_fit(a):
    import numpy as np
    rows, cases = load(a)
    names = features.names() + scorer.names()
    if a.only_jev:
        names = scorer.names() + ['log_added', 'log_removed', 'new_file'] + ['lang_' + l for l in features.LANGS]
    X, y = matrix(rows, names)
    print(len(rows), 'items,', int(y.sum()), 'positives,', len({r['repo'] for r in rows}), 'repositories', flush=True)
    sigmoid = lambda z: 1 / (1 + np.exp(-z))
    grid = {}
    for C in (0.01, 0.03, 0.1, 0.3, 1.0):
        z = oof(rows, X, y, C)
        grid[C] = (z, metrics(y, sigmoid(z)))
        print('C=%g' % C, grid[C][1], flush=True)
    C = max(grid, key=lambda c: grid[c][1]['pr_auc'])
    z = grid[C][0]
    a_, b_ = platt(z, y)
    p = sigmoid(a_ * z + b_)
    thr, op = pick_threshold(y, p, a.precision_floor)
    sc, lr = fit_lr(X, y, C)
    ablations = {}
    for label, keep in (('core features only', features.names()), ('jev answers only (+size, language)', scorer.names() + ['log_added', 'log_removed', 'new_file'] + ['lang_' + l for l in features.LANGS]),
                        ('push_back only (+size, language)', ['jev.max.push_back', 'jev.mean.push_back', 'jev.noisy_or.push_back', 'spots.none', 'log_added', 'log_removed', 'new_file'] + ['lang_' + l for l in features.LANGS])):
        Xa, _ = matrix(rows, keep)
        za = oof(rows, Xa, y, C)
        ablations[label] = metrics(y, sigmoid(za))
        print('ablation', label, ablations[label], flush=True)
    single = {}
    for n in scorer.names():
        k = names.index(n)
        single[n] = metrics(y, X[:, k])
    model = {'names': names, 'mean': [float(v) for v in sc.mean_], 'scale': [float(v) for v in sc.scale_],
             'coef': [float(v) for v in lr.coef_[0]], 'intercept': float(lr.intercept_[0]), 'platt': [a_, b_], 'threshold': round(thr, 3),
             'meta': {'trained_on': 'dev split only', 'items': len(rows), 'positives': int(y.sum()), 'C': C,
                      'threshold_rule': 'highest out-of-fold recall with precision >= %s' % a.precision_floor if a.precision_floor else 'best out-of-fold F1',
                      'questions': {q: v['instructions'] for q, v in questions.QUESTIONS.items()}}}
    if a.out:
        json.dump(model, open(a.out, 'w'), indent=1)
    rep = {'oof': metrics(y, p), 'C': C, 'grid': {str(c): grid[c][1] for c in grid}, 'threshold': thr, 'operating_point_oof': op,
           'at_threshold_oof': at_threshold(y, p, thr), 'by': strata(rows, y, p, thr), 'ablations_oof': ablations,
           'single_answer_auc': single, 'location': location_hits(rows, p, thr),
           'weights': sorted(zip(names, [round(float(v), 3) for v in lr.coef_[0]]), key=lambda t: -abs(t[1]))}
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        json.dump(rep, open(a.report, 'w'), indent=1)
    if a.oof:
        # {item_id: out-of-fold probability}, the shape next/eval/oof_run.py reads
        with open(a.oof, 'w') as fh:
            json.dump({r['item_id']: round(float(s), 5) for r, s in zip(rows, p)}, fh, sort_keys=True)
    print(json.dumps({k: rep[k] for k in ('oof', 'C', 'threshold', 'operating_point_oof', 'location')}, indent=1))
    print('natural', rep['by']['natural'], '\nreviewer_vs_approved', rep['by']['reviewer_vs_approved'])
    print('top weights', rep['weights'][:14])


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    e = sub.add_parser('estimate')
    e.add_argument('dev')
    e.add_argument('--rows', required=True)
    e.add_argument('--jobs', type=int, default=4)
    k = sub.add_parser('ask')
    k.add_argument('--rows', required=True)
    k.add_argument('--answers', required=True)
    k.add_argument('--budget', type=float, required=True)
    k.add_argument('--sample-cost', type=float, default=None)
    k.add_argument('--limit', type=int, default=None)
    k.add_argument('--threads', type=int, default=8)
    k.add_argument('--ledger', default=None)
    k.add_argument('--study-cap', type=float, default=12.0)
    k.add_argument('--what', default='training asks on dev')
    f = sub.add_parser('fit')
    f.add_argument('--rows', required=True)
    f.add_argument('--answers', required=True)
    f.add_argument('--out', default=os.path.join(HERE, 'model.json'))
    f.add_argument('--report', default=None)
    f.add_argument('--oof', default=None)
    f.add_argument('--precision-floor', type=float, default=BASELINE_DEV_PRECISION)
    f.add_argument('--only-jev', action='store_true')
    a = ap.parse_args()
    {'estimate': cmd_estimate, 'ask': cmd_ask, 'fit': cmd_fit}[a.cmd](a)


if __name__ == '__main__':
    main()
