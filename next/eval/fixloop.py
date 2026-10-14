"""The agent fix loop's deterministic half: workspaces, gate runs and final checks (harness v2).

The coding agents and the blind judge are fresh subagents run by workflows; everything they are
measured by happens here, the same way for every candidate.

  prepare  CANDIDATE [--ids a,b] [--controls]   make each workspace and run the gate once (iteration 0)
  gate     CANDIDATE FIX_ID ITER                record the agent's edits as a commit and run the gate
  check    CANDIDATE FIX_ID                     tests, smell removed, size, gaming, feature kept
  status   CANDIDATE                            one line per item: where it stands

A workspace is a fresh git repository whose whole history is two commits: the base (the tree of the
upstream commit's parent) and "The change" (the upstream commit's change with the smell injected).
The upstream commit itself, its message and the injection are nowhere in it, so an agent cannot
recover the clean version from history. Workspaces live under opaque names ($WORK/fx/<hash>/ws);
the records the harness keeps (meta, gate reports, checks) live apart, under $WORK/rec/.

With --controls, each item also gets a control workspace holding the clean upstream change with no
smell: a gate that fails it at iteration 0 raised a false alarm.

Every gate run writes its JSON verdict and the text report from the same run (candidates on the
shared core take --text-out; the qualm baseline is run again for text with its cache on, and the
two exit codes must agree). A run that exits 2 is retried once, for every candidate.
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SP = '/tmp/claude-0/-home-user-qualm/2ede26c6-6287-5d82-9ba2-8868fcdef917/scratchpad'
WORK = os.environ.get('BENCH_WORK', os.path.join(SP, 'bench3'))
SALT = os.environ.get('BENCH_SALT', 'harness-v2')
ITEMS = os.environ.get('FIX_ITEMS', os.path.join(ROOT, 'next', 'data', 'fixloop_items.json'))
INJECTIONS = os.environ.get('FIX_INJECTIONS', os.path.join(SP, 'fixloop'))
ENV = dict(os.environ, GIT_AUTHOR_NAME='dev', GIT_AUTHOR_EMAIL='dev@example.invalid', GIT_COMMITTER_NAME='dev',
           GIT_COMMITTER_EMAIL='dev@example.invalid', GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z',
           GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1')
TEXT_OUT = ('b-offline', 'c-llm', 'd-cascade', 'e-spot', 'n0-size')  # candidates on the shared core

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


def items():
    return json.load(open(ITEMS))['items']


def item_by_id(fix_id):
    for it in items():
        if it['fix_id'] == fix_id:
            return it
    sys.exit('no fix item ' + fix_id)


def key(cand, fix_id, control=False):
    return '%s/%s%s' % (cand, fix_id, '/control' if control else '')


def ws_of(cand, fix_id, control=False):
    h = hashlib.sha256((SALT + '|' + key(cand, fix_id, control)).encode()).hexdigest()[:16]
    return os.path.join(WORK, 'fx', h, 'ws')


def rec_dir(cand, fix_id, control=False):
    return os.path.join(WORK, 'rec', cand, fix_id + ('.control' if control else ''))


def subst(cmd, ws):
    return cmd.replace('$SP', SP).replace('$REPO', ws)


def checkout_tree(repo, rev, ws):
    """Write the files of `rev` into ws, exactly as committed (export-ignore and .gitignore do not apply)."""
    fd, idx = tempfile.mkstemp(prefix='fxidx')
    os.close(fd)
    os.unlink(idx)
    env = dict(ENV, GIT_INDEX_FILE=idx)
    try:
        for args in (['read-tree', rev], ['checkout-index', '-a', '-f']):
            code, _, err = sh(['git', '--git-dir', os.path.join(repo, '.git'), '--work-tree', ws] + args, env=env)
            if code:
                raise RuntimeError('%s: %s' % (' '.join(args), err[-300:]))
    finally:
        if os.path.exists(idx):
            os.unlink(idx)


def run_once(cand, ws, base, prefix, llm_dir):
    cand_dir = os.path.join(ROOT, 'next', 'candidates', cand)
    extra = ['--llm-dir', llm_dir] if llm_dir else []
    text_path = prefix + '.txt'
    t0 = time.time()
    if cand in TEXT_OUT:
        code, out, err = sh([os.path.join(cand_dir, 'run'), '--repo', ws, '--base', base, '--format', 'json', '--budget', '0.05',
                             '--text-out', text_path] + extra, cwd=ws)
        secs = time.time() - t0
        tcode = code
        if not os.path.exists(text_path) or not open(text_path).read().strip():
            with open(text_path, 'w') as fh:
                fh.write(err)
    else:
        code, out, err = sh([os.path.join(cand_dir, 'run'), '--repo', ws, '--base', base, '--format', 'json', '--budget', '0.05'] + extra, cwd=ws)
        secs = time.time() - t0
        tcode, tout, terr = None, '', ''
        for _ in range(3):  # the text run reads qualm's cache, so it reaches the same verdict
            tcode, tout, terr = sh([os.path.join(cand_dir, 'run'), '--repo', ws, '--base', base, '--format', 'text', '--budget', '0.05'] + extra, cwd=ws)
            if tcode == code:
                break
        with open(text_path, 'w') as fh:
            fh.write(tout if tout.strip() else terr)
    try:
        rep = json.loads(out)
    except ValueError:
        rep = None
    return {'exit': code, 'text_exit': tcode, 'seconds': round(secs, 2), 'report': rep, 'stderr_tail': err[-600:]}


def run_gate(cand, ws, base, prefix, llm_dir=None):
    rec = run_once(cand, ws, base, prefix, llm_dir)
    if rec['exit'] == 2:  # one retry, the same for every candidate
        first = rec
        rec = run_once(cand, ws, base, prefix, llm_dir)
        rec['retried'] = {'first_exit': first['exit'], 'first_stderr_tail': first['stderr_tail']}
    json.dump(rec, open(prefix + '.json', 'w'), indent=1)
    return rec


def build_workspace(it, ws, control):
    repo = os.path.join(SP, 'fixrepos', it['repo_key'])
    parent = sh(['git', '-C', repo, 'rev-parse', it['sha'] + '^'])[1].strip()
    if os.path.exists(os.path.dirname(ws)):
        shutil.rmtree(os.path.dirname(ws))
    os.makedirs(ws)
    checkout_tree(repo, parent, ws)
    git(ws, 'init', '-q', '-b', 'main')
    git(ws, 'add', '-A', '-f')
    git(ws, 'commit', '-q', '-m', 'Base')
    base = git(ws, 'rev-parse', 'HEAD')[1].strip()
    _, names, _ = sh(['git', '-C', repo, 'diff', '--name-status', '--no-renames', parent, it['sha']])
    checkout_tree(repo, it['sha'], ws)
    for row in names.splitlines():
        st, _, path = row.partition('\t')
        if st == 'D' and os.path.exists(os.path.join(ws, path)):
            os.unlink(os.path.join(ws, path))
    if not control:
        injected = open(os.path.join(INJECTIONS, it['fix_id'], 'head.txt')).read()
        with open(os.path.join(ws, it['file']), 'w') as fh:
            fh.write(injected)
    git(ws, 'add', '-A', '-f')
    git(ws, 'commit', '-q', '-m', 'The change')
    pr_head = git(ws, 'rev-parse', 'HEAD')[1].strip()
    return base, pr_head


def prepare(cand, ids, controls=False, llm_root=None):
    for it in items():
        if ids and it['fix_id'] not in ids:
            continue
        for control in ((False, True) if controls else (False,)):
            d = rec_dir(cand, it['fix_id'], control)
            if os.path.exists(os.path.join(d, 'iter0.json')):
                continue
            os.makedirs(d, exist_ok=True)
            ws = ws_of(cand, it['fix_id'], control)
            try:
                base, pr_head = build_workspace(it, ws, control)
            except RuntimeError as e:
                print('workspace failed', it['fix_id'], e)
                continue
            scode, _, serr = sh(subst(it['setup'], ws), cwd=ws, timeout=900)  # dependencies live outside the tree (pnpm store, bundle path, venv)
            if scode:
                print('setup failed', it['fix_id'], serr[-300:])
            json.dump({'base': base, 'pr_head': pr_head, 'ws': ws, 'control': control, 'item': it}, open(os.path.join(d, 'meta.json'), 'w'), indent=1)
            llm_dir = os.path.join(llm_root, hashlib.sha256(ws.encode()).hexdigest()[:16]) if llm_root else None
            rec = run_gate(cand, ws, base, os.path.join(d, 'iter0'), llm_dir)
            print(key(cand, it['fix_id'], control), 'gate exit', rec['exit'], flush=True)


def gate(cand, fix_id, k, llm_root=None):
    d = rec_dir(cand, fix_id)
    meta = json.load(open(os.path.join(d, 'meta.json')))
    ws = meta['ws']
    git(ws, 'add', '-A')
    git(ws, 'commit', '-q', '--allow-empty', '-m', 'Rework %d' % k)
    llm_dir = os.path.join(llm_root, hashlib.sha256(ws.encode()).hexdigest()[:16]) if llm_root else None
    rec = run_gate(cand, ws, meta['base'], os.path.join(d, 'iter%d' % k), llm_dir)
    print(fix_id, 'iteration', k, 'gate exit', rec['exit'], flush=True)
    return rec


def added_code(ws, base, rev, path):
    _, out, _ = git(ws, 'diff', '--no-color', '-U0', base, rev, '--', path)
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
    d = rec_dir(cand, fix_id)
    meta = json.load(open(os.path.join(d, 'meta.json')))
    it = meta['item']
    ws = meta['ws']
    repo = os.path.join(SP, 'fixrepos', it['repo_key'])
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
    code, so, se = sh(subst(it['test'], ws), cwd=ws, timeout=900)
    if code != 0:  # one rerun, the same for every candidate: some suites (expr's timing tests) are flaky
        code, so, se = sh(subst(it['test'], ws), cwd=ws, timeout=900)
        out['tests_rerun'] = True
    out['tests_pass'] = code == 0
    out['tests_tail'] = (so + se)[-400:]
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
    clean = sh(['git', '-C', repo, 'show', '%s:%s' % (it['sha'], it['file'])])[1]
    now = open(target_now).read() if os.path.exists(target_now) else ''
    lost = sorted(definitions(clean, it['language']) - definitions(now, it['language']))
    out['definitions_lost'] = lost
    if lost:
        gaming.append('removed definitions the original change had: %s' % ', '.join(lost[:5]))
    out['gaming'] = gaming
    out['gamed'] = bool(gaming)
    # deterministic simplicity: the injected lines are gone and the change is no larger than the injected one.
    # A line counts as gone only when its code is gone, not when it moved inside another statement.
    inj_patch = open(os.path.join(INJECTIONS, fix_id, 'inject.patch')).read()
    inj_lines = [l[1:].strip() for l in inj_patch.splitlines() if l.startswith('+') and not l.startswith('+++') and l[1:].strip()
                 and len(l[1:].strip()) > 3 and not re.fullmatch(r'[\]\)\}\s;,]*', l[1:].strip())]
    clean_set = set(l.strip() for l in clean.splitlines())
    inj_lines = [l for l in inj_lines if l not in clean_set]  # lines the clean version also has don't count
    now_text = re.sub(r'\s+', ' ', now)
    now_rows = [set(re.findall(r'[A-Za-z_]\w*', l)) for l in now.splitlines() if l.strip() and l.strip() not in clean_set]

    def remains(line):
        if re.sub(r'\s+', ' ', line) in now_text:
            return True
        toks = set(re.findall(r'[A-Za-z_]\w*', line))
        # folded into another statement: most of its names still stand together on one new line
        return len(toks) >= 3 and any(len(toks & r) >= 0.8 * len(toks) for r in now_rows)
    remaining = [l for l in inj_lines if remains(l)]
    out['injected_lines'] = len(inj_lines)
    out['injected_lines_remaining'] = len(remaining)
    out['smell_removed'] = len(inj_lines) > 0 and len(remaining) <= 0.2 * len(inj_lines)
    a_inj = len(added_code(ws, meta['base'], meta['pr_head'], it['file']))
    a_fin = len(added_code(ws, meta['base'], final, it['file']))
    out.update(added_lines_injected=a_inj, added_lines_final=a_fin)
    out['not_larger'] = a_fin <= a_inj
    out['det_simpler'] = out['smell_removed'] and out['not_larger']
    _, diff_inj, _ = git(ws, 'diff', '--no-color', meta['base'], meta['pr_head'], '--', it['file'])
    _, diff_fin, _ = git(ws, 'diff', '--no-color', meta['base'], final, '--', it['file'])
    open(os.path.join(d, 'diff_injected.patch'), 'w').write(diff_inj)
    open(os.path.join(d, 'diff_final.patch'), 'w').write(diff_fin)
    # the clean change, for validating the blind judge on a pair whose answer is known
    clean_ws_diff = sh(['git', '-C', repo, 'diff', '--no-color', it['sha'] + '^', it['sha'], '--', it['file']])[1]
    open(os.path.join(d, 'diff_clean.patch'), 'w').write(clean_ws_diff)
    json.dump(out, open(os.path.join(d, 'check.json'), 'w'), indent=1)
    print(json.dumps({k: out[k] for k in ('fix_id', 'gate_passes', 'tests_pass', 'det_simpler', 'gamed')}))
    return out


def status(cand):
    base = os.path.join(WORK, 'rec', cand)
    for fid in sorted(os.listdir(base)):
        d = os.path.join(base, fid)
        iters = sorted(f for f in os.listdir(d) if re.match(r'iter\d+\.json$', f))
        last = json.load(open(os.path.join(d, iters[-1]))) if iters else {}
        print(fid, len(iters) - 1, 'iterations, last gate exit', last.get('exit'), 'checked' if os.path.exists(os.path.join(d, 'check.json')) else '')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('prepare'); p.add_argument('candidate'); p.add_argument('--ids', default=''); p.add_argument('--controls', action='store_true')
    p.add_argument('--llm-root')
    g = sub.add_parser('gate'); g.add_argument('candidate'); g.add_argument('fix_id'); g.add_argument('iter', type=int); g.add_argument('--llm-root')
    c = sub.add_parser('check'); c.add_argument('candidate'); c.add_argument('fix_id')
    s = sub.add_parser('status'); s.add_argument('candidate')
    a = ap.parse_args()
    if a.cmd == 'prepare':
        prepare(a.candidate, set(filter(None, a.ids.split(','))), a.controls, a.llm_root)
    elif a.cmd == 'gate':
        gate(a.candidate, a.fix_id, a.iter, a.llm_root)
    elif a.cmd == 'check':
        check(a.candidate, a.fix_id)
    else:
        status(a.candidate)


if __name__ == '__main__':
    main()
