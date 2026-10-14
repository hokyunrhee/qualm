# b-offline

The offline candidate. It needs no network, no key and no money. It runs the shared core's detectors over the change and turns what they find, plus the shape of the change, into a probability with a logistic model trained on dev.

It tests one hypothesis: that the excess structure you can measure in the files predicts a reviewer's push-back as well as an LLM reading the diff does.

## What it does

1. **Reads the change inside the trust perimeter** (`../_core`), the same way every new candidate does.
2. **Runs the detectors** on the added lines:
   - debug output;
   - stubs and untracked TODOs;
   - commented-out code;
   - near-copies, by window fingerprints and unit similarity, within the change and against unchanged files beside it;
   - verbose constructs;
   - new functions nothing calls;
   - pass-through wrappers;
   - long or deeply nested functions;
   - guards whose own comment says the case cannot happen;
   - comments that narrate the edit.
3. **Scores each file** with a logistic model over size, shape and per-detector counts and confidences (`model.json`). The model is trained on the dev split with folds grouped by repository and calibrated with a Platt step.
4. **Fails a file** at probability ≥ 0.614, the highest out-of-fold recall on dev with precision at least 0.65. A failing file lists its findings with exact lines, what is wrong, how to fix it and when it is done.

## Running it

```
next/candidates/b-offline/run --repo . --base origin/main [--format text]
```

Exit 0 passes, 1 fails, 2 could not judge.

## How it does (dev, out-of-fold)

- PR-AUC 0.50 and ROC-AUC 0.80. qualm 0.1.1 scores PR-AUC 0.52 on the same items.
- On natural reviewer labels it is no better than change size alone (`n0-size`).
- At its operating point it catches few reviewer-flagged files, recall 0.13.

The detectors are precise on injected smells and noisy on natural code. Near-copy, for example, fires on 16% of dev files at precision 0.41.

`train.py` reproduces the model:

```
python3 -I train.py ../../data/dev.jsonl.gz --features CACHE.json --precision-floor 0.65 --oof OOF.json
```

## Known weaknesses

- Size dominates the model, and size does not transfer well across repositories.
- The detectors know syntax, not intent. A reviewer's "use the existing helper" is out of reach.
