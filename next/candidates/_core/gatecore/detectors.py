"""Detectors: the specific things an experienced reviewer asks to be simplified, found without a model.

Each detector looks only at what the change added and returns findings:
    {"id", "lines": [lo, hi], "message", "fix", "confidence", "related": [...]}
Messages describe the code; they never quote comments or strings from it, so text in a pull
request cannot speak through the report.
"""
import collections, difflib, hashlib, re

from . import lexer, units as unitmod

# ---------------------------------------------------------------- helpers


def finding(fid, lines, message, fix, confidence, related=None):
    return {'id': fid, 'lines': [int(lines[0]), int(lines[1])], 'message': message, 'fix': fix,
            'confidence': round(float(confidence), 2), 'related': related or []}


def ranges(nums, gap=1):
    return [tuple(r) for r in unitmod.added_ranges(nums, gap=gap)]


def unit_label(f, line):
    u = unitmod.innermost(f.units, line)
    return ('%s' % u.name) if u else None


def where(f, lo, hi):
    name = unit_label(f, lo)
    span = 'line %d' % lo if lo == hi else 'lines %d-%d' % (lo, hi)
    return '%s (in %s)' % (span, name) if name else span


# ---------------------------------------------------------------- debug output

DEBUG_CALLS = {
    'Go': re.compile(r'\b(fmt\.Print(?:ln|f)?|println|print|spew\.Dump|log\.Print(?:ln|f)?)\s*\(|\bfmt\.Fprint(?:ln|f)?\(\s*os\.Stderr'),
    'Python': re.compile(r'(^|[^.\w])(print\s*\(|pprint\s*\(|pdb\.set_trace\s*\(|breakpoint\s*\(\s*\)|import\s+pdb\b|ipdb\.set_trace|from\s+pprint\s+import)|\bsys\.stderr\.write\s*\('),
    'TypeScript': re.compile(r'\b(console\.(log|debug|dir|trace|table)\s*\(|debugger\s*;?\s*$|process\.stderr\.write\s*\()'),
    'JavaScript': re.compile(r'\b(console\.(log|debug|dir|trace|table)\s*\(|debugger\s*;?\s*$|process\.stderr\.write\s*\()'),
    'Ruby': re.compile(r'(^\s*(puts|p|pp|print)\b[\s(]|\$stderr\.(puts|print|write)\b|\bSTDERR\.(puts|print|write)\b|binding\.pry|\bbyebug\b|binding\.irb|\bdebugger\b)'),
    'Java': re.compile(r'\b(System\.(out|err)\.print(?:ln|f)?\s*\(|\.printStackTrace\s*\(\s*\))'),
}
ALWAYS_DEBUG = re.compile(r'pdb\.set_trace|breakpoint\s*\(\s*\)|import\s+pdb|binding\.pry|byebug|binding\.irb|^\s*debugger\b|spew\.Dump|\.printStackTrace\s*\(')
DEBUG_WORDS = re.compile(r'\b(debug|DEBUG|here|HERE|XXX|xxx|got|GOT|test|TEST|tmp|TMP|dump|trace|>>>|<<<|===|\*\*\*|!!!)\b|^[\W_]*$')


def debug_output(f, ctx):
    rx = DEBUG_CALLS.get(f.language)
    if not rx:
        return []
    script = f.is_script()
    hits = []
    for n in f.new_code_lines():
        code = f.code[n - 1]
        if not rx.search(code):
            continue
        if f.language == 'Go' and re.search(r'\bfmt\.Fprint', code) and not re.search(r'\bfmt\.Fprint(?:ln|f)?\(\s*os\.Stderr', code):
            continue
        strong = bool(ALWAYS_DEBUG.search(code)) or any(DEBUG_WORDS.search(s.strip()) for s in f.strings[n - 1] if s.strip())
        if f.language == 'Go' and 'log.Print' in code and not strong:
            continue
        if script and not strong:
            continue
        hits.append((n, strong))
    out = []
    for lo, hi in ranges([n for n, _ in hits], gap=6):
        strong = any(s for n, s in hits if lo <= n <= hi)
        k = sum(1 for n, _ in hits if lo <= n <= hi)
        out.append(finding('debug_output', (lo, hi),
                           'The change adds %s output at %s that reads like debugging left in.' % ('debugger or print' if k == 1 else '%d print or debugger statements' % k, where(f, lo, hi)),
                           'Delete the debugging statements. If the output is needed, send it through the logger the surrounding code already uses, at debug level. Done when no print or debugger call is left in the added lines.',
                           0.9 if strong else 0.6))
    return out


