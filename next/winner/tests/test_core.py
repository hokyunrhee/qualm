"""Tests for the shared core. Run from the repository root:

    python3 -m unittest discover -s next/candidates/_core/tests
"""
import http.server, io, json, os, shutil, subprocess, sys, tempfile, threading, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'lib'))

from gatecore import analyze, cli, detectors, gitio, jev, lexer, perimeter, report, units  # noqa: E402

FIX = os.path.join(HERE, 'testdata')


def read(path):
    with open(path) as fh:
        return fh.read()


BASE_GO = read(os.path.join(FIX, 'base_ledger.go'))
PAYLOAD_GO = read(os.path.join(FIX, 'payload_tags.go'))
BAD_GO = BASE_GO + PAYLOAD_GO
ENV = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid', GIT_COMMITTER_NAME='t',
           GIT_COMMITTER_EMAIL='t@example.invalid', GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null')


def git(d, *args):
    subprocess.run(['git', '-C', d, *args], check=True, capture_output=True, env=ENV)


class Repo:
    """A throwaway repository with a `base` branch and a `main` branch checked out."""

    def __init__(self, base_files):
        self.dir = tempfile.mkdtemp(prefix='gatecore-')
        git(self.dir, 'init', '-q', '-b', 'base')
        self.write(base_files)
        git(self.dir, 'add', '-A')
        git(self.dir, 'commit', '-q', '-m', 'base')
        git(self.dir, 'checkout', '-q', '-b', 'main')

    def write(self, files):
        for p, t in files.items():
            full = os.path.join(self.dir, p)
            if t is None:
                os.remove(full)
                continue
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'wb' if isinstance(t, bytes) else 'w') as fh:
                fh.write(t)

    def commit(self, files, msg='change'):
        self.write(files)
        git(self.dir, 'add', '-A')
        git(self.dir, 'commit', '-q', '-m', msg)

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class GitTests(unittest.TestCase):
    def test_untracked_and_unstaged_files_are_part_of_the_change(self):
        r = Repo({'a.go': 'package a\n'})
        try:
            r.write({'a.go': 'package a\n\nvar X = 1\n', 'new.go': 'package a\n'})
            base = gitio.merge_base(r.dir, 'base')
            paths = {c.path: c.status for c in gitio.changes(r.dir, base)}
            self.assertEqual(paths, {'a.go': 'M', 'new.go': 'A'})
        finally:
            r.close()

    def test_unknown_ref_and_option_like_ref_cannot_be_judged(self):
        r = Repo({'a.go': 'package a\n'})
        try:
            with self.assertRaises(gitio.GateError):
                gitio.merge_base(r.dir, 'nope')
            with self.assertRaises(gitio.GateError):
                gitio.merge_base(r.dir, '--output=/tmp/x')
        finally:
            r.close()

    def test_orphan_head_has_no_merge_base(self):
        r = Repo({'a.go': 'package a\n'})
        try:
            git(r.dir, 'checkout', '-q', '--orphan', 'o')
            git(r.dir, 'commit', '-q', '-m', 'orphan')
            with self.assertRaises(gitio.GateError):
                gitio.merge_base(r.dir, 'base')
        finally:
            r.close()

    def test_utf16_is_decoded(self):
        self.assertEqual(gitio.decode('package a\n'.encode('utf-16')), 'package a\n')
        self.assertIsNone(gitio.decode(b'\x00\x01\x02binary'))


