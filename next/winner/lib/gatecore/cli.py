"""The command every candidate shares: read the change, decide what to judge, run the detectors,
hand the judged files to the candidate's scorer, and print the verdict.

Exit codes: 0 passed, 1 failed, 2 could not judge (and so never passed).
"""
import argparse, sys, time

from . import analyze, detectors, features, gitio, perimeter, report


def parse(argv, tool, version):
    ap = argparse.ArgumentParser(prog=tool, description='Fail a change when an experienced reviewer would ask for it to be simpler.')
    ap.add_argument('--repo', default='.', help='the git repository (default: here)')
    ap.add_argument('--base', default=None, help='compare with the merge base of REF and HEAD (default: the default branch)')
    ap.add_argument('--format', choices=('json', 'text'), default='text')
    ap.add_argument('--no-cache', action='store_true', help='neither read nor write the answer cache')
    ap.add_argument('--budget', type=float, default=1.0, help='stop when this many dollars are spent (default 1.00)')
    ap.add_argument('--include-tests', action='store_true', help='judge test files too')
    ap.add_argument('--threshold', type=float, default=None, help='use this gate threshold for one run')
    ap.add_argument('--text-out', default=None, help='also write the text report of this same run to a file')
    ap.add_argument('--dry-run', action='store_true', help='list what would be judged and its cost, send nothing, and exit 2')
    ap.add_argument('--version', action='version', version='%s %s' % (tool, version))
    return ap.parse_args(argv)


def default_base(repo):
    """The default branch: origin/HEAD when the clone knows it, else main, master or trunk."""
    ref = gitio.git(repo, 'symbolic-ref', '--quiet', 'refs/remotes/origin/HEAD', check=False).strip()
    if ref:
        return ref.replace('refs/remotes/', '')
    for name in ('origin/main', 'origin/master', 'main', 'master', 'trunk'):
        if gitio.git(repo, 'rev-parse', '--verify', '--quiet', name + '^{commit}', check=False).strip():
            return name
    raise gitio.GateError('no default branch found; pass --base REF')


def prepare(args):
    """The change, read inside the perimeter, with every judged file analysed."""
    repo = gitio.repo_root(args.repo)
    base = gitio.merge_base(repo, args.base or default_base(repo))
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


def run(tool, version, make_scorer, argv=None, out=sys.stdout, err=sys.stderr, dry_run=None):
    args = parse(argv, tool, version)
    t0 = time.time()
    try:
        repo, base, policy, ctx, rows, warnings, found, feats = prepare(args)
    except gitio.GateError as e:
        print('%s: %s' % (tool, e), file=err)
        return 2
    except Exception as e:
        print('%s: could not judge: %s: %s' % (tool, type(e).__name__, str(e)[:300]), file=err)
        return 2
    if args.dry_run:
        out.write(dry_run(ctx, rows) if dry_run else '')
        return 2
    try:
        scorer = make_scorer(args, policy)
        results = scorer.score(ctx, found, feats)
    except gitio.GateError as e:
        print('%s: %s' % (tool, e), file=err)
        return 2
    except Exception as e:  # a crash is never a verdict: exit 2, not the 1 an uncaught error would give
        print('%s: could not judge: %s: %s' % (tool, type(e).__name__, str(e)[:300]), file=err)
        return 2
    for f in ctx.files:
        r = results.get(f.path)
        if r is None:
            rows.append({'path': f.path, 'status': 'error', 'language': f.language, 'reason': 'no verdict'})
            continue
        if r.get('error'):
            rows.append({'path': f.path, 'status': 'error', 'language': f.language, 'reason': r['error']})
            continue
        rows.append({'path': f.path, 'status': 'judged', 'language': f.language, 'score': round(r['score'], 4), 'threshold': r['threshold'],
                     'failed': bool(r['failed']), 'abstained': bool(r.get('abstained')), 'findings': r.get('findings', []),
                     'advice': r.get('advice', []), 'signals': r.get('signals', {})})
    rows.sort(key=lambda r: r['path'])
    usage = report.empty_usage()
    usage.update(getattr(scorer, 'usage', lambda: {})())
    usage['seconds'] = round(time.time() - t0, 2)
    errors = [r for r in rows if r['status'] == 'error']
    failed = [r for r in rows if r.get('failed')]
    rep = {'tool': tool, 'version': version, 'passed': not errors and not failed, 'base': base, 'files': rows,
           'warnings': warnings + list(getattr(scorer, 'warnings', [])), 'usage': usage}
    out.write(report.to_json(rep) + '\n' if args.format == 'json' else report.to_text(rep))
    if args.text_out:
        with open(args.text_out, 'w') as fh:
            fh.write(report.to_text(rep))
    if errors:
        return 2
    return 1 if failed else 0