# ---------------------------------------------------------------- placeholders and stubs

TODO = re.compile(r'\b(TODO|FIXME|XXX|HACK|TBD)\b')
TRACKED = re.compile(r'(#\d+|\b[A-Z][A-Z0-9]+-\d+\b|https?://|\bissue\b|\bb/\d+|\bcrbug|\bgo\.dev/issue|\(\w[\w.-]*\)|@\w+)', re.I)
STUB = {
    'Go': re.compile(r'panic\(\s*""\s*\)|panic\(\s*(errors\.New\()?""\)?\s*\)'),
    'Python': re.compile(r'raise\s+NotImplementedError'),
    'TypeScript': re.compile(r'throw\s+new\s+Error\(\s*""\s*\)'),
    'JavaScript': re.compile(r'throw\s+new\s+Error\(\s*""\s*\)'),
    'Ruby': re.compile(r'raise\s+NotImplementedError'),
    'Java': re.compile(r'throw\s+new\s+(UnsupportedOperationException|RuntimeException|IllegalStateException)\(\s*""\s*\)'),
}
STUB_TEXT = re.compile(r'not (yet )?implemented|unimplemented|implement me|todo|tbd|stub', re.I)


def placeholder(f, ctx):
    out = []
    todos = []
    for n in sorted(f.new):
        c = f.comments[n - 1] if n <= len(f.comments) else ''
        if c and TODO.search(c) and not TRACKED.search(c):
            todos.append(n)
    for lo, hi in ranges(todos, gap=2):
        out.append(finding('placeholder', (lo, hi),
                           'The change leaves an untracked TODO at %s: work the pull request says is unfinished.' % where(f, lo, hi),
                           'Either finish the work here, or delete the code path that is not ready. If it must wait, replace the TODO with a reference to an issue. Done when the added lines hold no bare TODO.',
                           0.55))
    rx = STUB.get(f.language)
    stubs = []
    for n in f.new_code_lines():
        code = f.code[n - 1]
        strs = ' '.join(f.strings[n - 1])
        if rx and rx.search(code) and (f.language in ('Python', 'Ruby') or STUB_TEXT.search(strs)):
            if f.language == 'Python':
                prev = '\n'.join(f.lines[max(0, n - 6):n])
                if '@abstractmethod' in prev or 'abc.abstractmethod' in prev or re.search(r'class \w+\((ABC|Protocol|.*Base\w*)\)', f.head):
                    continue
            stubs.append(n)
    # a new function whose whole body is pass / ... / return nil with a TODO
    for u in f.new_units():
        body = [n for n in range(u.start + 1, u.end + 1) if f.code[n - 1].strip() not in ('', '}', 'end', '};')]
        txt = ' '.join(f.code[n - 1].strip() for n in body)
        com = ' '.join(f.comments[n - 1] for n in range(u.start, u.end + 1))
        if len(body) <= 2 and (txt in ('pass', '...', 'return nil', 'return None', 'return null;', 'return', 'nil', 'return {}', 'return []', 'return nil, nil')) and TODO.search(com):
            stubs.append(u.start)
    for lo, hi in ranges(stubs, gap=3):
        out.append(finding('stub', (lo, hi),
                           'The change adds a stub at %s: code that exists but does not do its job yet.' % where(f, lo, hi),
                           'Remove the stub and anything that only exists to call it, or implement it. Done when no function added by this change is unimplemented.',
                           0.75))
    return out


# ---------------------------------------------------------------- commented-out code


COMMENT_LEAD = re.compile(r'^\s*(?://+|#+|/\*+|\*+(?!/))([ \t]*)')
EXAMPLE_INTRO = re.compile(r'(@example|\bexamples?\b|\busage\b|\be\.g\.|```|:\s*$)', re.I)


def _comment_indent(line):
    m = COMMENT_LEAD.match(line)
    return len(m.group(1).expandtabs(4)) if m else 0


