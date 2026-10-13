# Measurement scientist

## Bottom line

qualm 0.1.1's gate rests on one unlabelled codebase, an untested threshold and an unmeasured noise model. The benchmark can expose that, but its headline number, pooled PR-AUC over constructed and natural labels, can be won without reviewer-level judgement. About 600 held-out items detect a PR-AUC gain of roughly 0.07 to 0.10, not much less. The study should look for that gain on the natural labels.

## (a) Critique

**What each label source measures.**
- `reviewer_comment`: a reviewer typed one of about forty phrases near an added line and the nearby code later moved. That reflects review culture, size and attention as much as code. *Fact*: simplify_regex.py, and gerrit_collect.py counts `changed_later` as acted on. Of the eight dev_examples positives:
  - #1 praises removed duplication ("Great to not have duplicate logic" matches a STRONG pattern).
  - #5 asks about duplicate search documents.
  - #7 reads like an AI review tool ("[RECOMMENDED] … None of the 16 callers").
  - #2 is a nit ("redundant parens").

  Three of eight wrong gives a 14% to 69% Wilson interval. *Fact* for the sample; *conjecture* for the population rate.
- Dev positives are filtered by a Haiku classifier. Held-out Tier A is not, and Tier B leaves held-out (*fact*: classify_comments.js, assemble.py). Held-out positives are noisier, which shrinks every difference. On dev, a Claude judge is graded against labels a Claude model filtered. *Conjecture*: the size of both effects.
- `followup_cleanup` and `survived_90d` measure later churn. `approved_uncommented` is the absence of comment (*fact*). The baseline fails 14% of survived_90d but 4% of approved negatives (*fact*). That gap is likely source (GitHub history vs Gerrit), not code (*conjecture*).
- Injected smells test six fixed string templates: `print("DEBUG x =", x)`, `TODO: implement fallback`, `…V2` plus `(copy)`, `must not be None` (*fact*: mechanical.py). The generator is in the repo, so a regex can win the held-out injected stratum. A whitespace, comment and rename filter separates hard negatives.
- `post_fix` is the best negative here: same change and size, minus the flagged spot. It tests whether a tool sees the spot (*fact*, by construction).

**The pooled metric rewards the easy strata.** I simulated an assumed mix: 60 injected, 120 reviewer and 30 follow-up positives, plus 90 hard negatives. A candidate built from template regexes, a hard-negative filter and diff size beat a baseline-like scorer on pooled PR-AUC by +0.09 in every replicate. It lost 0.055 on the natural strata (*conjecture*: synthetic). Dev agrees: the baseline fails 64% of injected and 16% of reviewer positives, against 4% of approved negatives (*fact*). So most of its recall at 0.6 is injections (*conjecture*).

**Untested numbers.** The threshold of 0.6 came from 99 commits of one Ruby repository with no labels (*fact*: README). The ±0.05 band assumes informally measured noise of at most 0.03 (*fact*: design doc). Whether a file is re-asked depends on its first, noisy answer (*fact*: judge.go). Large diffs take the max over pieces, which raises both mean and variance with size, so size is built into the score (*fact*: judge.go `combine`). Baseline ECE of 0.128 on 198 items exceeds what a perfectly calibrated model shows at that n (mean 0.067, 95th percentile 0.097, simulated), but its CI is wide.

**Harness traps** (*facts*: metrics.py, determinism.py, assemble.py):
1. The bootstrap resamples by `case_id`, which splits pairs and makes CIs too narrow. Separate cases hold an injected item (`inj-…`) and its original, a hard negative and its source, and a post_fix (final patch set) and its positive (`…-psN`). Resample by repository, the split unit, or at least by origin change.
2. Could-not-judge items are dropped, so a candidate that exits 2 on hard cases is scored on an easier set. Score them as fails at 1.0.
3. ECE uses ten equal-width bins, and Jev's scores pile up near 0 and 1.
4. Flip rate means "not unanimous over k runs", which grows with k, and it is counted per file. A five-file PR flips about five times as often.
5. `cap()` slices `list(must)` from a Python set, so if `must` exceeds `want_neg`, hash randomisation under `-I` makes the held-out draw unreproducible from the seed.

## What held-out can detect

I simulated paired, case-clustered runs at n=600 with 210 positives. The standard error of ΔPR-AUC depends on how strongly the two candidates' errors correlate. The last column is the minimum detectable effect at 80% power.

| Comparison | Error correlation | SE | Minimum detectable effect |
|---|---|---|---|
| Two Jev variants | about 0.85 | 0.017 | about 0.05 |
| Moderately similar candidates | about 0.6 | 0.027 | about 0.075 |
| Heuristic vs Jev | about 0.3 | 0.033 | about 0.095 |

- Repository clustering and a Bonferroni correction over seven candidates make these about 1.3 times larger.
- A reviewer-only stratum of about 100 to 120 positives needs about 0.13.
- Precision at the baseline's operating point (about 70 of 100 flagged) has a CI of ±0.09, so "precision not lower" cannot be shown, only not refuted.
- Zero flips in 150 LLM items bounds the flip rate only at 2%. Showing ≤1% needs at most one flip in 600.
- PR-AUC moves with prevalence: one scorer gives 0.515 at 27% and 0.60 at 35%. Only paired differences transfer.

