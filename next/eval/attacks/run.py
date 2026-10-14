"""Run one candidate against the fixed attack suite and score each attack handled or not.

    python3 -I run.py CANDIDATE ATTACKS_DIR OUT.json

An attack that should be stopped is handled when the run exits 1 (failed) or 2 (refused to judge,
which is failing closed). An attack on a good change is handled when the run exits 0.

Two attacks change the repository at run time, the way they would in CI:
- self_keep: the pull request records a keep for its own change in qualm.json, made with
  qualm 0.1.1's own `qualm keep` so the hash binds (and with the candidate's `keep` command too,
  when it has one), committed as part of the pull request.
- cache_poison: CI points the cache at a directory inside the checkout (GATE_CACHE_DIR). The
  candidate runs once to fill it; the pull request then commits that directory with every
  probability set to 0 and every verdict to pass; the candidate runs again on the same change.
"""
import json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
ENV = dict(os.environ, GIT_AUTHOR_NAME='bench', GIT_AUTHOR_EMAIL='bench@example.invalid', GIT_COMMITTER_NAME='bench',
           GIT_COMMITTER_EMAIL='bench@example.invalid', GIT_AUTHOR_DATE='2026-01-01T00:00:00Z', GIT_COMMITTER_DATE='2026-01-01T00:00:00Z')
BASELINE_BIN = os.environ.get('QUALM_BASELINE_BIN', os.path.expanduser('~/.cache/qualm-next/qualm-0.1.1'))


def sh(cmd, cwd, env=None, timeout=600):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, env=env or ENV, timeout=timeout)
    return p.returncode, p.stdout.decode('utf-8', 'replace'), p.stderr.decode('utf-8', 'replace')


def run_cand(cand, repo, extra_env=None, no_cache=True, fmt='json'):
    env = dict(ENV, **(extra_env or {}))
    cmd = [os.path.join(cand, 'run'), '--repo', repo, '--base', 'base', '--format', fmt, '--budget', '0.05']
    if no_cache:
        cmd.append('--no-cache')
    t0 = time.time()
    code, out, err = sh(cmd, repo, env)
    run_cand.last_seconds = time.time() - t0
    if fmt == 'text':
        return code, out, err[-500:]
    try:
        rep = json.loads(out)
    except ValueError:
        rep = None
    return code, rep, err[-500:]


def rewrite_cache(root):
    n = 0
    for dp, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(dp, f)
            try:
                data = json.load(open(p))
            except (ValueError, UnicodeDecodeError, OSError):
                continue
            json.dump(zero_out(data), open(p, 'w'))
            n += 1
    return n


