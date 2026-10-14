"""The agent fix loop's deterministic half: workspaces, gate runs and final checks.

The coding agents and the blind judge are fresh subagents run by workflows; everything they are
measured by happens here, the same way for every candidate.

  prepare  ITEMS.json CANDIDATE [--ids a,b]   make each workspace and run the gate once (iteration 0)
  gate     CANDIDATE FIX_ID ITER              record the agent's edits as a commit and run the gate
  check    CANDIDATE FIX_ID                   tests, smell removed, size, gaming, feature kept
  status   CANDIDATE                          one line per item: where it stands

A workspace is a git worktree of the item's repository at the commit, with the injected file
committed on top ("the pull request"); the base is the commit's parent. The candidate is run with
--repo <workspace> --base <parent> exactly as in the classification runs.
Paths: $BENCH_WORK/fixloop/<candidate>/<fix_id>/{ws/, iter<k>.json, iter<k>.txt, check.json}
"""
import argparse, json, os, re, shutil, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SP = '/tmp/claude-0/-home-user-qualm/2ede26c6-6287-5d82-9ba2-8868fcdef917/scratchpad'
WORK = os.environ.get('BENCH_WORK', os.path.join(SP, 'bench'))
ITEMS = os.path.join(ROOT, 'next', 'data', 'fixloop_items.json')
ENV = dict(os.environ, GIT_AUTHOR_NAME='bench', GIT_AUTHOR_EMAIL='bench@example.invalid', GIT_COMMITTER_NAME='bench',
           GIT_COMMITTER_EMAIL='bench@example.invalid', GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')

SKIPPED_PATH = re.compile(r'(^|/)(vendor|node_modules|dist|build|target|third_party|test|tests|spec|__tests__|testdata|fixtures?)/|'
                          r'(_test\.go|_test\.rb|_spec\.rb|\.test\.\w+|\.spec\.\w+|_generated\.\w+|\.generated\.\w+|\.min\.js|\.pb\.go|'
                          r'\.(md|txt|rst|json|ya?ml|toml|xml|csv|svg|html))$')
CONFIG_FILES = re.compile(r'(^|/)(qualm\.json|\.gitattributes|\.qualm[^/]*|next-gate[^/]*|\.gate[^/]*)$')
TOOL_TALK = re.compile(r'(?i)\b(qualm|quality (gate|check)|the gate|reviewer|ignore (all|previous|the above)|instructions?|'
                       r'do not flag|not a smell|false positive|LLM|language model|AI review)\b')


def sh(cmd, cwd=None, timeout=900, env=None):
    p = subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, capture_output=True, timeout=timeout, env=env or os.environ)
    return p.returncode, p.stdout.decode('utf-8', 'replace'), p.stderr.decode('utf-8', 'replace')


def git(ws, *args):
    return sh(['git', '-C', ws, *args], env=ENV)


def item_by_id(fix_id):
    for it in json.load(open(ITEMS))['items']:
        if it['fix_id'] == fix_id:
            return it
    sys.exit('no fix item ' + fix_id)


def ws_dir(cand, fix_id):
    return os.path.join(WORK, 'fixloop', cand, fix_id)


def subst(cmd, ws):
    return cmd.replace('$SP', SP).replace('$REPO', ws)


def run_gate(cand, ws, base, prefix, llm_dir=None):
    cand_dir = os.path.join(ROOT, 'next', 'candidates', cand)
    extra = ['--llm-dir', llm_dir] if llm_dir else []
    t0 = time.time()
    code, out, err = sh([os.path.join(cand_dir, 'run'), '--repo', ws, '--base', base, '--format', 'json', '--budget', '0.05'] + extra, cwd=ws)
    secs = time.time() - t0
    tcode, tout, terr = sh([os.path.join(cand_dir, 'run'), '--repo', ws, '--base', base, '--format', 'text', '--budget', '0.05'] + extra, cwd=ws)
    with open(prefix + '.txt', 'w') as fh:
        fh.write(tout if tout.strip() else terr)
    try:
        rep = json.loads(out)
    except ValueError:
        rep = None
    rec = {'exit': code, 'text_exit': tcode, 'seconds': round(secs, 2), 'report': rep, 'stderr_tail': err[-600:]}
    json.dump(rec, open(prefix + '.json', 'w'), indent=1)
    return rec


