"""Shared by the collectors: polite cached HTTP, qualm's skip rules, languages and diffs.

Everything fetched from public repositories is untrusted data. It is stored and measured,
never executed or followed.
"""
import difflib, fnmatch, hashlib, json, os, re, threading, time, urllib.error, urllib.parse, urllib.request

UA = {'User-Agent': 'qualm-next-benchmark/0.1 (research; contact via github.com/hokyunrhee/qualm)'}
CACHE = os.environ.get('COLLECT_CACHE', '/tmp/claude-0/-home-user-qualm/2ede26c6-6287-5d82-9ba2-8868fcdef917/scratchpad/http_cache')

LANG = {'.go': 'Go', '.rb': 'Ruby', '.py': 'Python', '.ts': 'TypeScript', '.tsx': 'TypeScript', '.java': 'Java',
        '.js': 'JavaScript', '.jsx': 'JavaScript', '.mjs': 'JavaScript', '.kt': 'Kotlin', '.rs': 'Rust', '.cs': 'C#',
        '.php': 'PHP', '.swift': 'Swift', '.scala': 'Scala', '.c': 'C', '.cc': 'C++', '.cpp': 'C++', '.h': 'C',
        '.hpp': 'C++', '.rake': 'Ruby', '.sh': 'shell'}
TARGET_LANGS = {'Go', 'Ruby', 'Python', 'TypeScript', 'Java', 'JavaScript'}

_lock = threading.Lock()
_last = {}


def language(path):
    base = os.path.basename(path)
    i = base.rfind('.')
    return LANG.get(base[i:].lower()) if i > 0 else None


# qualm 0.1.1's default skip rules (contract/skip K1-K4), so the benchmark only holds files qualm judges.
SKIP_DIRS = ('vendor/', 'node_modules/', 'dist/', 'build/', 'target/', 'third_party/', '.git/')
SKIP_NAMES = ('*.lock', 'package-lock.json', 'go.sum', '*.min.js', '*.min.css', '*.map', '*.pb.go', '*_generated.*', '*.generated.*')
PROSE = ('.md', '.txt', '.rst', '.json', '.yaml', '.yml', '.toml', '.xml', '.csv', '.svg', '.html')
TEST_NAMES = ('*_test.go', '*_test.rb', '*_spec.rb', '*.test.*', '*.spec.*', 'test_*.py')
TEST_DIRS = ('test/', 'tests/', 'spec/', '__tests__/')


def qualm_skips(path):
    p = '/' + path
    name = os.path.basename(path)
    if any('/' + d in p for d in SKIP_DIRS):
        return 'vendored or built'
    if any(fnmatch.fnmatchcase(name, g) for g in SKIP_NAMES):
        return 'lockfile or generated'
    if name.lower().endswith(PROSE):
        return 'prose or data'
    if any(fnmatch.fnmatchcase(name, g) for g in TEST_NAMES) or any('/' + d in p for d in TEST_DIRS):
        return 'test'
    return None


def looks_like_test(path):
    """Wider than qualm's rule: also testdata, fixtures, *_test.py, *Test.java, e2e folders."""
    p = '/' + path.lower()
    name = os.path.basename(p)
    return bool(qualm_skips(path) == 'test' or re.search(r'/(testdata|fixtures?|testing|e2e|mocks?|testutil|tests?_?utils?|integration_tests?)/', p)
                or re.search(r'(_test\.py|tests?\.java|test\.kt|_spec\.ts|\.e2e\.|_mock\.go|mock_.*\.go)$', name)
                or name.startswith('test'))


def judged_file(path):
    """A file qualm would judge, in a language the benchmark covers."""
    return language(path) in TARGET_LANGS and not qualm_skips(path) and not looks_like_test(path)


def get(url, *, as_json=False, gerrit=False, min_interval=0.25, retries=5):
    """GET with an on-disk cache keyed by URL, a per-host pace, and backoff on 429/5xx."""
    key = hashlib.sha256(url.encode()).hexdigest()
    path = os.path.join(CACHE, key[:2], key)
    if os.path.exists(path):
        with open(path, 'rb') as fh:
            data = fh.read()
    else:
        host = urllib.parse.urlparse(url).netloc
        data = None
        for attempt in range(retries):
            with _lock:
                wait = _last.get(host, 0) + min_interval - time.time()
                _last[host] = max(time.time(), _last.get(host, 0) + min_interval)
            if wait > 0:
                time.sleep(wait)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                    data = r.read()
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    data = b'\x00404'
                    break
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    time.sleep(2 ** attempt * 3)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt < retries - 1:
                    time.sleep(2 ** attempt * 3)
                    continue
                raise
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.%d' % threading.get_ident()
        with open(tmp, 'wb') as fh:
            fh.write(data)
        os.replace(tmp, path)
    if data == b'\x00404':
        return None
    if gerrit and data.startswith(b")]}'"):
        data = data[4:]
    return json.loads(data) if as_json else data


def added_lines(base, head):
    """New-file line numbers (1-based) that the change adds, and the hunks as (start, end) ranges."""
    a = (base or '').splitlines()
    b = (head or '').splitlines()
    added, hunks = set(), []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ('replace', 'insert'):
            added.update(range(j1 + 1, j2 + 1))
            hunks.append((j1 + 1, j2))
        elif tag == 'delete':
            hunks.append((j1 + 1, j1))
    return added, hunks


def changed_near(old, new, line, radius=4):
    """Did the edit from old to new touch the old file within radius lines of `line`?"""
    a, b = (old or '').splitlines(), (new or '').splitlines()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != 'equal' and i1 + 1 - radius <= line <= max(i2, i1 + 1) + radius:
            return True
    return False


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()