## Protocol

- **Primary metric.** Repository-clustered paired ΔPR-AUC on the natural stratum: Tier A reviewer and follow-up positives against approved, survived and post_fix negatives. Report the pooled number (the study's definition of done) and each stratum beside it.
- **Yardsticks.**
  - N0: a logistic regression on log added and removed lines plus language.
  - The baseline's `push_back` after Platt recalibration.

  A candidate that doesn't beat N0 on natural labels has learned size.
- **Matched pairs.** Report P(score(positive) > score(post_fix)) and P(score(injected) > score(original)).
- **Threshold.** Choose it on repository-grouped out-of-fold dev scores, at a pre-registered file-level FPR on natural negatives equal to the baseline's. Compare recall at that matched FPR, with a 0.05 precision non-inferiority margin.
- **Calibration.** Use Platt or beta on the logit. Plain isotonic creates ties, and ties change AP. Report Brier with its decomposition, log loss, and equal-mass ECE with CIs.
- **Determinism.** Use three cache-off runs. Report pairwise and PR-level flip rates with exact upper bounds, and the share of files within measured noise of the threshold. Five Jev repeats of all dev items cost about $3.
- **Held-out discipline.** Name one confirmatory candidate before unsealing; the rest are exploratory with Holm adjustment. Unseal once. Report template-perturbed (LLM-written) injections apart.

## (b) Candidates

**M1: calibrated stack.**
- **Computes.** Deterministic features per file:
  - log added and removed lines
  - growth of the largest touched unit
  - added nesting depth
  - within-file near-copy similarity from winnowed fingerprints
  - generic leftovers (print or console calls, TODO, not-implemented)
  - guards on just-assigned values
  - comment, whitespace and rename-only flags
  - language
- **Asks.** Jev once with qualm's ten questions, keeping all ten logits.
- **Decides.**
  - A logistic regression with L2 regularisation and about 20 coefficients is fit on dev with repository-grouped five-fold cross-fitting. Natural strata carry at least 60% of the weight. Its output is the calibrated probability, thresholded as in the protocol.
  - Within 2.5 measured σ of the threshold, it re-asks Jev up to four times and averages logits.
  - If the result is still inside that band, the Jev-free feature model decides, so the last step is deterministic.
- **Reports.** Fired features as findings with exact lines, plus `related` lines for copies. Jev diagnoses are located by hunk. Each finding's confidence is its out-of-fold precision.
- **Cost.** About 1.2 Jev requests per file, about $0.002 per PR.
- **Hypothesis.** qualm's signal is real but miscalibrated and partly discarded: nine answers never gate, and size enters only via the max over pieces.
- **Falsified if** any of these hold:
  - The held-out natural-stratum ΔPR-AUC CI against the baseline includes 0.
  - On dev out-of-fold, the Jev features or the deterministic features add less than 0.02 AP.
  - M1 fails to beat N0 on natural labels.

**M2: M1 plus risk-controlled escalation.**
- **Band.** Learn-then-Test on M1's out-of-fold dev scores picks a band [l, u] outside which error is at most 10% with 90% confidence. Calibration is repository-grouped, so the guarantee is nominal across repositories. Expect 15% to 25% of files inside.
- **Asks.** Haiku 5.5, three times per file in the band (about 4k tokens in, 300 out).
- **Decides.** The score is the mean of M1's probability and the yes-fraction. This is pre-specified because there are too few in-band items to fit.
- **Reports.** M1's findings plus the LLM's located requests.
- **Cost.** About $0.0017 per escalated file and under $0.01 per PR. p95 latency is about 10 s.
- **Hypothesis.** Residual error sits in M1's uncertain band, and an LLM resolves it.
- **Falsified if** any of these hold:
  - In-band held-out verdicts are no more accurate than M1's (McNemar).
  - The M2 − M1 ΔPR-AUC CI includes 0.
  - The point flip rate exceeds 1%.

## (c) Predictions

1. No candidate's held-out ΔPR-AUC against the baseline, on reviewer_comment positives versus natural negatives, will have a 95% CI above zero.
2. Removing the injected and hard-negative strata will shrink the winner's pooled held-out PR-AUC gain by at least 50%.
3. N0 will reach ROC-AUC ≥0.65 on dev reviewer-versus-natural items, within 0.05 of the baseline there.
4. The baseline's fail rate on post_fix negatives will be within 5 points of its rate on the matching reviewer positives, and P(score(positive) > score(post_fix)) will be below 0.6.
5. Platt recalibration of the baseline will cut held-out Brier by at least 0.01 and ECE by at least 0.04, with PR-AUC unchanged.
6. Jev-based candidates will meet ≤1% file flips over three runs. Any LLM-judged candidate without a deterministic last step will show two or more flips on the 150-item subset.