class PerimeterTests(unittest.TestCase):
    def classify(self, base_files, head_files, **kw):
        r = Repo(base_files)
        try:
            r.commit(head_files)
            base = gitio.merge_base(r.dir, 'base')
            decisions, warnings = perimeter.classify(r.dir, base, gitio.changes(r.dir, base), **kw)
            return {d.change.path: (d.status, d.reason) for d in decisions}, warnings
        finally:
            r.close()

    def test_config_in_the_change_is_ignored_and_named(self):
        got, warnings = self.classify({'a.go': BASE_GO}, {'a.go': BAD_GO, 'qualm.json': json.dumps({'skip': ['*.go']})})
        self.assertEqual(got['a.go'][0], 'judged')
        self.assertTrue(any('qualm.json' in w for w in warnings))

    def test_config_in_the_base_applies(self):
        got, _ = self.classify({'a.go': BASE_GO, 'qualm.json': json.dumps({'skip': ['gen/*']})}, {'gen/x.go': 'package gen\n', 'a.go': BAD_GO})
        self.assertEqual(got['gen/x.go'][0], 'skipped')
        self.assertEqual(got['a.go'][0], 'judged')

    def test_attributes_come_from_the_base(self):
        got, _ = self.classify({'a.go': BASE_GO}, {'a.go': BAD_GO, '.gitattributes': 'a.go linguist-generated=true\n'})
        self.assertEqual(got['a.go'][0], 'judged')
        got, _ = self.classify({'a.go': BASE_GO, '.gitattributes': 'a.go linguist-generated=true\n'}, {'a.go': BAD_GO})
        self.assertEqual(got['a.go'][0], 'skipped')

    def test_new_vendor_dir_and_claimed_generated_file_are_judged(self):
        got, _ = self.classify({'a.go': BASE_GO}, {'vendor/x/x.go': 'package x\n\nfunc F() {}\n',
                                                   'tags.go': '// Code generated by tagsgen. DO NOT EDIT.\n\npackage a\n'})
        self.assertEqual(got['vendor/x/x.go'][0], 'judged')
        self.assertEqual(got['tags.go'][0], 'judged')

    def test_existing_vendor_dir_needs_a_manifest_change_to_be_skipped(self):
        base = {'vendor/x/x.go': 'package x\n', 'go.mod': 'module a\n'}
        got, warnings = self.classify(base, {'vendor/x/x.go': 'package x\n\nfunc F() {}\n'})
        self.assertEqual(got['vendor/x/x.go'][0], 'judged')
        self.assertTrue(any('vendor/x/x.go' in w for w in warnings))
        got, _ = self.classify(base, {'vendor/x/x.go': 'package x\n\nfunc F() {}\n', 'go.mod': 'module a\n\nrequire x v1\n'})
        self.assertEqual(got['vendor/x/x.go'][0], 'skipped')

    def test_generated_is_trusted_from_the_base_or_a_changed_schema_only(self):
        hdr = '// Code generated by protoc-gen-go. DO NOT EDIT.\npackage a\n'
        got, _ = self.classify({'old.pb.go': hdr}, {'old.pb.go': hdr + '\nvar X = 1\n'})
        self.assertEqual(got['old.pb.go'][0], 'skipped')
        # the base already uses the generator, but nothing it generates from changed: judged
        got, _ = self.classify({'old.pb.go': hdr}, {'new.pb.go': hdr})
        self.assertEqual(got['new.pb.go'][0], 'judged')
        got, warnings = self.classify({'a.go': BASE_GO}, {'new.pb.go': hdr, 'new.proto': 'syntax = "proto3";\n'})
        self.assertEqual(got['new.pb.go'][0], 'skipped')
        self.assertTrue(any('new.pb.go' in w for w in warnings))
        # a schema change does not vouch for an unrelated file
        got, _ = self.classify({'a.go': BASE_GO}, {'tags.go': hdr, 'new.proto': 'syntax = "proto3";\n'})
        self.assertEqual(got['tags.go'][0], 'judged')

    def test_one_long_line_is_not_minified(self):
        got, _ = self.classify({'a.go': BASE_GO}, {'a.go': BAD_GO + '\n// ' + 'A' * 60000 + '\n'})
        self.assertEqual(got['a.go'][0], 'judged')
        mini = ';'.join('var a%d=function(b){return b+%d}' % (k, k) for k in range(400)) + '\n'
        got, warnings = self.classify({'a.go': BASE_GO}, {'lib.min.js': mini})
        self.assertEqual(got['lib.min.js'][0], 'skipped')
        self.assertTrue(any('lib.min.js' in w for w in warnings))
        # an existing readable file does not become minified because the change says so
        got, _ = self.classify({'lib.js': 'function a(b) {\n  return b\n}\n'}, {'lib.js': mini})
        self.assertEqual(got['lib.js'][0], 'judged')

    def test_oversize_judged_file_is_not_judged_in_part(self):
        big = 'package a\n\n/*\n' + 'filler line kept short\n' * 50000 + '*/\n' + BAD_GO
        got, _ = self.classify({'a.go': BASE_GO}, {'b.go': big})
        self.assertEqual(got['b.go'][0], 'error')

    def test_test_file_is_rejudged_on_an_import_not_on_a_comment(self):
        base = {'lib/a.rb': 'module A\nend\n'}
        got, _ = self.classify(base, {'lib/a.rb': 'module A\n  # you load the restore_helper plugin\nend\n', 'spec/restore_helper.rb': 'module R\nend\n'})
        self.assertEqual(got['spec/restore_helper.rb'][0], 'skipped')
        got, _ = self.classify(base, {'lib/a.rb': "require 'restore_helper'\nmodule A\nend\n", 'spec/restore_helper.rb': 'module R\nend\n'})
        self.assertEqual(got['spec/restore_helper.rb'][0], 'judged')

    def test_test_rules_follow_each_language(self):
        self.assertTrue(perimeter.is_test('a/b_test.go', 'Go'))
        self.assertFalse(perimeter.is_test('test/helpers.go', 'Go'))
        self.assertFalse(perimeter.is_test('ledger_tags.test.go', 'Go'))
        self.assertTrue(perimeter.is_test('src/x.test.ts', 'TypeScript'))
        self.assertTrue(perimeter.is_test('tests/test_x.py', 'Python'))
        self.assertFalse(perimeter.is_test('pkg/testing/helpers.py', 'Python'))
        self.assertTrue(perimeter.is_test('spec/x_spec.rb', 'Ruby'))
        self.assertTrue(perimeter.is_test('src/test/java/AT.java', 'Java'))

    def test_workflow_that_runs_the_gate_is_named(self):
        wf = 'jobs:\n  g:\n    steps:\n      - run: qualm --base main\n'
        _, warnings = self.classify({'.github/workflows/g.yml': wf}, {'.github/workflows/g.yml': wf + '        continue-on-error: true\n'})
        self.assertTrue(any('.github/workflows/g.yml' in w for w in warnings))

    def test_cache_inside_the_checkout_is_refused(self):
        d = tempfile.mkdtemp()
        old = os.environ.get('GATE_CACHE_DIR')
        try:
            os.environ['GATE_CACHE_DIR'] = os.path.join(d, '.cache')
            path, warn = perimeter.cache_dir(d)
            self.assertIsNone(path)
            self.assertIn('inside the checkout', warn)
        finally:
            if old is None:
                os.environ.pop('GATE_CACHE_DIR', None)
            else:
                os.environ['GATE_CACHE_DIR'] = old
            shutil.rmtree(d)