def is_doc_example(f, n):
    """A code-like comment line that is an example inside documentation: the comment block around
    it has prose, and the code sits deeper than the prose or follows an introduction (Example:,
    @example, a line ending in a colon, a fence)."""
    def in_block(k):
        return not f.code[k - 1].strip() and bool(f.comments[k - 1] or COMMENT_LEAD.match(f.lines[k - 1]))
    lo = n
    while lo > 1 and in_block(lo - 1):
        lo -= 1
    hi = n
    while hi < len(f.code) and in_block(hi + 1):
        hi += 1
    prose = [k for k in range(lo, hi + 1) if f.comments[k - 1] and not lexer.looks_like_code(f.comments[k - 1])
             and len(re.findall(r'[A-Za-z]{2,}', f.comments[k - 1])) >= 3]
    if not prose:
        return False
    if any(EXAMPLE_INTRO.search(f.comments[k - 1]) for k in prose if k < n):
        return True
    return _comment_indent(f.lines[n - 1]) > min(_comment_indent(f.lines[k - 1]) for k in prose)


ONE_LINE_CODE = re.compile(r'[;{}]\s*$|\)\s*$|^\s*[A-Za-z_][\w.:\[\]]*\s*(=|:=|\+=|-=)\s*\S')


def commented_out_code(f, ctx):
    rows = []
    for n in sorted(f.new):
        if n > len(f.code):
            continue
        if f.code[n - 1].strip():
            continue
        c = f.comments[n - 1]
        if c and lexer.looks_like_code(c) and not is_doc_example(f, n):
            rows.append(n)
    out = []
    for lo, hi in ranges(rows, gap=1):
        k = sum(1 for n in rows if lo <= n <= hi)
        if k < 2 and not ONE_LINE_CODE.search(f.comments[lo - 1]):
            continue
        out.append(finding('commented_out_code', (lo, hi),
                           'The change adds %d line%s of commented-out code at %s.' % (k, '' if k == 1 else 's', where(f, lo, hi)),
                           'Delete the commented-out code; version control keeps the old version. Done when the added comments are prose, not code.',
                           0.7 if k >= 2 else 0.5))
    return out


# ---------------------------------------------------------------- copies

W = 5          # significant lines per window
MIN_CONTENT = 22
SHINGLE = 5
TRIVIAL = {'{', '}', '(', ')', ';', ',', 'end', ')', '};', '});', '})'}


def norm_lines(code_lines, language):
    """(line number, normalized tokens, content-token count) for each line that carries code."""
    out = []
    for n, code in enumerate(code_lines, 1):
        toks = lexer.tokens(code, language)
        content = [t for t in toks if t not in TRIVIAL]
        if len(content) < 2:
            continue
        out.append((n, tuple(toks), len(content)))
    return out


def windows(rows):
    for k in range(0, max(0, len(rows) - W + 1)):
        win = rows[k:k + W]
        if sum(r[2] for r in win) < MIN_CONTENT:
            continue
        key = hashlib.blake2b(repr([r[1] for r in win]).encode(), digest_size=10).digest()
        yield key, win[0][0], win[-1][0]


class CloneIndex:
    """Window fingerprints of every file the change touches plus the unchanged files next to them."""

    def __init__(self):
        self.where = collections.defaultdict(list)  # key -> [(source, path, lo, hi)]
        self.unit_shingles = []  # (source, path, unit, shingles, tokens)
        self.seen = set()

    def add(self, source, path, text, language, unit_list=None, code_lines=None):
        if (source, path) in self.seen:
            return
        self.seen.add((source, path))
        code_lines = code_lines if code_lines is not None else [s[0] for s in lexer.scan(text, language)]
        rows = norm_lines(code_lines, language)
        for key, lo, hi in windows(rows):
            self.where[key].append((source, path, lo, hi))
        for u in unit_list if unit_list is not None else unitmod.find_units(text, language):
            toks = []
            for n in range(u.start + 1, u.end + 1):
                if n <= len(code_lines):
                    toks.extend(lexer.tokens(code_lines[n - 1], language))
            if len(toks) < 40:
                continue
            sh = set(tuple(toks[i:i + SHINGLE]) for i in range(len(toks) - SHINGLE + 1))
            self.unit_shingles.append((source, path, u, sh, toks))


def build_index(f, ctx):
    idx = getattr(ctx, '_clone_index', None)
    if idx is None:
        idx = CloneIndex()
        for g in ctx.files:
            idx.add('head', g.path, g.head, g.language, g.units, g.code)
        ctx._clone_index = idx
    if f.language not in getattr(ctx, '_indexed_dirs', {}).get(f.path.rsplit('/', 1)[0] if '/' in f.path else '', set()):
        changed = set(ctx.by_path)
        removed_paths = {c.path for c in ctx.all_changes if getattr(c, 'status', '') == 'D'} | {c.old_path for c in ctx.all_changes if getattr(c, 'old_path', None)}
        for path, text in ctx.base_neighbours(f):
            if path in changed or path in removed_paths:
                continue
            idx.add('base', path, text, f.language)
        for path, text in ctx.copy_sources(f):
            idx.add('base', path, text, f.language)
        d = f.path.rsplit('/', 1)[0] if '/' in f.path else ''
        ctx.__dict__.setdefault('_indexed_dirs', {}).setdefault(d, set()).add(f.language)
    return idx


