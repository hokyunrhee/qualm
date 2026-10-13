"""Translate qualm 0.1.1's JSON report into the shared report (next/interface.md)."""
import json, sys, time

QUESTIONS = {q['id']: q for q in json.load(open(__file__.rsplit('/', 4)[0] + '/internal/questions/builtin.json'))}


def main(path, start):
    r = json.load(open(path))
    files = []
    for f in r.get('files') or []:
        out = {'path': f['path'], 'status': f['status']}
        if f.get('reason'):
            out['reason'] = f['reason']
        a = f.get('answers') or {}
        if f['status'] == 'judged':
            gate = a.get('push_back', {}).get('value')
            out.update(score=gate, threshold=0.6, failed=bool(f.get('failed')), abstained=False,
                       signals={k: v.get('value') for k, v in a.items()})
            findings = []
            for qid, v in sorted(a.items(), key=lambda kv: -kv[1].get('value', 0)):
                q = QUESTIONS.get(qid, {})
                if q.get('role') == 'diagnosis' and v.get('value', 0) >= 0.5:
                    where = (f.get('where') or {}).get(qid)
                    findings.append({'id': qid, 'lines': list(where) if where else None,
                                     'message': q.get('instructions', ''), 'fix': 'Rework the change so this no longer applies.',
                                     'confidence': v['value']})
            out['findings'] = findings
        files.append(out)
    u = r.get('usage') or {}
    print(json.dumps({'tool': 'a-baseline', 'version': '0.1.1', 'passed': r.get('passed'), 'base': r.get('base'), 'files': files,
                      'warnings': r.get('warnings') or [],
                      'usage': {'jev_requests': u.get('requests', 0), 'jev_input_tokens': u.get('input_tokens', 0),
                                'jev_cost_usd': u.get('cost', 0), 'llm_requests': 0, 'llm_input_tokens': 0, 'llm_output_tokens': 0,
                                'llm_cost_usd': 0, 'llm_cost_estimated': False, 'seconds': round(time.time() - float(start), 3)}}, indent=1))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
