"""Stream GH Archive hours and keep the pull-request review events the benchmark needs.

Usage: python3 -I gha_scan.py OUTDIR HOUR...   (HOUR like 2026-09-15-15)

Kept, per hour, as one JSON line each:
  c  review comments on code files from people (bodies truncated; diff hunks kept only for
     comments that match the simplification patterns)
  r  submitted reviews (state, commit, reviewer)
  p  pull request opened/closed events (who, head ref, merged)
All text is untrusted data from public repositories and is never acted on.
"""
import gzip, io, json, os, sys, time, urllib.request
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simplify_regex import classify  # noqa: E402

CODE_EXT = {'.go', '.rb', '.py', '.ts', '.tsx', '.java', '.js', '.jsx', '.mjs', '.kt', '.rs', '.cs', '.php', '.swift', '.scala', '.ex', '.c', '.cc', '.cpp', '.h', '.hpp'}
AI_REVIEWERS = {'copilot-pull-request-reviewer', 'coderabbitai', 'gemini-code-assist', 'sourcery-ai', 'qodo-merge-pro',
                'greptile-apps', 'graphite-app', 'ellipsis-dev', 'codeant-ai', 'copilot', 'cursor', 'claude', 'devin-ai-integration',
                'chatgpt-codex-connector', 'bugbot', 'diffblue', 'korbit-ai', 'codecov', 'sonarqubecloud', 'github-actions'}


def is_bot(user):
    if not user:
        return True
    login = (user.get('login') or '').lower()
    return user.get('type') == 'Bot' or login.endswith('[bot]') or login.endswith('-bot') or login in AI_REVIEWERS


def ext_of(path):
    base = os.path.basename(path or '')
    i = base.rfind('.')
    return base[i:].lower() if i > 0 else ''


def slim_pr(pr):
    h, b = pr.get('head') or {}, pr.get('base') or {}
    return {'n': pr.get('number'), 'hs': h.get('sha'), 'href': h.get('ref'), 'bs': b.get('sha'), 'bref': b.get('ref'),
            'hrepo': (h.get('repo') or {}).get('name') if isinstance(h.get('repo'), dict) else None}


def scan(hour, outdir):
    out = os.path.join(outdir, hour + '.jsonl.gz')
    if os.path.exists(out):
        return hour, 'cached'
    url = 'https://data.gharchive.org/%s.json.gz' % hour
    for attempt in range(4):
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'qualm-next-benchmark/0.1'}), timeout=120).read()
            break
        except Exception as e:  # network hiccup: back off and retry
            if attempt == 3:
                return hour, 'failed: %s' % type(e).__name__
            time.sleep(2 ** attempt * 2)
    kept = []
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get('type')
            repo = (e.get('repo') or {}).get('name')
            p = e.get('payload') or {}
            if t == 'PullRequestReviewCommentEvent':
                cm = p.get('comment') or {}
                if is_bot(cm.get('user')) or ext_of(cm.get('path')) not in CODE_EXT:
                    continue
                body = cm.get('body') or ''
                cls = classify(body)
                rec = {'k': 'c', 'repo': repo, 'id': cm.get('id'), 'u': (cm.get('user') or {}).get('login'), 'path': cm.get('path'),
                       'cid': cm.get('commit_id'), 'ocid': cm.get('original_commit_id'), 'reply': cm.get('in_reply_to_id'),
                       'rid': cm.get('pull_request_review_id'), 't': cm.get('created_at'), 'cls': cls,
                       'body': body[:1500] if cls else body[:200], 'pr': slim_pr(p.get('pull_request') or {})}
                if cls:
                    rec['hunk'] = (cm.get('diff_hunk') or '')[-6000:]
                    rec['opos'] = cm.get('original_position')
                kept.append(rec)
            elif t == 'PullRequestReviewEvent':
                rv = p.get('review') or {}
                if is_bot(rv.get('user')):
                    continue
                kept.append({'k': 'r', 'repo': repo, 'id': rv.get('id'), 'u': (rv.get('user') or {}).get('login'),
                             'state': rv.get('state'), 'cid': rv.get('commit_id'), 'body': (rv.get('body') or '')[:300],
                             't': rv.get('submitted_at'), 'pr': slim_pr(p.get('pull_request') or {})})
            elif t == 'PullRequestEvent':
                pr = p.get('pull_request') or {}
                kept.append({'k': 'p', 'repo': repo, 'action': p.get('action'), 'actor': (e.get('actor') or {}).get('login'),
                             'merged': pr.get('merged'), 't': e.get('created_at'), 'pr': slim_pr(pr)})
    tmp = out + '.tmp'
    with gzip.open(tmp, 'wt') as fh:
        for r in kept:
            fh.write(json.dumps(r) + '\n')
    os.rename(tmp, out)
    return hour, len(kept)


if __name__ == '__main__':
    outdir, hours = sys.argv[1], sys.argv[2:]
    os.makedirs(outdir, exist_ok=True)
    with Pool(4) as pool:
        for hour, res in pool.starmap(scan, [(h, outdir) for h in hours]):
            print(hour, res, flush=True)