def raw_differences(f, lo, hi, other_text_lines, olo, ohi, language):
    a = []
    for n in range(lo, hi + 1):
        a += re.findall(r'[A-Za-z_]\w*', f.code[n - 1])
    b = []
    for n in range(olo, ohi + 1):
        if n <= len(other_text_lines):
            b += re.findall(r'[A-Za-z_]\w*', other_text_lines[n - 1])
    pairs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'replace' and i2 - i1 == 1 and j2 - j1 == 1 and (a[i1], b[j1]) not in pairs:
            pairs.append((a[i1], b[j1]))
    kw = lexer.KEYWORDS.get(language, set())
    return [p for p in pairs if p[0] not in kw and p[1] not in kw][:3]


def near_copy(f, ctx):
    idx = build_index(f, ctx)
    rows = norm_lines(f.code, f.language)
    hits = []  # (lo, hi, source, path, olo, ohi)
    for key, lo, hi in windows(rows):
        span = [n for n in range(lo, hi + 1) if f.code[n - 1].strip()]
        if sum(1 for n in span if n in f.added) < 0.6 * len(span):
            continue
        for src, path, olo, ohi in idx.where.get(key, ()):
            if src == 'head' and path == f.path and not (ohi < lo or olo > hi):
                continue
            hits.append((lo, hi, src, path, olo, ohi))
    out = []
    # fold window hits into ranges per counterpart
    by_other = collections.defaultdict(list)
    for lo, hi, src, path, olo, ohi in hits:
        by_other[(src, path)].append((lo, hi, olo, ohi))
    reported = []
    for (src, path), hs in sorted(by_other.items()):
        hs.sort()
        groups = []
        for lo, hi, olo, ohi in hs:
            if groups and lo <= groups[-1][1] + 1 and (olo <= groups[-1][3] + 1 and ohi >= groups[-1][2]):
                g = groups[-1]
                groups[-1] = (g[0], max(g[1], hi), min(g[2], olo), max(g[3], ohi))
            else:
                groups.append((lo, hi, olo, ohi))
        for lo, hi, olo, ohi in groups:
            if any(not (hi < r[0] or lo > r[1]) for r in reported):
                continue
            # two copies the change itself added in the same file: report once, at the later copy
            if src == 'head' and path == f.path and olo > lo and sum(1 for k in range(olo, ohi + 1) if k in f.added) >= 0.6 * (ohi - olo + 1):
                continue
            # two new copies in two changed files: report once, in the file whose path sorts later
            if src == 'head' and path != f.path and path > f.path and path in ctx.by_path and \
                    sum(1 for k in range(olo, ohi + 1) if k in ctx.by_path[path].added) >= 0.6 * (ohi - olo + 1):
                continue
            other_lines = (ctx.by_path[path].code if src == 'head' else [s[0] for s in lexer.scan(ctx.fetched.get(path, ''), f.language)])
            diffs = raw_differences(f, lo, hi, other_lines, olo, ohi, f.language)
            n = hi - lo + 1
            if src == 'head' and path == f.path:
                rel = 'lines %d-%d of this file' % (olo, ohi)
            else:
                rel = 'lines %d-%d of %s%s' % (olo, ohi, path, '' if src == 'head' else ' (unchanged by this pull request)')
            name = unit_label(f, lo)
            other_f = ctx.by_path.get(path) if src == 'head' else None
            oname = unit_label(other_f, olo) if other_f else None
            changed = ('; the differences are %s' % ', '.join('%s for %s' % (x, y) for x, y in diffs)) if diffs else ' almost token for token'
            msg = 'Lines %d-%d%s repeat %s%s%s.' % (lo, hi, ' (in %s)' % name if name else '', rel, ' (%s)' % oname if oname else '', changed)
            fix = ('Write the shared steps once: extract them into one function (or reuse %s) that takes what differs as a parameter, '
                   'and call it from both places. Done when the repeated lines exist only once.' % (oname or 'the existing code'))
            conf = min(0.95, 0.6 + 0.03 * n)
            out.append(finding('near_copy', (lo, hi), msg, fix, conf, [{'path': path, 'lines': [olo, ohi]}]))
            reported.append((lo, hi))
    # near-miss copies, unit against unit: catch copies with lines inserted or reworded
    new = [u for u in f.new_units() if u.length >= 5]
    for u in new:
        if any(not (u.end < r[0] or u.start > r[1]) for r in reported):
            continue
        toks = []
        for n in range(u.start + 1, u.end + 1):
            toks.extend(lexer.tokens(f.code[n - 1], f.language))
        if len(toks) < 40:
            continue
        sh = set(tuple(toks[i:i + SHINGLE]) for i in range(len(toks) - SHINGLE + 1))
        best = None
        for src, path, ou, osh, otoks in idx.unit_shingles:
            if src == 'head' and path == f.path and ou.start == u.start:
                continue
            if src == 'head' and path == f.path and not (ou.end < u.start or ou.start > u.end):
                continue
            if not (0.5 <= len(otoks) / len(toks) <= 2.0):
                continue
            inter = len(sh & osh)
            if not inter:
                continue
            jac = inter / len(sh | osh)
            cont = inter / min(len(sh), len(osh))
            score = max(jac, 0.85 * cont if min(len(sh), len(osh)) >= 60 else 0)
            if score >= 0.55 and (best is None or score > best[0]):
                best = (score, src, path, ou)
        if not best:
            continue
        score, src, path, ou = best
        # the copy is the later one when both are new in this file
        if src == 'head' and path == f.path and ou.start > u.start and ou in f.new_units():
            continue
        if src == 'head' and path != f.path and path > f.path and path in ctx.by_path and ou in ctx.by_path[path].new_units():
            continue
        rel = ('%s at lines %d-%d of this file' % (ou.name, ou.start, ou.end) if (src == 'head' and path == f.path)
               else '%s at lines %d-%d of %s' % (ou.name, ou.start, ou.end, path))
        out.append(finding('near_copy', (u.start, u.end),
                           '%s (lines %d-%d) is a near copy of %s: %d%% of its token sequence is shared.' % (u.name, u.start, u.end, rel, round(100 * score)),
                           'Merge the two: keep one function, pass in what differs, and delete the other. Done when the logic is written once.',
                           min(0.9, 0.35 + score * 0.6), [{'path': path, 'lines': [ou.start, ou.end]}]))
        reported.append((u.start, u.end))
    return out


