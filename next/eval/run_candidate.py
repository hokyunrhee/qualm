"""Run one candidate over benchmark cases and record what it said.

    python3 -I run_candidate.py CANDIDATE SPLIT_FILE OUT.jsonl [--cases ids.txt] [--jobs 6]
                                [--no-cache] [--budget-per-case 0.05] [--tag run1] [--llm-dir DIR]

CANDIDATE is a directory under next/candidates/ with an executable `run` (next/interface.md).
Each case is materialized once (cached under $BENCH_WORK/cases/<case_id>) and the candidate is
run with --repo <case> --base base --format json. One JSON line per case is appended to OUT:
{case_id, exit, seconds, report, stderr_tail}. Cases already in OUT are skipped, so a run resumes.

Money: before starting, the ledger (results/spend.jsonl) is summed. The run refuses to start when
the total plus the expected cost (--expected-per-case for each case) would pass BUDGET_USD (20),
and while it runs it stops starting cases once the ledger plus what this run has spent plus one
full per-case budget for every running job would pass it, so the cap holds whatever the cases
cost. Each finished run appends one line to the ledger with what the reports say was spent.
"""
import argparse, concurrent.futures, gzip, json, os, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from materialize import materialize  # noqa: E402

BUDGET_USD = 20.0
LEDGER = os.path.join(HERE, 'results', 'spend.jsonl')
WORK = os.environ.get('BENCH_WORK', '/tmp/claude-0/-home-user-qualm/2ede26c6-6287-5d82-9ba2-8868fcdef917/scratchpad/bench')


def spent():
    total = 0.0
    if os.path.exists(LEDGER):
        for line in open(LEDGER):
            if line.strip():
                total += float(json.loads(line).get('cost_usd', 0))
    return total


def load_cases(path):
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt') as fh:
        return [json.loads(l) for l in fh]


def case_repo(case):
    dest = os.path.join(WORK, 'cases', case['case_id'])
    if not os.path.exists(os.path.join(dest, '.git', 'refs', 'heads', 'main')):
        tmp = tempfile.mkdtemp(dir=os.path.join(WORK, 'cases'))
        materialize(case, tmp)
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.rename(tmp, dest)
    return dest


RUNNING = {'spent': 0.0, 'already': 0.0}


def run_one(cand_dir, case, args):
    if RUNNING['already'] + RUNNING['spent'] + args.budget_per_case * args.jobs > BUDGET_USD:
        return {'case_id': case['case_id'], 'exit': 3, 'seconds': 0, 'report': None, 'stderr_tail': 'not run: the study budget guard stopped the run'}
    repo = case_repo(case)
    cmd = [os.path.join(cand_dir, 'run'), '--repo', repo, '--base', 'base', '--format', 'json',
           '--budget', str(args.budget_per_case)]
    if args.no_cache:
        cmd.append('--no-cache')
    if args.llm_dir:
        d = os.path.join(args.llm_dir, case['case_id'])
        os.makedirs(d, exist_ok=True)
        cmd += ['--llm-dir', d]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=args.timeout, cwd=repo)
        code, out, err = p.returncode, p.stdout.decode('utf-8', 'replace'), p.stderr.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired:
        code, out, err = 124, '', 'timeout'
    secs = time.time() - t0
    try:
        report = json.loads(out) if out.strip() else None
    except ValueError:
        report = None
    return {'case_id': case['case_id'], 'exit': code, 'seconds': round(secs, 3), 'report': report, 'stderr_tail': err[-600:]}


def cost_of(rec):
    u = ((rec.get('report') or {}).get('usage') or {})
    return float(u.get('jev_cost_usd') or 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('candidate')
    ap.add_argument('split_file')
    ap.add_argument('out')
    ap.add_argument('--cases')
    ap.add_argument('--jobs', type=int, default=6)
    ap.add_argument('--no-cache', action='store_true')
    ap.add_argument('--budget-per-case', type=float, default=0.05)
    ap.add_argument('--expected-per-case', type=float, default=None, help='for the start check; defaults to --budget-per-case')
    ap.add_argument('--timeout', type=int, default=600)
    ap.add_argument('--tag', default='')
    ap.add_argument('--llm-dir')
    a = ap.parse_args()
    cand_dir = a.candidate if os.path.isdir(a.candidate) else os.path.join(ROOT, 'next', 'candidates', a.candidate)
    cases = load_cases(a.split_file)
    if a.cases:
        want = [l.strip() for l in open(a.cases) if l.strip()]
        by_id = {c['case_id']: c for c in cases}
        cases = [by_id[i] for i in want if i in by_id]
    done = set()
    if os.path.exists(a.out):
        for line in open(a.out):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get('exit') in (0, 1):
                done.add(rec['case_id'])
    todo = [c for c in cases if c['case_id'] not in done]
    worst = len(todo) * (a.expected_per_case if a.expected_per_case is not None else a.budget_per_case)
    already = spent()
    RUNNING['already'] = already
    if already + worst > BUDGET_USD:
        sys.exit('refusing: %.4f already spent, this run is expected to add %.2f, budget %.2f' % (already, worst, BUDGET_USD))
    os.makedirs(os.path.join(WORK, 'cases'), exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    total, n, failures = 0.0, 0, 0
    t0 = time.time()
    with open(a.out, 'a') as fh, concurrent.futures.ThreadPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(run_one, cand_dir, c, a) for c in todo]
        for f in concurrent.futures.as_completed(futs):
            rec = f.result()
            rec['tag'] = a.tag
            fh.write(json.dumps(rec) + '\n')
            fh.flush()
            total += cost_of(rec)
            RUNNING['spent'] = total
            n += 1
            failures += rec['exit'] not in (0, 1)
            if n % 100 == 0:
                print('  %d/%d cases, $%.4f, %d could not judge' % (n, len(todo), total, failures), flush=True)
    with open(LEDGER, 'a') as led:
        led.write(json.dumps({'when': time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime()), 'what': 'run %s on %s (%s) -> %s' % (
            os.path.basename(cand_dir), os.path.basename(a.split_file), a.tag, os.path.relpath(a.out, ROOT)),
            'cases': n, 'cost_usd': round(total, 6)}) + '\n')
    print('%s: %d cases in %.0fs, $%.4f, %d could not judge. Ledger total $%.4f' % (
        os.path.basename(cand_dir), n, time.time() - t0, total, failures, spent()), flush=True)


if __name__ == '__main__':
    main()
