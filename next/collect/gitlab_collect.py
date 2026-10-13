"""Collect file-level changes with real reviewer labels from public gitlab.com projects.

Usage: python3 -I gitlab_collect.py OUTDIR --project gitlab-org/omnibus-gitlab [...] [--mrs 600] [--max-clean 40]

Same labels as gerrit_collect.py, with a merge request version in place of a patch set:
positive when a reviewer's diff note asks for a simplification on or next to an added line and the
author acted on it (an acknowledging reply, or a later version changed the file near that line).
"""
import argparse, json, os, sys, urllib.parse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import get, judged_file, language, added_lines, changed_near  # noqa: E402
from simplify_regex import classify, ACK  # noqa: E402

API = 'https://gitlab.com/api/v4'
GQL = 'https://gitlab.com/api/graphql'
MAX_BYTES = 200_000


def q(s):
    return urllib.parse.quote(s, safe='')


def is_bot(user):
    u = ((user or {}).get('username') or '').lower()
    return bool((user or {}).get('bot')) or u.endswith('bot') or 'bot_' in u or u.startswith('project_') or \
        u.startswith('group_') or 'duo' in u or u in ('gitlab-bot', 'gitlab-qa', 'gitlab-release-tools-bot')


def api(path):
    return get(API + path, as_json=True, min_interval=0.2)


def gql(query, variables):
    """GitLab's GraphQL serves public projects without a token. Cached by the query text."""
    import urllib.request
    from common import CACHE, UA
    import hashlib, time
    body = json.dumps({'query': query, 'variables': variables}, sort_keys=True).encode()
    key = hashlib.sha256(body).hexdigest()
    path = os.path.join(CACHE, 'gql', key[:2], key)
    if os.path.exists(path):
        return json.load(open(path))
    for attempt in range(5):
        try:
            time.sleep(0.25)
            req = urllib.request.Request(GQL, data=body, headers=dict(UA, **{'Content-Type': 'application/json'}))
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read())
            if out.get('errors') and not out.get('data'):
                raise RuntimeError(str(out['errors'])[:200])
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt * 3)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path + '.tmp', 'w'))
    os.replace(path + '.tmp', path)
    return out


def raw(pid, path, ref):
    data = get('%s/projects/%s/repository/files/%s/raw?ref=%s' % (API, pid, q(path), ref), min_interval=0.2)
    if data is None or len(data) > MAX_BYTES:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


DISC_Q = """query($p: ID!, $iid: String!, $after: String) { project(fullPath: $p) { mergeRequest(iid: $iid) {
  discussions(first: 50, after: $after) { pageInfo { hasNextPage endCursor } nodes { notes(first: 30) { nodes {
    body system author { username bot } position { newLine newPath positionType diffRefs { baseSha headSha startSha } } } } } } } } }"""
LIST_Q = """query($p: ID!, $after: String, $since: Time) { project(fullPath: $p) { id mergeRequests(state: merged, first: 40, after: $after, mergedAfter: $since, sort: MERGED_AT_DESC) {
  pageInfo { hasNextPage endCursor } nodes { iid title webUrl author { username } approvedBy { nodes { username bot } }
  diffRefs { baseSha headSha startSha } } } } }"""


