"""What d-cascade asks Jev, and the two views of a change it asks about.

The raw view is qualm 0.1.1's request, byte for byte in content: the same normalised git diff
(-U8, Myers, a/ b/ prefixes), the same ten questions with the same guard sentence, split into the
same 60 KB pieces with the strongest piece speaking for the file. So qualm's signal is kept whole.

The masked view is the same change with every comment removed and every string literal of
natural language replaced by a placeholder, diffed again, and asked qualm's questions that still
make sense without comments plus four that name what reviewers ask for. Text in the change cannot
talk to the judge through this view, and two partly independent readings steady the verdict.
"""
import difflib, os, re

from gatecore import gitio, lexer

DIFF_FORMAT = 'A unified diff of one file. Lines starting with - are the earlier version, lines starting with + are the later version.'
MASKED_FORMAT = ('A unified diff of one file with its comments removed and natural-language string literals replaced by "<text>". '
                 'Lines starting with - are the earlier version, lines starting with + are the later version.')
PIECE_LIMIT = 60000
LANGUAGES = {'.rb': 'Ruby', '.erb': 'ERB', '.go': 'Go', '.js': 'JavaScript', '.mjs': 'JavaScript', '.cjs': 'JavaScript', '.jsx': 'JavaScript',
             '.ts': 'TypeScript', '.tsx': 'TypeScript', '.py': 'Python', '.rs': 'Rust', '.java': 'Java', '.kt': 'Kotlin', '.swift': 'Swift',
             '.cs': 'C#', '.php': 'PHP', '.ex': 'Elixir', '.exs': 'Elixir', '.c': 'C', '.h': 'C', '.cc': 'C++', '.cpp': 'C++', '.hpp': 'C++',
             '.sh': 'shell', '.sql': 'SQL', '.scala': 'Scala'}

# qualm 0.1.1's built-in questions (internal/questions/builtin.json), in its order.
QUALM = {
    'push_back': {'type': 'noul', 'instructions': 'Would an experienced reviewer ask for this change to be simplified before it merges? Judge the code itself and set comments and documentation aside.'},
    'direction': {'type': 'choice', 'instructions': 'Compared with the earlier version, how hard is the later version to understand and change? Judge the code itself and set comments and documentation aside, so a version that differs only in its comments counts as the same.',
                  'criteria': {'harder': 'The later version is harder to understand or riskier to change.', 'same': 'There is no real difference in how hard the code is to understand and change.', 'easier': 'The later version is easier to understand or safer to change.'}},
    'simplified': {'type': 'noul', 'instructions': 'Did the change remove complexity from this file: fewer branches, less state to track, or logic moved out to a clearer place?'},
    'comments_only': {'type': 'noul', 'instructions': 'Does the later version differ from the earlier one only in comments, documentation or whitespace?'},
    'new_behaviour': {'type': 'noul', 'instructions': 'Is this change mostly new behaviour being added, with little of the existing behaviour reshaped?'},
    'grew_a_big_unit': {'type': 'noul', 'instructions': 'Did the change make an already long function or class longer, where the new work could have gone in a unit of its own?'},
    'added_copies': {'type': 'noul', 'instructions': 'Did the change add logic that is a near-copy of logic already in the file?'},
    'added_impossible_guards': {'type': 'noul', 'instructions': "Did the change add checks or fallbacks for states the surrounding code shows can't happen?"},
    'added_placeholders': {'type': 'noul', 'instructions': 'Did the change add placeholder implementations, debug output or unfinished scaffolding?'},
    'added_unused_flexibility': {'type': 'noul', 'instructions': 'Did the change add options, hooks or indirection that the file uses in only one place?'},
}

VERBS = {
    'remove': {'type': 'noul', 'instructions': 'Could some of the added lines be deleted without changing what the program does: unused variables, parameters or functions, redundant checks, dead branches, or needless conversions?'},
    'reuse': {'type': 'noul', 'instructions': 'Does the added code re-implement something that the standard library, the language, or code already visible in this diff provides?'},
    'shorten': {'type': 'noul', 'instructions': 'Is there a noticeably shorter and more direct way to write the added logic in this language, such as a built-in, a simpler construct, or fewer intermediate variables?'},
    'indirection': {'type': 'noul', 'instructions': 'Does the change add a layer that has only one user or one implementation: a wrapper, an interface, a class, a parameter or a configuration option that is not needed yet?'},
}
MASKED_QUESTIONS = {k: QUALM[k] for k in ('push_back', 'simplified', 'new_behaviour', 'grew_a_big_unit', 'added_copies',
                                          'added_impossible_guards', 'added_placeholders', 'added_unused_flexibility')}
MASKED_QUESTIONS.update(VERBS)
HUNK_QUESTIONS = {k: MASKED_QUESTIONS[k] for k in ('push_back', 'added_copies', 'added_impossible_guards', 'added_placeholders',
                                                   'added_unused_flexibility', 'remove', 'reuse', 'shorten', 'indirection')}

GIT_HEADERS = ('diff --git ', 'index ', 'old mode ', 'new mode ', 'new file mode ', 'deleted file mode ', 'similarity index ',
               'dissimilarity index ', 'rename from ', 'rename to ', 'copy from ', 'copy to ')
