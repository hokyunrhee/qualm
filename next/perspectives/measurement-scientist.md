# Measurement scientist

## Bottom line

qualm 0.1.1's gate rests on one unlabelled codebase, a threshold nobody tested and a noise model nobody measured. The benchmark can show this. But its headline number, pooled PR-AUC over constructed and natural labels together, can be won without any reviewer-level judgement. With about 600 held-out items the study can detect a PR-AUC gain of roughly 0.07 to 0.10, and not much less, so it should look for that gain on the natural labels.

## (a) Critique

**What each label source measures**

- `reviewer_comment` records that a reviewer on this project used one of about forty phrases near an added line, and that the code near that line later moved. That depends on review culture, file size and how closely the reviewer read, as much as on the code itself. *Fact*: simplify_regex.py, and gerrit_collect.py counts `changed_later` as "acted on". Of the eight positives in dev_examples.md, three look mislabelled:
  - #1 praises the removal of duplication ("Great to not have duplicate logic"), which matches a STRONG pattern.
  - #5 asks about duplicate search documents.
  - #7 reads like an AI review tool ("[RECOMMENDED] … None of the 16 callers").
  
  A fourth, #2, is a nit ("redundant parens"). *Fact.* Three in eight puts the population error rate somewhere between 14% and 69% (Wilson 95%). *Conjecture*: the population rate itself.
- Dev reviewer positives are filtered by a Haiku classifier. Held-out Tier A positives are not filtered at all, and Tier B is dropped from held-out (*fact*: classify_comments.js, assemble.py). Held-out positives are therefore noisier than dev positives, and noisy in a different way. That shrinks every measured difference. It also means a Claude-based judge is graded on dev against labels a Claude model filtered. *Conjecture*: how large either effect is.
- `followup_cleanup` and `survived_90d` measure whether the area churned later. `approved_uncommented` only records that nobody commented (*fact*). The baseline fails 14% of survived_90d negatives but 4% of approved ones (*fact*). That gap probably reflects where the items came from (GitHub history against Gerrit), not the code (*conjecture*).
- Injected smells test whether a tool spots six fixed string templates, such as `print("DEBUG x =", x)`, `TODO: implement fallback`, a `V2` copy with `(copy)` added, and `must not be None` (*fact*: mechanical.py). The generator sits in the repository, so a regex can win the held-out injected stratum. A whitespace, comment and rename filter separates the hard negatives (*conjecture*: how much this inflates scores).
- `post_fix` is the most informative negative in the set: the same change at the same size, minus the one spot the reviewer flagged. It tests whether a tool sees that spot (*fact*, by construction).

**The pooled metric rewards the easy strata**

I simulated an assumed held-out mix of 60 injected, 120 reviewer and 30 follow-up positives, with 90 hard negatives. A candidate built from template regexes, a hard-negative filter and diff size beat a baseline-like scorer on pooled PR-AUC by +0.09 in every replicate, while losing 0.055 on the natural strata (*conjecture*: synthetic data). The dev numbers point the same way. The baseline fails 64% of injected positives but only 16% of reviewer positives, against 4% of approved negatives (*fact*). So most of its recall at 0.6 comes from injections (*conjecture*, from that arithmetic).

**The threshold and the re-ask are untested numbers**

The 0.6 threshold was set on 99 commits of one Ruby repository, with no labels (*fact*: README). The ±0.05 band assumes noise of at most 0.03, measured informally (*fact*: design doc). Two details make it weaker than it looks (*fact*: judge.go):
- Whether a file is re-asked depends on the first answer, which is itself noisy.
- A large diff takes the highest value across its pieces, which raises both the mean and the variance as size grows. Size is built into the score.

The baseline's ECE of 0.128 on 198 items sits above what a perfectly calibrated model would show at that size (mean 0.067, 95th percentile 0.097 in my simulation), but its confidence interval is wide.

**The harness can mislead** (*facts*: metrics.py, determinism.py, assemble.py)

1. The bootstrap resamples by `case_id`, but matched items live in separate cases: an injected item (`inj-…`) and its clean original, a hard negative and its source, a post_fix and its positive (`…-psN` against the final patch set). The pairs get split, so the CIs come out too narrow. Resample by repository, which is the unit the split was made on, or at least by origin change.
2. Could-not-judge items are dropped from the accuracy metrics, so a candidate that exits 2 on hard cases is scored on an easier set. Score those items as fails at 1.0.
3. ECE uses ten equal-width bins. Jev's scores pile up near 0 and 1, which leaves most bins nearly empty.
4. Flip rate is counted per file, as "not unanimous across k runs", so it grows with k. A PR with five judged files flips about five times as often as one file.
5. `cap()` takes `list(must)[:want_neg]` from a Python set, whose order changes with hash randomisation under `-I`. If `must` ever exceeds `want_neg`, the held-out draw can't be reproduced from the seed.

## How small a difference the held-out set can see

I simulated paired, case-clustered results at 600 items with 210 positives. The standard error of the PR-AUC difference, and the smallest difference detectable with 80% power, depend on how closely the two candidates' errors correlate:

| Candidates compared | Error correlation | SE | Smallest detectable difference |
|---|---|---|---|
| Two Jev variants | about 0.85 | 0.017 | about 0.05 |
| Moderately similar | about 0.6 | 0.027 | about 0.075 |
| Heuristic against Jev | about 0.3 | 0.033 | about 0.095 |

- Clustering by repository and a Bonferroni correction over seven candidates make each of these about 1.3 times larger.
- A reviewer-only stratum, with perhaps 100 to 120 positives (*conjecture*), needs a difference of about 0.13.
- Precision at the baseline's operating point (about 70 true positives in about 100 flags) carries a CI of ±0.09. "Precision not lower" can't be shown; at best it fails to be refuted.
- No flips in 150 LLM items only bounds the flip rate at 2%. Showing ≤1% takes at most one flip in 600.
- PR-AUC moves with prevalence: the same scorer gets 0.515 at 27% positives and 0.60 at 35%. Only paired differences carry over between dev and held-out.

