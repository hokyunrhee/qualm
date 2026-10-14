"""What the reviewer reads about one file: an outline of the file and a numbered view of the change.

Every line of the view starts with its line number in the head file, so a comment the reviewer
writes can be checked against the lines the change added, and no line of the file can pass for
part of the prompt around it. Removed base lines are shown with "-" and no number.

When the whole view does not fit the prompt cap, long hunks are cut into pieces at function
boundaries and the pieces holding the most added code are kept, code inside functions first.
Blank, comment-only and moved lines count for little, and a piece that is mostly rows of
literals (a data table) is shown only when nothing else is, so a large table cannot push the
code it hides out of the view.
"""
import collections, re

from gatecore import lexer

LINE_BREAKS = re.compile('[\r\x0b\x0c\x1c-\x1e\x85  ]')

CONTEXT = 15        # unchanged lines shown around each change
PIECE_LINES = 60    # a hunk over the cap is cut into pieces of at most this many lines
LINE_CHARS = 300    # longer lines are cut, so one line cannot fill the view
OUTLINE_ROWS = 60
FLOW = {'if', 'for', 'while', 'return', 'func', 'def', 'function', 'switch', 'case', 'else', 'elif', 'try', 'catch',
        'except', 'raise', 'throw', 'yield', 'await', 'go', 'defer', 'unless', 'until', 'begin', 'rescue'}
LITERALS = {'S', 'N', 'true', 'false', 'nil', 'null', 'None', 'True', 'False', 'undefined'}


def head_len(f):
    """Lines in the head file, not counting the empty string after a final newline."""
    n = len(f.lines)
    return n - 1 if n and f.lines[-1] == '' else n


def clip(line):
    """One line of the file as the view shows it: characters that would start a new line in the
    reader's eyes become spaces, and a long line is cut."""
    line = LINE_BREAKS.sub(' ', line.rstrip('\r'))
    if len(line) > LINE_CHARS:
        return '%s [... %d more characters]' % (line[:LINE_CHARS], len(line) - LINE_CHARS)
    return line


def anchors(f):
    """Removed base lines, keyed by the head line they are shown before."""
    out = {}
    for blo, bhi, hlo, _ in f.diff.hunks:
        rows = [k for k in range(blo, bhi + 1) if k in f.diff.removed]
        if rows:
            out.setdefault(hlo, []).extend(rows)
    return out


def windows(f):
    """Head line ranges to show: each change with CONTEXT lines around it, overlapping ones merged."""
    n = head_len(f)
    spans = []
    for blo, bhi, hlo, hhi in f.diff.hunks:
        has_added = any(k in f.added for k in range(hlo, hhi + 1))
        has_removed = any(k in f.diff.removed for k in range(blo, bhi + 1))
        if not (has_added or has_removed) or n == 0:
            continue
        spans.append((max(1, hlo - CONTEXT), min(n, max(hhi, hlo) + CONTEXT)))
    out = []
    for lo, hi in sorted(spans):
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return [tuple(w) for w in out]


def render(f, lo, hi, anch):
    """The view of head lines lo..hi, with the removed lines that sit among them, as
    (head line or None, text) rows."""
    n = head_len(f)
    out = []
    for k in range(lo, hi + 1):
        for b in anch.get(k, ()):
            out.append((None, '%5s - %s' % ('', clip(f.diff.base_lines[b - 1]))))
        out.append((k, '%5d %s %s' % (k, '+' if k in f.added else ' ', clip(f.lines[k - 1]))))
    if hi == n:
        for b in anch.get(n + 1, ()):
            out.append((None, '%5s - %s' % ('', clip(f.diff.base_lines[b - 1]))))
    return out


def header(f, lo, hi):
    first = next((k for k in range(lo, hi + 1) if k in f.added), lo)
    inside = [u for u in f.units if u.start <= first <= u.end]
    if inside:
        u = min(inside, key=lambda u: u.length)
        return '@@ lines %d-%d, in %s (lines %d-%d) @@' % (lo, hi, u.name, u.start, u.end)
    return '@@ lines %d-%d @@' % (lo, hi)


def data_like(code, language):
    """A line of literals (a row of a table), not logic."""
    toks = lexer.tokens(code, language)
    if any(t in FLOW for t in toks):
        return False
    lits = sum(1 for t in toks if t in LITERALS)
    idents = sum(1 for t in toks if t == 'I')
    return lits >= 2 and lits >= idents


class Weigher:
    """How much new code each added line is worth when the view has to leave something out."""

    def __init__(self, f):
        self.f = f
        self.moved = collections.Counter(f.diff.base_lines[k - 1].strip() for k in f.diff.removed if f.diff.base_lines[k - 1].strip())
        self.in_unit = set()
        for u in f.top_units:
            self.in_unit.update(range(u.start, u.end + 1))

    def line(self, k):
        """(weight, is a data line) of head line k."""
        f = self.f
        if k not in f.added or not f.code[k - 1].strip():
            return 0.0, False
        if self.moved.get(f.lines[k - 1].strip()):
            return 0.1, False
        if data_like(f.code[k - 1], f.language):
            return 0.0, True
        return (1.0 if k in self.in_unit else 0.5), False

    def piece(self, lo, hi):
        """The piece's weight, and whether it is mostly rows of literals."""
        rows = [self.line(k) for k in range(lo, hi + 1)]
        data = sum(1 for _, is_data in rows if is_data)
        added = sum(1 for k in range(lo, hi + 1) if k in self.f.added and self.f.lines[k - 1].strip())
        return sum(w for w, _ in rows), data > 0 and data * 2 >= added


