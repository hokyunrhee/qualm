"""The command every candidate shares: read the change, decide what to judge, run the detectors,
hand the judged files to the candidate's scorer, and print the verdict.

Exit codes: 0 passed, 1 failed, 2 could not judge (and so never passed).
"""
import argparse, sys, time

from . import analyze, detectors, features, gitio, perimeter, report


class NeedsLLM(Exception):
    pass


def parse(argv, tool):
    ap = argparse.ArgumentParser(prog=tool, description='Decide whether each changed file should be simpler before it merges.')
    ap.add_argument('--repo', default='.')
    ap.add_argument('--base', required=True)
    ap.add_argument('--format', choices=('json', 'text'), default='json')
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--budget', type=float, default=None)
    ap.add_argument('--llm-dir', default=None)
    ap.add_argument('--include-tests', action='store_true')
    ap.add_argument('--threshold', type=float, default=None, help='override the operating point (for evaluation)')
    return ap.parse_args(argv)


def prepare(args):
    """The change, read inside the perimeter, with every judged file analysed."""
    repo = gitio.repo_root(args.repo)
    base = gitio.merge_base(repo, args.base)
    changes = gitio.changes(repo, base)
    policy = perimeter.load_policy(repo, base)
    decisions, warnings = perimeter.classify(repo, base, changes, include_tests=args.include_tests, policy=policy)
    files, rows = [], []
    for d in decisions:
        c = d.change
        if d.status != 'judged':
            row = {'path': c.path, 'status': d.status, 'reason': d.reason}
            if d.language:
                row['language'] = d.language
            rows.append(row)
            continue
        try:
            files.append(analyze.FileCtx(c.path, d.language, c.head, c.base, c.status, c.old_path))
        except (RecursionError, MemoryError, ValueError) as e:
            rows.append({'path': c.path, 'status': 'error', 'language': d.language, 'reason': 'could not be analysed: %s' % type(e).__name__})
    ctx = analyze.ChangeCtx(repo, base, files, changes)
    found = {}
    feats = {}
    for f in files:
        found[f.path] = detectors.run_all(f, ctx)
        feats[f.path] = features.file_features(f, found[f.path])
    return repo, base, policy, ctx, rows, warnings, found, feats


def run(tool, version, make_scorer, argv=None, out=sys.stdout, err=sys.stderr):
    args = parse(argv, tool)
    t0 = time.time()
    try:
        repo, base, policy, ctx, rows, warnings, found, feats = prepare(args)
    except gitio.GateError as e:
        print('%s: %s' % (tool, e), file=err)
        return 2
    scorer = make_scorer(args, policy)
    try:
        results = scorer.score(ctx, found, feats)
    except NeedsLLM as e:
        print('needs llm: %s' % e, file=err)
        return 2
    except gitio.GateError as e:
        print('%s: %s' % (tool, e), file=err)
        return 2
    for f in ctx.files:
        r = results.get(f.path)
        if r is None:
            rows.append({'path': f.path, 'status': 'error', 'language': f.language, 'reason': 'no verdict'})
            continue
        row = {'path': f.path, 'status': 'judged', 'language': f.language, 'score': round(r['score'], 4), 'threshold': r['threshold'],
               'failed': bool(r['failed']), 'abstained': bool(r.get('abstained')), 'findings': r.get('findings', []), 'signals': r.get('signals', {})}
        if r.get('error'):
            row = {'path': f.path, 'status': 'error', 'language': f.language, 'reason': r['error']}
        rows.append(row)
    rows.sort(key=lambda r: r['path'])
    usage = report.empty_usage()
    usage.update(getattr(scorer, 'usage', lambda: {})())
    usage['seconds'] = round(time.time() - t0, 2)
    errors = [r for r in rows if r['status'] == 'error']
    failed = [r for r in rows if r.get('failed')]
    rep = {'tool': tool, 'version': version, 'passed': not errors and not failed, 'base': base, 'files': rows,
           'warnings': warnings + list(getattr(scorer, 'warnings', [])), 'usage': usage}
    out.write(report.to_json(rep) + '\n' if args.format == 'json' else report.to_text(rep))
    if errors:
        return 2
    return 1 if failed else 0