# ---------------------------------------------------------------- verbose or redundant constructs

VERBOSE = {
    'Go': [
        (re.compile(r'\bif\s+(\w+)\s*==\s*nil\s*&&\s*len\(\s*\1\s*\)\s*==\s*0'), 'checks a slice for nil and for zero length; len of a nil slice is already 0', 'Check len(x) == 0 alone.'),
        (re.compile(r'\bif\s+len\(\s*(\w+)\s*\)\s*(>\s*0|!=\s*0)\s*\{\s*$'), None, None),  # resolved below: only before a range over it
        (re.compile(r'==\s*true\b|==\s*false\b|!=\s*true\b|!=\s*false\b'), 'compares a boolean to true or false', 'Use the boolean (or its negation) directly.'),
    ],
    'Python': [
        (re.compile(r'(==|!=|\bis\b|\bis not\b)\s*(True|False)\b'), 'compares to True or False', 'Use the value (or `not value`) directly.'),
        (re.compile(r'\bin\s+\w+(\.\w+)*\.keys\(\)'), 'tests membership in .keys()', 'Test membership in the dict itself.'),
        (re.compile(r'\bfor\s+\w+\s+in\s+range\(\s*len\('), 'loops over range(len(...))', 'Loop over the items (with enumerate if the index is needed).'),
        (re.compile(r'\.get\(\s*[^,()]+,\s*None\s*\)'), 'passes None as the default to .get()', 'Drop the default; .get() already returns None.'),
        (re.compile(r'\bexcept\s*(\w+\s*)?:\s*pass\b|\bexcept\s+Exception\s*(as\s+\w+)?\s*:\s*$'), 'catches every exception', 'Catch only the exception you expect, or let it propagate.'),
        (re.compile(r'\bis\s+not\s+None\s+and\s+len\('), 'checks for None and for length', 'Test the value\'s truthiness once.'),
    ],
    'TypeScript': [
        (re.compile(r'===?\s*true\b|===?\s*false\b|!==?\s*true\b'), 'compares a boolean to true or false', 'Use the boolean directly.'),
        (re.compile(r'(\w+)\s*!==\s*undefined\s*&&\s*\1\s*!==\s*null|(\w+)\s*!==\s*null\s*&&\s*\2\s*!==\s*undefined'), 'checks for null and undefined separately', 'Use `x != null` or optional chaining.'),
        (re.compile(r'\bcatch\s*\(\s*\w*\s*\)\s*\{\s*\}'), 'swallows an exception with an empty catch', 'Let the error propagate, or handle it.'),
        (re.compile(r'\breturn\s+await\b'), 'returns an awaited value from an async function', 'Return the promise directly unless inside try/catch.'),
    ],
    'Ruby': [
        (re.compile(r'==\s*true\b|==\s*false\b'), 'compares to true or false', 'Use the value directly.'),
        (re.compile(r'\.nil\?\s*==\s*false|!\s*\w+\.nil\?\s*&&\s*!\s*\w+\.empty\?'), 'spells out a presence check', 'Use `present?`/`any?` or the value\'s truthiness.'),
        (re.compile(r'\brescue\s+Exception\b'), 'rescues Exception', 'Rescue the specific error (StandardError at most).'),
    ],
    'Java': [
        (re.compile(r'==\s*true\b|==\s*false\b|!=\s*true\b'), 'compares a boolean to true or false', 'Use the boolean directly.'),
        (re.compile(r'catch\s*\([^)]*\)\s*\{\s*\}'), 'swallows an exception with an empty catch', 'Let the exception propagate, or handle it.'),
    ],
}
VERBOSE['JavaScript'] = VERBOSE['TypeScript']