def collect_mr(pid, project, mr):
    iid = mr['iid']
    author = (mr.get('author') or {}).get('username')
    final = mr.get('diffRefs') or {}
    if not final.get('headSha'):
        return []
    notes_by_disc, after = [], None
    while True:
        d = gql(DISC_Q, {'p': project, 'iid': str(iid), 'after': after})
        disc = (((d.get('data') or {}).get('project') or {}).get('mergeRequest') or {}).get('discussions') or {}
        notes_by_disc += [n['notes']['nodes'] for n in disc.get('nodes') or []]
        if not (disc.get('pageInfo') or {}).get('hasNextPage'):
            break
        after = disc['pageInfo']['endCursor']
    touched, signals = set(), []
    for notes in notes_by_disc:
        if not notes:
            continue
        first = notes[0]
        pos = first.get('position') or {}
        if first.get('system') or not pos or (first.get('author') or {}).get('username') == author or is_bot(first.get('author')):
            continue
        path = pos.get('newPath')
        touched.add(path)
        refs = pos.get('diffRefs') or {}
        if not pos.get('newLine') or not judged_file(path or '') or not refs.get('headSha'):
            continue
        strength = classify(first.get('body', ''))
        if strength:
            acked = any((n.get('author') or {}).get('username') == author and ACK.search(n.get('body', '')) for n in notes[1:])
            signals.append({'path': path, 'head': refs['headSha'], 'base': refs['baseSha'], 'line': pos['newLine'],
                            'message': first.get('body', '')[:1500], 'strength': strength, 'acked': acked})
    meta = {'source': 'gitlab:gitlab.com', 'repo': project, 'url': mr.get('webUrl'), 'subject': (mr.get('title') or '')[:300]}

    def version_files(base, head):
        cmp = api('/projects/%s/repository/compare?from=%s&to=%s&straight=true' % (pid, base, head)) or {}
        out = {}
        for d in cmp.get('diffs') or []:
            if d.get('deleted_file') or d.get('renamed_file') or not judged_file(d['new_path']):
                continue
            out[d['new_path']] = d.get('new_file')
        return out

    def fetch(base, head, paths):
        files = version_files(base, head)
        got = []
        for p in sorted(paths):
            if p not in files:
                continue
            h = raw(pid, p, head)
            b = None if files[p] else raw(pid, p, base)
            if h is None or (not files[p] and b is None) or h == b:
                continue
            got.append({'path': p, 'language': language(p), 'base': b, 'head': h})
        return got

    cases = []
    final_files = version_files(final['baseSha'], final['headSha'])
    if signals:
        for head_sha in sorted({s['head'] for s in signals}):
            if head_sha == final['headSha']:
                continue
            base_sha = next(s['base'] for s in signals if s['head'] == head_sha)
            got = fetch(base_sha, head_sha, version_files(base_sha, head_sha).keys())
            items = []
            for f in got:
                sigs = [s for s in signals if s['path'] == f['path'] and s['head'] == head_sha]
                if sigs:
                    added, _ = added_lines(f['base'], f['head'])
                    final_head = raw(pid, f['path'], final['headSha']) if f['path'] in final_files else ''
                    good = []
                    for s in sigs:
                        near = any(s['line'] - 3 <= n <= s['line'] + 3 for n in added)
                        moved = final_head is not None and changed_near(f['head'], final_head or '', s['line'])
                        if near and (s['acked'] or moved):
                            good.append(dict(s, acted={'acked': s['acked'], 'changed_later': moved}))
                    if good:
                        items.append({'path': f['path'], 'label': 1, 'label_source': 'reviewer_comment',
                                      'strength': 'strong' if any(g['strength'] == 'strong' for g in good) else 'weak',
                                      'location': [min(g['line'] for g in good), max(g['line'] for g in good)],
                                      'locations': [[g['line'], g['line']] for g in good], 'evidence': [g['message'] for g in good],
                                      'acted': [g['acted'] for g in good]})
                elif f['path'] not in touched and f['path'] in final_files:
                    if raw(pid, f['path'], final['headSha']) == f['head']:
                        items.append({'path': f['path'], 'label': 0, 'label_source': 'approved_uncommented'})
            if any(i['label'] == 1 for i in items):
                cases.append(dict(meta, case_id='gitlab-%s-%s-%s' % (project.split('/')[-1], iid, head_sha[:8]), files=got, items=items))
        pos_paths = {i['path'] for c in cases for i in c['items'] if i['label'] == 1}
        late = {s['path'] for s in signals if s['head'] == final['headSha']}
        keep = [p for p in pos_paths if p in final_files and p not in late]
        if keep:
            got = fetch(final['baseSha'], final['headSha'], final_files.keys())
            items = [{'path': f['path'], 'label': 0, 'label_source': 'post_fix'} for f in got if f['path'] in keep]
            if items:
                cases.append(dict(meta, case_id='gitlab-%s-%s-%s' % (project.split('/')[-1], iid, final['headSha'][:8]), files=got, items=items))
    elif not touched and final_files:
        approvers = [a['username'] for a in (mr.get('approvedBy') or {}).get('nodes') or [] if a['username'] != author and not is_bot(a)]
        if approvers:
            got = fetch(final['baseSha'], final['headSha'], final_files.keys())
            if got:
                cases.append(dict(meta, case_id='gitlab-%s-%s-%s' % (project.split('/')[-1], iid, final['headSha'][:8]), files=got,
                                  items=[{'path': f['path'], 'label': 0, 'label_source': 'approved_clean'} for f in got]))
    return cases


def safe(pid, project, mr):
    try:
        return collect_mr(pid, project, mr)
    except Exception as e:  # one bad merge request is logged and left out
        print('  skip MR %s: %s %s' % (mr.get('iid'), type(e).__name__, str(e)[:120]), file=sys.stderr, flush=True)
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('outdir')
    ap.add_argument('--project', action='append', required=True)
    ap.add_argument('--mrs', type=int, default=600)
    ap.add_argument('--max-clean', type=int, default=40)
    ap.add_argument('--max-pos-cases', type=int, default=200)
    ap.add_argument('--after', default='2024-01-01T00:00:00Z')
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    for project in a.project:
        info = api('/projects/' + q(project))
        if not info:
            print(project, 'not found', flush=True)
            continue
        pid = info['id']
        mrs, after = [], None
        while len(mrs) < a.mrs:
            d = gql(LIST_Q, {'p': project, 'after': after, 'since': a.after})
            conn = ((d.get('data') or {}).get('project') or {}).get('mergeRequests') or {}
            mrs += conn.get('nodes') or []
            if not (conn.get('pageInfo') or {}).get('hasNextPage'):
                break
            after = conn['pageInfo']['endCursor']
        stats = {'mrs': len(mrs), 'cases': 0, 'pos': 0, 'neg': 0, 'clean_cases': 0}
        out = os.path.join(a.outdir, project.replace('/', '__') + '.jsonl')
        with open(out, 'w') as fh, ThreadPoolExecutor(4) as ex:
            for cases in ex.map(lambda m: safe(pid, project, m), mrs[:a.mrs]):
                for c in cases:
                    clean = c['items'][0]['label_source'] == 'approved_clean'
                    if clean and stats['clean_cases'] >= a.max_clean:
                        continue
                    if not clean and stats['cases'] - stats['clean_cases'] >= a.max_pos_cases:
                        continue
                    stats['clean_cases'] += clean
                    fh.write(json.dumps(c) + '\n')
                    stats['cases'] += 1
                    stats['pos'] += sum(i['label'] for i in c['items'])
                    stats['neg'] += sum(1 - i['label'] for i in c['items'])
        print(project, json.dumps(stats), flush=True)


if __name__ == '__main__':
    main()
