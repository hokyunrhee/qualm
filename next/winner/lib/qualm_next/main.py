"""qualm-next's command line: judge the change (the default), or record a keep.

    qualm-next [--base REF] [--format text|json] [--budget DOLLARS] [--no-cache] [--include-tests] [--dry-run]
    qualm-next keep PATH [PATH ...] --reason TEXT
"""
import argparse, datetime, json, os, sys

from gatecore import cli, gitio, perimeter

import asks
import scorer

TOOL = 'qualm-next'
PRICE_PER_TOKEN = 0.042 / 1e6   # Jev's input price through OpenRouter
TOKENS_PER_BYTE = 1 / 3.5


def dry_run(ctx, rows):
    """What would be sent, and roughly what it would cost: two requests per judged file (raw and masked)."""
    out, total = [], 0.0
    for f in ctx.files:
        change = next((c for c in ctx.all_changes if c.path == f.path), None)
        raw = asks.git_diff(ctx.repo, ctx.base, change) if change is not None else None
        raw = raw if raw is not None else asks.py_diff(f.path, f.base, f.head, old_path=f.old_path)
        masked = asks.masked_diff(f.path, f.base, f.head, f.language, f.old_path)
        cost = (len(raw.encode()) + len(masked.encode()) + 4000) * TOKENS_PER_BYTE * PRICE_PER_TOKEN
        total += cost
        out.append('  %s  (%s, about $%.5f)' % (f.path, f.language, cost))
    skipped = [r for r in rows if r['status'] != 'judged']
    head = ['DRY RUN: nothing was sent and nothing was judged, so this run does not pass (exit 2).',
            'Would judge %d file%s, for about $%.4f in Jev requests (more for a failing file or a close call):' % (
                len(ctx.files), '' if len(ctx.files) == 1 else 's', total)]
    tail = []
    if skipped:
        why = {}
        for r in skipped:
            k = (r['status'] if r['status'] != 'skipped' else (r.get('reason') or 'skipped')).split(' (')[0]
            why[k] = why.get(k, 0) + 1
        tail = ['Would not judge %d file%s: %s.' % (len(skipped), '' if len(skipped) == 1 else 's',
                                                    ', '.join('%d %s' % (n, k) for k, n in sorted(why.items(), key=lambda x: -x[1])))]
    return '\n'.join(head + out + tail) + '\n'


def keep(argv):
    ap = argparse.ArgumentParser(prog=TOOL + ' keep', description='Record that a person accepted the change to these files as it stands.')
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--reason', required=True)
    ap.add_argument('--repo', default='.')
    a = ap.parse_args(argv)
    try:
        repo = gitio.repo_root(a.repo)
    except gitio.GateError as e:
        print('%s keep: %s' % (TOOL, e), file=sys.stderr)
        return 2
    path = os.path.join(repo, 'qualm.json')
    data = json.load(open(path)) if os.path.exists(path) else {}
    keeps = [k for k in data.get('keeps') or [] if isinstance(k, dict)]
    for p in a.paths:
        rel = os.path.relpath(os.path.abspath(p), repo)
        full = os.path.join(repo, rel)
        if not os.path.isfile(full):
            print('%s keep: %s is not a file' % (TOOL, p), file=sys.stderr)
            return 2
        with open(full, encoding='utf-8', errors='replace') as fh:
            h = perimeter.content_hash(fh.read())
        keeps = [k for k in keeps if k.get('path') != rel]
        keeps.append({'path': rel, 'hash': h, 'reason': a.reason, 'date': datetime.date.today().isoformat()})
        print('kept %s' % rel)
    data['keeps'] = keeps
    with open(path, 'w') as fh:
        json.dump(data, fh, indent=2)
        fh.write('\n')
    print('A keep is read from the base branch: it takes effect once this qualm.json is merged, never for the pull request '
          'that adds it.')
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['keep']:
        return keep(argv[1:])
    return cli.run(TOOL, scorer.VERSION, scorer.Scorer, argv, dry_run=dry_run)
