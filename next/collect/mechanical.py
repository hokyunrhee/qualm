"""Deterministic smell injection and hard negatives, applied to the head of a clean change.

Each injector takes (base, head, language, rng) and returns (new_head, [start, end]) with the
location in new-file lines, or None when the file offers no place for it. Each hard-negative
transform returns (new_base, new_head) for a change that a reviewer would not push back on.
Injection only touches lines the clean change itself adds, so the injected code is part of the
diff a candidate sees.
"""
import difflib, re

INDENT = re.compile(r'^[ \t]*')

FUNC_START = {
    'Go': re.compile(r'^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\('),
    'Python': re.compile(r'^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\('),
    'Ruby': re.compile(r'^\s*def\s+(?:self\.)?([A-Za-z_]\w*[!?]?)'),
    'TypeScript': re.compile(r'^\s*(?:export\s+)?(?:(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*[(<]|(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*(?::[^=]+)?=>\s*\{\s*$|(?:(?:public|private|protected|static|async|override|readonly)\s+)*(?!if\b|for\b|while\b|switch\b|catch\b|return\b|function\b)([A-Za-z_$][\w$]*)\s*(?:<[^>]*>)?\([^)]*\)\s*(?::\s*[^{=]+)?\{\s*$)'),
    'JavaScript': re.compile(r'^\s*(?:export\s+)?(?:(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{\s*$|(?:(?:static|async)\s+)*(?!if\b|for\b|while\b|switch\b|catch\b|return\b|function\b)([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{\s*$)'),
    'Java': re.compile(r'^\s*(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\], ?]+\s+([a-z]\w*)\s*\([^;]*$'),
}
ASSIGN = {
    'Go': re.compile(r'^(\s*)([a-z]\w*)\s*:?=\s*(?!nil\b)(.+)$'),
    'Python': re.compile(r'^(\s*)([a-z_]\w*)\s*=\s*(?!None\b)([^=].*)$'),
    'Ruby': re.compile(r'^(\s*)([a-z_]\w*)\s*=\s*(?!nil\b)([^=~].*)$'),
    'TypeScript': re.compile(r'^(\s*)(?:const|let)\s+([a-zA-Z_$][\w$]*)\s*(?::[^=]+)?=\s*(?!null\b|undefined\b)(.+?);?\s*$'),
    'JavaScript': re.compile(r'^(\s*)(?:const|let|var)\s+([a-zA-Z_$][\w$]*)\s*=\s*(?!null\b|undefined\b)(.+?);?\s*$'),
    'Java': re.compile(r'^(\s*)(?:final\s+)?[A-Z][\w<>\[\], ?]*\s+([a-z]\w*)\s*=\s*(?!null\b)(.+);\s*$'),
}
CONSTRUCTED = {
    'Go': re.compile(r'^(?:&\w|make\(|\[\]|map\[|new\(|\w+\{|"|\d)'),
    'Python': re.compile(r'^(?:\[|\{|"|\'|\d|[A-Z]\w*\(|dict\(|list\(|set\()'),
    'Ruby': re.compile(r'^(?:\[|\{|"|\'|\d|[A-Z][\w:]*\.new\b|Hash\.new|Array\.new)'),
    'TypeScript': re.compile(r'^(?:\[|\{|"|\'|`|\d|new\s+[A-Z])'),
    'JavaScript': re.compile(r'^(?:\[|\{|"|\'|`|\d|new\s+[A-Z])'),
    'Java': re.compile(r'^(?:new\s+[A-Z]|"|\d|List\.of|Map\.of|Set\.of)'),
}
COMMENT = {'Go': '//', 'Python': '#', 'Ruby': '#', 'TypeScript': '//', 'JavaScript': '//', 'Java': '//'}
BLOCK_LANGS = {'Go', 'TypeScript', 'JavaScript', 'Java'}


def added_set(base, head):
    a, b = (base or '').splitlines(), head.splitlines()
    out = set()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ('replace', 'insert'):
            out.update(range(j1, j2))
    return out


def indent_of(line):
    return INDENT.match(line).group(0)