## Protocol for every candidate

- **Primary measure.** Repository-clustered paired ΔPR-AUC on the natural stratum: Tier A reviewer and follow-up positives against approved, survived and post_fix negatives. Report pooled PR-AUC (the study's definition of done) and each stratum alongside it.
- **Yardsticks.**
  - N0: a logistic regression on log added lines, log removed lines and language.
  - The baseline's `push_back` with Platt recalibration.
  
  A candidate that doesn't beat N0 on the natural stratum has learned diff size.
- **Matched pairs.** Report P(score(positive) > score(its post_fix)) and P(score(injected) > score(its original)).
- **Threshold.** Choose it from repository-grouped out-of-fold dev scores, at a pre-registered file-level false-positive rate on natural negatives equal to the baseline's. Compare recall at that matched rate, which doesn't depend on prevalence, with a precision non-inferiority margin of 0.05.
- **Calibration.** Use Platt or beta calibration on the logit. Avoid plain isotonic regression: its flat steps create ties, and ties change PR-AUC. Report Brier with its decomposition, log loss, and equal-mass ECE with CIs.
- **Determinism.** Run each candidate three times with no cache. Report pairwise and PR-level flip rates with exact upper bounds, plus the share of files within the measured noise of the threshold. Measure Jev's noise directly: five repeats of every dev item cost about $3.
- **Held-out discipline.** Name one confirmatory candidate before unsealing. Treat the rest as exploratory, with Holm-adjusted intervals. Unseal once.
- **Injections.** Also run the injected stratum on template-perturbed variants (the LLM-written injections) and report it separately.

## (b) Candidates

**M1: calibrated stack**

- **What it computes.** Cheap deterministic features for each file:
  - log added and removed lines
  - growth of the largest touched unit
  - added nesting depth
  - within-file near-copy similarity (winnowed fingerprints of added lines against the rest of the file)
  - generic leftover tokens (print or console calls, TODO, not-implemented)
  - guards on just-assigned values
  - flags for comment-only, whitespace-only and rename-only changes
  - language
- **What it asks.** Jev, once per file, with qualm's ten questions. It keeps all ten logits, not just `push_back`.
- **How it decides.**
  - An L2 logistic regression with about 20 coefficients outputs a calibrated probability. It is fit with repository-grouped five-fold cross-fitting on dev, with the natural strata carrying at least 60% of the weight.
  - The threshold is chosen as in the protocol above.
  - When the logit lands within 2.5 measured σ of the threshold, Jev is asked up to four more times and the logits are averaged. If the result is still inside that band, the Jev-free feature model decides, so the last step is deterministic.
- **What the report tells the agent.** Each feature that fired, as a finding with exact lines and `related` lines for copies; Jev's diagnoses, located by hunk. Each finding's confidence is its out-of-fold precision.
- **Cost.** About 1.2 Jev requests per file: roughly $0.0004 per file, $0.002 per PR and a few seconds.
- **Hypothesis.** qualm's signal is real but miscalibrated and partly thrown away: nine of its ten answers never gate, and size enters only through max-over-pieces.
- **What falsifies it.** Any of these:
  - The held-out natural-stratum ΔPR-AUC against the baseline has a CI that includes 0.
  - On dev out-of-fold, the Jev features or the deterministic features add less than 0.02 PR-AUC.
  - M1 fails to beat N0 on the natural stratum.

**M2: M1 plus risk-controlled escalation**

- **How it picks what to escalate.** Learn-then-Test on M1's out-of-fold dev scores chooses a band [l, u] outside which the error rate is at most 10% with 90% confidence. Calibration is repository-grouped, so the guarantee is only nominal across repositories.
- **What it asks.** Expect 15% to 25% of files in the band. Each goes to Haiku 5.5 three times (about 4k tokens in, 300 out).
- **How it decides.** For a file in the band, the score is the mean of M1's probability and the share of the three Haiku answers that say yes. The rule is fixed in advance, because there are too few in-band items to fit anything.
- **What the report adds.** The LLM's located requests.
- **Cost.** About $0.0017 per escalated file and under $0.01 per PR. p95 latency is about 10 s.
- **Hypothesis.** M1's remaining errors sit in its uncertain band, and an LLM that reads the change resolves them.
- **What falsifies it.** Any of these:
  - On held-out in-band files, the verdict is no more accurate than M1's (paired McNemar test).
  - The held-out M2 minus M1 ΔPR-AUC has a CI that includes 0.
  - The point flip rate exceeds 1%.

## (c) Predictions

1. On held-out, no candidate's ΔPR-AUC against the baseline, restricted to reviewer_comment positives and natural negatives, will have a 95% CI entirely above zero.
2. At least half of the winning candidate's pooled held-out PR-AUC gain will come from the injected and hard-negative strata: the gain shrinks by 50% or more when they are removed.
3. N0 (size and language only) will reach a ROC-AUC of at least 0.65 on dev reviewer-versus-natural items, within 0.05 of the baseline on that stratum.
4. The baseline's fail rate on post_fix negatives will be within 5 points of its rate on the matching reviewer positives, and P(score(positive) > score(post_fix)) will be below 0.6.
5. Platt recalibration of the baseline will cut held-out Brier by at least 0.01 and ECE by at least 0.04, with PR-AUC unchanged.
6. Jev-based candidates will meet a file flip rate of at most 1% over three runs on held-out. Any LLM-judged candidate without a deterministic last step will show two or more flips on the 150-item subset.
