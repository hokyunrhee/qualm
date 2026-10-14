"""A small lexer for the six languages the gate judges, good enough to tell code from comments
and strings and to compare code by shape.

`scan(text, language)` returns, per line, the code with comments and string contents blanked,
the comment text, and the string literals. `tokens(code)` turns masked code into normalized
tokens: identifiers become `I`, numbers `N`, strings `S`, keywords and punctuation stay.
"""
import re

LANG_BY_EXT = {'.go': 'Go', '.rb': 'Ruby', '.rake': 'Ruby', '.py': 'Python', '.ts': 'TypeScript', '.tsx': 'TypeScript',
               '.mts': 'TypeScript', '.cts': 'TypeScript', '.java': 'Java', '.js': 'JavaScript', '.jsx': 'JavaScript',
               '.mjs': 'JavaScript', '.cjs': 'JavaScript', '.kt': 'Kotlin', '.rs': 'Rust', '.cs': 'C#', '.php': 'PHP',
               '.swift': 'Swift', '.scala': 'Scala', '.c': 'C', '.cc': 'C++', '.cpp': 'C++', '.h': 'C', '.hpp': 'C++'}
JUDGED = {'Go', 'Ruby', 'Python', 'TypeScript', 'Java', 'JavaScript'}

KEYWORDS = {
    'Go': 'break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false',
    'Python': 'and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield None True False self',
    'Ruby': 'alias and begin break case class def defined? do else elsif end ensure false for if in module next nil not or redo rescue retry return self super then true undef unless until when while yield',
    'TypeScript': 'abstract any as async await boolean break case catch class const continue debugger default delete do else enum export extends false finally for from function if implements import in instanceof interface let new null number private protected public readonly return static string super switch this throw true try type typeof undefined var void while yield',
    'JavaScript': 'async await break case catch class const continue debugger default delete do else export extends false finally for from function if import in instanceof let new null return static super switch this throw true try typeof undefined var void while yield',
    'Java': 'abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for if implements import instanceof int interface long native new null package private protected public return short static super switch synchronized this throw throws transient true false try void volatile while var',
}
KEYWORDS = {k: set(v.split()) for k, v in KEYWORDS.items()}
for _l in ('Kotlin', 'Rust', 'C#', 'PHP', 'Swift', 'Scala', 'C', 'C++'):
    KEYWORDS[_l] = KEYWORDS['Java']

HASH_COMMENT = {'Python', 'Ruby'}


def language_of(path):
    import os
    name = os.path.basename(path)
    i = name.rfind('.')
    if i <= 0:
        return None
    return LANG_BY_EXT.get(name[i:].lower())


class Line:
    __slots__ = ('code', 'comment', 'strings')

    def __init__(self):
        self.code, self.comment, self.strings = [], [], []


def scan(text, language):
    """Per line: (code with strings blanked to "", comment text, [string literals])."""
    lines = text.split('\n')
    out = [Line() for _ in lines]
    hash_c = language in HASH_COMMENT
    state = None  # None, 'block', ('str', quote), ('tstr', quote)
    buf = []
    i_line = 0
    block_end = '*/'
    for n, line in enumerate(lines):
        L = out[n]
        i, m = 0, len(line)
        # a string or block comment carried over from an earlier line: keep this line's indentation
        # in the masked code, so indentation-based units still see where the line sits
        lead = line[:len(line) - len(line.lstrip())] if isinstance(state, tuple) else ''
        if lead:
            L.code.append(lead)
        if state == 'rubydoc':
            if line.startswith('=end'):
                state = None
            L.comment.append(line)
            continue
        if language == 'Ruby' and state is None and line.startswith('=begin'):
            state = 'rubydoc'
            L.comment.append(line)
            continue
        while i < m:
            c = line[i]
            if state == 'block':
                j = line.find(block_end, i)
                if j < 0:
                    L.comment.append(line[i:])
                    i = m
                else:
                    L.comment.append(line[i:j])
                    i = j + len(block_end)
                    state = None
                continue
            if isinstance(state, tuple):
                kind, q = state
                if line.startswith(q, i):
                    L.strings.append(''.join(buf))
                    buf = []
                    L.code.append('""')
                    i += len(q)
                    state = None
                    continue
                if c == '\\' and q != '`' and not (language == 'Go' and q == '`'):
                    buf.append(line[i:i + 2])
                    i += 2
                    continue
                buf.append(c)
                i += 1
                continue
            # normal code
            if hash_c and c == '#':
                if language == 'Ruby' and i + 1 < m and line[i + 1] == '{':
                    L.code.append(c)
                    i += 1
                    continue
                L.comment.append(line[i + 1:])
                i = m
                continue
            if not hash_c and line.startswith('//', i):
                L.comment.append(line[i + 2:])
                i = m
                continue
            if not hash_c and line.startswith('/*', i):
                state, block_end = 'block', '*/'
                i += 2
                continue
            if (language == 'Python' and (line.startswith('"""', i) or line.startswith("'''", i))) or (language == 'Java' and line.startswith('"""', i)):
                state = ('tstr', line[i:i + 3])
                buf = []
                i += 3
                continue
            if c == '/' and language in ('TypeScript', 'JavaScript'):
                end = _regex_end(line, i, ''.join(L.code))
                if end:
                    L.code.append('""')
                    i = end
                    continue
            if c in '"\'' or (c == '`' and language in ('Go', 'TypeScript', 'JavaScript')):
                # a quote inside an identifier (Ruby's foo? or Rust lifetimes) is rare enough to ignore
                if c == "'" and language in ('Go', 'Java') and i + 2 < m and line[i + 2] == "'":
                    L.code.append("''")
                    L.strings.append(line[i + 1])
                    i += 3
                    continue
                state = ('str', c)
                buf = []
                i += 1
                continue
            L.code.append(c)
            i += 1
        if isinstance(state, tuple) and state[0] == 'str' and state[1] in '"\'':
            # an unterminated one-line string: close it at the end of the line
            L.strings.append(''.join(buf))
            buf = []
            L.code.append('""')
            state = None
        elif isinstance(state, tuple):
            buf.append('\n')
    return [(''.join(L.code), ' '.join(s.strip() for s in L.comment).strip(), L.strings) for L in out]