BOOL_RETURN = {
    'Python': re.compile(r'^\s*if\s+(.+):\s*\n\s*return\s+(True|False)\s*\n\s*(else:\s*\n\s*)?return\s+(True|False)\s*$', re.M),
    'Go': re.compile(r'if\s+(.+)\s*\{\s*\n\s*return\s+(true|false)\s*\n\s*\}\s*(else\s*\{\s*)?\n\s*return\s+(true|false)', re.M),
    'TypeScript': re.compile(r'if\s*\((.+)\)\s*\{?\s*\n\s*return\s+(true|false);?\s*\n\s*\}?\s*(else\s*\{?\s*)?\n?\s*return\s+(true|false);?', re.M),
    'Java': re.compile(r'if\s*\((.+)\)\s*\{?\s*\n\s*return\s+(true|false);\s*\n\s*\}?\s*(else\s*\{?\s*)?\n?\s*return\s+(true|false);', re.M),
    'Ruby': re.compile(r'if\s+(.+)\s*\n\s*(?:return\s+)?(true|false)\s*\n\s*else\s*\n\s*(?:return\s+)?(true|false)\s*\n\s*end', re.M),
}
BOOL_RETURN['JavaScript'] = BOOL_RETURN['TypeScript']

GO_ERR_TAIL = re.compile(r'if\s+err\s*!=\s*nil\s*\{\s*\n\s*return\s+err\s*\n\s*\}\s*\n\s*return\s+nil\s*$', re.M)


def verbose(f, ctx):
    rows = []
    for n in f.added_code_lines():
        code = f.code[n - 1]
        for rx, what, fix in VERBOSE.get(f.language, []):
            m = rx.search(code)
            if not m or what is None:
                if m and what is None and f.language == 'Go':
                    # `if len(x) > 0 {` directly around `for ... range x`
                    nxt = f.code[n] if n < len(f.code) else ''
                    if re.search(r'\brange\s+' + re.escape(m.group(1)) + r'\b', nxt):
                        rows.append((n, 'checks the length of %s before ranging over it; a range over an empty slice does nothing' % 'a slice', 'Drop the length check.'))
                continue
            rows.append((n, what, fix))
    masked = '\n'.join(f.code)
    rx = BOOL_RETURN.get(f.language)
    starts = [0]
    for line in f.code:
        starts.append(starts[-1] + len(line) + 1)
    import bisect
    for rxx, what, fix in ((rx, 'returns true or false from an if/else on a condition', 'Return the condition itself.'),
                           (GO_ERR_TAIL if f.language == 'Go' else None, 'checks err only to return it and then return nil', 'Return the call\'s error directly.')):
        if rxx is None:
            continue
        for m in rxx.finditer(masked):
            n = bisect.bisect_right(starts, m.start())
            if n in f.added:
                rows.append((n, what, fix))
    out = []
    seen = set()
    for n, what, fix in rows:
        if n in seen:
            continue
        seen.add(n)
        out.append(finding('verbose_construct', (n, n), 'Line %d %s.' % (n, what), fix + ' Done when the line says it directly.', 0.45))
    return out


