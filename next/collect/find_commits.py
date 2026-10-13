"""List commits in a local clone that make a good base for controlled injection.

A good commit adds 15-250 lines to one to three source files qualm would judge, touches a test
file in the same commit (so the new behaviour is exercised), and isn't a merge.
Usage: python3 -I find_commits.py REPO [--max 12]
"""
import argparse, json, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import judged_file, qualm_skips, looks_like_test  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('repo')
    ap.add_argument('--max', type=int, default=12)
    ap.add_argument('--since', default='2019-01-01')
    a = ap.parse_args()
    log = subprocess.run(['git', '-C', a.repo, 'log', '--no-merges', '--first-parent', '--since', a.since, '--format=%H', '--numstat'],
                         capture_output=True, text=True).stdout
    out, sha, files = [], None, []

    def flush():
        if not sha:
            return
        src = [(p, ad) for p, ad, de in files if judged_file(p)]
        tests = [p for p, ad, de in files if looks_like_test(p) or qualm_skips(p) == 'test']
        added = sum(ad for _, ad in src)
        if 1 <= len(src) <= 3 and 15 <= added <= 250 and tests and max(ad for _, ad in src) >= 12:
            out.append({'sha': sha, 'files': [p for p, _ in src], 'added': added, 'tests': tests[:5]})

    for line in log.splitlines():
        if len(line) == 40 and ' ' not in line and '\t' not in line:
            flush()
            sha, files = line, []
        elif line.strip():
            parts = line.split('\t')
            if len(parts) == 3 and parts[0].isdigit():
                files.append((parts[2], int(parts[0]), int(parts[1])))
    flush()
    for c in out[:a.max]:
        c['subject'] = subprocess.run(['git', '-C', a.repo, 'log', '-1', '--format=%s', c['sha']], capture_output=True, text=True).stdout.strip()[:100]
        print(json.dumps(c))


if __name__ == '__main__':
    main()