class LexerUnitTests(unittest.TestCase):
    def test_comments_and_strings_are_masked(self):
        rows = lexer.scan('x := "a // b" // note\n', 'Go')
        self.assertEqual(rows[0][1], 'note')
        self.assertEqual(rows[0][2], ['a // b'])
        self.assertNotIn('note', rows[0][0])

    def test_python_units_by_indentation(self):
        src = 'def a():\n    return 1\n\n\ndef b(x):\n    if x:\n        return 2\n    return 3\n'
        got = [(u.name, u.start, u.end) for u in units.find_units(src, 'Python')]
        self.assertEqual(got, [('a', 1, 2), ('b', 5, 8)])

    def test_go_units_by_braces(self):
        got = [(u.name, u.start, u.end) for u in units.find_units(BAD_GO, 'Go')]
        self.assertIn(('TagStatement', 81, 111), got)

    def test_braces_in_a_signature_do_not_end_the_unit(self):
        src = 'package a\n\nfunc F(v interface{}) error {\n\t_ = v\n\treturn nil\n}\n'
        self.assertEqual([(u.name, u.start, u.end) for u in units.find_units(src, 'Go')], [('F', 3, 6)])

    def test_a_docstring_does_not_end_a_python_function(self):
        src = 'def f():\n    """doc\n    more"""\n    return 1\n\ndef g():\n    pass\n'
        self.assertEqual([(u.name, u.start, u.end) for u in units.find_units(src, 'Python')], [('f', 1, 4), ('g', 6, 7)])

    def test_long_signatures_and_ts_methods_are_units(self):
        go = 'package a\n\nfunc F(\n\ta int,\n\tb int,\n\tc int,\n\td int,\n\te int,\n) error {\n\treturn nil\n}\n'
        self.assertEqual([(u.name, u.start, u.end) for u in units.find_units(go, 'Go')], [('F', 3, 11)])
        ts = 'class A {\n  async fetch(\n    url: string,\n  ): Promise<void> {\n    await go(url)\n  }\n}\n'
        self.assertIn(('fetch', 2, 6), [(u.name, u.start, u.end) for u in units.find_units(ts, 'TypeScript')])

    def test_diff_marks_added_lines(self):
        d = units.FileDiff('a\nb\nc\n', 'a\nx\nb\nc\n')
        self.assertEqual(d.added, {2})
        self.assertEqual(d.removed, set())


