"""Features for benchmark cases without a git checkout, for training on the dev split.

A case holds the base and head text of every changed file. The repository a materialized case
becomes holds exactly those files, so a change context built from them in memory sees what the
`run` command sees: no unchanged neighbours, and identifiers looked up in the base texts only.
"""
import re

from . import analyze, detectors, features, lexer, perimeter


class MemoryCtx(analyze.ChangeCtx):
    def __init__(self, files, base_texts):
        super().__init__(None, None, files, ())
        self.base_texts = base_texts

    def base_neighbours(self, f, limit=40, max_bytes=200_000):
        return []

    def referenced_in_base(self, name):
        rx = re.compile(r'(?<![\w$])' + re.escape(name) + r'(?![\w$])')
        return any(rx.search(t) for t in self.base_texts if t)


def judged_case_files(case, include_tests=False):
    out = []
    for fl in case['files']:
        lang = lexer.language_of(fl['path'])
        head = fl.get('head')
        if lang not in lexer.JUDGED or head is None or perimeter.is_minified(head):
            continue
        if not include_tests and perimeter.is_test(fl['path'], lang):
            continue
        out.append((fl, lang))
    return out


def case_features(case, paths=None):
    """{path: (features, findings)} for the judged files of a case (or just `paths`)."""
    files = []
    for fl, lang in judged_case_files(case):
        try:
            files.append(analyze.FileCtx(fl['path'], lang, fl['head'], fl.get('base'), 'A' if fl.get('base') is None else 'M', fl.get('old_path')))
        except (RecursionError, ValueError):
            continue
    have = {f.path for f in files}
    for fl in case['files']:
        # a scored file the generic rules would skip is still featurized, so every label has a row
        if paths and fl['path'] in paths and fl['path'] not in have and fl.get('head') is not None:
            lang = lexer.language_of(fl['path'])
            if lang in lexer.JUDGED:
                files.append(analyze.FileCtx(fl['path'], lang, fl['head'], fl.get('base'), 'M', fl.get('old_path')))
    ctx = MemoryCtx(files, [fl.get('base') for fl in case['files']])
    out = {}
    for f in files:
        if paths and f.path not in paths:
            continue
        found = detectors.run_all(f, ctx)
        out[f.path] = (features.file_features(f, found), found)
    return out