# ---------------------------------------------------------------- functions nobody calls; wrappers that only forward

EXPORTED = {
    'Go': lambda name, line: name[:1].isupper(),
    'Python': lambda name, line: not name.startswith('_') and not line.startswith((' ', '\t')),
    'TypeScript': lambda name, line: bool(re.search(r'\bexport\b|\bpublic\b', line)) or not line.startswith((' ', '\t')) and 'export' in line,
    'JavaScript': lambda name, line: bool(re.search(r'\bexport\b|module\.exports|exports\.', line)),
    'Ruby': lambda name, line: True,
    'Java': lambda name, line: not re.search(r'\bprivate\b', line),
}
SPECIAL_NAMES = re.compile(r'^(main|init|setUp|tearDown|constructor|render|toString|equals|hashCode|__\w+__|Test\w*|Benchmark\w*|Example\w*|Fuzz\w*|String|Error|Len|Less|Swap|ServeHTTP|MarshalJSON|UnmarshalJSON|initialize|to_s|call|each|run|handle|execute|apply|get|set|ngOn\w+|componentDid\w+)$')


def unused_new(f, ctx, limit=6):
    out = []
    checked = 0
    other_heads = '\n'.join(g.head for g in ctx.files if g.path != f.path)
    for u in f.new_units():
        if checked >= limit:
            break
        name = u.name
        header = f.lines[u.start - 1]
        if len(name) < 3 or SPECIAL_NAMES.match(name):
            continue
        if EXPORTED.get(f.language, lambda *a: True)(name, header):
            continue
        if f.language == 'Python' and re.search(r'^\s+def\s', header) and not name.startswith('_'):
            continue  # a public method: something outside the change may call it
        if re.search(r'^\s*@', f.lines[u.start - 2] if u.start >= 2 else ''):
            continue  # decorated: registered somewhere
        rx = re.compile(r'(?<![\w$])' + re.escape(name) + r'(?![\w$])')
        uses_here = sum(1 for n, code in enumerate(f.code, 1) if not (u.start <= n <= u.end) and rx.search(code))
        uses_here += sum(1 for s in f.strings for x in s if name in x)
        if uses_here or rx.search(other_heads):
            continue
        checked += 1
        if ctx.referenced_in_base(name):
            continue
        out.append(finding('unused_code', (u.start, u.end),
                           '%s (lines %d-%d) is new and nothing in the repository calls it.' % (name, u.start, u.end),
                           'Delete %s, or call it where it is needed. Done when every function the change adds is used.' % name,
                           0.7))
    return out


def pass_through(f, ctx):
    out = []
    for u in f.new_units():
        body = [n for n in range(u.start + 1, u.end + 1) if f.code[n - 1].strip() and f.code[n - 1].strip() not in ('}', 'end', '};')]
        if len(body) != 1 or u.length > 6:
            continue
        line = f.code[body[0] - 1].strip()
        m = re.match(r'^(?:return\s+)?(?:await\s+)?([\w.]+)\((.*)\)\s*;?$', line)
        if not m:
            continue
        header = f.code[u.start - 1]
        params = re.findall(r'[A-Za-z_]\w*', header.split('(', 1)[1].split(')', 1)[0]) if '(' in header else []
        args = [a.strip() for a in m.group(2).split(',') if a.strip()]
        if not args or not all(re.fullmatch(r'[A-Za-z_]\w*', a) and a in params for a in args):
            continue
        callee = m.group(1)
        if callee.split('.')[-1] == u.name:
            continue
        out.append(finding('pass_through', (u.start, u.end),
                           '%s (lines %d-%d) only forwards its arguments to %s.' % (u.name, u.start, u.end, callee),
                           'Call %s directly and delete the wrapper, unless the wrapper is a public API that must exist. Done when the forwarding function is gone.' % callee,
                           0.5))
    return out


# ---------------------------------------------------------------- size and shape

LONG = {'Go': 70, 'Java': 60, 'TypeScript': 60, 'JavaScript': 60, 'Python': 50, 'Ruby': 35}