REGEX_BEFORE = re.compile(r'(^|[(,=:\[!&|?{};+\-*%<>~^]|\b(return|typeof|case|in|of|void|delete|throw|yield|await))\s*$')


def _regex_end(line, i, before):
    """Where a JavaScript regular expression literal that starts at line[i] ends, or None when the
    slash is division. A slash after an operand (a name, a number, a closing bracket) divides."""
    if not REGEX_BEFORE.search(before):
        return None
    j, in_class = i + 1, False
    while j < len(line):
        ch = line[j]
        if ch == '\\':
            j += 2
            continue
        if ch == '[':
            in_class = True
        elif ch == ']':
            in_class = False
        elif ch == '/' and not in_class:
            if j == i + 1:
                return None
            j += 1
            while j < len(line) and line[j].isalpha():
                j += 1
            return j
        j += 1
    return None


TOKEN = re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*[?!]?|\d[\w.]*|""|\'\'|==|!=|<=|>=|&&|\|\||:=|->|=>|\+\+|--|\+=|-=|\S')


def tokens(code, language):
    kw = KEYWORDS.get(language, set())
    out = []
    for t in TOKEN.findall(code):
        if t in ('""', "''"):
            out.append('S')
        elif t[0].isdigit():
            out.append('N')
        elif t[0].isalpha() or t[0] in '_$':
            out.append(t if t in kw else 'I')
        else:
            out.append(t)
    return out


def identifiers(code, language):
    kw = KEYWORDS.get(language, set())
    return [t for t in re.findall(r'[A-Za-z_$][A-Za-z0-9_$]*', code) if t not in kw]


CODE_LIKE = re.compile(r'''(^\s*(if|for|while|return|def|func|function|var|let|const|import|from|class|else|elif|end|puts|print|fmt\.|console\.)\b)|[;{}]\s*$|\w+\(.*\)\s*;?\s*$|^\s*\w+(\.\w+)*\s*(=|:=|\+=)\s*\S''')
PROSE_HINT = re.compile(r'\b(the|a|an|is|are|this|that|we|when|because|should|will|if you|note)\b', re.I)


def looks_like_code(comment):
    """A comment line that reads as code that was commented out rather than prose."""
    c = comment.strip()
    if len(c) < 4 or c.startswith(('TODO', 'FIXME', 'NOTE', 'XXX', 'HACK', 'nolint', 'eslint', 'type:', 'noqa', 'pylint', '@', 'go:', '+build', 'lint:')):
        return False
    if c.startswith(('http://', 'https://')):
        return False
    if not CODE_LIKE.search(c):
        return False
    words = re.findall(r'[A-Za-z]+', c)
    prose = len(PROSE_HINT.findall(c))
    punct = sum(1 for ch in c if ch in '(){}[];=.:,<>+-*/&|!')
    return punct >= 2 and prose <= max(1, len(words) // 5)