def unit_end(lines, start, lang):
    """Index of the last line of the unit that starts at `start`, by indentation and braces."""
    ind = len(indent_of(lines[start]).expandtabs(4))
    if lang in BLOCK_LANGS:
        depth, opened = 0, False
        for i in range(start, min(len(lines), start + 400)):
            code = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`[^`]*`', '""', lines[i].split('//')[0])
            depth += code.count('{') - code.count('}')
            opened = opened or '{' in code
            if opened and depth <= 0:
                return i
        return None
    if lang == 'Ruby':
        for i in range(start + 1, min(len(lines), start + 400)):
            if lines[i].strip() == 'end' and len(indent_of(lines[i]).expandtabs(4)) == ind:
                return i
        return None
    last = start
    for i in range(start + 1, min(len(lines), start + 400)):
        if not lines[i].strip():
            continue
        if len(indent_of(lines[i]).expandtabs(4)) <= ind:
            return last
        last = i
    return last


def added_units(base, head, lang, min_len=5):
    lines = head.splitlines()
    added = added_set(base, head)
    rx = FUNC_START.get(lang)
    out = []
    for i, l in enumerate(lines):
        m = rx.match(l) if rx else None
        if m and i in added:
            end = unit_end(lines, i, lang)
            if end is not None and end - i + 1 >= min_len and sum(j in added for j in range(i, end + 1)) >= 0.8 * (end - i + 1):
                out.append((i, end, next(g for g in m.groups() if g)))
    return out


def splice(lines, at, new):
    return lines[:at] + new + lines[at:]


def finish(lines, head):
    text = '\n'.join(lines)
    return text + ('\n' if head.endswith('\n') else '')


def inject_debug_print(base, head, lang, rng):
    lines = head.splitlines()
    added = added_set(base, head)
    rx = ASSIGN.get(lang)
    spots = [i for i in sorted(added) if rx and rx.match(lines[i]) and not lines[i].rstrip().endswith((',', '(', '[', '{', '\\'))
             and i + 1 < len(lines) and lines[i + 1].strip() and not lines[i + 1].lstrip().startswith(('.', '}', ')', ']', 'else', 'elif', 'end', 'rescue', 'except'))]
    if not spots:
        return None
    i = rng.choice(spots)
    ind, var = rx.match(lines[i]).group(1), rx.match(lines[i]).group(2)
    stmt = {'Go': 'println("DEBUG %s:", %s)' % (var, var),
            'Python': 'print("DEBUG %s =", %s)' % (var, var),
            'Ruby': 'puts "DEBUG %s=#{%s.inspect}"' % (var, var),
            'TypeScript': 'console.log("DEBUG %s", %s);' % (var, var),
            'JavaScript': 'console.log("DEBUG %s", %s);' % (var, var),
            'Java': 'System.out.println("DEBUG %s=" + %s);' % (var, var)}[lang]
    return finish(splice(lines, i + 1, [ind + stmt]), head), [i + 2, i + 2]


def inject_placeholder(base, head, lang, rng):
    units = added_units(base, head, lang)
    if not units:
        return None
    s, e, name = rng.choice(units)
    lines = head.splitlines()
    ind = indent_of(lines[s])
    c = COMMENT[lang]
    nm = name.rstrip('!?')
    stub = {
        'Go': ['', ind + 'func %sFallback() error {' % nm, ind + '\t%s TODO: implement fallback' % c, ind + '\treturn nil', ind + '}'],
        'Python': ['', ind + 'def %s_fallback(*args, **kwargs):' % nm, ind + '    %s TODO: implement fallback' % c, ind + '    pass'],
        'Ruby': ['', ind + 'def %s_fallback(*args)' % nm, ind + '  %s TODO: implement fallback' % c, ind + '  nil', ind + 'end'],
        'TypeScript': ['', ind + 'function %sFallback(): void {' % nm, ind + '  %s TODO: implement fallback' % c, ind + '}'],
        'JavaScript': ['', ind + 'function %sFallback() {' % nm, ind + '  %s TODO: implement fallback' % c, ind + '}'],
        'Java': ['', ind + 'private void %sFallback() {' % nm, ind + '    %s TODO: implement fallback' % c,
                 ind + '    throw new UnsupportedOperationException("not implemented yet");', ind + '}'],
    }[lang]
    return finish(splice(lines, e + 1, stub), head), [e + 3, e + 1 + len(stub)]


def inject_near_copy(base, head, lang, rng):
    units = [u for u in added_units(base, head, lang, min_len=6) if u[1] - u[0] <= 60]
    if not units:
        return None
    s, e, name = rng.choice(units)
    lines = head.splitlines()
    body = lines[s:e + 1]
    nm = name.rstrip('!?')
    suffix = {'Python': '_v2', 'Ruby': '_v2'}.get(lang, 'V2')
    new_name = nm + suffix
    copy = [re.sub(r'\b%s\b' % re.escape(nm), new_name, body[0], count=1)] + body[1:]
    # change one literal so it is a near copy, not an exact one
    for k in range(1, len(copy)):
        m = re.search(r'(?<![\w.])(\d+)(?![\w.])', copy[k])
        if m:
            copy[k] = copy[k][:m.start()] + str(int(m.group(1)) + 1) + copy[k][m.end():]
            break
        m = re.search(r'"([A-Za-z][^"\\]{2,})"', copy[k])
        if m:
            copy[k] = copy[k][:m.start(1)] + m.group(1) + ' (copy)' + copy[k][m.end(1):]
            break
    return finish(splice(lines, e + 1, [''] + copy), head), [e + 3, e + 2 + len(copy)]


def inject_impossible_guard(base, head, lang, rng):
    lines = head.splitlines()
    added = added_set(base, head)
    rx, built = ASSIGN.get(lang), CONSTRUCTED.get(lang)
    spots = []
    for i in sorted(added):
        m = rx.match(lines[i]) if rx else None
        if m and built.match(m.group(3).strip()) and not lines[i].rstrip().endswith((',', '(', '[', '{', '\\')) \
                and i + 1 < len(lines) and not lines[i + 1].lstrip().startswith(('.', '}', ')', ']')):
            spots.append(i)
    if not spots:
        return None
    i = rng.choice(spots)
    ind, var = rx.match(lines[i]).group(1), rx.match(lines[i]).group(2)
    guard = {
        'Go': [ind + 'if %s == nil {' % var, ind + '\tpanic("%s is nil")' % var, ind + '}'],
        'Python': [ind + 'if %s is None:' % var, ind + '    raise ValueError("%s must not be None")' % var],
        'Ruby': [ind + 'raise ArgumentError, "%s is nil" if %s.nil?' % (var, var)],
        'TypeScript': [ind + 'if (%s === null || %s === undefined) {' % (var, var), ind + '  throw new Error("%s is not defined");' % var, ind + '}'],
        'JavaScript': [ind + 'if (%s === null || %s === undefined) {' % (var, var), ind + '  throw new Error("%s is not defined");' % var, ind + '}'],
        'Java': [ind + 'if (%s == null) {' % var, ind + '    throw new IllegalStateException("%s is null");' % var, ind + '}'],
    }[lang]
    if lang == 'Go' and re.match(r'^(?:\d|")', rx.match(lines[i]).group(3).strip()):
        return None  # a Go string or number can't be compared with nil
    return finish(splice(lines, i + 1, guard), head), [i + 2, i + 1 + len(guard)]


INJECTORS = {'debug_print': inject_debug_print, 'placeholder': inject_placeholder,
             'near_copy': inject_near_copy, 'impossible_guard': inject_impossible_guard}


# Hard negatives: (base, head) pairs that differ in ways a reviewer would not push back on.

def neg_rename(text, lang, rng):
    names = {}
    for m in re.finditer(r'\b([a-z][a-z0-9]*[A-Z]\w*|[a-z]+_[a-z_]+)\b', text):
        names[m.group(1)] = names.get(m.group(1), 0) + 1
    cands = [n for n, k in names.items() if 3 <= k <= 40 and len(n) >= 6]
    if not cands:
        return None
    old = rng.choice(sorted(cands))
    new = (old + '_value') if '_' in old else (old + 'Value')
    if re.search(r'\b%s\b' % re.escape(new), text):
        return None
    return text, re.sub(r'\b%s\b' % re.escape(old), new, text)


def neg_comments(text, lang, rng):
    lines = text.splitlines()
    rx = FUNC_START.get(lang)
    starts = [i for i, l in enumerate(lines) if rx and rx.match(l)]
    if len(starts) < 2:
        return None
    picked = sorted(rng.sample(starts, min(3, len(starts))), reverse=True)
    for i in picked:
        name = next(g for g in rx.match(lines[i]).groups() if g)
        ind = indent_of(lines[i])
        if lang == 'Python':
            # a docstring goes after the def line(s); find the line ending with ':'
            j = i
            while j < len(lines) and not lines[j].rstrip().endswith(':'):
                j += 1
            if j + 1 >= len(lines):
                continue
            body_ind = indent_of(lines[j + 1]) or ind + '    '
            lines = splice(lines, j + 1, [body_ind + '"""Return the result of %s for the given arguments."""' % name.replace('_', ' ')])
        else:
            c = COMMENT[lang]
            lines = splice(lines, i, [ind + '%s %s handles the %s step.' % (c, name, re.sub(r'(?<!^)(?=[A-Z])', ' ', name).lower())])
    head = '\n'.join(lines) + ('\n' if text.endswith('\n') else '')
    return (text, head) if head != text else None


def neg_formatting(text, lang, rng):
    lines = text.splitlines()
    if lang == 'Python':
        # collapse double blank lines and add trailing spaces in the base; the head is the clean file
        base = []
        for l in lines:
            if base and not l.strip() and not base[-1].strip():
                continue
            base.append(l + ('  ' if l.strip() and rng.random() < 0.15 else ''))
    else:
        base = [l + (' ' if l.strip() and rng.random() < 0.2 else '') for l in lines if l.strip() or rng.random() < 0.5]
    b = '\n'.join(base) + ('\n' if text.endswith('\n') else '')
    return (b, text) if b != text else None


HARD_NEGATIVES = {'rename': neg_rename, 'comments_only': neg_comments, 'formatting': neg_formatting}