def nesting(f, lo, hi):
    if f.language in ('Python', 'Ruby'):
        base = None
        deepest = 0
        for n in range(lo, hi + 1):
            line = f.code[n - 1]
            if not line.strip():
                continue
            ind = len(line.expandtabs(4)) - len(line.expandtabs(4).lstrip())
            if base is None:
                base = ind
            deepest = max(deepest, (ind - base) // (4 if f.language == 'Python' else 2))
        return deepest
    depth = deepest = 0
    for n in range(lo, hi + 1):
        for ch in f.code[n - 1]:
            if ch == '{':
                depth += 1
                deepest = max(deepest, depth)
            elif ch == '}':
                depth -= 1
    return max(0, deepest - 1)


def unit_size(f, ctx):
    out = []
    for u, k in f.changed_units(min_added=5):
        added_share = k / max(1, sum(1 for n in range(u.start, u.end + 1) if f.code[n - 1].strip()))
        sig = sum(1 for n in range(u.start, u.end + 1) if f.code[n - 1].strip())
        limit = LONG.get(f.language, 60)
        depth = nesting(f, u.start, u.end)
        if sig > limit and added_share >= 0.5:
            out.append(finding('long_function', (u.start, u.end),
                               '%s (lines %d-%d) has %d lines of code, most of them added by this change.' % (u.name, u.start, u.end, sig),
                               'Split %s at its natural steps into smaller functions with clear names, or remove steps it does not need. Done when no added function is over %d lines.' % (u.name, limit),
                               min(0.8, 0.35 + (sig - limit) / (2.0 * limit))))
        if depth >= 5 and added_share >= 0.3:
            out.append(finding('deep_nesting', (u.start, u.end),
                               '%s (lines %d-%d) nests %d levels deep.' % (u.name, u.start, u.end, depth),
                               'Flatten it: return early on the error and edge cases, and move inner loops into helpers. Done when the deepest block is at most 3 levels in.',
                               0.5))
    return out


# ---------------------------------------------------------------- narrating comments


NARRATE = re.compile(r'^\s*(added|adding|updated?|changed?|new:|modified|fixed|now (we|it|this)|as (requested|per)|this (function|method|code) (now|was)|here we|we now|the following|previously|refactored|moved (this|from)|replaced|removed the|this is the (new|updated))\b', re.I)


def narration(f, ctx):
    rows = [n for n in sorted(f.added) if n <= len(f.comments) and f.comments[n - 1] and NARRATE.search(f.comments[n - 1])]
    out = []
    for lo, hi in ranges(rows, gap=3):
        out.append(finding('change_narration', (lo, hi),
                           'The comment at %s describes the edit rather than the code; it will be wrong after the next change.' % where(f, lo, hi),
                           'Delete the comment, or rewrite it to say why the code is the way it is. Done when no added comment narrates the change.',
                           0.35))
    return out


DEFENSIVE = re.compile(r"(can'?t|cannot|should ?n[o']t|never|won'?t) (happen|occur|be (nil|null|none|empty))|just in case|to be safe|be safe|defensive|already (validated|checked)|validated (in|by|elsewhere|earlier|above)|paranoi|sanity check|shouldn'?t be possible|impossible", re.I)
GUARD_OPEN = re.compile(r'^\s*(if|unless|elif|else if|} else if)\b')


def defensive_guard(f, ctx):
    out = []
    for n in f.added_code_lines():
        if not GUARD_OPEN.match(f.code[n - 1]):
            continue
        near = ' '.join(f.comments[k - 1] for k in range(max(1, n - 1), min(len(f.comments), n + 3) + 1))
        if DEFENSIVE.search(near):
            end = n
            for k in range(n + 1, min(len(f.code), n + 6) + 1):
                end = k
                if f.code[k - 1].strip() in ('}', 'end') or (f.language == 'Python' and k > n + 1 and f.code[k - 1].strip() and
                        len(f.code[k - 1]) - len(f.code[k - 1].lstrip()) <= len(f.code[n - 1]) - len(f.code[n - 1].lstrip())):
                    break
            out.append(finding('defensive_guard', (n, end),
                               'The check at %s guards a case its own comment says cannot happen.' % where(f, n, end),
                               'Delete the check, and rely on the place that already guarantees the invariant. If the case can really happen, handle it as an error instead. Done when the guard is gone or justified by a real path.',
                               0.65))
    return out


ALL = [debug_output, defensive_guard, placeholder, commented_out_code, near_copy, verbose, unused_new, pass_through, unit_size, narration]


def run_all(f, ctx, skip=()):
    found = []
    for det in ALL:
        if det.__name__ in skip:
            continue
        try:
            found.extend(det(f, ctx))
        except RecursionError:
            continue
    found.sort(key=lambda d: (-d['confidence'], d['lines'][0]))
    return found
