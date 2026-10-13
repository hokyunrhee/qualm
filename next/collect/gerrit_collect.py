"""Collect file-level changes with real reviewer labels from a public Gerrit host.

Usage: python3 -I gerrit_collect.py HOST OUTDIR --project P [--project P ...] [--max-signal N] [--max-clean N] [--after 2023-01-01]

Positive: a reviewer (not the owner, not a bot) left an inline comment on patch set k asking for
the change to be simplified (simplify_regex), on or next to a line patch set k adds, and the owner
acted on it (a "Done"-style reply, or a later patch set changed the file near that line).
Negative: a file at patch set k nobody commented on in any patch set, whose content then merged
unchanged; the final, approved version of a positive file ("post_fix"); every file of a merged
change that drew no inline comments from reviewers ("approved_clean").
"""
import argparse, base64, json, os, sys, urllib.parse
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import get, judged_file, language, added_lines, changed_near  # noqa: E402
from simplify_regex import classify, ACK  # noqa: E402

KEYWORDS = ['simplify', 'simpler', 'simplified', 'redundant', 'unnecessary', 'duplicate', 'duplicated', 'duplication',
            'overkill', 'convoluted', 'complicated', 'unused', 'helper', 'needed', 'necessary', 'reuse', 'extract', 'yagni', 'happen']
BOT_WORDS = ('bot', 'robot', 'luci', 'zuul', 'ci', 'jenkins', 'automation', 'service', 'gerritcodereview', 'openstack proposal', 'tricium')
MAX_BYTES = 200_000


def q(s):
    return urllib.parse.quote(s, safe='')


class Host:
    def __init__(self, host):
        self.base = 'https://' + host + ('/r' if host == 'gerrit.wikimedia.org' else '')

    def api(self, path):
        return get(self.base + path, as_json=True, gerrit=True)

    def content(self, change, rev, path, parent=False):
        url = '%s/changes/%s/revisions/%s/files/%s/content%s' % (self.base, change, rev, q(path), '?parent=1' if parent else '')
        raw = get(url, gerrit=False)
        if raw is None:
            return None
        try:
            text = base64.b64decode(raw).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            return None
        return text


def is_bot(acct):
    name = ((acct or {}).get('name') or '') + ' ' + ((acct or {}).get('username') or '') + ' ' + ((acct or {}).get('email') or '')
    tags = (acct or {}).get('tags') or []
    low = name.lower()
    return 'SERVICE_USER' in tags or any((' ' + w + ' ') in (' ' + low.replace('-', ' ').replace('.', ' ') + ' ') for w in BOT_WORDS)


def search(h, query, limit):
    out, start = [], 0
    while len(out) < limit:
        page = h.api('/changes/?q=%s&n=%d&S=%d' % (q(query), min(100, limit - len(out)), start))
        if not page:
            break
        out.extend(page)
        start += len(page)
        if not page[-1].get('_more_changes'):
            break
    return out


def thread_root(c, byid):
    seen = 0
    while c.get('in_reply_to') and c['in_reply_to'] in byid and seen < 50:
        c = byid[c['in_reply_to']]
        seen += 1
    return c


