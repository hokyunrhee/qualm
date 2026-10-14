"""Everything the detectors look at for one changed file, computed once.

A FileCtx holds the head and base text, the lexed lines, the units, and the diff. A ChangeCtx
holds every judged file of the change plus the repository at the base commit, which the
copy detector and the unused-code check search.
"""
import posixpath, re

from . import gitio, lexer, units


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
        self.base_scan = lexer.scan(base, language) if base else []
        self.base_units = units.find_units(base, language, self.base_scan) if base else []

    def is_added(self, n):
        return n in self.added

    def added_code_lines(self):
        """Line numbers of added lines that carry code (not blank, not comment-only)."""
        return [n for n in sorted(self.added) if n <= len(self.code) and self.code[n - 1].strip()]

    def changed_units(self, min_added=1):
        out = []
        for u in self.top_units:
            k = sum(1 for n in range(u.start, u.end + 1) if n in self.added and self.code[n - 1].strip())
            if k >= min_added:
                out.append((u, k))
        return out

    def new_units(self, share=0.8):
        """Units the change wrote (at least `share` of their non-blank lines added)."""
        out = []
        for u in self.units:
            rows = [n for n in range(u.start, u.end + 1) if self.lines[n - 1].strip()]
            if rows and sum(1 for n in rows if n in self.added) >= share * len(rows):
                out.append(u)
        return out

    def is_script(self):
        """A program's entry point, where printing is the job: Go package main, a Python
        `__main__` guard, a bin/ or scripts/ path, a CLI module."""
        p = '/' + self.path
        if re.search(r'/(bin|scripts?|tools?|cmd|cli|examples?|demo|benchmarks?|hack|contrib|misc|internal/cmd)/', p):
            return True
        if re.search(r'(^|/)(cli|main|__main__|manage|setup|cmd|command|commands|console|repl|script)[^/]*\.\w+$', self.path.lower()):
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

    def base_neighbours(self, f, limit=40, max_bytes=200_000):
        """Base versions of the code files next to f (same directory), for finding copies of
        existing code. Read once per directory."""
        d = posixpath.dirname(f.path)
        if d in self._dir_cache:
            return self._dir_cache[d]
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
        self._dir_cache[d] = out
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
