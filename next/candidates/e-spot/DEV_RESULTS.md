# e-spot on dev

All numbers are from the dev split only. Model numbers are out-of-fold: 5 folds grouped by repository (301 repositories), so no repository is in a model's training and test folds at once. They cover 5,139 scored files with 1,133 positives. Every scored file was asked about; nothing was sampled. The model is `model.json` and the full report is `dev_cv.json`. The out-of-fold scores are in `oof_dev.json`, in the `{item_id: score}` shape `next/eval/oof_run.py` reads.

## Out-of-fold, pooled

| | value |
|---|---|
| PR-AUC | 0.533 (prevalence 0.22) |
| ROC-AUC | 0.807 |
| Brier | 0.135 |
| Threshold | 0.494 (the highest recall with precision ≥ 0.61, qualm 0.1.1's precision on dev at its own gate) |
| Precision / recall at the threshold | 0.610 / 0.300 |
| Files failed | 10.8% |

## Against the other candidates, same items (PR-AUC / ROC-AUC)

qualm's `push_back` is its raw score from the full dev run. The others are their out-of-fold scores in `next/eval/results/oof/`.

| stratum | n | e-spot | qualm push_back | d-cascade | b-offline | a-recal | n0-size |
|---|---|---|---|---|---|---|---|
| pooled | 5139 | 0.533 / 0.807 | 0.524 / 0.781 | 0.602 / 0.832 | 0.499 / 0.798 | 0.532 / 0.778 | 0.448 / 0.784 |
| natural | 4859 | 0.425 / 0.782 | 0.405 / 0.751 | 0.468 / 0.801 | 0.408 / 0.777 | 0.410 / 0.748 | 0.417 / 0.780 |
| reviewer vs approved | 4053 | 0.593 / 0.838 | 0.518 / 0.797 | 0.643 / 0.858 | 0.577 / 0.835 | 0.524 / 0.793 | 0.605 / 0.842 |
| reviewer vs post-fix | 1567 | 0.625 / 0.544 | 0.654 / 0.552 | 0.640 / 0.553 | 0.604 / 0.524 | 0.654 / 0.552 | 0.601 / 0.521 |
| injected vs clean | 1896 | 0.762 / 0.953 | 0.753 / 0.947 | 0.919 / 0.987 | 0.691 / 0.941 | 0.755 / 0.946 | 0.368 / 0.885 |

"Natural" is reviewer and follow-up positives against approved, post-fix and survived negatives, as in `metrics.py`.

### Through the study's `metrics.py`, with bootstrap intervals

These numbers come from `oof_run.py` and `metrics.py` with 1,000 case resamples, against qualm 0.1.1's dev run. Each cell is the difference from qualm with its 95% interval.

| | e-spot | d-cascade | b-offline | n0-size | a-recal |
|---|---|---|---|---|---|
| natural PR-AUC | +0.018 [−0.022, +0.056] | +0.060 [+0.028, +0.089] | +0.000 [−0.044, +0.045] | +0.009 [−0.034, +0.054] | −0.003 [−0.014, +0.002] |
| natural ROC-AUC | +0.026 [+0.000, +0.048] | +0.045 [+0.022, +0.065] | +0.020 [−0.009, +0.047] | +0.024 [−0.007, +0.051] | −0.009 [−0.022, −0.001] |
| natural Brier | −0.029 [−0.037, −0.020] | −0.033 [−0.041, −0.025] | −0.026 [−0.036, −0.017] | −0.028 [−0.037, −0.019] | −0.023 [−0.030, −0.015] |
| pooled PR-AUC | +0.000 [−0.034, +0.036] | +0.069 [+0.041, +0.096] | −0.033 [−0.071, +0.005] | −0.081 [−0.122, −0.041] | −0.005 [−0.014, +0.001] |

Pooled `metrics.py` values: e-spot has PR-AUC 0.524, ROC-AUC 0.796, Brier 0.137, ECE 0.015, and precision/recall 0.605/0.294 at its gate. qualm has 0.524 / 0.781 / 0.161, ECE 0.146, and 0.610/0.312.

Matched pairs, as P(the flagged version scores higher):

| pair | e-spot | qualm | d-cascade |
|---|---|---|---|
| reviewer-flagged file vs its post-fix version (588 pairs) | 0.572 | 0.647 | 0.622 |
| injected file vs its original (137 pairs) | 0.912 | 0.982 | 0.956 |

On matched pairs, where size barely changes, e-spot is worse than qualm's file-level `push_back`.

`metrics.py` gives e-spot 0.524 pooled where the item-level table above gives 0.533. The gap comes from a harness quirk: 11 `case_id`s appear on 25 lines of `dev.jsonl.gz`, and `metrics.load_run` keeps one record per `case_id`. The other lines' 14 scored items, all of them positives, are therefore read as score 0. Every candidate's `metrics.py` numbers lose the same items.

## By language (out-of-fold, all labels)

| language | n | positives | PR-AUC | ROC-AUC | Brier | precision | recall | files failed |
|---|---|---|---|---|---|---|---|---|
| Go | 1375 | 350 | 0.498 | 0.783 | 0.158 | 0.514 | 0.309 | 15.3% |
| Python | 1371 | 360 | 0.551 | 0.783 | 0.156 | 0.615 | 0.378 | 16.1% |
| TypeScript | 907 | 162 | 0.539 | 0.835 | 0.112 | 0.661 | 0.228 | 6.2% |
| Java | 912 | 132 | 0.555 | 0.853 | 0.092 | 0.829 | 0.220 | 3.8% |
| Ruby | 411 | 94 | 0.533 | 0.774 | 0.154 | 0.867 | 0.138 | 3.7% |
| JavaScript | 163 | 35 | 0.748 | 0.857 | 0.103 | 0.850 | 0.486 | 12.3% |

## By label source (out-of-fold, at the threshold)

PR-AUC is undefined within a single-label source, so this table gives how often each source fails, next to qualm 0.1.1 at its gate of 0.6 on the same files.

| label source | n | e-spot fails | qualm fails | Brier |
|---|---|---|---|---|
| reviewer_comment (+) | 923 | 24.6% | 22.9% | 0.451 |
| followup_cleanup (+) | 26 | 7.7% | 38.5% | 0.559 |
| injection_mechanical (+) | 144 | 68.8% | 78.5% | 0.204 |
| injection_llm (+) | 40 | 30.0% | 50.0% | 0.346 |
| approved_clean (−) | 1616 | 2.2% | 4.1% | 0.033 |
| approved_uncommented (−) | 1234 | 2.3% | 2.4% | 0.037 |
| approved_agent (−) | 280 | 6.1% | 3.9% | 0.065 |
| survived_90d (−) | 136 | 4.4% | 11.8% | 0.082 |
| post_fix (−) | 644 | 20.2% | 14.8% | 0.140 |
| hard_negative (−) | 96 | 0.0% | 8.3% | 0.021 |

## What the unit answers add (out-of-fold PR-AUC, pooled)

| features | PR-AUC | ROC-AUC |
|---|---|---|
| core features only (size, shape, detectors, language) | 0.499 | 0.798 |
| unit `push_back` only (+ size, language) | 0.493 | 0.797 |
| all six unit answers (+ size, language) | 0.510 | 0.800 |
| all six unit answers + core features (e-spot) | 0.533 | 0.807 |
| size and language only | 0.453 | 0.784 |

On natural labels, the unit answers with language alone (no size) reach 0.425 PR-AUC, and size with language alone reaches 0.421. On reviewer comments against approved files they reach 0.595 and 0.612. The answers mostly measure how much code changed. The five verbs add 0.017 PR-AUC over `push_back` alone, short of the 0.02 the staff engineer set as the bar for "the verbs are not decoration". A gradient-boosted model over the same features reaches 0.533, no better, so the logistic form is not what limits it. Other aggregates gained nothing either: noisy-or per verb, logit means, the top two spots, the largest spot's answers.

## Where the findings point

Out-of-fold, 227 reviewer positives fail the gate. 193 of them (85%) carry a spot finding within 2 lines of the reviewer's comment, with 2.7 spot findings per failing file.

From real `run` reports on the fixed `dev150` subset, scored by `metrics.py` (the verdicts there are in-sample because the model was trained on all of dev, but the findings are not trained):

| | e-spot | qualm 0.1.1 |
|---|---|---|
| failing positives with a finding on the location | 28 / 28 | 25 / 29 |
| ... with a finding under 15 lines | 61% | 0% |
| ... with intersection-over-union ≥ 0.2 | 64% | 38% |
| median finding width | 14 lines | 56 lines |

Every failing file in every run carried at least one finding.

## Cost, time and stability

The cold runs (`--no-cache`) were two runs over `det100`, with Jev's reported costs:

- Per PR: median $0.0004, p90 $0.0025, max $0.013, mean $0.0010. Requests per PR: median 5, p90 35. Seconds per PR: median 1.2, p95 4.5.
- File verdicts flipped between the two runs: 1 of 421 (0.24%). PR verdicts flipped: 0 of 100. The median score difference was 0.002. 27 files were close calls settled by the mean of five draws.
- Before settling used a mean of five within 0.05 (qualm's median of three within 0.04), `det30` flipped 1 of 59 files.

## Jev spend for e-spot

The total is $1.292 of the $2.50 cap, all of it reported by Jev and all of it logged in `next/eval/results/spend.jsonl`:

| what | cases | $ |
|---|---|---|
| live smoke run, one PR | 1 | 0.000558 |
| pilot: a stratified 7% of dev scored files | 333 | 0.061626 |
| unit asks on every dev scored file | 2831 | 0.754754 |
| retry of 2 requests that got HTTP 499 | 2831 | 0.000213 |
| unit asks redone after the core's unit fix and a masking tidy-up (23% of requests changed; the rest from the signed cache) | 2831 | 0.206660 |
| end-to-end run of dev150 (cached) | 150 | 0.025821 |
| two cold runs of det30 | 60 | 0.028636 |
| two cold runs of det100 | 200 | 0.186999 |
| end-to-end run of dev150 with the final findings rule | 150 | 0.026889 |

Training proper cost $1.02. A cold training run from scratch on the final code would cost about $0.85.
