"""Changes that write no new code, settled without asking the reviewer.

A change that only deletes lines, only re-indents or moves lines the base already had, or only
edits imports gives a reviewer nothing to simplify. Settling these by rule keeps a mass rename
from costing a hundred calls and from collecting a hundred chances of a spurious fail.

Moved lines are matched as a multiset: an added line counts as moved only against a distinct
removed line, so deleting a function and pasting it back twice is new code.
"""
import collections, re

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


def settled(f):
    """Why the change needs no review (a short reason), or None when it does."""
    added = [k for k in sorted(f.added) if f.lines[k - 1].strip()]
    if not added:
        return 'the change adds no lines; it only deletes'
    moved = collections.Counter(f.diff.base_lines[k - 1].strip() for k in f.diff.removed if f.diff.base_lines[k - 1].strip())
    imports = import_lines(f)
    left = []
    n_moved = n_import = 0
    for k in added:
        text = f.lines[k - 1].strip()
        if moved[text] > 0:
            moved[text] -= 1
            n_moved += 1
        elif k in imports:
            n_import += 1
        else:
            left.append(k)
    if left:
        return None
    if not n_import:
        return 'the change only re-indents or moves lines the file already had'
    if not n_moved:
        return 'the change only edits imports'
    return 'the change only edits imports and re-indents or moves existing lines'