def collect_change(h, host, num):
    ch = h.api('/changes/%s?o=ALL_REVISIONS&o=ALL_FILES&o=DETAILED_ACCOUNTS&o=DETAILED_LABELS' % num)
    if not ch or ch.get('status') != 'MERGED':
        return []
    comments = h.api('/changes/%s/comments' % num) or {}
    owner = (ch.get('owner') or {}).get('_account_id')
    revs = sorted(ch['revisions'].items(), key=lambda kv: kv[1]['_number'])
    by_ps = {r['_number']: (sha, r) for sha, r in revs}
    final_ps = revs[-1][1]['_number']
    byid = {c['id']: c for cs in comments.values() for c in cs}
    # Which (path, patch set) pairs any reviewer commented on, and the signal comments with their thread.
    reviewer_touched = set()
    signals = []
    for path, cs in comments.items():
        for c in cs:
            a = c.get('author') or {}
            if a.get('_account_id') == owner or is_bot(a):
                continue
            reviewer_touched.add(path)
            if c.get('in_reply_to') or c.get('side') == 'PARENT' or not c.get('line'):
                continue
            strength = classify(c.get('message', ''))
            if strength and judged_file(path):
                replies = [r for r in comments.get(path, []) if r is not c and thread_root(r, byid) is c]
                acked = any((r.get('author') or {}).get('_account_id') == owner and ACK.search(r.get('message', '')) for r in replies)
                signals.append({'path': path, 'ps': c['patch_set'], 'line': c['line'], 'range': c.get('range'),
                                'message': c.get('message', '')[:1500], 'strength': strength, 'acked': acked})
    cases = []

    def files_at(ps):
        sha, rev = by_ps[ps]
        return sha, {p: f for p, f in (rev.get('files') or {}).items() if judged_file(p) and f.get('status') not in ('D',)}

    def fetch(ps, paths):
        sha, files = files_at(ps)
        out = []
        for p in sorted(paths):
            f = files.get(p)
            if not f or f.get('size', 0) > MAX_BYTES or f.get('status') == 'R':
                continue
            head = h.content(num, ps, p)
            base = None if f.get('status') == 'A' else h.content(num, ps, p, parent=True)
            if head is None or (f.get('status') != 'A' and base is None) or head == base:
                continue
            out.append({'path': p, 'language': language(p), 'base': base, 'head': head, 'new_sha': f.get('new_sha')})
        return out

    meta = {'source': 'gerrit:' + host, 'repo': ch['project'], 'url': '%s/c/%s/+/%s' % (h.base, ch['project'], num),
            'subject': ch.get('subject', '')[:300], 'owner': owner}
    final_sha, final_files = files_at(final_ps)
    if signals:
        for ps in sorted({s['ps'] for s in signals}):
            if ps not in by_ps or ps == final_ps:
                continue
            _, files = files_at(ps)
            got = fetch(ps, files.keys())
            byp = {f['path']: f for f in got}
            items = []
            for path, f in byp.items():
                sigs = [s for s in signals if s['path'] == path and s['ps'] == ps]
                if sigs:
                    added, _ = added_lines(f['base'], f['head'])
                    final_head = h.content(num, final_ps, path) if path in final_files else ''
                    good = []
                    for s in sigs:
                        lo = s['range']['start_line'] if s.get('range') else s['line']
                        hi = s['range']['end_line'] if s.get('range') else s['line']
                        near = any(lo - 3 <= n <= hi + 3 for n in added)
                        moved = final_head is not None and changed_near(f['head'], final_head or '', s['line'])
                        if near and (s['acked'] or moved):
                            good.append(dict(s, acted={'acked': s['acked'], 'changed_later': moved}, loc=[lo, hi]))
                    if good:
                        strongest = 'strong' if any(g['strength'] == 'strong' for g in good) else 'weak'
                        items.append({'path': path, 'label': 1, 'label_source': 'reviewer_comment', 'strength': strongest,
                                      'location': [min(g['loc'][0] for g in good), max(g['loc'][1] for g in good)],
                                      'locations': [g['loc'] for g in good], 'evidence': [g['message'] for g in good],
                                      'acted': [g['acted'] for g in good]})
                    # a commented file without an acted-on simplification request is left unlabelled
                elif path not in reviewer_touched and path in final_files and final_files[path].get('new_sha') == f['new_sha']:
                    items.append({'path': path, 'label': 0, 'label_source': 'approved_uncommented'})
            if any(i['label'] == 1 for i in items):
                cases.append(dict(meta, case_id='gerrit-%s-%s-ps%d' % (host.split('.')[0], num, ps), patch_set=ps,
                                  files=[{k: v for k, v in f.items() if k != 'new_sha'} for f in got], items=items))
        # The approved final version of each positive file.
        pos_paths = {i['path'] for c in cases for i in c['items'] if i['label'] == 1}
        late = {s['path'] for s in signals if s['ps'] == final_ps}
        keep = [p for p in pos_paths if p in final_files and p not in late]
        if keep:
            got = fetch(final_ps, final_files.keys())
            items = [{'path': f['path'], 'label': 0, 'label_source': 'post_fix'} for f in got if f['path'] in keep]
            if items:
                cases.append(dict(meta, case_id='gerrit-%s-%s-ps%d' % (host.split('.')[0], num, final_ps), patch_set=final_ps,
                                  files=[{k: v for k, v in f.items() if k != 'new_sha'} for f in got], items=items))
    elif not reviewer_touched:
        approved = any(v.get('value', 0) >= 2 and not is_bot(v) and v.get('_account_id') != owner
                       for v in ((ch.get('labels') or {}).get('Code-Review') or {}).get('all', []))
        if approved and final_files:
            got = fetch(final_ps, final_files.keys())
            if got:
                cases.append(dict(meta, case_id='gerrit-%s-%s-ps%d' % (host.split('.')[0], num, final_ps), patch_set=final_ps,
                                  files=[{k: v for k, v in f.items() if k != 'new_sha'} for f in got],
                                  items=[{'path': f['path'], 'label': 0, 'label_source': 'approved_clean'} for f in got]))
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('host')
    ap.add_argument('outdir')
    ap.add_argument('--project', action='append', required=True)
    ap.add_argument('--max-signal', type=int, default=150)
    ap.add_argument('--max-clean', type=int, default=60)
    ap.add_argument('--after', default='2023-01-01')
    ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args()
    h = Host(a.host)
    os.makedirs(a.outdir, exist_ok=True)
    kw = ' OR '.join('comment:%s' % k for k in KEYWORDS)
    for project in a.project:
        sig = search(h, 'status:merged after:%s project:%s (%s)' % (a.after, project, kw), a.max_signal)
        clean = search(h, 'status:merged after:%s project:%s -is:wip' % (a.after, project), a.max_clean * 4)
        sig_ids = [c['_number'] for c in sig]
        clean_ids = [c['_number'] for c in clean if c['_number'] not in set(sig_ids)]
        n_clean = 0
        stats = {'signal_changes': len(sig_ids), 'cases': 0, 'pos': 0, 'neg': 0}
        out = os.path.join(a.outdir, project.replace('/', '__') + '.jsonl')
        with open(out, 'w') as fh, ThreadPoolExecutor(a.workers) as ex:
            for cases in ex.map(lambda n: safe(h, a.host, n), sig_ids):
                for c in cases:
                    fh.write(json.dumps(c) + '\n')
                    stats['cases'] += 1
                    stats['pos'] += sum(i['label'] for i in c['items'])
                    stats['neg'] += sum(1 - i['label'] for i in c['items'])
            for start in range(0, len(clean_ids), 8):
                if n_clean >= a.max_clean:
                    break
                for cases in ex.map(lambda n: safe(h, a.host, n), clean_ids[start:start + 8]):
                    for c in cases:
                        if c['items'][0]['label_source'] != 'approved_clean' or n_clean >= a.max_clean:
                            continue
                        n_clean += 1
                        fh.write(json.dumps(c) + '\n')
                        stats['cases'] += 1
                        stats['neg'] += len(c['items'])
        print(a.host, project, json.dumps(stats), flush=True)


def safe(h, host, num):
    try:
        return collect_change(h, host, num)
    except Exception as e:  # one bad change must not stop the collection; it is logged and left out
        print('  skip change %s: %s %s' % (num, type(e).__name__, str(e)[:120]), file=sys.stderr, flush=True)
        return []


if __name__ == '__main__':
    main()
