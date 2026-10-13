"""Turn GH Archive review events into labelled file-level changes, fetching code over git only.

Usage: python3 -I github_collect.py GHA_DIR WORKDIR OUT.jsonl [--per-repo 4] [--workers 4]

Positive: a person's review comment matching simplify_regex, anchored (by its diff hunk) on or
next to an added line, where the pull request's later head changed the file near that line.
Negative: a file of the same pull request at the same commit that no person commented on and
that reached the pull request's final head unchanged ("approved_uncommented").
Agent-written: the head branch starts with an agent's prefix (copilot/, codex/, claude/, ...).
Repositories are fetched with --filter=blob:none into WORKDIR and deleted afterwards.
"""
import argparse, collections, gzip, glob, json, os, re, shutil, subprocess, sys
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import judged_file, language, added_lines, changed_near  # noqa: E402
from simplify_regex import ACK  # noqa: E402

AGENT_PREFIX = ('copilot/', 'codex/', 'claude/', 'cursor/', 'devin/', 'jules/', 'sweep/', 'openhands/', 'aider/', 'codegen-')
AI_LIKE = re.compile(r'(?i)\*\*P[0-3]\b|!\[P[0-3]|prompt for ai|ai agent|\U0001F916|generated (?:by|with)|coderabbit|<details>|severity:|\*\*(?:issue|problem|suggestion|why)\*\*|^#+ |suggested (?:fix|change):')
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@')
MAX_BYTES = 200_000
ENV = dict(os.environ, GIT_TERMINAL_PROMPT='0', GIT_ASKPASS='/bin/false')


def git(repo, *args, timeout=300, binary=False):
    try:
        r = subprocess.run(['git', '-C', repo, '-c', 'core.quotepath=off', '-c', 'gc.auto=0', '-c', 'maintenance.auto=false', *args],
                           capture_output=True, timeout=timeout, env=ENV)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    return r.stdout if binary else r.stdout.decode('utf-8', 'replace')


def show(repo, rev, path):
    data = git(repo, 'show', '%s:%s' % (rev, path), binary=True, timeout=120)
    if data is None or len(data) > MAX_BYTES or b'\x00' in data[:8000]:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


def line_of(hunk):
    """New-file line of the comment, which GitHub anchors on the hunk's last line. None if on the old side."""
    lines = (hunk or '').split('\n')
    start = None
    for i, l in enumerate(lines):
        m = HUNK.match(l)
        if m:
            start, body = int(m.group(1)), lines[i + 1:]
    if start is None or not body:
        return None
    if body[-1].startswith('-'):
        return None
    n = start - 1
    for l in body:
        if not l.startswith('-'):
            n += 1
    return n


def load(gha_dir):
    prs = collections.defaultdict(lambda: {'sig': [], 'touched': collections.defaultdict(set), 'replies': [], 'approved': False,
                                           'merged': False, 'href': '', 'bs': None, 'author': None})
    for f in sorted(glob.glob(os.path.join(gha_dir, '*.jsonl.gz'))):
        for l in gzip.open(f, 'rt'):
            r = json.loads(l)
            pr = r.get('pr') or {}
            if not pr.get('n') or not r.get('repo'):
                continue
            p = prs[(r['repo'], pr['n'])]
            p['href'] = p['href'] or (pr.get('href') or '')
            p['bs'] = p['bs'] or pr.get('bs')
            if r['k'] == 'c':
                p['touched'][r['path']].add(r['u'])
                if r.get('reply'):
                    p['replies'].append(r)
                elif r.get('cls') and judged_file(r['path']):
                    p['sig'].append(r)
            elif r['k'] == 'r' and r['state'] == 'approved':
                p['approved'] = True
            elif r['k'] == 'p':
                if r['action'] == 'opened':
                    p['author'] = r.get('actor')
                if r.get('merged'):
                    p['merged'] = True
    return prs


def do_repo(args):
    repo, items, workdir, per_repo = args
    path = os.path.join(workdir, repo.replace('/', '__') + '.git')
    out = []
    try:
        os.makedirs(path, exist_ok=True)
        git(path, 'init', '-q', '--bare')
        git(path, 'remote', 'add', 'origin', 'https://github.com/%s.git' % repo)
        git(path, 'config', 'remote.origin.promisor', 'true')
        git(path, 'config', 'remote.origin.partialclonefilter', 'blob:none')
        for (n, p) in items[:per_repo]:
            out.extend(do_pr(path, repo, n, p))
    except Exception as e:  # one repository failing is logged and left out
        print('  skip %s: %s %s' % (repo, type(e).__name__, str(e)[:100]), file=sys.stderr, flush=True)
    finally:
        shutil.rmtree(path, ignore_errors=True)
    return out


