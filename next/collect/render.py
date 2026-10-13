"""Render a benchmark item as a short unified diff excerpt, for people and perspective agents."""
import difflib


def excerpt(case, item, context=3, max_lines=60):
    f = next(x for x in case['files'] if x['path'] == item['path'])
    a = (f.get('base') or '').splitlines()
    b = f['head'].splitlines()
    diff = list(difflib.unified_diff(a, b, 'a/' + (f.get('old_path') or f['path']), 'b/' + f['path'], n=context, lineterm=''))
    loc = item.get('location')
    if loc and len(diff) > max_lines:
        # keep the hunks that cover the location
        keep, cur, cur_start = [], [], None
        for line in diff[2:]:
            if line.startswith('@@'):
                if cur:
                    keep.append((cur_start, cur))
                cur, cur_start = [line], int(line.split('+')[1].split(',')[0].split(' ')[0])
            else:
                cur.append(line)
        if cur:
            keep.append((cur_start, cur))
        near = [h for s, h in keep if s - 40 <= loc[0] <= s + len(h) + 40] or [keep[0][1]]
        diff = diff[:2] + [l for h in near for l in h]
    if len(diff) > max_lines:
        diff = diff[:max_lines] + ['... (%d more diff lines)' % (len(diff) - max_lines)]
    return '\n'.join(diff)
