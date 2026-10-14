"""Everything the detectors look at for one changed file, computed once.

A FileCtx holds the head and base text, the lexed lines, the units, and the diff. A ChangeCtx
holds every judged file of the change plus the repository at the base commit, which the
copy detector and the unused-code check search.
"""
import posixpath, re

from . import gitio, lexer, units

CLOSERS = ('', '}', '};', '})', '});', ')', '];', ']', 'end', 'end)', '},', '),')


class FileCtx:
    def __init__(self, path, language, head, base, status='M', old_path=None):
        self.path, self.language, self.status, self.old_path = path, language, status, old_path
        self.head, self.base = head, base
        self.lines = head.split('\n')
        self.scan = lexer.scan(head, language)
        self.code = [s[0] for s in self.scan]
        self.comments = [s[1] for s in self.scan]
        self.strings = [s[2] for s in self.scan]
        self.units = units.find_units(head, language, self.scan)
        self.top_units = units.outermost(self.units)
        self.diff = units.FileDiff(base, head)
        self.added = self.diff.added
        # added lines whose text the change did not just move, re-indent or re-end: what leftover
        # detectors (prints, TODOs, commented-out code) may call new
        removed_text = {self.diff.base_lines[k - 1].strip() for k in self.diff.removed}
        self.new = {n for n in self.added if n <= len(self.lines) and self.lines[n - 1].strip() not in removed_text}
        self.base_scan = lexer.scan(base, language) if base else []
        self.base_units = units.find_units(base, language, self.base_scan) if base else []

    def is_added(self, n):
        return n in self.added

    def added_code_lines(self):
        """Line numbers of added lines that carry code (not blank, not comment-only)."""
        return [n for n in sorted(self.added) if n <= len(self.code) and self.code[n - 1].strip()]

    def new_code_lines(self):
        """Added code lines that are new text, not moved or re-indented existing lines."""
        return [n for n in sorted(self.new) if n <= len(self.code) and self.code[n - 1].strip()]

    def changed_units(self, min_added=1):
        out = []
        for u in self.top_units:
            k = sum(1 for n in range(u.start, u.end + 1) if n in self.added and self.code[n - 1].strip())
            if k >= min_added:
                out.append((u, k))
        return out

    def new_units(self, share=0.8):
        """Units the change wrote (at least `share` of their lines added). Closing brackets and `end`
        lines do not count: a diff may pair a new function's closing brace with an old one."""
        out = []
        for u in self.units:
            rows = [n for n in range(u.start, u.end + 1) if self.code[n - 1].strip() not in CLOSERS and self.lines[n - 1].strip()]
            if rows and sum(1 for n in rows if n in self.added) >= share * len(rows):
                out.append(u)
        return out

    def is_script(self):
        """A program's entry point, where printing is the job: Go package main, a Python
        `__main__` guard, a bin/ or scripts/ path, a CLI module."""
        p = '/' + self.path
        if re.search(r'/(bin|scripts?|tools?|cmd|cli|examples?|demo|benchmarks?|hack|contrib|misc|internal/cmd)/', p):
            return True
        if re.search(r'(^|/)(cli|main|__main__|manage|setup|cmd|command|commands|console|repl|script)\.\w+$', self.path.lower()):
            return True
        if self.language == 'Go' and re.search(r'^package main\b', self.head, re.M):
            return True
        if self.language == 'Python' and "__name__ == '__main__'" in self.head.replace('"', "'"):
            return True
        if self.language in ('JavaScript', 'TypeScript') and self.head.startswith('#!'):
            return True
        if self.language == 'Ruby' and (self.head.startswith('#!') or '/exe/' in p or '/rake' in p or self.path.endswith('.rake')):
            return True
        if self.language == 'Java' and 'public static void main(' in self.head:
            return True
        return False


class ChangeCtx:
    def __init__(self, repo, base, files, all_changes=()):
        self.repo, self.base, self.files = repo, base, files
        self.by_path = {f.path: f for f in files}
        self.all_changes = all_changes
        self._dir_cache = {}
        self._grep_cache = {}
        self._copy_cache = {}
        self.fetched = {}  # base text of every unchanged file read for the copy index

    def base_neighbours(self, f, limit=40, max_bytes=200_000):
        """Base versions of the code files next to f (same directory), for finding copies of
        existing code. Read once per directory."""
        d = posixpath.dirname(f.path)
        if (d, f.language) in self._dir_cache:
            return self._dir_cache[(d, f.language)]
        out = []
        if self.repo:
            names = gitio.base_dir_files(self.repo, self.base, d, limit=200)
            names = [n for n in names if lexer.language_of(n) == f.language]
            names = sorted(names)[:limit]
            total = 0
            for n in names:
                text = gitio.base_file(self.repo, self.base, n)
                if text is None:
                    continue
                total += len(text)
                if total > max_bytes:
                    break
                out.append((n, text))
                self.fetched[n] = text
        self._dir_cache[(d, f.language)] = out
        return out

    def copy_sources(self, f, limit=6, max_bytes=300_000):
        """Unchanged files anywhere in the base commit that share several distinctive lines with what
        f added: where a copy of existing code in another directory comes from."""
        if not self.repo:
            return []
        if f.path in self._copy_cache:
            return self._copy_cache[f.path]
        lines = []
        for n in f.new_code_lines():
            t = f.lines[n - 1].strip()
            if len(t) >= 24 and len(re.findall(r'[A-Za-z_]\w*', t)) >= 3 and t not in lines and '\0' not in t:
                lines.append(t)
        out = []
        if len(lines) >= 3:
            lines = lines[:40]
            args = ['grep', '-c', '-F', '-I']
            for t in lines:
                args += ['-e', t]
            got = gitio.git(self.repo, *(args + [self.base, '--']), check=False)
            changed = {g.path for g in self.files} | {c.path for c in self.all_changes} | {c.old_path for c in self.all_changes if getattr(c, 'old_path', None)}
            counts = []
            for row in got.splitlines():
                path, _, k = row[len(self.base) + 1:].rpartition(':')
                if not path or path in changed or lexer.language_of(path) != f.language or not k.isdigit():
                    continue
                if int(k) >= max(3, len(lines) // 6):
                    counts.append((-int(k), path))
            total = 0
            for _k, path in sorted(counts)[:limit]:
                text = self.fetched.get(path) or gitio.base_file(self.repo, self.base, path)
                if text is None:
                    continue
                total += len(text)
                if total > max_bytes:
                    break
                out.append((path, text))
                self.fetched[path] = text
        self._copy_cache[f.path] = out
        return out

    def referenced_in_base(self, name):
        """Whether an identifier appears anywhere in the base commit's tree (word match)."""
        if name in self._grep_cache:
            return self._grep_cache[name]
        hit = False
        if self.repo:
            out = gitio.git(self.repo, 'grep', '-l', '-w', '-F', '-I', '--max-count=1', '-e', name, self.base, '--', check=False)
            hit = bool(out.strip())
        self._grep_cache[name] = hit
        return hit
