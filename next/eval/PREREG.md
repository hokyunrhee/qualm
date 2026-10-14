# Pre-registration of the held-out evaluation

Written before the held-out split is opened. Nothing below changes after the split is opened; anything decided afterwards is labelled post hoc in the report.

## What is frozen

The freeze record `eval/results/freeze.json` holds:
- the git tag `freeze-v3`, which is commit `6d15221`;
- the commit each candidate is run from;
- the SHA-256 of every file that decides a verdict: models, bands, settings, the shared core and the candidates' code.

Every held-out run is made from a worktree of the commit its row names. Nothing in those trees changes after this point.

| Candidate | Configuration measured | Commit |
|---|---|---|
| `a-baseline` | qualm 0.1.1 unmodified (built from `5915e3d`); fails at `push_back >= 0.6` | `freeze-v3` (`6d15221`) |
| `a-recal` | the baseline's run, scores mapped through one Platt step (`platt.json`); same verdicts | `6d15221` |
| `d-cascade` | **v3, model only**: raw and masked Jev views; logistic model; threshold 0.506; close calls read 3, 7, then at most 11 times, median decides. Localization by function, verb by log-odds gap. No LLM band. | `6d15221` |
| `c-llm` | one Claude Haiku 5.5 reading per judged file (answered here by a fresh subagent per request); threshold 0.35 | `6d15221` |
| `e-spot` | as trained, on the core it was trained with | `59b23cd` |
| `b-offline` | as trained, on the core it was trained with | `59b23cd` |
| `n0-size` | size-only yardstick, as trained | `59b23cd` |

`b-offline`, `e-spot` and `n0-size` learned weights over the core's detector and unit features. The v3 core changes those features, so each runs on the core it was trained with: each candidate is its own frozen artifact. They therefore lack the v3 perimeter fixes, and their attack results show it.

## Held-out split

The held-out split is `heldout.v1.tar.enc`. Before use, its tar and every member are checked against `data/heldout.v1.sha256.json`. It holds:
- 509 cases;
- 634 scored items, of which 244 are positives;
- 125 repositories;
- 34 fix-loop items.

