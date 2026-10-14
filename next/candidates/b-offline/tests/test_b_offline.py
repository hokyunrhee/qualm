"""b-offline tests. Run from the repository root:

    python3 -m unittest discover -s next/candidates/b-offline/tests
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CAND = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(CAND, '..', '_core'))
sys.path.insert(0, CAND)

from gatecore import features  # noqa: E402
from gatecore.model import LogisticModel  # noqa: E402

FIX = os.path.join(CAND, '..', '..', 'eval', 'attacks', 'fixture')
ENV = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid', GIT_COMMITTER_NAME='t',
           GIT_COMMITTER_EMAIL='t@example.invalid', GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')


def read(p):
    with open(p) as fh:
        return fh.read()


class ModelTests(unittest.TestCase):
    def test_model_loads_with_a_threshold_inside_zero_one(self):
        m = LogisticModel.load(os.path.join(CAND, 'model.json'))
        self.assertTrue(0 < m.threshold < 1)

    def test_every_model_feature_is_one_the_core_computes(self):
        m = LogisticModel.load(os.path.join(CAND, 'model.json'))
        self.assertTrue(set(m.names) <= set(features.names()), sorted(set(m.names) - set(features.names())))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        base = read(os.path.join(FIX, 'base_ledger.go'))
        subprocess.run(['git', 'init', '-q', '-b', 'main', self.dir], check=True, env=ENV)
        with open(os.path.join(self.dir, 'ledger.go'), 'w') as fh:
            fh.write(base)
        subprocess.run(['git', '-C', self.dir, 'add', '-A'], check=True, env=ENV)
        subprocess.run(['git', '-C', self.dir, 'commit', '-q', '-m', 'base'], check=True, env=ENV)
        subprocess.run(['git', '-C', self.dir, 'branch', 'base'], check=True, env=ENV)
        self.base = base

    def tearDown(self):
        shutil.rmtree(self.dir)

    def run_with(self, text):
        with open(os.path.join(self.dir, 'ledger.go'), 'w') as fh:
            fh.write(text)
        p = subprocess.run([os.path.join(CAND, 'run'), '--repo', self.dir, '--base', 'base', '--format', 'json'],
                           capture_output=True, text=True, env=ENV)
        return p.returncode, json.loads(p.stdout) if p.stdout.strip() else None

    def test_the_payload_fails_with_located_findings(self):
        code, rep = self.run_with(self.base + read(os.path.join(FIX, 'payload_tags.go')))
        self.assertEqual(code, 1)
        f = rep['files'][0]
        self.assertTrue(f['failed'])
        self.assertTrue(any(d['id'] == 'debug_output' for d in f['findings']))

    def test_the_clean_change_passes(self):
        code, rep = self.run_with(self.base + read(os.path.join(FIX, 'clean_tags.go')))
        self.assertEqual(code, 0, rep)


if __name__ == '__main__':
    unittest.main()
