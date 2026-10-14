"""Changes that write no new code, settled without asking the reviewer.

A change that only deletes lines, re-indents or moves lines the base already had, renames
identifiers, or edits imports gives a reviewer nothing to simplify. Settling these by rule keeps
a mass rename from costing a hundred calls and from collecting a hundred chances of a spurious
fail. Any added line the rules cannot account for sends the file to the reviewer.

Moved lines are matched as a multiset: an added line counts as moved only against a distinct
removed line, so deleting a function and pasting it back twice is new code. A rename must be
one name for one name throughout the file: the old name is gone from the head, the new name was
not in the base, and the paired lines differ in nothing else (string literals included, except
in import statements).
"""
import collections, re

from gatecore import lexer

IDENT = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*')

IMPORT = {
    'Go': re.compile(r'^\s*import\b'),
    'Python': re.compile(r'^\s*(import\s+[\w.]|from\s+\.*[\w.]*\s+import\b)'),
    'TypeScript': re.compile(r'^\s*(import\b|export\s+(\*|type\s*\{|\{)[^;]*\bfrom\b|(const|let|var)\s+[\w${}\s,:]+=\s*require\s*\()'),
    'JavaScript': re.compile(r'^\s*(import\b|export\s+(\*|\{)[^;]*\bfrom\b|(const|let|var)\s+[\w${}\s,:]+=\s*require\s*\()'),
    'Java': re.compile(r'^\s*import\s+(static\s+)?[\w.]+(\.\*)?\s*;'),
    'Ruby': re.compile(r'^\s*(require|require_relative)\b'),
}


def import_lines(f):
    """Head lines that belong to import statements, multi-line import blocks included."""
    rx = IMPORT.get(f.language)
    out = set()
    if not rx:
        return out
    depth = 0
    for k, code in enumerate(f.code, 1):
        if depth > 0 or rx.match(code):
            out.add(k)
            depth = max(0, depth + sum(code.count(c) for c in '([{') - sum(code.count(c) for c in ')]}'))
    return out


def renamed_lines(f, imports):
    """Added lines that are a removed line with identifiers renamed, one name for one name across
    the whole file."""
    if f.base is None:
        return set()
    keywords = lexer.KEYWORDS.get(f.language, set())
    mapping, back, out = {}, {}, set()
    for blo, bhi, hlo, hhi in f.diff.hunks:
        olds = [k for k in range(blo, bhi + 1) if k in f.diff.removed and f.diff.base_lines[k - 1].strip()]
        news = [k for k in range(hlo, hhi + 1) if k in f.added and f.lines[k - 1].strip()]
        if len(olds) != len(news):
            continue
        for b, h in zip(olds, news):
            old_code, _, old_strings = f.base_scan[b - 1]
            if old_strings != f.strings[h - 1] and h not in imports:
                continue
            ot, nt = lexer.TOKEN.findall(old_code), lexer.TOKEN.findall(f.code[h - 1])
            if len(ot) != len(nt):
                continue
            pairs = [(x, y) for x, y in zip(ot, nt) if x != y]
            if not pairs or not all(IDENT.fullmatch(x) and IDENT.fullmatch(y) and x not in keywords and y not in keywords for x, y in pairs):
                continue
            if any(mapping.get(x, y) != y or back.get(y, x) != x for x, y in pairs):
                return set()
            for x, y in pairs:
                mapping[x], back[y] = y, x
            out.add(h)
    if not mapping:
        return set()
    head_names = {t for c in f.code for t in IDENT.findall(c)}
    base_names = {t for c, _, _ in f.base_scan for t in IDENT.findall(c)}
    if any(x in head_names or y in base_names for x, y in mapping.items()):
        return set()
    return out


def settled(f):
    """Why the change needs no review (a short reason), or None when it does."""
    added = [k for k in sorted(f.added) if f.lines[k - 1].strip()]
    if not added:
        return 'the change adds no lines; it only deletes'
    moved = collections.Counter(f.diff.base_lines[k - 1].strip() for k in f.diff.removed if f.diff.base_lines[k - 1].strip())
    imports = import_lines(f)
    renamed = renamed_lines(f, imports)
    how = set()
    for k in added:
        text = f.lines[k - 1].strip()
        if moved[text] > 0:
            moved[text] -= 1
            how.add('re-indents or moves existing lines')
        elif k in renamed:
            how.add('renames identifiers')
        elif k in imports:
            how.add('edits imports')
        else:
            return None
    return 'the change only ' + ' and '.join(sorted(how))