Labels are the ones sealed at 00:47 on 14 October. Human adjudication does not change them:
- If answers arrive, they are reported as agreement between label sources (Cohen's kappa).
- They are also reported as one sensitivity analysis, in which the 12 tier-B items are added with the human's label.

When the split is opened, held-out repositories are checked against dev by root commit, not only by name. A repository whose root commit is also in dev is reported, and every result is given with and without it.

## Confirmatory comparison

There is exactly one: **`d-cascade` minus `a-baseline`, PR-AUC over all 634 held-out items.**
- The interval is a 95% paired percentile bootstrap that resamples repositories, with 4,000 resamples and seed 7.
- It counts as a win only if the whole interval lies above 0, and precision at each candidate's own gate is not lower (the point difference is at least 0; the interval is reported).

## Secondary comparisons

The other candidates are compared with `a-baseline` on the same PR-AUC. Their bootstrap p-values are Holm-adjusted over the family `{a-recal, b-offline, c-llm, e-spot, n0-size}`. Each comparison is reported with its interval and adjusted p.

`c-llm` is scored on the full held-out set, like the others.

## Analyses reported for every candidate

The reported metrics are:
- PR-AUC and ROC-AUC;
- precision, recall and F1 at the candidate's own gate;
- Brier score, with Murphy's decomposition over 10 equal-mass bins;
- log loss;
- ECE, with 10 equal-width bins and with 10 equal-mass bins, the latter with a bootstrap interval;
- the false-alarm rate;
- localization: a finding overlaps the known smell or comment location (±2 lines), whether the finding is under 15 lines wide, an IoU of at least 0.2, and the median finding width.

They are broken down:
- by language;
- by label source;
- by smell;
- for agent-written changes;
- by added-line tercile (cut points 5 and 28, fixed from dev);
- by origin.

Also reported:
- **Natural stratum.** Reviewer-comment and follow-up positives against approved, post-fix and surviving negatives. It is also reported without `approved_agent` negatives, which are 48% of the held-out natural negatives against 7% on dev.
- **Pooled without hard negatives.** Hard negatives are 25% of held-out negatives against 2.4% on dev, and masking comments handles them by construction.
- **Matched false-alarm rate.** Precision and recall at the thresholds in `eval/results/matched_thresholds.json`. Those were fixed on dev, so that each candidate fails 5.58% of dev's natural negatives, the baseline's own rate.
- **Calibration under a prior shift.** Held-out prevalence is 38%, dev's 22%. Platt steps were fitted at 22%, so absolute calibration on held-out mixes the prior shift with miscalibration. Only paired differences are interpreted.

Could-not-judge:
- A file the candidate could not judge fails closed, scored as a fail at 1.0. That covers a case with no report, a file marked error, and a file left out of the report.
- Every case that exits 2 or times out is retried once, for every candidate, and the retry's record is kept.

## Predictions

These are made before opening, from repository-bootstrap emulation of the held-out mix on dev scores (`perspectives/phase4_measurement_audit.md`).

| Comparison | Expected difference | Power |
|---|---|---|
| `d-cascade` − `a-baseline`, pooled PR-AUC | about +0.065 (minimum detectable 0.065) | about 79% |
| Same, without hard negatives | about +0.021 | — |
| `d-cascade` − `a-baseline`, natural PR-AUC | about +0.03 | about 20% |

A natural result whose interval crosses 0 is the likely outcome and is not a win.

## Determinism

For `d-cascade` and `a-baseline`:
- **Runs.** Every held-out case is run 5 times with every cache off, Jev's included. Run 1 is the accuracy run above.
- **Flips** are reported three ways: per file over every judged file, per pull request (exit code), and pairwise between runs. Each comes with an exact 95% upper bound (Clopper-Pearson).
- **Could-not-judge counts as a fail** when comparing verdicts.
- **Threshold.** The ≤1% requirement is met only if the per-file point rate is at most 1%. Whether the upper bound is below 1% is reported separately.

For `c-llm`, a smaller set is run: 3 runs over 30 held-out cases, each with fresh subagents. With 30 cases the bound is wide, and this is stated.

## Versions measured on dev

These dev measurements were made just before the freeze, on commits whose verdicts match `6d15221`:
- **d-cascade determinism** (dev det100): `d3feba7`. Every later change before the freeze touches findings only, not verdicts: the verb rule, Ruby comment rules, and dropping import blocks as places.
- **d-cascade dev fix loop:** `1528ae9`, which differs from the freeze only by import blocks no longer being places.
- **a-baseline dev fix loop:** run with the harness at `fd6aca0`; the harness did not change after that.

## Cost and latency

Taken from the accuracy runs: Jev cost, per-request token counts and wall time.
- Reported per pull request: the mean, median and p95 of cost and of wall time. "Typical" in the definition of done is read as the median, with the mean and p95 next to it.
- For `c-llm`, cost is estimated from token counts at Haiku 5.5 list price ($0.10 / $0.50 per million input / output tokens).
- Wall time for `c-llm` excludes answer time, because the answers come from subagents. The shipped latency is not measured.

## Fix loop

`a-baseline` and `d-cascade` are run on the 34 held-out items, plus 34 clean controls, with harness v2 (`eval/fixloop.py`):
- **Agents.** At most two rounds. Each round uses a fresh coding agent per item (`workflows/fix_agents.js`), and every agent gets the same prompt.
- **Judge.** A blind judge per reworked item (`workflows/blind_judge.js`). It is validated beforehand on clean-against-injected pairs and on identical pairs.
- **Reported:** caught; success; success given caught; controls blocked; and success without the judge's vote.
- **Tests.** An exact McNemar test, and a repository bootstrap. With 8 repositories the bootstrap is rough, and this is stated.

Dev's discordance implies a minimum detectable difference of about 0.26. A result short of that is reported as inconclusive, not as a loss or a win.

The LLM configurations (`c-llm`, and `d-cascade` with its band) are not run in the fix loop:
- `c-llm` cannot meet the determinism requirement on dev, with a 12.8% flip rate. It is reported but cannot be the winner.
- The band is not part of the measured `d-cascade`.

## Attacks

Every candidate is run against two suites:
- **Round 1:** 53 attacks plus controls, including the corrected `good_with_legit_markers_valid_check`. The original `good_with_legit_markers` is reported as contested, because its "input check" cannot be true.
- **Round 2:** 15 attacks.

There is no held-out attack set. The suites were visible while the candidates were built.

## What decides the winner

The winner is the candidate that meets the most of the definition of done on held-out:
- PR-AUC better than `a-baseline`, with the interval above 0, and precision not lower;
- fix-loop success better, with the interval above 0;
- flip rate ≤1%;
- cost ≤ $0.05 per pull request and p95 wall time ≤ 60 s;
- every attack handled.

Among candidates that can ship without an LLM key, `d-cascade` is the designated winner if it meets the PR-AUC condition. Every condition it misses is reported as missed.
