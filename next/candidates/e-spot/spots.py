"""Spots: the parts of a changed file that e-spot asks Jev about, and what Jev is shown of each.

A spot is a changed top-level unit (a function or method with at least MIN_ADDED added code lines)
or the file's module-level code (added code outside every unit). A spot too long to show whole is
cut into parts, each showing the unit's first line and its own share of the added lines. A file
gets at most MAX_SPOTS spots, the ones with the most added code; what is left out is reported in
the signals. A file with added code but no spot that reaches MIN_ADDED still gets its largest one.

What Jev sees of a spot is masked: every comment becomes a placeholder, and every string literal
of six or more words (or spanning lines) becomes a placeholder too, so text in the change cannot
talk to the judge. Short strings stay, because they are often code (keys, formats, names).
"""
import re

from gatecore import lexer, units as unitmod

import questions

MIN_ADDED = 3        # added code lines that make a unit worth a request
MAX_SPOTS = 8        # requests per file
MAX_LATER = 150      # rows of the unit after the change shown in one request
MAX_EARLIER = 80     # rows of the unit before the change
MAX_SIMILAR = 40     # rows of the most similar other unit
MAX_OUTLINE = 40     # entries in the outline
CONTEXT = 4          # unchanged lines kept around a change when a unit is too long to show whole
MODULE_CONTEXT = 3   # unchanged lines kept around module-level changes
MAX_LINE_CHARS = 200
WORDS_TO_MASK = 6
SIMILAR_MIN = 0.5    # share of the spot's added token 5-grams found in another unit
SHINGLE = 5

COMMENT_PLACEHOLDER = '[comment]'
STRING_PLACEHOLDER = '[text]'
WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
QUOTES = re.compile(r'""|\'\'')
SAFE_NAME = re.compile(r'[^\w$.?!=]')


class Spot:
    """kind is 'unit' or 'module'; start-end is the unit (or the module-level region); added holds
    the spot's added code lines; part is (k, n) when a long unit was cut into n parts."""
    __slots__ = ('kind', 'name', 'start', 'end', 'added', 'part')

    def __init__(self, kind, name, start, end, added, part=None):
        self.kind, self.name, self.start, self.end, self.added, self.part = kind, name, start, end, added, part

    @property
    def n_added(self):
        return len(self.added)

    @property
    def label(self):
        return 'module-level code' if self.kind == 'module' else safe_name(self.name)

    def __repr__(self):
        part = '' if not self.part else ' part %d/%d' % self.part
        return 'Spot(%s %s %d-%d +%d%s)' % (self.kind, self.name, self.start, self.end, self.n_added, part)


def safe_name(name):
    return SAFE_NAME.sub('', name or '')[:60] or 'unit'


# ------------------------------------------------------------------ units (with core workarounds)

def _reindent(lines, scanned):
    """The core lexer drops the indentation of a line that closes a multi-line string (it is part
    of the string), so a Python function with a multi-line docstring ends at its `def` line.
    Giving such lines back their raw indentation lets the core's unit finder see the whole body."""
    out = []
    for raw, row in zip(lines, scanned):
        code = row[0]
        if code.strip():
            ri, ci = len(raw) - len(raw.lstrip()), len(code) - len(code.lstrip())
            if ci < ri:
                code = raw[:ri] + code.lstrip()
        out.append((code,) + tuple(row[1:]))
    return out


