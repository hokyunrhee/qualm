# e-spot

e-spot asks Jev about each changed function rather than about a whole file's diff. It tests one claim: qualm 0.1.1 misses most of the changes reviewers flag because it judges a file at a time with too little context, not because Jev is weak. Reviewers push back on spots, such as one function or a few lines.

**What dev says about that claim:** on natural labels, the per-unit answers carry about as much information as the size of the change. e-spot ranks files better than qualm's `push_back` (natural stratum PR-AUC 0.425 against 0.405). It does not clearly beat a model of size and language alone (0.417 on natural labels, and 0.605 against e-spot's 0.593 on reviewer comments against approved files). It ranks worse than d-cascade, which recombines qualm's file-level answers (0.468 natural, 0.602 pooled). Its clear gain is the report: findings sit on the unit the reviewer commented on, 14 lines wide at the median instead of 56. The numbers are in `DEV_RESULTS.md`.

## What it does

For each changed code file (the shared core decides which files are judged, reading policy from the base commit):

1. **Spots.** It picks the changed top-level units, meaning functions and methods with at least 3 added code lines. Added code outside every unit becomes one more spot, the module-level code. A file gets at most 8 spots, the ones with the most added code, and the signals list what was left out. A file whose change is too small for any spot to reach 3 lines still gets its largest spot, because reviewers often comment on one- or two-line changes. A unit too long to show in one request is cut into parts, each showing the unit's first line and its own added lines, so no added line is hidden from Jev.
2. **One Jev request per spot** (`typesafe/jev-1.13`, qualm's wire format through `gatecore.jev`). The request carries:
   - the language and the path;
   - a short outline of the file: function names and line ranges, with new and changed ones marked;
   - the unit after the change, with head line numbers and `+` on added lines;
   - the unit before the change, with `-` on removed lines, when it existed;
   - the most similar other unit in the file, when one holds at least half of the spot's added code shape.

   Every comment is replaced by a placeholder. Every string literal of six or more words, or one that spans lines, is replaced by a placeholder too. Text in the change therefore cannot talk to the judge. Six questions are asked as probabilities, one per reviewer verb (remove, reuse, merge, shorten, split) plus `push_back`. They are all in `questions.py`. Up to 6 requests run at once, all through one budget.
3. **Score.** The answers are folded per file into a max and a size-weighted mean per question, a noisy-or of `push_back`, and spot counts. These join the core's features (size, shape, detector hits) in a logistic model trained on dev only, with folds grouped by repository and a Platt step (`train.py`, `model.json`). Scoring uses only `gatecore.model.LogisticModel`, so the standard library is enough at run time. The file fails when its probability reaches the threshold, 0.494. That threshold was chosen on out-of-fold dev scores as the highest recall whose precision stays at or above qualm 0.1.1's precision on dev (0.61).
4. **Close calls.** A file within 0.05 of the threshold has each of its spots asked four more times and is judged on the mean of the five answers. The settled answers are cached under their own key.
5. **Findings.** Each spot whose answers are unusual becomes one finding at that spot's added lines. "Unusual" means at or above the 90th percentile of Jev's answers about spots in dev files no reviewer flagged (the table is in `model.json`). The finding names the spot's rarest verb and carries a plain-words message, a fix template for that verb and a "done when" clause. A merge finding points through `related` at the similar unit. The core detectors' exact-line findings come first. A failing file always carries at least one finding; passing files carry advice only above the 98th percentile. Messages name line numbers and unit names only; nothing from the change's comments or strings is echoed.
6. **Could not judge.** The run exits 2 and never passes when there is no key, when Jev still fails after retries (the core client retries 429 and 5xx; e-spot also retries an HTTP 499 once), or when the budget is spent. A crash also exits 2.

## Running it

```
OPENROUTER_API_KEY=... next/candidates/e-spot/run --repo PATH --base REF [--format json|text] [--budget DOLLARS] [--no-cache]
```

- **Key:** `OPENROUTER_API_KEY`, read from the environment of the process that calls Jev, and never printed or stored. Without it the run exits 2 with `no API key`.
- **Budget:** `--budget` caps what one run may spend on Jev; the default is $0.25. A spent budget exits 2.
- **Cache:** answers are cached in `GATE_CACHE_DIR` (or the user cache directory), signed with a key derived from the API key. The cache is never used inside the checkout, so a pull request cannot forge it. `--no-cache` turns it off.
- **Exit codes:** 0 passed, 1 failed, 2 could not judge.
- `usage` reports `jev_requests`, `jev_input_tokens` and `jev_cost_usd`, the cost Jev reported for the requests this run actually sent (cache hits cost nothing).

Tests, from the worktree root (a local fake Jev endpoint; the real key is never used):

```
python3 -m unittest discover -s next/candidates/e-spot/tests
```

Retraining (dev only; asking Jev costs about $0.85 cold, and the signed cache makes a repeat free):

```
python3 -I next/candidates/e-spot/train.py estimate next/data/dev.jsonl.gz --rows ROWS.pkl                  # no network: spots, requests, cost
GATE_CACHE_DIR=... python3 -I next/candidates/e-spot/train.py ask --rows ROWS.pkl --answers ANS.jsonl --budget 1.0 --ledger LEDGER
python3 -I next/candidates/e-spot/train.py fit --rows ROWS.pkl --answers ANS.jsonl --report dev_cv.json --oof oof_dev.json
```

`ask` takes `--sample-cost` to sample files stratified by language and label source with a fixed seed when the full set would cost more. That was not needed here: every one of the 5,139 scored dev files was asked about.

## Cost per pull request

These were measured on dev with cold runs (`--no-cache`) over the fixed `det100` subset, two runs, using what Jev reported:

| | median | p90 | max | mean |
|---|---|---|---|---|
| Jev cost per PR | $0.0004 | $0.0025 | $0.013 | $0.0010 |
| Jev requests per PR | 5 | 35 | | |
| seconds per PR | 1.2 | 4.5 (p95) | | |

A typical PR costs well under a tenth of a cent. A request averages about 1,700 input tokens, which is $0.00007.

## Changes from the brief, and why

- **Every file with added code is asked about.** Without this fallback, files whose change is one or two lines get no Jev answer. Reviewer comments are often on exactly such lines.
- **Long units are cut into parts** instead of truncated, so every added line reaches Jev.
- **`similar` context** gives the merge and reuse questions the evidence they need, which usually sits outside the hunk. It is sent only when a strong match exists, so it costs little.
- **Unit boundaries are repaired inside e-spot** where the core's finder gives up: a parameter list and return type running over more than four lines (common in formatted TypeScript, Go and Java) made the core drop the function, and its code was then judged as module-level. The core's later fixes for braces in signatures and for Python docstrings are merged; e-spot's own re-indentation for docstrings now has no effect but is harmless.
- **Finding cuts are per verb and relative to clean code.** A flat 0.5 cut let `shorten` dominate, because Jev rates most code as shortenable (its median answer on clean files is 0.38). Picking one verb per spot by how unusual its answer is raised the reviewer-location hit rate on failing files from 0.75 to 0.85 on out-of-fold dev, at the same number of findings (2.7 per failing file).
- **Close calls take the mean of five draws** within 0.05 of the threshold. qualm's median of three within 0.04 still let 1 of 59 files flip between two cold runs of `det30`. After the change, 1 of 421 files flipped on `det100`, and no PR verdict flipped.
- **The threshold sits at qualm's own precision** rather than at the best F1, because the study's definition of done asks for precision not lower than the baseline's.

## Known weaknesses

- **The central claim did not hold on natural labels.** The unit answers add little beyond change size. On reviewer comments against approved files, size and language alone score 0.605 PR-AUC and e-spot 0.593. The verbs add 0.017 PR-AUC over the `push_back`-only unit model, short of the staff engineer's 0.02 bar. Pooled, e-spot is at 0.533 against 0.524 for qualm's raw `push_back`. d-cascade, which recombines qualm's ten file-level answers with the core's features, is at 0.602 pooled and 0.468 natural, ahead of e-spot in every language but Ruby and JavaScript. Asking per unit, as built here, did not beat asking per file.
- **Low recall at a gate precision.** At the threshold e-spot fails 25% of reviewer-flagged files and 69% of mechanically injected smells. It fails 2.2% of approved files, but 20% of the post-fix versions of reviewer-flagged files. It can barely tell a file before a reviewer's fix from after it (ROC 0.54). qualm and d-cascade (both 0.55) and the size model (0.52) can't either. On matched pairs it does worse than qualm's file-level `push_back`, because there its size features cannot help. It scores the flagged version above the post-fix version in 57% of pairs (qualm 65%) and an injected file above its original in 91% (qualm 98%).
- **Masking costs evidence.** Comments are hidden, but some requests are justified by them, for example a docstring that admits a check does nothing. Identifiers and paths are not masked, so a pull request can still speak through names.
- **Ruby and GitLab changes do poorly.** Ruby recall at the gate is 14%. Items from GitLab have an out-of-fold PR-AUC of 0.30.
- **Findings say which verb and where, not what to write.** A spot finding's `confidence` is Jev's raw answer, not a calibrated probability. A finding covers the spot's added lines, which for a new function means the whole function.
- **Unit detection is heuristic.** It finds units by indentation and braces, nested functions fold into their parent, and class bodies count as module-level code.
- **Jev is not deterministic.** Cold runs differ by about 0.002 in score at the median. Close calls are settled, but a file can still flip.