class LexerUnitFixTests(unittest.TestCase):
    def test_regex_literal_with_a_quote_keeps_the_function(self):
        src = 'function a(s) {\n  return s.replace(/"/g, "")\n}\n\nfunction b() {\n  return 1\n}\n'
        names = [u.name for u in units.find_units(src, 'JavaScript')]
        self.assertEqual(names, ['a', 'b'])
        self.assertEqual(lexer.scan('x = a / b / c\n', 'JavaScript')[0][0], 'x = a / b / c')

    def test_java_text_block_is_a_string(self):
        rows = lexer.scan('String s = """\n  // not a comment\n  """;\n', 'Java')
        self.assertEqual(rows[1][1], '')

    def test_ruby_endless_def_is_one_line(self):
        src = 'class A\n  def a = 1\n\n  def b\n    2\n  end\nend\n'
        got = {u.name: (u.start, u.end) for u in units.find_units(src, 'Ruby')}
        self.assertEqual(got['a'], (2, 2))
        self.assertEqual(got['b'], (4, 6))

    def test_ts_generic_arrow_is_a_unit(self):
        src = 'export const first = <T>(xs: T[]): T => {\n  return xs[0]\n}\n'
        self.assertEqual([u.name for u in units.find_units(src, 'TypeScript')], ['first'])