# Headers the core's patterns miss: a method or arrow function whose parameter list opens at the end
# of the line and continues on the next ones.
JS_LONG = [re.compile(r'^\s+(?:(?:public|private|protected|static|readonly|async|override|abstract|get|set)\s+)*\*?([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\($'),
           re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:<[^>]*>\s*)?\($')]


def _header_name(line, language):
    """The name a function header on this (masked) line declares, read as the core's find_units reads it."""
    if language == 'Go':
        m = unitmod.GO_FUNC.match(line)
        return m and m.group(2)
    if language in ('TypeScript', 'JavaScript'):
        for rx in unitmod.JS_FUNCS + JS_LONG:
            m = rx.match(line)
            if m and m.group(1) not in unitmod.JS_NOT:
                return m.group(1)
        return None
    m = unitmod.JAVA_METHOD.match(line)
    if m and m.group(1) not in unitmod.JAVA_NOT and not re.match(r'^\s*(return|new|else|throw)\b', line) and '=' not in line.split('(')[0]:
        return m.group(1)
    return None


def _body(masked, k, limit=30):
    """(line index where the body's brace opens, line index where it closes) for a function whose
    header starts at line k, reading past a parameter list and return type that run over several
    lines; None when no body follows."""
    depth, opened = 0, False
    for j in range(k, min(len(masked), k + limit)):
        text, i = masked[j], 0
        while i < len(text):
            if text.startswith(('=>', '->'), i):
                i += 2
                continue
            ch = text[i]
            if ch in '([<':
                depth += 1
                opened = opened or ch == '('
            elif ch in ')]>':
                depth = max(0, depth - 1)
            elif ch == ';' and depth == 0:
                return None
            elif ch == '{' and depth == 0:
                if not opened:
                    return None
                end = _close_brace(masked, j, i)
                return None if end is None else (j, end)
            i += 1
        if opened and depth == 0:
            return None  # the parameter list closed and no body began on its line: a call, not a declaration
    return None


def _close_brace(masked, j, i, limit=4000):
    depth = 0
    for r in range(j, min(len(masked), j + limit)):
        for ch in masked[r][i if r == j else 0:]:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return r
    return None


def _brace_units(text, language, scanned):
    """The core's units, corrected where the core closed a unit on a brace inside its parameter
    list (a destructured parameter), plus functions with long multi-line signatures the core gives
    up on."""
    masked = [s[0] for s in scanned]
    found = []
    for u in unitmod.find_units(text, language, scanned):
        b = _body(masked, u.start - 1)
        if b is not None and u.end - 1 <= b[0] < b[1]:
            # the core closed the unit before (or on the line where) its body opens
            u = unitmod.Unit(u.name, u.start, b[1] + 1, u.kind)
        found.append(u)
    covered = set()
    for u in found:
        covered.update(range(u.start, u.end + 1))
    for k, line in enumerate(masked):
        if k + 1 in covered:
            continue
        name = _header_name(line, language)
        if not name:
            continue
        b = _body(masked, k)
        if b is not None and b[1] > k:
            found.append(unitmod.Unit(name, k + 1, b[1] + 1))
            covered.update(range(k + 1, b[1] + 2))
    return unitmod.outermost(found)


def _units(text, lines, scanned, language):
    if language == 'Python':
        return unitmod.outermost(unitmod.find_units(text, 'Python', _reindent(lines, scanned)))
    if language == 'Ruby':
        return unitmod.outermost(unitmod.find_units(text, 'Ruby', scanned))
    return _brace_units(text, language, scanned)


def top_units(f):
    """The head file's outermost units."""
    got = getattr(f, '_espot_top', None)
    if got is None:
        got = _units(f.head, f.lines, f.scan, f.language)
        f._espot_top = got
    return got


def base_top_units(f):
    """The base file's outermost units."""
    got = getattr(f, '_espot_base_top', None)
    if got is None:
        got = [] if f.base is None else _units(f.base, f.diff.base_lines, f.base_scan, f.language)
        f._espot_base_top = got
    return got


def _inside(units_):
    rows = set()
    for u in units_:
        rows.update(range(u.start, u.end + 1))
    return rows


# ------------------------------------------------------------------ choosing spots

def candidates(f):
    """Every unit with added code, plus the module-level pseudo-unit, with their added code lines."""
    code_added = f.added_code_lines()
    out = []
    for u in top_units(f):
        added = [n for n in code_added if u.start <= n <= u.end]
        if added:
            out.append(Spot('unit', u.name, u.start, u.end, added))
    inside = _inside(top_units(f))
    module = [n for n in code_added if n not in inside]
    if module:
        out.append(Spot('module', 'module', module[0], module[-1], module))
    return out


def head_rows(f, spot, limit=None):
    """The head rows a spot shows: the whole unit when it fits, else its first line and the added
    lines with some context. Module-level code shows its added lines with context."""
    limit = MAX_LATER if limit is None else limit
    if spot.kind == 'module':
        inside = _inside(top_units(f))
        rows = set()
        for n in spot.added:
            rows.update(range(max(1, n - MODULE_CONTEXT), min(len(f.lines), n + MODULE_CONTEXT) + 1))
        return sorted(r for r in rows if r not in inside)[:limit]
    if not spot.part:
        return windows(spot.start, spot.end, spot.added, limit)
    return windows(spot.start, spot.end, spot.added, limit, whole=False)


def split(f, spot):
    """A spot whose rows do not fit one request, cut into parts by its added lines."""
    if len(head_rows(f, spot, limit=10 ** 9)) <= MAX_LATER:
        return [spot]
    ctx = MODULE_CONTEXT if spot.kind == 'module' else CONTEXT
    room = MAX_LATER - 2 * ctx - 2
    groups, cur = [], []
    for n in spot.added:
        if cur and n - cur[0] > room:
            groups.append(cur)
            cur = []
        cur.append(n)
    groups.append(cur)
    if len(groups) == 1:
        return [spot]
    return [Spot(spot.kind, spot.name, spot.start, spot.end, g, (k + 1, len(groups))) for k, g in enumerate(groups)]


def select(f, min_added=MIN_ADDED, max_spots=MAX_SPOTS):
    """(spots asked about, spots left out)."""
    cands = candidates(f)
    big = [s for s in cands if s.n_added >= min_added]
    if not big and cands:
        big = [max(cands, key=lambda s: (s.n_added, -s.start))]
    small = [s for s in cands if s not in big]
    parts = [p for s in big for p in split(f, s)]
    parts.sort(key=lambda s: (-s.n_added, s.added[0]))
    kept, over = parts[:max_spots], parts[max_spots:]
    kept.sort(key=lambda s: s.added[0])
    return kept, over + small


# ------------------------------------------------------------------ masking

def comment_marker(language):
    return '#' if language in lexer.HASH_COMMENT else '//'


def mask_string(s):
    if '\n' in s or len(WORD.findall(s)) >= WORDS_TO_MASK:
        return STRING_PLACEHOLDER
    return s


def mask_line(raw, scanned, language):
    """One source line with comments and long strings replaced by placeholders."""
    code, comment, strings = scanned
    quotes = list(QUOTES.finditer(code))
    if len(quotes) == len(strings):
        parts, pos = [], 0
        for m, s in zip(quotes, strings):
            q = m.group(0)[0]
            parts.append(code[pos:m.start()])
            parts.append(q + mask_string(s) + q)
            pos = m.end()
        parts.append(code[pos:])
        text = ''.join(parts)
    else:
        text = code  # strings stay blanked when the line cannot be lined up with them
    text = text.rstrip()
    indent = raw[:len(raw) - len(raw.lstrip())]
    if text.strip() and len(text) - len(text.lstrip()) < len(indent):
        text = indent + text.lstrip()  # a line closing a multi-line string keeps its indentation
    if comment:
        mark = comment_marker(language) + ' ' + COMMENT_PLACEHOLDER
        text = text + '  ' + mark if text.strip() else indent + mark
    elif not text.strip() and raw.strip():
        # a line inside a multi-line string (or a comment line the lexer left empty)
        text = indent + STRING_PLACEHOLDER
    if len(text) > MAX_LINE_CHARS:
        text = text[:MAX_LINE_CHARS] + ' …'
    return text


def is_placeholder_only(text, language):
    return text.strip() in ('', comment_marker(language) + ' ' + COMMENT_PLACEHOLDER, STRING_PLACEHOLDER)


def render(lines, scanned, rows, marks, mark_char, language):
    """Numbered, masked lines. Runs of comment-only, string-only or blank lines collapse into their
    first line; a gap in the rows shown is marked with an ellipsis."""
    out = []
    prev = None
    prev_text = None
    for n in rows:
        if n < 1 or n > len(lines):
            continue
        text = mask_line(lines[n - 1], scanned[n - 1], language)
        if prev is not None and n > prev + 1:
            out.append('    …')
            prev_text = None
        elif prev is not None and prev_text is not None and is_placeholder_only(text, language) and (
                text.strip() == prev_text.strip() or (not text.strip() and is_placeholder_only(prev_text, language))):
            prev = n  # a repeat of the placeholder above, or a blank line after one
            continue
        out.append(('%5d %s %s' % (n, mark_char if n in marks else ' ', text)).rstrip())
        prev, prev_text = n, text
    return '\n'.join(out)


def windows(lo, hi, focus, limit, context=CONTEXT, whole=True):
    """The rows of lo..hi to show: all of them when they fit (and `whole`), else the first line and
    the focus lines with some context, cut at `limit` rows."""
    if whole and hi - lo + 1 <= limit:
        return list(range(lo, hi + 1))
    rows = {lo}
    for n in focus:
        rows.update(range(max(lo, n - context), min(hi, n + context) + 1))
    return sorted(rows)[:limit]


# ------------------------------------------------------------------ context

def head_to_base(f, n):
    """Where head line n sat in the base file, going by the hunks before it."""
    shift = 0
    for blo, bhi, hlo, hhi in f.diff.hunks:
        if hlo > n:
            break
        if hhi >= n >= hlo:
            return max(1, blo + min(n - hlo, max(0, bhi - blo)))
        shift += (bhi - blo + 1) - (hhi - hlo + 1)
    return max(1, n + shift)


def base_match(f, spot):
    """The unit's earlier version: the same-named base unit that the spot's unchanged lines come
    from, or, for a unit rewritten from scratch, a same-named base unit the change removed whole.
    (A class can hold a method of the same name as another class's, so the name alone is not enough.)"""
    if spot.kind != 'unit' or f.base is None:
        return None
    named = [u for u in base_top_units(f) if u.name == spot.name]
    if not named:
        return None
    mapped = [head_to_base(f, n) for n in range(spot.start, spot.end + 1) if n not in f.added]

    def kept(u):
        return sum(1 for b in mapped if u.start <= b <= u.end)
    best = max(named, key=lambda u: (kept(u), -u.start))
    if kept(best):
        return best
    bl, removed = f.diff.base_lines, f.diff.removed
    gone = [u for u in named if all(n in removed or not bl[n - 1].strip() for n in range(u.start, min(u.end, len(bl)) + 1))]
    if gone:
        at = head_to_base(f, spot.start)
        return min(gone, key=lambda u: (abs(u.start - at), u.start))
    return None


def outline(f, limit=MAX_OUTLINE):
    """The head file's functions with their line ranges: + new, * changed by the pull request."""
    tops = top_units(f)
    if not tops:
        return '(no functions found)'
    base_names = {u.name for u in base_top_units(f)}
    added = set(f.added_code_lines())

    def mark(u):
        if f.base is None or u.name not in base_names:
            return '+'
        return '*' if any(u.start <= n <= u.end for n in added) else ' '
    marks = {id(u): mark(u) for u in tops}
    keep = list(tops)
    if len(tops) > limit:
        changed = sorted(added) or [1]
        keep = [u for u in tops if marks[id(u)] != ' ']
        rest = [u for u in tops if marks[id(u)] == ' ']
        rest.sort(key=lambda u: min(abs(u.start - n) for n in changed))
        keep = (keep + rest)[:limit]
        keep.sort(key=lambda u: u.start)
    rows = ['%s %d-%d %s' % (marks[id(u)], u.start, u.end, safe_name(u.name)) for u in keep]
    if len(tops) > len(keep):
        rows.append('… and %d more' % (len(tops) - len(keep)))
    return '\n'.join(rows)


def _shingles(f, rows):
    toks = []
    for n in rows:
        if 1 <= n <= len(f.code):
            toks.extend(lexer.tokens(f.code[n - 1], f.language))
    return {tuple(toks[i:i + SHINGLE]) for i in range(max(0, len(toks) - SHINGLE + 1))}


def similar(f, spot):
    """(share, unit): the other unit whose unchanged code holds most of the spot's added code
    shape, when it holds at least SIMILAR_MIN of it."""
    mine = _shingles(f, spot.added)
    if len(mine) < 8:
        return None
    best = None
    for u in top_units(f):
        if spot.kind == 'unit' and u.start == spot.start:
            continue
        rows = [n for n in range(u.start, u.end + 1) if n not in f.added]
        if not rows:
            continue
        share = len(mine & _shingles(f, rows)) / len(mine)
        if share >= SIMILAR_MIN and (best is None or share > best[0]):
            best = (share, u)
    return best


# ------------------------------------------------------------------ the request

def describe(f, spot, base_unit):
    total = len(f.lines)
    part = ''
    if spot.part:
        part = ' Part %d of %d: added lines %d-%d.' % (spot.part[0], spot.part[1], spot.added[0], spot.added[-1])
    if spot.kind == 'module':
        return 'Module-level code of a %d-line file: %d added code lines outside any function.%s' % (total, spot.n_added, part)
    status = 'new' if f.base is None or base_unit is None else 'changed'
    before = '' if base_unit is None else ', %d lines before the change' % base_unit.length
    return '%s, lines %d-%d of %d (%s; %d lines long now%s; %d added code lines).%s' % (
        spot.label, spot.start, spot.end, total, status, spot.end - spot.start + 1, before, spot.n_added, part)


def earlier_text(f, spot, base_unit):
    if f.base is None:
        return None
    bl = f.diff.base_lines
    removed = f.diff.removed
    if spot.kind == 'unit':
        if base_unit is None:
            return None
        lo, hi = base_unit.start, base_unit.end
        if spot.part:
            lo = max(lo, head_to_base(f, spot.added[0]) - CONTEXT)
            hi = min(hi, head_to_base(f, spot.added[-1]) + CONTEXT)
        gone = [n for n in range(lo, hi + 1) if n in removed]
        if spot.part:
            rows = windows(base_unit.start, base_unit.end, gone, MAX_EARLIER, whole=False) if gone else []
        else:
            rows = windows(base_unit.start, base_unit.end, gone, MAX_EARLIER)
        if not rows:
            return None
    else:
        inside = _inside(base_top_units(f))
        lo = head_to_base(f, spot.added[0]) - MODULE_CONTEXT
        hi = head_to_base(f, spot.added[-1]) + MODULE_CONTEXT
        gone = [n for n in sorted(removed) if lo <= n <= hi and n not in inside and n <= len(bl) and bl[n - 1].strip()]
        if not gone:
            return None
        rows = set()
        for n in gone:
            rows.update(range(max(1, n - MODULE_CONTEXT), min(len(bl), n + MODULE_CONTEXT) + 1))
        rows = sorted(r for r in rows if r not in inside or r in removed)[:MAX_EARLIER]
    return render(bl, f.base_scan, rows, removed, '-', f.language)


def state(f, spot, file_outline=None):
    """The Jev state for one spot."""
    base_unit = base_match(f, spot)
    st = {
        'format': questions.STATE_FORMAT,
        'language': f.language,
        'path': f.path,
        'unit': describe(f, spot, base_unit),
        'outline': file_outline if file_outline is not None else outline(f),
        'later': render(f.lines, f.scan, head_rows(f, spot), set(f.added), '+', f.language),
    }
    earlier = earlier_text(f, spot, base_unit)
    if earlier:
        st['earlier'] = earlier
    sim = similar(f, spot)
    if sim:
        u = sim[1]
        rows = list(range(u.start, min(u.end, u.start + MAX_SIMILAR - 1) + 1))
        st['similar'] = '%s, lines %d-%d:\n%s' % (safe_name(u.name), u.start, u.end, render(f.lines, f.scan, rows, set(), ' ', f.language))
    return st
