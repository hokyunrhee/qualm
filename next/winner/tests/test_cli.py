"""qualm-next's own command line: version, dry run, default base, keep, and no key."""
import json, os, shutil, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(os.path.dirname(HERE), 'bin', 'qualm-next')
FIX = os.path.join(HERE, 'testdata')
ENV = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid', GIT_COMMITTER_NAME='t',
           GIT_COMMITTER_EMAIL='t@example.invalid', GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')
ENV.pop('OPENROUTER_API_KEY', None)


def read(p):
    with open(p) as fh:
        return fh.read()


class Repo:
    def __init__(self):
        self.dir = tempfile.mkdtemp()
        self.git('init', '-q', '-b', 'main')
        self.write('ledger.go', read(os.path.join(FIX, 'base_ledger.go')))
        self.git('add', '-A')
        self.git('commit', '-q', '-m', 'base')
        self.git('checkout', '-q', '-b', 'feature')

    def git(self, *a):
        return subprocess.run(['git', '-C', self.dir] + list(a), check=True, capture_output=True, text=True, env=ENV).stdout

    def write(self, path, text):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, 'w') as fh:
            fh.write(text)

    def run(self, *a):
        p = subprocess.run([BIN] + list(a), cwd=self.dir, capture_output=True, text=True, env=ENV)
        return p.returncode, p.stdout, p.stderr

    def close(self):
        shutil.rmtree(self.dir)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.r = Repo()
        self.bad = read(os.path.join(FIX, 'base_ledger.go')) + read(os.path.join(FIX, 'payload_tags.go'))

    def tearDown(self):
        self.r.close()

    def test_version(self):
        code, out, _ = self.r.run('--version')
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith('qualm-next '))

    def test_dry_run_sends_nothing_and_never_passes(self):
        self.r.write('ledger.go', self.bad)
        code, out, _ = self.r.run('--dry-run')
        self.assertEqual(code, 2)
        self.assertIn('DRY RUN', out)
        self.assertIn('ledger.go', out)

    def test_the_default_base_is_the_default_branch(self):
        self.r.write('ledger.go', self.bad)
        code, out, err = self.r.run('--dry-run', '--format', 'json')
        self.assertEqual(code, 2, err)
        self.assertIn('Would judge 1 file', out)

    def test_no_key_cannot_judge_but_nothing_to_judge_passes(self):
        self.r.write('ledger.go', self.bad)
        code, _, err = self.r.run()
        self.assertEqual(code, 2)
        self.assertIn('OPENROUTER_API_KEY', err)
        self.r.git('checkout', '-q', '--', 'ledger.go')
        self.r.write('NOTES.md', 'notes\n')
        code, out, _ = self.r.run()
        self.assertEqual(code, 0, out)

    def test_a_keep_in_the_pull_request_does_not_count(self):
        self.r.write('ledger.go', self.bad)
        code, out, _ = self.r.run('keep', 'ledger.go', '--reason', 'accepted by a maintainer')
        self.assertEqual(code, 0)
        keeps = json.load(open(os.path.join(self.r.dir, 'qualm.json')))['keeps']
        self.assertEqual([k['path'] for k in keeps], ['ledger.go'])
        # the keep sits in the pull request: the file is still judged (no key here, so the run cannot pass)
        code, _, _ = self.r.run('--format', 'json')
        self.assertEqual(code, 2)

    def test_a_keep_in_the_base_keeps_the_change(self):
        self.r.git('checkout', '-q', 'main')
        self.r.write('ledger.go', self.bad)
        self.assertEqual(self.r.run('keep', 'ledger.go', '--reason', 'accepted by a maintainer')[0], 0)
        self.r.git('add', 'qualm.json')
        self.r.git('commit', '-q', '-m', 'keep')
        self.r.git('checkout', '-q', '-b', 'feature2')  # carries the uncommitted change to ledger.go
        code, out, err = self.r.run('--format', 'json', '--base', 'main')
        self.assertEqual(code, 0, err)
        rep = json.loads(out)
        self.assertEqual([f['status'] for f in rep['files'] if f['path'] == 'ledger.go'], ['kept'])

if __name__ == '__main__':
    unittest.main()
