"""Build a git repository from a benchmark case, identically on every machine.

    python3 -I materialize.py CASES.jsonl[.gz] CASE_ID DEST

DEST gets a branch `base` with the base versions of the case's files and `main` (checked out)
with the head versions. Authors, committers and dates are fixed, so the commit ids are the same
wherever the case is built.
"""
import gzip, json, os, subprocess, sys

ENV = dict(os.environ, GIT_AUTHOR_NAME='bench', GIT_AUTHOR_EMAIL='bench@example.invalid', GIT_COMMITTER_NAME='bench',
           GIT_COMMITTER_EMAIL='bench@example.invalid', GIT_AUTHOR_DATE='2026-01-01T00:00:00Z',
           GIT_COMMITTER_DATE='2026-01-01T00:00:00Z', GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')


def load_case(path, case_id):
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt') as fh:
        for line in fh:
            if case_id in line:
                c = json.loads(line)
                if c['case_id'] == case_id:
                    return c
    raise SystemExit('no case %s in %s' % (case_id, path))


def git(dest, *args):
    subprocess.run(['git', '-C', dest, *args], check=True, env=ENV, capture_output=True)


def write(dest, rel, text):
    full = os.path.join(dest, rel)
    if not os.path.realpath(full).startswith(os.path.realpath(dest) + os.sep):
        raise SystemExit('refusing a path outside the repository: %r' % rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text)


def materialize(case, dest):
    os.makedirs(dest, exist_ok=True)
    git(dest, 'init', '-q', '-b', 'base')
    git(dest, 'config', 'core.autocrlf', 'false')
    for f in case['files']:
        if f.get('base') is not None:
            write(dest, f.get('old_path') or f['path'], f['base'])
    git(dest, 'add', '-A')
    git(dest, 'commit', '-q', '--allow-empty', '-m', 'base')
    git(dest, 'checkout', '-q', '-b', 'main')
    for f in case['files']:
        if f.get('old_path') and f['old_path'] != f['path']:
            git(dest, 'rm', '-q', '--', f['old_path'])
        if f.get('head') is None:
            if f.get('base') is not None:
                git(dest, 'rm', '-q', '--', f['path'])
            continue
        write(dest, f['path'], f['head'])
    git(dest, 'add', '-A')
    git(dest, 'commit', '-q', '--allow-empty', '-m', 'change')
    return dest


if __name__ == '__main__':
    materialize(load_case(sys.argv[1], sys.argv[2]), sys.argv[3])
