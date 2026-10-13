"""Turn the LLM-written injections into benchmark cases and the fix-loop item list.

    python3 -I fixloop_cases.py SELECTED.json INJECT_RESULTS.json OUT_CASES_DIR OUT_ITEMS.json

A case holds every judged file the commit changes (base = the commit's parent, head = the commit),
with the target file's head replaced by the injected version. Only the target file is scored:
label 1, label_source "injection_llm", its smell, and its location (the span of lines the
injection added or changed, read from the patch). Writes fixloop_dev.jsonl and
fixloop_heldout.jsonl into OUT_CASES_DIR, and the item list the fix loop runs from.
"""
import json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import judged_file, language  # noqa: E402

SP = '/tmp/claude-0/-home-user-qualm/2ede26c6-6287-5d82-9ba2-8868fcdef917/scratchpad'
HUNK = re.compile(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@')


def git_show(repo, rev, path):
    p = subprocess.run(['git', '-C', repo, 'show', '%s:%s' % (rev, path)], capture_output=True)
    return p.stdout.decode('utf-8') if p.returncode == 0 else None


def patch_span(patch):
    lines, n = [], None
    for l in patch.splitlines():
        m = HUNK.match(l)
        if m:
            n = int(m.group(1))
            continue
        if n is None or l.startswith('---') or l.startswith('+++'):
            continue
        if l.startswith('+'):
            lines.append(n)
            n += 1
        elif l.startswith('-'):
            continue
        else:
            n += 1
    return [min(lines), max(lines)] if lines else None


def main():
    selected, results, out_dir, out_items = sys.argv[1:5]
    sel = {it['fix_id']: it for it in json.load(open(selected))['items']}
    res = {r['fix_id']: r for r in json.load(open(results))}
    os.makedirs(out_dir, exist_ok=True)
    items_out = []
    fh = {s: open(os.path.join(out_dir, 'fixloop_%s.jsonl' % s), 'w') for s in ('dev', 'heldout')}
    for fid, it in sorted(sel.items()):
        r = res.get(fid)
        d = os.path.join(SP, 'fixloop', fid)
        if not r or not r.get('ok') or not r.get('tests_pass_after') or not os.path.exists(os.path.join(d, 'head.txt')):
            print('skip', fid, (r or {}).get('reason_if_not_ok', 'no result'))
            continue
        patch = open(os.path.join(d, 'inject.patch')).read()
        if not patch.strip():
            print('skip', fid, 'empty patch')
            continue
        repo = os.path.join(SP, 'fixrepos', it['repo_key'])
        names = subprocess.run(['git', '-C', repo, 'diff', '--name-status', '--no-renames', it['sha'] + '^', it['sha']],
                               capture_output=True, text=True).stdout
        files = []
        for row in names.splitlines():
            st, p = row.split('\t', 1)
            if st not in ('A', 'M') or not judged_file(p):
                continue
            head = open(os.path.join(d, 'head.txt')).read() if p == it['file'] else git_show(repo, it['sha'], p)
            base = None if st == 'A' else git_show(repo, it['sha'] + '^', p)
            files.append({'path': p, 'language': language(p), 'base': base, 'head': head})
        if not any(f['path'] == it['file'] for f in files):
            print('skip', fid, 'target not judged')
            continue
        loc = patch_span(patch) or r.get('location')
        case = {'case_id': fid, 'source': 'injection_llm', 'repo': it['repo'], 'url': 'https://github.com/%s/commit/%s' % (it['repo'], it['sha']),
                'subject': '', 'split': it['split'], 'files': files,
                'items': [{'path': it['file'], 'label': 1, 'label_source': 'injection_llm', 'smell': it['smell'], 'location': loc,
                           'injector_note': r.get('description', '')[:300]}]}
        fh[it['split']].write(json.dumps(case) + '\n')
        items_out.append(dict(it, dir=d, location=loc, description=r.get('description', '')))
    for f in fh.values():
        f.close()
    json.dump({'items': items_out}, open(out_items, 'w'), indent=1)
    print('fix-loop items:', {s: sum(1 for i in items_out if i['split'] == s) for s in ('dev', 'heldout')})


if __name__ == '__main__':
    main()
