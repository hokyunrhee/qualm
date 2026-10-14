"""Functions and methods: where they start and end, in each of the judged languages.

Units are found from masked code (comments and strings blanked), by brace matching for the
C-like languages, by indentation for Python, and by `def`/`end` pairs for Ruby. A file's
change is described by the lines the head added and the base lines it removed.
"""
import difflib, re

from . import lexer

GO_FUNC = re.compile(r'^func\s*(\([^)]*\)\s*)?([A-Za-z_]\w*)')
JAVA_METHOD = re.compile(r'^\s*(?:@\w+(?:\([^)]*\))?\s+)*(?:(?:public|private|protected|static|final|synchronized|abstract|default|native|override|suspend|open|internal)\s+)*'
                         r'(?:<[^>]+>\s+)?[\w.<>\[\]?,\s]+?\s+([A-Za-z_]\w*)\s*\([^;]*$')
JS_FUNCS = [re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*[<(]'),
            re.compile(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*(?::[^=]+)?=>|[A-Za-z_$][\w$]*\s*=>)'),
            re.compile(r'^\s*(?:(?:public|private|protected|static|readonly|async|override|abstract|get|set)\s+)*\*?([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\s*\([^;]*\)\s*(?::\s*[^={;]+)?\s*\{\s*$'),
            re.compile(r'^\s*([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?(?:function\b|\([^)]*\)\s*=>)')]
JS_NOT = {'if', 'for', 'while', 'switch', 'catch', 'return', 'function', 'else', 'with', 'constructor_'}
JAVA_NOT = {'if', 'for', 'while', 'switch', 'catch', 'return', 'new', 'else', 'synchronized', 'try'}
PY_DEF = re.compile(r'^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)')
PY_CLASS = re.compile(r'^(\s*)class\s+([A-Za-z_]\w*)')
RB_DEF = re.compile(r'^(\s*)def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)')


class Unit:
    __slots__ = ('name', 'start', 'end', 'kind')

    def __init__(self, name, start, end, kind='function'):
        self.name, self.start, self.end, self.kind = name, start, end, kind

    @property
    def length(self):
        return self.end - self.start + 1

    def __repr__(self):
        return 'Unit(%s %d-%d)' % (self.name, self.start, self.end)


def _brace_end(masked, start_idx, limit=4000):
    """Index of the line holding the brace that closes the first `{` at or after start_idx."""
    depth, opened = 0, False
    for k in range(start_idx, min(len(masked), start_idx + limit)):
        for ch in masked[k]:
            if ch == '{':
                depth += 1
                opened = True
            elif ch == '}':
                depth -= 1
                if opened and depth == 0:
                    return k
        if not opened and k > start_idx + 3:
            return None
        if not opened and masked[k].rstrip().endswith(';'):
            return None
    return None


def find_units(text, language, scanned=None):
    scanned = scanned or lexer.scan(text, language)
    masked = [s[0] for s in scanned]
    units = []
    if language == 'Python':
        for k, line in enumerate(masked):
            m = PY_DEF.match(line)
            if not m:
                continue
            indent = len(m.group(1).expandtabs())
            end = k
            for j in range(k + 1, len(masked)):
                raw = masked[j]
                if not raw.strip():
                    continue
                if len(raw) - len(raw.lstrip()) <= indent and not raw.lstrip().startswith((')', ']', '}')):
                    break
                end = j
            units.append(Unit(m.group(2), k + 1, end + 1))
        return units
    if language == 'Ruby':
        for k, line in enumerate(masked):
            m = RB_DEF.match(line)
            if not m:
                continue
            indent = m.group(1)
            if re.search(r'\bend\s*$', line) or re.search(r'\)\s*=\s*\S', line):
                units.append(Unit(m.group(2), k + 1, k + 1))
                continue
            end = None
            for j in range(k + 1, min(len(masked), k + 2000)):
                if re.match(r'^' + re.escape(indent) + r'end\b', masked[j]):
                    end = j
                    break
            if end is not None:
                units.append(Unit(m.group(2), k + 1, end + 1))
        return units
    for k, line in enumerate(masked):
        name = None
        if language == 'Go':
            m = GO_FUNC.match(line)
            name = m and m.group(2)
        elif language in ('TypeScript', 'JavaScript'):
            for rx in JS_FUNCS:
                m = rx.match(line)
                if m and m.group(1) not in JS_NOT:
                    name = m.group(1)
                    break
        else:
            m = JAVA_METHOD.match(line)
            if m and m.group(1) not in JAVA_NOT and not re.match(r'^\s*(return|new|else|throw)\b', line) and '=' not in line.split('(')[0]:
                name = m.group(1)
        if not name:
            continue
        end = _brace_end(masked, k)
        if end is None:
            if language in ('TypeScript', 'JavaScript') and '=>' in line and '{' not in line:
                # a one-expression arrow function
                units.append(Unit(name, k + 1, k + 1))
            continue
        units.append(Unit(name, k + 1, end + 1))
    return units


def innermost(units, line):
    best = None
    for u in units:
        if u.start <= line <= u.end and (best is None or u.length < best.length):
            best = u
    return best


def outermost(units):
    """Units not nested in another unit (closures and nested defs fold into their parent)."""
    out = []
    for u in sorted(units, key=lambda u: (u.start, -u.end)):
        if out and out[-1].start <= u.start and u.end <= out[-1].end:
            continue
        out.append(u)
    return out


class FileDiff:
    """The added head lines and removed base lines of one file, with the hunks that hold them."""

    def __init__(self, base, head):
        a = (base or '').split('\n')
        b = (head or '').split('\n')
        if base is None:
            a = []
        self.base_lines, self.head_lines = a, b
        self.added, self.removed = set(), set()
        self.hunks = []  # (base_lo, base_hi, head_lo, head_hi), 1-based inclusive, empty ranges have hi < lo
        # trim the common head and tail first; diff the middle line by line unless it is huge
        p = 0
        while p < len(a) and p < len(b) and a[p] == b[p]:
            p += 1
        s = 0
        while s < len(a) - p and s < len(b) - p and a[len(a) - 1 - s] == b[len(b) - 1 - s]:
            s += 1
        mid_a, mid_b = a[p:len(a) - s], b[p:len(b) - s]
        if len(mid_a) * len(mid_b) > 30_000_000:
            ops = [('replace', 0, len(mid_a), 0, len(mid_b))]
        else:
            ops = difflib.SequenceMatcher(None, mid_a, mid_b, autojunk=False).get_opcodes()
        for tag, i1, i2, j1, j2 in ops:
            if tag == 'equal' or (i1 == i2 and j1 == j2):
                continue
            i1, i2, j1, j2 = i1 + p, i2 + p, j1 + p, j2 + p
            self.removed.update(range(i1 + 1, i2 + 1))
            self.added.update(range(j1 + 1, j2 + 1))
            self.hunks.append((i1 + 1, i2, j1 + 1, j2))
        # trailing empty line artefacts
        if b and b[-1] == '':
            self.added.discard(len(b))
        if a and a[-1] == '':
            self.removed.discard(len(a))

    @property
    def n_added(self):
        return sum(1 for k in self.added if self.head_lines[k - 1].strip())

    @property
    def n_removed(self):
        return sum(1 for k in self.removed if self.base_lines[k - 1].strip())


def added_ranges(added, gap=0):
    """Sorted line numbers folded into [lo, hi] runs."""
    out = []
    for k in sorted(added):
        if out and k <= out[-1][1] + 1 + gap:
            out[-1][1] = k
        else:
            out.append([k, k])
    return out