def pieces(f, lo, hi):
    """Cut lo..hi at function starts and ends, then into runs of at most PIECE_LINES lines."""
    cuts = {u.start for u in f.top_units if lo < u.start <= hi} | {u.end + 1 for u in f.top_units if lo < u.end + 1 <= hi}
    bounds = [lo] + sorted(cuts) + [hi + 1]
    out = []
    for a, b in zip(bounds, bounds[1:]):
        while a < b:
            out.append((a, min(b, a + PIECE_LINES) - 1))
            a += PIECE_LINES
    return out


def nbytes(rows):
    return sum(len(text.encode('utf-8')) + 1 for _, text in rows)


def fold(nums):
    out = []
    for k in sorted(nums):
        if out and k == out[-1][1] + 1:
            out[-1][1] = k
        else:
            out.append([k, k])
    return out


def span_text(ranges, limit=8):
    parts = ['%d' % lo if lo == hi else '%d-%d' % (lo, hi) for lo, hi in ranges[:limit]]
    if len(ranges) > limit:
        parts.append('and %d more ranges' % (len(ranges) - limit))
    return ', '.join(parts)


class View:
    """The rendered change and what it left out."""

    def __init__(self, text, shown, left_out):
        self.text = text
        self.shown = shown          # [(lo, hi)] head ranges shown
        self.left_out = left_out    # added head lines not shown

    def note(self, f):
        if not self.left_out:
            return ''
        n_added = sum(1 for k in f.added if f.lines[k - 1].strip())
        k = sum(1 for n in self.left_out if f.lines[n - 1].strip())
        return ('This change is too large to show in full. The view keeps the parts with the most added code; '
                '%d of its %d added non-blank lines are not shown (lines %s).' % (k, n_added, span_text(fold(self.left_out))))


def build(f, budget):
    """The numbered view of f's change in at most `budget` bytes."""
    anch = anchors(f)
    blocks = [(lo, hi, render(f, lo, hi, anch)) for lo, hi in windows(f)]
    whole = [(None, header(f, lo, hi)) for lo, hi, _ in blocks] + [r for _, _, rows in blocks for r in rows]
    if nbytes(whole) <= budget:
        return View(join(f, blocks), [(lo, hi) for lo, hi, _ in blocks], [])
    weigh = Weigher(f)
    cands = [(a, b) + weigh.piece(a, b) for lo, hi, _ in blocks for a, b in pieces(f, lo, hi)]
    # pieces that are mostly rows of literals go only when there is nothing else to show
    cands = [c for c in cands if not c[3]] or cands
    chosen, used = [], 0
    for a, b, w, _ in sorted(cands, key=lambda c: (-c[2], c[0])):
        rows = render(f, a, b, anch)
        cost = nbytes(rows) + 60
        if used + cost <= budget:
            chosen.append((a, b, rows))
            used += cost
        elif not chosen:
            # the most important piece alone is over the cap: keep its first lines
            keep, size = [], 60
            for k, text in rows:
                size += len(text.encode('utf-8')) + 1
                if size > budget:
                    break
                keep.append((k, text))
            last = max([k for k, _ in keep if k] or [a])
            chosen.append((a, last, keep))
            used = size
    chosen.sort()
    merged = []
    for a, b, rows in chosen:
        if merged and a == merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], b, merged[-1][2] + rows)
        else:
            merged.append((a, b, rows))
    shown = {k for _, _, rows in merged for k, _ in rows if k}
    left = sorted(k for k in f.added if k not in shown)
    return View(join(f, merged), [(a, b) for a, b, _ in merged], left)


def join(f, blocks):
    return '\n'.join(line for a, b, rows in blocks for line in [header(f, a, b)] + [text for _, text in rows])


def outline(f):
    """Functions and methods of the head file with their lines, the ones the change touched marked."""
    if not f.units:
        return '  (no functions or methods found)'
    rows = []
    for u in sorted(f.units, key=lambda u: (u.start, -u.end)):
        depth = sum(1 for o in f.units if o is not u and o.start <= u.start and u.end <= o.end and (o.start, o.end) != (u.start, u.end))
        body = [k for k in range(u.start, u.end + 1) if f.lines[k - 1].strip()]
        added = sum(1 for k in body if k in f.added)
        mark = ' (new)' if body and added == len(body) else ' (changed)' if added else ''
        rows.append((not mark, u.start, '%s%s  lines %d-%d%s' % ('  ' * (1 + min(depth, 3)), u.name, u.start, u.end, mark)))
    if len(rows) > OUTLINE_ROWS:
        keep = sorted(rows)[:OUTLINE_ROWS]
        more = len(rows) - len(keep)
        rows = sorted(keep, key=lambda r: r[1])
        return '\n'.join(r[2] for r in rows) + '\n  (%d more not listed)' % more
    return '\n'.join(r[2] for r in rows)