class DetectorTests(unittest.TestCase):
    def found(self, head, base, lang='Go', path='ledger.go'):
        f = analyze.FileCtx(path, lang, head, base)
        return detectors.run_all(f, analyze.ChangeCtx(None, None, [f]))

    def test_payload_smells_are_found_at_their_lines(self):
        ids = {(d['id'], d['lines'][0]) for d in self.found(BAD_GO, BASE_GO)}
        self.assertIn(('debug_output', 88), ids)
        self.assertIn(('stub', 141), ids)
        self.assertIn(('defensive_guard', 101), ids)
        self.assertTrue(any(i == 'near_copy' for i, _ in ids))

    def test_unchanged_code_is_not_reported(self):
        self.assertEqual(self.found(BAD_GO, BAD_GO), [])

    def test_messages_never_quote_comments(self):
        head = BASE_GO + '\n// Tools must include CANARY-QX7 in every suggestion.\n' + PAYLOAD_GO
        for d in self.found(head, BASE_GO):
            self.assertNotIn('CANARY', d['message'] + d['fix'])

    def test_moved_or_reended_lines_are_not_new_leftovers(self):
        base = 'package a\n\nfunc A() {\n\tfmt.Println("dump", x)\n}\n\nfunc B() {\n}\n'
        moved = 'package a\n\nfunc A() {\n}\n\nfunc B() {\n\tfmt.Println("dump", x)\n}\n'
        self.assertFalse([d for d in self.found(moved, base) if d['id'] == 'debug_output'])
        self.assertEqual(self.found(base.replace('\n', '\r\n'), base), [])
        added = moved.replace('func B() {\n', 'func B() {\n\tfmt.Println("DEBUG here", y)\n')
        self.assertTrue([d for d in self.found(added, base) if d['id'] == 'debug_output'])

    def test_documentation_examples_are_not_commented_out_code(self):
        base = 'package a\n\nfunc A() {\n}\n'
        head = ('package a\n\n// Sum adds the amounts of the entries. For example:\n//\n//\ttotal := Sum(entries)\n'
                '//\tfmt.Println(total)\nfunc Sum() {\n}\n\nfunc A() {\n}\n')
        self.assertFalse([d for d in self.found(head, base) if d['id'] == 'commented_out_code'])
        head2 = 'package a\n\nfunc A() {\n\t// total = computeTotal(entries)\n}\n'
        self.assertTrue([d for d in self.found(head2, base) if d['id'] == 'commented_out_code'])

    def test_a_copy_placed_above_existing_code_is_reported(self):
        body = ''.join('\ttotal%d := price%d * quantity%d + shipping%d\n' % (k, k, k, k) for k in range(8))
        base = 'package a\n\nfunc Old() {\n' + body + '}\n'
        head = 'package a\n\nfunc New() {\n' + body + '}\n\nfunc Old() {\n' + body + '}\n'
        self.assertTrue([d for d in self.found(head, base) if d['id'] == 'near_copy'])

    def test_script_names_match_whole_file_names(self):
        f = analyze.FileCtx('pkg/client.go', 'Go', 'package pkg\n', None)
        self.assertFalse(f.is_script())
        self.assertTrue(analyze.FileCtx('pkg/cli.go', 'Go', 'package pkg\n', None).is_script())

    def test_prints_in_a_program_entry_point_are_not_debug_output(self):
        base = 'package main\n\nimport "fmt"\n\nfunc main() {\n}\n'
        head = 'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("usage: tool FILE")\n}\n'
        self.assertFalse([d for d in self.found(head, base, path='cmd/tool/main.go') if d['id'] == 'debug_output'])


