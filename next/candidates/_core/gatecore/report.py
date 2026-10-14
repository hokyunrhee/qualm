"""The two renderings of a verdict: JSON for machines, text for the coding agent that fixes the change."""
import json

PRICE_NOTE = 'LLM costs are estimates from token counts at list price.'


def empty_usage():
    return {'jev_requests': 0, 'jev_input_tokens': 0, 'jev_cost_usd': 0.0, 'llm_requests': 0, 'llm_input_tokens': 0,
            'llm_output_tokens': 0, 'llm_cost_usd': 0.0, 'llm_cost_estimated': True, 'seconds': 0.0}


def to_json(rep):
    return json.dumps(rep, indent=1, sort_keys=False)


def to_text(rep):
    files = rep['files']
    failing = [f for f in files if f.get('failed')]
    judged = [f for f in files if f.get('status') == 'judged']
    out = []
    if rep['passed']:
        out.append('PASS: none of the %d changed code file%s needs simplifying before merge.' % (len(judged), '' if len(judged) == 1 else 's'))
    else:
        errors = [f for f in files if f.get('status') == 'error']
        if errors and not failing:
            out.append('FAIL: the gate could not judge %d file%s, so it does not pass the change.' % (len(errors), '' if len(errors) == 1 else 's'))
        else:
            out.append('FAIL: %d of %d changed code file%s should be simpler before merge.' % (len(failing), len(judged), '' if len(judged) == 1 else 's'))
    for f in failing:
        out.append('')
        out.append('%s  (score %.2f, gate %.2f)' % (f['path'], f['score'], f['threshold']))
        if not f.get('findings'):
            out.append('  The change as a whole reads as more complicated than it needs to be, but no single spot stood out.')
            out.append('  Fix: re-read the added code and remove what the feature does not need: duplicated steps, unused helpers, defensive checks for impossible states, leftover debugging.')
        for d in f.get('findings') or []:
            lo, hi = d['lines']
            span = 'line %d' % lo if lo == hi else 'lines %d-%d' % (lo, hi)
            out.append('  - %s [%s, confidence %.2f]' % (span, d['id'], d['confidence']))
            out.append('    What: %s' % d['message'])
            out.append('    Fix: %s' % d['fix'])
            for r in d.get('related') or []:
                out.append('    See: %s lines %d-%d' % (r['path'], r['lines'][0], r['lines'][1]))
    advice = [(f, d) for f in files if not f.get('failed') and f.get('status') == 'judged' for d in (f.get('findings') or [])]
    if advice and not rep['passed']:
        out.append('')
        out.append('Advice below the gate (optional):')
        for f, d in advice[:8]:
            lo, hi = d['lines']
            out.append('  - %s:%d-%d %s' % (f['path'], lo, hi, d['message']))
    for f in files:
        if f.get('status') == 'error':
            out.append('')
            out.append('%s: could not judge: %s' % (f['path'], f.get('reason', '')))
    if rep.get('warnings'):
        out.append('')
        for w in rep['warnings']:
            out.append('Warning: %s' % w)
    out.append('')
    if rep['passed']:
        out.append('Nothing to do.')
    elif failing:
        out.append('Next: make the fixes above, keep the behaviour and the tests as they are, then run the gate again. '
                   'Do not rename, reformat or add comments to get past it; it judges what the code does.')
    else:
        out.append('Next: fix the problem above (or ask a maintainer), then run the gate again.')
    return '\n'.join(out) + '\n'
