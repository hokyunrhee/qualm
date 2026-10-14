"""The freeze record: for each candidate, the commit it is run from and the SHA-256 of every file
under its directory and the shared core at that commit, read from git (not the working tree).

    python3 -I freeze.py --out results/freeze.json NAME=COMMIT [NAME=COMMIT ...]
"""
import argparse, hashlib, json, subprocess


def files_at(commit, path):
    out = subprocess.run(['git', 'ls-tree', '-r', '--name-only', commit, '--', path], capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p and '/tests/' not in p and not p.endswith(('.md', '.pyc'))]


def sha(commit, path):
    data = subprocess.run(['git', 'show', '%s:%s' % (commit, path)], capture_output=True, check=True).stdout
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('pairs', nargs='+')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    rec = {}
    for pair in a.pairs:
        name, commit = pair.split('=', 1)
        full = subprocess.run(['git', 'rev-parse', commit], capture_output=True, text=True, check=True).stdout.strip()
        paths = files_at(full, 'next/candidates/' + name) + files_at(full, 'next/candidates/_core/gatecore')
        rec[name] = {'commit': full, 'files': {p: sha(full, p) for p in sorted(paths)}}
        print(name, full[:10], len(paths), 'files')
    json.dump(rec, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