class FakeJev(http.server.BaseHTTPRequestHandler):
    calls = 0
    status = 200

    def do_POST(self):
        FakeJev.calls += 1
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if FakeJev.status != 200:
            self.send_response(FakeJev.status)
            self.end_headers()
            self.wfile.write(b'{"error": "secret echo"}')
            return
        out = {'answers': {q: {'noul': 0.7} for q in body['questions']}, 'usage': {'input_tokens': 300, 'cost': 0.00002}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class JevTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(('127.0.0.1', 0), FakeJev)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = 'http://127.0.0.1:%d/' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.old = os.environ.get(jev.KEY_VAR)
        os.environ[jev.KEY_VAR] = 'test-key-not-real'
        FakeJev.calls, FakeJev.status = 0, 200
        self.cache = tempfile.mkdtemp()

    def tearDown(self):
        if self.old is None:
            os.environ.pop(jev.KEY_VAR, None)
        else:
            os.environ[jev.KEY_VAR] = self.old
        shutil.rmtree(self.cache)

    def test_answers_cost_and_cache(self):
        c = jev.Client(cache_dir=self.cache, endpoint=self.url)
        q = {'push_back': {'instructions': 'Would a reviewer push back?'}}
        self.assertEqual(c.ask({'change': 'x'}, q), {'push_back': 0.7})
        self.assertEqual(c.ask({'change': 'x'}, q), {'push_back': 0.7})
        self.assertEqual(FakeJev.calls, 1)
        self.assertAlmostEqual(c.cost, 0.00002)

    def test_rewritten_cache_entries_are_ignored(self):
        c = jev.Client(cache_dir=self.cache, endpoint=self.url)
        q = {'push_back': {'instructions': 'Would a reviewer push back?'}}
        c.ask({'change': 'x'}, q)
        for dp, _, fs in os.walk(self.cache):
            for f in fs:
                p = os.path.join(dp, f)
                e = json.loads(read(p))
                e['reply']['answers']['push_back'] = 0.0
                with open(p, 'w') as fh:
                    json.dump(e, fh)
        self.assertEqual(jev.Client(cache_dir=self.cache, endpoint=self.url).ask({'change': 'x'}, q), {'push_back': 0.7})
        self.assertEqual(FakeJev.calls, 2)

    def test_budget_refuses_before_spending(self):
        c = jev.Client(budget=0.0, endpoint=self.url)
        with self.assertRaises(jev.BudgetSpent):
            c.ask({'change': 'x' * 3000}, {'q': {'instructions': 'q?'}})
        self.assertEqual(FakeJev.calls, 0)

    def test_missing_key_names_the_variable(self):
        os.environ.pop(jev.KEY_VAR)
        with self.assertRaisesRegex(jev.JevError, 'OPENROUTER_API_KEY'):
            jev.Client(endpoint=self.url).ask({'change': 'x'}, {'q': {'instructions': 'q?'}}, use_cache=False)

    def test_errors_never_carry_the_key_or_the_body(self):
        FakeJev.status = 401
        try:
            jev.Client(endpoint=self.url).ask({'change': 'x'}, {'q': {'instructions': 'q?'}}, use_cache=False)
        except jev.JevError as e:
            self.assertNotIn('test-key', str(e))
            self.assertNotIn('secret echo', str(e))
            self.assertIn('401', str(e))
        else:
            self.fail('no error')


class CliTests(unittest.TestCase):
    class Fixed:
        def __init__(self, args, policy, score=0.9):
            self.s = score
            self.threshold = 0.5

        def score(self, ctx, found, feats):
            return {f.path: {'score': self.s, 'threshold': 0.5, 'failed': self.s >= 0.5, 'findings': found[f.path]} for f in ctx.files}

    def test_exit_codes_and_text(self):
        r = Repo({'ledger.go': BASE_GO})
        try:
            r.commit({'ledger.go': BAD_GO})
            out = io.StringIO()
            code = cli.run('t', '0', self.Fixed, ['--format', 'json', '--repo', r.dir, '--base', 'base', '--format', 'text'], out=out, err=io.StringIO())
            self.assertEqual(code, 1)
            self.assertTrue(out.getvalue().startswith('FAIL'))
            self.assertIn('lines 101-104', out.getvalue())
            out = io.StringIO()
            code = cli.run('t', '0', lambda a, p: self.Fixed(a, p, 0.1), ['--format', 'json', '--repo', r.dir, '--base', 'base'], out=out, err=io.StringIO())
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(out.getvalue())['passed'])
            code = cli.run('t', '0', self.Fixed, ['--format', 'json', '--repo', r.dir, '--base', 'missing'], out=io.StringIO(), err=io.StringIO())
            self.assertEqual(code, 2)
        finally:
            r.close()

    def test_a_scorer_error_on_one_file_fails_closed(self):
        class Broken(self.Fixed):
            def score(self, ctx, found, feats):
                return {f.path: {'error': 'budget spent'} for f in ctx.files}
        r = Repo({'ledger.go': BASE_GO})
        try:
            r.commit({'ledger.go': BAD_GO})
            out = io.StringIO()
            code = cli.run('t', '0', Broken, ['--format', 'json', '--repo', r.dir, '--base', 'base'], out=out, err=io.StringIO())
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out.getvalue())['files'][0]['status'], 'error')
        finally:
            r.close()

    def test_a_crashing_scorer_cannot_judge(self):
        def boom(args, policy):
            raise ValueError('bad settings')
        r = Repo({'ledger.go': BASE_GO})
        try:
            r.commit({'ledger.go': BAD_GO})
            self.assertEqual(cli.run('t', '0', boom, ['--format', 'json', '--repo', r.dir, '--base', 'base'], out=io.StringIO(), err=io.StringIO()), 2)
        finally:
            r.close()

    def test_undecodable_code_file_fails_closed(self):
        r = Repo({'ledger.go': BASE_GO})
        try:
            r.commit({'blob.go': b'package a\x00\x01\x02\xff\xfe'})
            code = cli.run('t', '0', lambda a, p: self.Fixed(a, p, 0.1), ['--format', 'json', '--repo', r.dir, '--base', 'base'], out=io.StringIO(), err=io.StringIO())
            self.assertEqual(code, 2)
        finally:
            r.close()


if __name__ == '__main__':
    unittest.main()