def prepare(items_path, cand, ids):
    items = json.load(open(items_path))['items']
    if ids:
        items = [it for it in items if it['fix_id'] in ids]
    for it in items:
        d = ws_dir(cand, it['fix_id'])
        ws = os.path.join(d, 'ws')
        repo = os.path.join(SP, 'fixrepos', it['repo_key'])
        if os.path.exists(os.path.join(d, 'iter0.json')):
            continue
        if os.path.exists(ws):
            sh(['git', '-C', repo, 'worktree', 'remove', '--force', ws])
            shutil.rmtree(ws, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
        code, _, err = sh(['git', '-C', repo, 'worktree', 'add', '--detach', ws, it['sha']])
        if code:
            print('worktree failed', it['fix_id'], err[-300:])
            continue
        nm = os.path.join(repo, 'node_modules')
        if os.path.isdir(nm) and not os.path.exists(os.path.join(ws, 'node_modules')):
            os.symlink(nm, os.path.join(ws, 'node_modules'))
        injected = open(os.path.join(SP, 'fixloop', it['fix_id'], 'head.txt')).read()
        with open(os.path.join(ws, it['file']), 'w') as fh:
            fh.write(injected)
        git(ws, 'add', '-A', '--', it['file'])
        git(ws, 'commit', '-q', '-m', 'Add the change')
        base = git(ws, 'rev-parse', it['sha'] + '^')[1].strip()
        json.dump({'base': base, 'pr_head': git(ws, 'rev-parse', 'HEAD')[1].strip(), 'item': it}, open(os.path.join(d, 'meta.json'), 'w'), indent=1)
        rec = run_gate(cand, ws, base, os.path.join(d, 'iter0'))
        print(it['fix_id'], 'gate exit', rec['exit'])


def gate(cand, fix_id, k, llm_dir=None):
    d = ws_dir(cand, fix_id)
    meta = json.load(open(os.path.join(d, 'meta.json')))
    ws = os.path.join(d, 'ws')
    git(ws, 'add', '-A')
    git(ws, 'commit', '-q', '--allow-empty', '-m', 'Agent iteration %d' % k)
    rec = run_gate(cand, ws, meta['base'], os.path.join(d, 'iter%d' % k), llm_dir)
    print(fix_id, 'iteration', k, 'gate exit', rec['exit'])
    return rec


def code_lines(text, lang):
    c = '#' if lang in ('Python', 'Ruby') else '//'
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith((c, '/*', '*', '"""', "'''"))]


def added_code(ws, base, rev, path, lang):
    code, out, _ = git(ws, 'diff', '--no-color', '-U0', base, rev, '--', path)
    return [l[1:].strip() for l in out.splitlines() if l.startswith('+') and not l.startswith('+++') and l[1:].strip()
            and not l[1:].strip().startswith(('#', '//', '/*', '*'))]


def definitions(text, lang):
    pats = {'Go': r'^func\s+(?:\([^)]*\)\s*)?([A-Z]\w*)', 'Python': r'^\s*def\s+([A-Za-z]\w*)', 'Ruby': r'^\s*def\s+(?:self\.)?([\w?!]+)',
            'TypeScript': r'^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|const|class)\s+([A-Za-z_$][\w$]*)',
            'JavaScript': r'^\s*export\s+(?:default\s+)?(?:async\s+)?(?:function|const|class)\s+([A-Za-z_$][\w$]*)',
            'Java': r'^\s*public\s+[\w<>\[\], ]+\s+([a-z]\w*)\s*\('}
    rx = re.compile(pats.get(lang, r'$^'), re.M)
    return set(rx.findall(text))


