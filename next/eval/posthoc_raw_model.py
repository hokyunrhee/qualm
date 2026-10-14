"""Post-hoc ablation, labelled as such in REPORT.md: qualm 0.1.1's own ten answers, weighed by a
logistic model trained on dev only (d-cascade's train.py, --set raw, the same threshold rule), and
applied unchanged to the baseline's recorded answers. No new Jev request, no tuning on the split it
scores. It asks how much of d-cascade's gain a one-request qualm with a trained model would keep.

    python3 -I eval/posthoc_raw_model.py MODEL.json BASELINE_RUN.jsonl OUT.jsonl
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'candidates', '_core'))
sys.path.insert(0, os.path.join(HERE, '..', 'candidates', 'd-cascade'))

from gatecore.model import LogisticModel  # noqa: E402
import scorer  # noqa: E402


def main():
    model_path, run_path, out_path = sys.argv[1:4]
    model = LogisticModel.load(model_path)
    with open(run_path) as fh, open(out_path, 'w') as out:
        for line in fh:
            rec = json.loads(line)
            rep = rec.get('report')
            if rep:
                rep['tool'] = 'a-rawmodel'
                passed = True
                for f in rep.get('files', []):
                    if f.get('status') != 'judged':
                        continue
                    sig = f.get('signals') or {}
                    if 'push_back' not in sig:
                        f['status'] = 'error'
                        passed = False
                        continue
                    p = model.predict(scorer.feature_vector(sig, None, {}))
                    f['score'], f['threshold'] = round(p, 5), model.threshold
                    f['failed'] = p >= model.threshold
                    passed = passed and not f['failed']
                rep['passed'] = passed
                if rec.get('exit') in (0, 1):
                    rec['exit'] = 0 if passed else 1
            rec['tag'] = 'posthoc-rawmodel'
            out.write(json.dumps(rec) + '\n')


if __name__ == '__main__':
    main()