def do_pr(path, repo, n, p):
    final_ref = 'refs/remotes/pr/%d' % n
    shas = sorted({s['ocid'] for s in p['sig'] if s.get('ocid')})
    if not shas or not p['bs']:
        return []
    want = ['+refs/pull/%d/head:%s' % (n, final_ref)] + shas + [p['bs']]
    if git(path, 'fetch', '-q', '--filter=blob:none', '--depth=200', 'origin', *want, timeout=600) is None:
        # A force-pushed commit may be gone; try without the comment commits.
        return []
    cases = []
    for ocid in shas:
        mb = (git(path, 'merge-base', p['bs'], ocid) or '').strip()
        if not mb:
            continue
        names = git(path, 'diff', '--no-renames', '--name-status', mb, ocid) or ''
        files, added_paths = [], {}
        for row in names.splitlines():
            parts = row.split('\t')
            if len(parts) != 2 or parts[0] not in ('A', 'M') or not judged_file(parts[1]):
                continue
            added_paths[parts[1]] = parts[0] == 'A'
        if len(added_paths) > 40:
            continue
        for fp, is_new in sorted(added_paths.items()):
            head = show(path, ocid, fp)
            base = None if is_new else show(path, mb, fp)
            if head is None or (not is_new and base is None) or head == base:
                continue
            files.append({'path': fp, 'language': language(fp), 'base': base, 'head': head})
        items = []
        for f in files:
            sigs = [s for s in p['sig'] if s['ocid'] == ocid and s['path'] == f['path']]
            final = show(path, final_ref, f['path'])
            if sigs:
                added, _ = added_lines(f['base'], f['head'])
                good = []
                for s in sigs:
                    if AI_LIKE.search(s.get('body') or '') or len(s.get('body') or '') > 700:
                        continue  # reads like an AI review tool posting through a person's account
                    ln = line_of(s.get('hunk'))
                    if not ln or not any(ln - 3 <= a <= ln + 3 for a in added):
                        continue
                    acked = any(r.get('reply') == s['id'] and r['u'] != s['u'] and ACK.search(r.get('body') or '') for r in p['replies'])
                    moved = changed_near(f['head'], final or '', ln)
                    if acked or moved:
                        good.append((ln, s, {'acked': acked, 'changed_later': moved}))
                if good:
                    items.append({'path': f['path'], 'label': 1, 'label_source': 'reviewer_comment',
                                  'strength': 'strong' if any(g[1]['cls'] == 'strong' for g in good) else 'weak',
                                  'location': [min(g[0] for g in good), max(g[0] for g in good)],
                                  'locations': [[g[0], g[0]] for g in good], 'evidence': [g[1]['body'] for g in good],
                                  'acted': [g[2] for g in good]})
            elif f['path'] not in p['touched'] and final is not None and final == f['head']:
                items.append({'path': f['path'], 'label': 0, 'label_source': 'approved_uncommented'})
        if any(i['label'] == 1 for i in items):
            cases.append({'case_id': 'github-%s-%d-%s' % (repo.replace('/', '__'), n, ocid[:8]), 'source': 'github',
                          'repo': repo, 'url': 'https://github.com/%s/pull/%d' % (repo, n), 'subject': '',
                          'agent_written': p['href'].lower().startswith(AGENT_PREFIX), 'head_ref': p['href'][:80],
                          'files': files, 'items': items})
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('gha_dir')
    ap.add_argument('workdir')
    ap.add_argument('out')
    ap.add_argument('--per-repo', type=int, default=4)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--limit-repos', type=int, default=0)
    a = ap.parse_args()
    prs = load(a.gha_dir)
    byrepo = collections.defaultdict(list)
    for (repo, n), p in prs.items():
        if p['sig']:
            byrepo[repo].append((n, p))
    # Agent-written pull requests first within a repository, then by number.
    jobs = [(repo, sorted(v, key=lambda x: (not x[1]['href'].lower().startswith(AGENT_PREFIX), x[0])), a.workdir, a.per_repo)
            for repo, v in sorted(byrepo.items())]
    if a.limit_repos:
        jobs = jobs[:a.limit_repos]
    done_path = a.out + '.done'
    done_repos = set(open(done_path).read().split()) if os.path.exists(done_path) else set()
    jobs = [j for j in jobs if j[0] not in done_repos]
    print('%d repositories with signal still to fetch (%d done)' % (len(jobs), len(done_repos)), flush=True)
    import multiprocessing
    n = 0
    with open(a.out, 'a') as fh, open(done_path, 'a') as dh, multiprocessing.Pool(a.workers, maxtasksperchild=20) as pool:
        for repo, cases in pool.imap_unordered(do_repo_named, jobs):
            n += 1
            for c in cases:
                fh.write(json.dumps(c) + '\n')
            fh.flush()
            dh.write(repo + '\n')
            dh.flush()
            if n % 25 == 0:
                print('  %d/%d repositories' % (n, len(jobs)), flush=True)


def do_repo_named(job):
    return job[0], do_repo(job)


if __name__ == '__main__':
    main()