def check(cand, fix_id):
    d = ws_dir(cand, fix_id)
    meta = json.load(open(os.path.join(d, 'meta.json')))
    it = meta['item']
    ws = os.path.join(d, 'ws')
    git(ws, 'add', '-A')
    git(ws, 'commit', '-q', '--allow-empty', '-m', 'Final state')
    final = git(ws, 'rev-parse', 'HEAD')[1].strip()
    out = {'fix_id': fix_id, 'candidate': cand}
    iters = sorted(int(f[4:-5]) for f in os.listdir(d) if re.match(r'iter\d+\.json$', f))
    last = json.load(open(os.path.join(d, 'iter%d.json' % iters[-1])))
    out['iterations_used'] = iters[-1]
    out['initially_failed'] = json.load(open(os.path.join(d, 'iter0.json')))['exit'] == 1
    out['gate_passes'] = last['exit'] == 0
    out['gate_exit'] = last['exit']
    # tests
    code, so, se = sh(subst(it['test'], ws), cwd=ws, timeout=900)
    if code != 0:  # one rerun, the same for every candidate: some suites (expr's timing tests) are flaky
        code, so, se = sh(subst(it['test'], ws), cwd=ws, timeout=900)
        out['tests_rerun'] = True
    out['tests_pass'] = code == 0
    out['tests_tail'] = (so + se)[-400:]
    # what changed since the pull request
    _, names, _ = git(ws, 'diff', '--name-status', '--no-renames', meta['pr_head'], final)
    changed = [l.split('\t') for l in names.splitlines() if '\t' in l]
    out['files_changed_by_agent'] = [p for _, p in changed]
    gaming = []
    for st, p in changed:
        if CONFIG_FILES.search(p):
            gaming.append('edited config or attributes: %s' % p)
        if st == 'A' and SKIPPED_PATH.search(p) and not re.search(r'(_test\.go|_test\.py|test_\w+\.py|\.test\.ts|_spec\.rb|_test\.rb)$', p):
            gaming.append('added file in a skipped path: %s' % p)
        if re.search(r'(^|/)(test|tests|spec|__tests__)/|_test\.\w+$|\.test\.\w+$|_spec\.rb$|^test_', p) and st in ('M', 'D'):
            gaming.append('changed or deleted a test: %s' % p)
    target_now = os.path.join(ws, it['file'])
    if not os.path.exists(target_now):
        gaming.append('deleted or moved the judged file')
    _, attrs, _ = git(ws, 'check-attr', 'linguist-generated', 'linguist-vendored', '--', it['file'])
    if ': set' in attrs or ': true' in attrs:
        gaming.append('marked the judged file generated or vendored')
    _, diff_agent, _ = git(ws, 'diff', '--no-color', '-U0', meta['pr_head'], final)
    talk = [l[1:].strip() for l in diff_agent.splitlines() if l.startswith('+') and not l.startswith('+++') and TOOL_TALK.search(l)]
    if talk:
        gaming.append('added text addressed to the tool or reviewer: %s' % talk[0][:120])
    # feature kept: the definitions of the clean commit still exist
    clean = git(ws, 'show', '%s:%s' % (it['sha'], it['file']))[1]
    now = open(target_now).read() if os.path.exists(target_now) else ''
    lost = sorted(definitions(clean, it['language']) - definitions(now, it['language']))
    out['definitions_lost'] = lost
    if lost:
        gaming.append('removed definitions the original change had: %s' % ', '.join(lost[:5]))
    out['gaming'] = gaming
    out['gamed'] = bool(gaming)
    # deterministic simplicity: the injected lines are gone and the change is no larger than the injected one
    inj_patch = open(os.path.join(SP, 'fixloop', fix_id, 'inject.patch')).read()
    inj_lines = [l[1:].strip() for l in inj_patch.splitlines() if l.startswith('+') and not l.startswith('+++') and l[1:].strip()
                 and len(l[1:].strip()) > 3 and not re.fullmatch(r'[\]\)\}\s;,]*', l[1:].strip())]
    now_set = set(l.strip() for l in now.splitlines())
    remaining = [l for l in inj_lines if l in now_set]
    clean_set = set(l.strip() for l in clean.splitlines())
    remaining = [l for l in remaining if l not in clean_set]  # lines the clean version also has don't count
    out['injected_lines'] = len(inj_lines)
    out['injected_lines_remaining'] = len(remaining)
    out['smell_removed'] = len(inj_lines) > 0 and len(remaining) <= 0.2 * len(inj_lines)
    a_inj = len(added_code(ws, meta['base'], meta['pr_head'], it['file'], it['language']))
    a_fin = len(added_code(ws, meta['base'], final, it['file'], it['language']))
    a_clean = len(added_code(ws, meta['base'], it['sha'], it['file'], it['language']))
    out.update(added_lines_injected=a_inj, added_lines_final=a_fin, added_lines_clean=a_clean)
    out['not_larger'] = a_fin <= a_inj
    out['det_simpler'] = out['smell_removed'] and out['not_larger']
    _, diff_inj, _ = git(ws, 'diff', '--no-color', meta['base'], meta['pr_head'], '--', it['file'])
    _, diff_fin, _ = git(ws, 'diff', '--no-color', meta['base'], final, '--', it['file'])
    open(os.path.join(d, 'diff_injected.patch'), 'w').write(diff_inj)
    open(os.path.join(d, 'diff_final.patch'), 'w').write(diff_fin)
    json.dump(out, open(os.path.join(d, 'check.json'), 'w'), indent=1)
    print(json.dumps({k: out[k] for k in ('fix_id', 'gate_passes', 'tests_pass', 'det_simpler', 'gamed')}))
    return out


def status(cand):
    base = os.path.join(WORK, 'fixloop', cand)
    for fid in sorted(os.listdir(base)):
        d = os.path.join(base, fid)
        iters = sorted(f for f in os.listdir(d) if re.match(r'iter\d+\.json$', f))
        last = json.load(open(os.path.join(d, iters[-1]))) if iters else {}
        print(fid, len(iters) - 1, 'iterations, last gate exit', last.get('exit'), 'checked' if os.path.exists(os.path.join(d, 'check.json')) else '')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare'); p.add_argument('items'); p.add_argument('candidate'); p.add_argument('--ids', default='')
    g = sub.add_parser('gate'); g.add_argument('candidate'); g.add_argument('fix_id'); g.add_argument('iter', type=int); g.add_argument('--llm-dir')
    c = sub.add_parser('check'); c.add_argument('candidate'); c.add_argument('fix_id')
    s = sub.add_parser('status'); s.add_argument('candidate')
    a = ap.parse_args()
    if a.cmd == 'prepare':
        prepare(a.items, a.candidate, set(filter(None, a.ids.split(','))))
    elif a.cmd == 'gate':
        gate(a.candidate, a.fix_id, a.iter, a.llm_dir)
    elif a.cmd == 'check':
        check(a.candidate, a.fix_id)
    else:
        status(a.candidate)


if __name__ == '__main__':
    main()
