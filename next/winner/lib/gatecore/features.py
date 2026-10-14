"""One feature vector per judged file: the size and shape of the change and what the detectors found."""
import math

from . import detectors

LANGS = ('Go', 'Python', 'TypeScript', 'JavaScript', 'Ruby', 'Java')
DETECTOR_IDS = ('debug_output', 'defensive_guard', 'placeholder', 'stub', 'commented_out_code', 'near_copy', 'verbose_construct',
                'unused_code', 'pass_through', 'long_function', 'deep_nesting', 'change_narration')


def file_features(f, findings):
    added = f.diff.n_added
    removed = f.diff.n_removed
    code_added = len(f.added_code_lines())
    comment_added = sum(1 for n in f.added if n <= len(f.comments) and f.comments[n - 1] and not f.code[n - 1].strip())
    new_units = f.new_units()
    changed = f.changed_units(min_added=1)
    x = {
        'log_added': math.log1p(added),
        'log_removed': math.log1p(removed),
        'added_share': added / max(1, added + removed),
        'new_file': 1.0 if f.base is None else 0.0,
        'log_file_lines': math.log1p(len(f.lines)),
        'log_hunks': math.log1p(len(f.diff.hunks)),
        'comment_share': comment_added / max(1, code_added + comment_added),
        'n_new_units': math.log1p(len(new_units)),
        'n_changed_units': math.log1p(len(changed)),
        'max_new_unit': math.log1p(max([u.length for u in new_units] or [0])),
        'max_depth': max([detectors.nesting(f, u.start, u.end) for u, _ in changed] or [0]) / 5.0,
        'is_script': 1.0 if f.is_script() else 0.0,
    }
    for lang in LANGS:
        x['lang_' + lang] = 1.0 if f.language == lang else 0.0
    for did in DETECTOR_IDS:
        hits = [d for d in findings if d['id'] == did]
        x['n_' + did] = math.log1p(len(hits))
        x['max_' + did] = max([d['confidence'] for d in hits] or [0.0])
    copied = set()
    for d in findings:
        if d['id'] == 'near_copy':
            copied.update(range(d['lines'][0], d['lines'][1] + 1))
    x['copy_share'] = len(copied & set(f.added_code_lines())) / max(1, code_added)
    return x


def names():
    """Feature names in a fixed order."""
    base = ['log_added', 'log_removed', 'added_share', 'new_file', 'log_file_lines', 'log_hunks', 'comment_share', 'n_new_units',
            'n_changed_units', 'max_new_unit', 'max_depth', 'is_script']
    base += ['lang_' + l for l in LANGS]
    for did in DETECTOR_IDS:
        base += ['n_' + did, 'max_' + did]
    base.append('copy_share')
    return base
