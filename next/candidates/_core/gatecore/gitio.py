"""Reading a change out of git, inside a trust perimeter.

The change is the working tree against the merge base of the base ref and HEAD, as qualm reads it:
committed, staged, unstaged and untracked files all count. Everything that decides how the change
is judged (the config, keeps, skip patterns and git attributes) is read from the base commit,
never from the change itself, so a pull request cannot steer its own judge.

Every git call pins the options a user's configuration could change (colour, external diff
tools, text conversion, rename detection, prefixes) and passes --literal-pathspecs, so the same
change reads the same everywhere and no file name is taken for a pattern.
"""
import codecs, os, subprocess

GIT_ENV_DROP = ('GIT_DIFF_OPTS', 'GIT_EXTERNAL_DIFF', 'GIT_PAGER', 'GIT_CONFIG_PARAMETERS')
PINNED = ['-c', 'core.quotepath=off', '-c', 'diff.noprefix=false', '-c', 'diff.mnemonicprefix=false', '-c', 'color.ui=never',
          '-c', 'diff.external=', '-c', 'core.attributesFile=/dev/null', '-c', 'gc.auto=0']
MAX_FILE_BYTES = 1_000_000


class GateError(Exception):
    """The change could not be judged. The run exits 2."""


def _env():
    env = {k: v for k, v in os.environ.items() if k not in GIT_ENV_DROP}
    env['GIT_LITERAL_PATHSPECS'] = '1'
    env['LC_ALL'] = 'C'
    return env


def git(repo, *args, check=True, binary=False, input=None):
    p = subprocess.run(['git', '-C', repo, *PINNED, *args], capture_output=True, env=_env(), input=input)
    if check and p.returncode != 0:
        raise GateError('git %s failed: %s' % (args[0], p.stderr.decode('utf-8', 'replace').strip()[:300]))
    return p.stdout if binary else p.stdout.decode('utf-8', 'replace')


def repo_root(path):
    try:
        return git(path, 'rev-parse', '--show-toplevel').strip()
    except GateError:
        raise GateError('%s is not inside a git repository' % path)


def merge_base(repo, base_ref):
    """The commit the change is measured from. A ref that resolves to nothing, or a HEAD with no
    common history (an orphan branch), cannot be judged."""
    if base_ref.startswith('-'):
        raise GateError('the base ref %r looks like an option' % base_ref)
    base = git(repo, 'rev-parse', '--verify', '--quiet', base_ref + '^{commit}', check=False).strip()
    if not base:
        raise GateError('the base ref %r resolves to no commit' % base_ref)
    head = git(repo, 'rev-parse', '--verify', '--quiet', 'HEAD^{commit}', check=False).strip()
    if not head:
        return base  # an unborn branch: everything in the working tree is the change
    mb = git(repo, 'merge-base', base, head, check=False).strip()
    if not mb:
        raise GateError('HEAD shares no history with %s, so there is no change to measure' % base_ref)
    return mb


def decode(data):
    """Text from bytes. UTF-16 (with a byte-order mark) and UTF-8 are read; anything else is None."""
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
        try:
            return data.decode('utf-16')
        except UnicodeDecodeError:
            return None
    if data.startswith(codecs.BOM_UTF8):
        data = data[3:]
    if b'\x00' in data[:8000]:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('latin-1') if data.count(b'\x00') == 0 and len(data) < MAX_FILE_BYTES else None


class Change:
    """One changed path: its status, old path for a rename, and base and head text (None when absent)."""

    def __init__(self, path, status, old_path=None, base_text=None, head_text=None, head_is_symlink=False, undecodable=False, size=0):
        self.path, self.status, self.old_path = path, status, old_path
        self.base, self.head = base_text, head_text
        self.head_is_symlink = head_is_symlink
        self.undecodable = undecodable
        self.size = size

    def __repr__(self):
        return 'Change(%s %s)' % (self.status, self.path)


def read_base(repo, base, path):
    data = git(repo, 'cat-file', 'blob', '%s:%s' % (base, path), check=False, binary=True)
    return data


def changes(repo, base):
    """Every path that differs between the base commit and the working tree, untracked files included."""
    out = git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--name-status', '-z', '-M50%', base, '--')
    parts = out.split('\0')
    rows, i = [], 0
    while i < len(parts) and parts[i]:
        st = parts[i]
        if st.startswith('R') or st.startswith('C'):
            rows.append((st[0], parts[i + 2], parts[i + 1]))
            i += 3
        else:
            rows.append((st[0], parts[i + 1], None))
            i += 2
    untracked = git(repo, 'ls-files', '-z', '--others', '--exclude-standard').split('\0')
    for p in untracked:
        if p:
            rows.append(('A', p, None))
    result = []
    for st, path, old in rows:
        if st == 'D':
            continue
        full = os.path.join(repo, path)
        is_link = os.path.islink(full)
        head_bytes = b''
        if is_link:
            head_bytes = os.readlink(full).encode()
        elif os.path.isfile(full):
            with open(full, 'rb') as fh:
                head_bytes = fh.read(MAX_FILE_BYTES + 1)
        base_bytes = None
        if st in ('M', 'T'):
            base_bytes = read_base(repo, base, path)
        elif st in ('R', 'C'):
            base_bytes = read_base(repo, base, old)
        head_text = decode(head_bytes)
        base_text = decode(base_bytes) if base_bytes is not None else None
        undecodable = head_text is None or (base_bytes is not None and base_text is None)
        result.append(Change(path, st, old, base_text, head_text, is_link, undecodable, len(head_bytes)))
    result.sort(key=lambda c: c.path)
    return result


def base_file(repo, base, path):
    data = read_base(repo, base, path)
    return decode(data) if data else None


def base_dir_files(repo, base, directory, limit=60):
    """Files directly in a directory of the base commit, for looking up copies of existing code."""
    spec = (directory.rstrip('/') + '/') if directory else ''
    out = git(repo, 'ls-tree', '-z', '--name-only', base, '--', spec or '.', check=False)
    names = [n for n in out.split('\0') if n][:limit]
    return names


def attributes_from_base(repo, base, paths, names=('linguist-generated', 'linguist-vendored', 'binary', 'diff')):
    """git attributes as the base commit defines them; the change's own .gitattributes is ignored."""
    if not paths:
        return {}
    out = git(repo, 'check-attr', '-z', '--source', base, *names, '--', *paths, check=False)
    parts = out.split('\0')
    attrs = {}
    for k in range(0, len(parts) - 2, 3):
        p, name, val = parts[k], parts[k + 1], parts[k + 2]
        if val not in ('unspecified', 'unset'):
            attrs.setdefault(p, {})[name] = val
    return attrs