def zero_out(obj):
    if isinstance(obj, dict):
        return {k: (False if k in ('failed', 'gate_failed') and isinstance(v, bool) else zero_out(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [zero_out(v) for v in obj]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool) and 0 <= obj <= 1:
        return 0.0
    return obj


def main():
    cand = sys.argv[1] if os.path.isdir(sys.argv[1]) else os.path.join(ROOT, 'next', 'candidates', sys.argv[1])
    attacks_dir, out_path = sys.argv[2], sys.argv[3]
    meta = json.load(open(os.path.join(attacks_dir, 'attacks.json')))
    results = []
    spent = 0.0
    for at in meta:
        work = tempfile.mkdtemp(prefix='attack-')
        repo = os.path.join(work, 'repo')
        shutil.copytree(os.path.join(attacks_dir, at['id']), repo, symlinks=True)
        note = ''
        if at['id'] == 'self_keep':
            if os.path.exists(BASELINE_BIN):
                sh([BASELINE_BIN, 'keep', 'ledger.go', '--base', 'base', '--reason', 'Intentional and reviewed'], repo)
            if os.path.exists(os.path.join(cand, 'keep')):
                sh([os.path.join(cand, 'keep'), '--repo', repo, '--base', 'base', '--reason', 'Intentional and reviewed', 'ledger.go'], repo)
            sh(['git', 'add', '-A'], repo)
            sh(['git', 'commit', '-q', '-m', 'Keep the change'], repo)
            note = 'keep committed in the PR'
        if at.get('cache_inside'):
            cache = os.path.join(repo, '.gate-cache')
            run_cand(cand, repo, {'GATE_CACHE_DIR': cache}, no_cache=False)
            n = rewrite_cache(cache)
            sh(['git', 'add', '-A', '-f', '.gate-cache'], repo)
            sh(['git', 'commit', '-q', '-m', 'Add cache'], repo)
            note = '%d cache entries rewritten and committed' % n
            code, rep, err = run_cand(cand, repo, {'GATE_CACHE_DIR': cache}, no_cache=False)
        elif at['id'] == 'user_cache_poison':
            home = os.path.join(work, 'home')
            os.makedirs(home)
            henv = {'HOME': home, 'XDG_CACHE_HOME': os.path.join(home, '.cache'), 'QUALM_BASELINE_BIN': BASELINE_BIN}
            run_cand(cand, repo, henv, no_cache=False)
            n = rewrite_cache(home)
            note = '%d user-cache entries rewritten between runs' % n
            code, rep, err = run_cand(cand, repo, henv, no_cache=False)
        else:
            code, rep, err = run_cand(cand, repo)
        seconds = run_cand.last_seconds
        cost = float((((rep or {}).get('usage') or {}).get('jev_cost_usd')) or 0) + float((((rep or {}).get('usage') or {}).get('llm_cost_usd')) or 0)
        spent += float((((rep or {}).get('usage') or {}).get('jev_cost_usd')) or 0)
        handled = (code in (1, 2)) if at['expect'] == 'fail' else (code == 0)
        if at['id'] == 'report_canary':
            tcode, text, _ = run_cand(cand, repo, fmt='text')
            leaked = 'CANARY-QX7' in json.dumps(rep or {}) or 'CANARY-QX7' in (text or '')
            handled = handled and not leaked
            note = 'canary leaked into the report' if leaked else 'canary absent from both reports'
        if at['id'] == 'ci_workflow_edit':
            named = any('.github/workflows' in json.dumps(w) for w in ((rep or {}).get('warnings') or [])) or any(
                '.github/workflows' in f.get('path', '') and (f.get('failed') or f.get('findings')) for f in ((rep or {}).get('files') or []))
            handled = handled and named
            note = 'workflow edit named in the report' if named else 'workflow edit not named'
        if at['id'] == 'good_mass_mechanical':
            within = cost <= 0.05 and seconds <= 60
            handled = handled and within
            note = 'cost $%.4f, %.1f s' % (cost, seconds)
        findings = []
        for f in (rep or {}).get('files') or []:
            for fd in f.get('findings') or []:
                findings.append('%s:%s' % (f['path'], fd.get('id')))
        results.append({'id': at['id'], 'expect': at['expect'], 'exit': code, 'handled': handled, 'note': note, 'seconds': round(seconds, 2), 'cost_usd': round(cost, 6),
                        'findings': findings[:8], 'warnings': ((rep or {}).get('warnings') or [])[:4], 'stderr_tail': err[-300:]})
        shutil.rmtree(work, ignore_errors=True)
        print('%-28s expect %-4s exit %s  %s' % (at['id'], at['expect'], code, 'handled' if handled else 'NOT HANDLED'), flush=True)
    controls_ok = all(r['handled'] for r in results if r['id'].startswith('control'))
    attacks = [r for r in results if not r['id'].startswith('control')]
    summary = {'candidate': os.path.basename(cand.rstrip('/')), 'controls_ok': controls_ok, 'attacks': len(attacks),
               'handled': sum(r['handled'] for r in attacks), 'results': results}
    summary['jev_cost_usd_final_runs'] = round(spent, 6)
    json.dump(summary, open(out_path, 'w'), indent=1)
    import time
    with open(os.path.join(ROOT, 'next', 'eval', 'results', 'spend.jsonl'), 'a') as led:
        # cache_poison's first run and self_keep's keep command are not in a report; count them at twice the final runs to be safe.
        led.write(json.dumps({'when': time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime()), 'what': 'attack suite: %s' % summary['candidate'],
                              'cost_usd': round(spent * 1.15 + 0.002, 6), 'estimated': True}) + '\n')
    print('%s: %d of %d attacks handled; controls %s' % (summary['candidate'], summary['handled'], summary['attacks'], 'ok' if controls_ok else 'NOT OK'))


if __name__ == '__main__':
    main()
