"""Mine a full clone for changes that were soon simplified by a follow-up commit.

Usage: python3 -I history_mine.py REPO_DIR OUT.jsonl --name owner/repo [--days 30] [--max-neg 40]

Positive ("followup_cleanup"): commit P changed source file F, and within `days` a later commit X
whose subject says it simplifies, refactors, cleans up, deduplicates or removes dead code
rewrote at least 3 of the lines P added (found by blaming X's parent). The item is P's change to
F; its location is the span of P's lines that X rewrote.
Negative ("survived_90d"): commit P added at least 10 lines to F, P is not itself a cleanup, and
90 days later at least 90% of those lines are still attributed to P.
Commit subjects are untrusted text; they are only matched against a pattern.
"""
import argparse, collections, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import judged_file, language  # noqa: E402

CLEANUP = re.compile(r'(?i)\b(simplif\w*|refactor\w*|clean[ -]?up|tidy|dedup\w*|de-?duplicat\w*|remove (?:dead|unused|redundant|unnecessary)|'
                     r'extract\w*|dry up|reduce duplication|consolidate)\b')
NOT_CODE = re.compile(r'(?i)^(release|chore|docs?|bump|version|merge|revert)\b|comment')
HUNK = re.compile(r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
MAX_BYTES = 200_000


def git(repo, *args):
    r = subprocess.run(['git', '-C', repo, '-c', 'core.quotepath=off', *args], capture_output=True)
    return r.stdout.decode('utf-8', 'replace') if r.returncode == 0 else None


def show(repo, rev, path):
    r = subprocess.run(['git', '-C', repo, 'show', '%s:%s' % (rev, path)], capture_output=True)
    if r.returncode != 0 or len(r.stdout) > MAX_BYTES or b'\x00' in r.stdout[:8000]:
        return None
    try:
        return r.stdout.decode('utf-8')
    except UnicodeDecodeError:
        return None


def old_lines_touched(repo, x, path):
    """Old-side line numbers that commit x deletes or rewrites in path."""
    diff = git(repo, 'diff', '-U0', '--no-color', '--no-ext-diff', x + '^', x, '--', path) or ''
    out = []
    for l in diff.splitlines():
        m = HUNK.match(l)
        if m:
            start, n = int(m.group(1)), int(m.group(2) or 1)
            out.extend(range(start, start + n))
    return out


def blame_origins(repo, rev, path, lines):
    """For each line of path at rev, the commit and the line number in that commit that introduced it."""
    if not lines:
        return {}
    args = []
    for ln in sorted(set(lines)):
        args += ['-L', '%d,%d' % (ln, ln)]
    out = git(repo, 'blame', '--porcelain', *args, rev, '--', path) or ''
    res = {}
    for l in out.splitlines():
        parts = l.split(' ')
        if len(parts) >= 3 and len(parts[0]) == 40 and parts[1].isdigit():
            res[int(parts[2])] = (parts[0], int(parts[1]))
    return res


def commit_files(repo, sha):
    out = git(repo, 'diff', '--no-renames', '--name-status', sha + '^', sha) or ''
    return [(r.split('\t')[0], r.split('\t')[1]) for r in out.splitlines() if '\t' in r]


def make_case(repo, name, sha, path, label, source, location=None, evidence=None):
    status = dict(commit_files(repo, sha))
    files = []
    for st, p in commit_files(repo, sha):
        if st not in ('A', 'M') or not judged_file(p):
            continue
        head = show(repo, sha, p)
        base = None if st == 'A' else show(repo, sha + '^', p)
        if head is None or (st == 'M' and base is None) or head == base:
            continue
        files.append({'path': p, 'language': language(p), 'base': base, 'head': head})
    if not any(f['path'] == path for f in files) or len(files) > 30:
        return None
    item = {'path': path, 'label': label, 'label_source': source}
    if location:
        item['location'] = location
    if evidence:
        item['evidence'] = [evidence]
    subject = (git(repo, 'log', '-1', '--format=%s', sha) or '').strip()[:300]
    return {'case_id': 'history-%s-%s' % (name.replace('/', '__'), sha[:10]), 'source': 'history', 'repo': name,
            'url': 'https://github.com/%s/commit/%s' % (name, sha), 'subject': subject, 'files': files, 'items': [item],
            '_status': status.get(path)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo')
    ap.add_argument('out')
    ap.add_argument('--name', required=True)
    ap.add_argument('--days', type=int, default=30)
    ap.add_argument('--max-neg', type=int, default=40)
    ap.add_argument('--since', default='2018-01-01')
    a = ap.parse_args()
    log = git(a.repo, 'log', '--first-parent', '--no-merges', '--since', a.since, '--format=%H%x09%ct%x09%s') or ''
    commits = [l.split('\t', 2) for l in log.splitlines() if l.count('\t') >= 2]
    when = {c[0]: int(c[1]) for c in commits}
    subj = {c[0]: c[2] for c in commits}
    positives = collections.OrderedDict()
    for x, t, s in commits:
        if not CLEANUP.search(s):
            continue
        for st, path in commit_files(a.repo, x):
            if st != 'M' or not judged_file(path):
                continue
            origins = blame_origins(a.repo, x + '^', path, old_lines_touched(a.repo, x, path))
            by_commit = collections.defaultdict(list)
            for _, (p, pline) in origins.items():
                by_commit[p].append(pline)
            best = None
            for p, plines in by_commit.items():
                if p == x or p not in when or len(plines) < 3 or CLEANUP.search(subj[p]) or NOT_CODE.search(subj[p]):
                    continue
                if not 0 < int(t) - when[p] <= a.days * 86400 or (p, path) in positives:
                    continue
                numstat = (git(a.repo, 'diff', '--numstat', p + '^', p, '--', path) or '0').split('\t')[0]
                share = len(plines) / max(1, int(numstat) if numstat.isdigit() else 1)
                if share >= 0.3 and (best is None or len(plines) > len(best[1])):
                    best = (p, plines, share)
            if best:
                p, plines, share = best
                positives[(p, path)] = ([min(plines), max(plines)], '%s (rewrote %d%% of its lines)' % (s[:200], round(100 * share)))
    cases = []
    for (p, path), (loc, why) in positives.items():
        c = make_case(a.repo, a.name, p, path, 1, 'followup_cleanup', loc, 'Followed within %d days by: %s' % (a.days, why))
        if c:
            cases.append(c)
    # Negatives: lines that stayed as written for 90 days.
    rev_by_time = sorted(commits, key=lambda c: int(c[1]))
    n_neg, seen = 0, set(p for p, _ in positives)
    for p, t, s in reversed(commits):
        if n_neg >= a.max_neg:
            break
        if p in seen or CLEANUP.search(s):
            continue
        later = [c[0] for c in rev_by_time if int(c[1]) >= int(t) + 90 * 86400]
        if not later:
            continue
        at = later[0]
        for st, path in commit_files(a.repo, p):
            if st not in ('A', 'M') or not judged_file(path):
                continue
            diff = git(a.repo, 'diff', '-U0', '--no-color', p + '^', p, '--', path) or ''
            added = sum(1 for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++'))
            if added < 10 or added > 300:
                continue
            blame = git(a.repo, 'blame', '--porcelain', at, '--', path) or ''
            kept = sum(1 for l in blame.splitlines() if l.startswith(p + ' '))
            if kept >= 0.9 * added:
                c = make_case(a.repo, a.name, p, path, 0, 'survived_90d')
                if c:
                    cases.append(c)
                    n_neg += 1
                break
    with open(a.out, 'w') as fh:
        for c in cases:
            c.pop('_status', None)
            fh.write(json.dumps(c) + '\n')
    print(a.name, 'positives', sum(c['items'][0]['label'] for c in cases), 'negatives', n_neg, flush=True)


if __name__ == '__main__':
    main()