PINNED = ['diff', '--no-color', '--no-ext-diff', '--no-textconv', '--diff-algorithm=myers', '--src-prefix=a/', '--dst-prefix=b/',
          '--inter-hunk-context=0', '--indent-heuristic', '--submodule=short', '--full-index']


def language(path):
    return LANGUAGES.get(os.path.splitext(path)[1].lower())


def normalise(raw):
    i = 0 if raw.startswith('--- ') else raw.find('\n--- ')
    if i < 0:
        return ''
    if i:
        i += 1
    return ''.join(l for l in raw[i:].splitlines(keepends=True) if not l.startswith(GIT_HEADERS))


def git_diff(repo, base, change):
    """qualm's normalised diff of one change, read from git as qualm reads it."""
    if change.status == 'A' and not gitio.git(repo, 'ls-files', '--', change.path).strip():
        raw = gitio.git(repo, *PINNED, '--no-index', '-U8', '--', '/dev/null', change.path, check=False)
    else:
        args = ['--relative', '-U8', '-M', base, '--']
        if change.old_path:
            args.append(change.old_path)
        raw = gitio.git(repo, *PINNED, *args, change.path, check=False)
    text = normalise(raw)
    if not text.strip() or '\n@@ ' not in '\n' + text:
        return None  # git saw no text change (binary, UTF-16, mode only): fall back to our own diff
    return text


def py_diff(path, base, head, context=8, old_path=None):
    a = [] if base is None else base.splitlines(keepends=True)
    b = head.splitlines(keepends=True)
    if a and not a[-1].endswith('\n'):
        a[-1] += '\n\\ No newline at end of file\n'
    if b and not b[-1].endswith('\n'):
        b[-1] += '\n\\ No newline at end of file\n'
    lines = list(difflib.unified_diff(a, b, '/dev/null' if base is None else 'a/' + (old_path or path), 'b/' + path, n=context))
    if not lines:
        return ''
    lines[0] = lines[0].rstrip() + '\n'
    lines[1] = lines[1].rstrip() + '\n'
    return ''.join(lines)


LONG_STRING = re.compile(r'[A-Za-z]{2,}(\s+\S+){5,}')


def masked_text(text, lang):
    """The text with comments removed and natural-language strings replaced, line for line."""
    if text is None:
        return None
    out = []
    for code, _, strings in lexer.scan(text, lang):
        it = iter(strings)

        def sub(m):
            s = next(it, '')
            if m.group(0) == "''":
                return "'" + s[:4] + "'"
            return '"<text>"' if LONG_STRING.search(s) else '"' + s[:80].replace('\n', ' ') + '"'
        out.append(re.sub(r'""|\'\'', sub, code).rstrip())
    return '\n'.join(out)


def masked_diff(path, base, head, lang, old_path=None):
    return py_diff(path, masked_text(base, lang), masked_text(head, lang), old_path=old_path)


def hunks(diff):
    """The header and the hunks of a normalised diff."""
    cut = 0 if diff.startswith('@@ ') else diff.find('\n@@ ')
    if cut < 0:
        return diff, []
    if cut:
        cut += 1
    header, rest = diff[:cut], diff[cut:]
    parts = re.split(r'(?m)^(?=@@ )', rest)
    return header, [p for p in parts if p]


def split(diff, limit=PIECE_LIMIT):
    """qualm's pieces: whole hunks packed up to the limit, an oversized hunk cut by lines."""
    if len(diff) <= limit:
        return [diff]
    header, hs = hunks(diff)
    if not hs:
        return [diff]
    pieces, cur, open_ = [], header, False
    for h in hs:
        if len(header) + len(h) > limit:
            if open_:
                pieces.append(cur)
            cur, open_ = header, False
            lines = h.splitlines(keepends=True)
            first, body = lines[0], lines[1:]
            chunk = header + first
            for line in body:
                if len(chunk) + len(line) > limit and chunk != header + first:
                    pieces.append(chunk)
                    chunk = header + first
                chunk += line
            pieces.append(chunk)
            continue
        if open_ and len(cur) + len(h) > limit:
            pieces.append(cur)
            cur, open_ = header, False
        cur += h
        open_ = True
    if open_:
        pieces.append(cur)
    return pieces


def state(diff, lang, fmt=DIFF_FORMAT):
    s = {'format': fmt, 'change': diff}
    if lang:
        s['language'] = lang
    return s


def ask_pieces(client, diff, lang, questions, fmt, use_cache=True):
    """Ask about every piece; for each question the strongest piece speaks for the file."""
    best = {}
    for piece in split(diff):
        ans = client.ask(state(piece, lang, fmt), questions, use_cache=use_cache)
        for q, v in ans.items():
            best[q] = max(best.get(q, 0.0), v)
    return best


def hunk_ranges(diff):
    """[(head_lo, head_hi, text)] for each hunk, with head line numbers from its header."""
    _, hs = hunks(diff)
    out = []
    for h in hs:
        m = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', h)
        if not m:
            continue
        lo = int(m.group(1))
        n = int(m.group(2)) if m.group(2) is not None else 1
        out.append((lo, lo + max(n, 1) - 1, h))
    return out
