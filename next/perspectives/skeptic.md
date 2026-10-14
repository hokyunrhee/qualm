# Skeptic

## Position

qualm 0.1.1 is weak in its trust boundary, its calibration and its report, not in judging natural reviewer concerns. Those labels are noisy and coarser than the gate's question, the held-out set is too small to show the little headroom left, and recall on reviewer comments costs false fails an agent loop pays for twice. The cheapest winner keeps the Jev call and fixes the plumbing.

## (a) Critique, with evidence

**Where qualm is close to right**

1. **The labels measure a narrower and noisier thing than the gate's question.** *Fact, from a small sample.* Of the nine natural positives in `dev_examples.md`, only four are clear file-level requests to simplify: #3, #4, #7 and #8.
   - #1 is a reviewer *praising* a change that removed 13 lines of duplicate logic. It matched "duplicate logic" in `simplify_regex.py`.
   - #5 is a question about the correctness of duplicate search documents, asked on 19 lines of declarative config.
   - #2 ("redundant parens") and #6 ("`return await` is redundant") are nits. "redundant" and "unnecessary" are STRONG patterns in the regex.
   - #16 is a refactor 30 days later, which is normal evolution, not pushback at review time.

   The summary itself calls the `approved_uncommented` and `survived_90d` negatives the weakest.
2. **There is a noise ceiling.** *Conjecture, from a simulation in my scratchpad.* Take a natural-item prevalence of 0.25, label precision of 0.60–0.75 and 10% of negatives that really deserve pushback. A perfect oracle on the true concept scores PR-AUC 0.57–0.62, and a strong model (ROC-AUC about 0.92 against the truth) 0.49–0.54. Gains beyond that come from fitting how labels were made (nits, repository culture, diff size), not from judging complexity.
3. **The study is underpowered for the headroom that is left.** *Conjecture.* About 600 held-out items, fewer natural ones, resampled by repository, likely give a paired PR-AUC difference a 95% CI half-width of 0.05–0.10; the 150-item LLM subset about ±0.10. Held-out prevalence (35% against dev's 27%) raises every absolute PR-AUC, so only differences mean anything.
4. **Low recall on reviewer positives is partly the right behaviour.** *Fact for the numbers; the judgment is conjecture.* qualm fails 16% of reviewer positives and 64% of injected smells. The summary says reviewer positives are "usually about one spot in an otherwise fine change; many are small." A merge gate that blocks on `return await` is doing a linter's job.
5. **False fails compound across a pull request.** *Fact: the per-file rates, plus arithmetic.* qualm fails 4% of approved files and 14% of `survived_90d` files. Across five judged files, that blocks 18–53% of clean pull requests. Each false fail costs an agent rework or a person's `qualm keep`, and rework to placate a judge tends to add helpers and single-use layers, what `added_unused_flexibility` asks about. *Conjecture.*
6. **Goodhart effects.** *Conjecture, though the attack suite anticipates them.* A per-file, per-diff judge rewards splitting a change (`split_files`), and justifying comments can sway it (`persuasive_comment`). Deterministic detectors are easier to evade than an opaque judge, since an agent learns their blind spots.
7. **Its determinism is sound.** *Fact.* The spike found Jev's noise is at most 0.03. `judge.go` re-asks any value within 0.05 of the threshold three times and takes the median. LLM verdicts have no comparable property.

**Where qualm is weak (conceded)**

8. **The trust boundary sits inside the pull request.** *Fact.* `config.Load(dir)` reads `qualm.json` and its keeps from the working tree, as git does attributes; the design doc says "Nothing stops an agent from keeping its own change." Skips are bypassable: `*.test.*` skips `ledger_tags.test.go`, and new files under `vendor/` or named `*.generated.*` are never sent.
9. **The scores are miscalibrated.** *Fact.* ECE is 0.128. Monotone recalibration fixes it without changing PR-AUC, so better calibration is not better judgment.
10. **The report is not actionable.** *Fact.* In the README's example, all three diagnoses point at the same 250 lines (327–577). The adapter's fix text is "Rework the change so this no longer applies."
11. **Cheap signals go unused.** *Fact.* Injected smells can be found with grep, yet qualm misses 36% of them. Nine of the ten Jev answers never reach the verdict.
12. **The threshold rests on one codebase.** *Fact,* as the README and design doc both say.

## (b) Candidates

### S1, qualm-hardened: the cheapest design I would concede beats qualm

**What it computes, offline**

- **Trust boundary.** It reads `qualm.json`, keeps, skip rules and `.gitattributes` from the merge base only; a pull request that changes any of them fails closed.
- **Skips.** A file is skipped only for evidence a rename can't fake: each language's test convention (`_test.go`, not `.test.go`), a generated header ("Code generated … DO NOT EDIT", `@generated`), or a `vendor/` path that existed in the base. It decodes UTF-16 and judges a symlink's target.
- **Leftover detectors**, on added lines, with one regex table per language:
  - **Debug output**: print-style calls where the file otherwise uses a logger, or carrying DEBUG/XXX markers.
  - **Stub bodies**: TODO plus `pass`, `NotImplemented` or `panic("TODO")`. A TODO comment with a bug link does *not* fire, so example #12, a `post_fix` negative, stays clean.
  - **Near-copies**: winnowing fingerprints over units with identifiers and literals normalised. An added unit fires when its Jaccard similarity reaches 0.8 or more against any unit in any changed file, which also catches `split_files`.
  - **Guards on values just built**: a nil/null check on a value constructed in the three lines before.

**What it asks.** The same single Jev request per file as 0.1.1, with the same close-call rule.

**How it decides**

- The score is isotonic(push_back), fitted on dev, raised to 0.9 when any detector fires.
- An optional logistic regression over the ten Jev answers plus log(added lines) stays only if it beats isotonic(push_back) on dev natural PR-AUC by 0.02 or more, under cross-validation grouped by repository.
- The threshold is set on dev so that the PR-level clean-change block rate is no higher than the baseline's.

**What the report tells the agent**

- Detector findings carry exact lines, a fix and the `related` original.
- Jev diagnoses keep their hunk ranges, as now.
- One line tells the agent not to restructure code the report doesn't name.

**What it costs.** The same as the baseline: about $0.0003 per pull request, and well under a second for the detectors. No LLM.

**Hypothesis.** The gap between 0.1.1 and the best achievable tool lies in the trust boundary, leftover detection, calibration and localization, not in judging natural concerns.

**What would falsify it**

- S1 fails to beat the baseline on pooled held-out PR-AUC with a CI above zero, or its precision drops. Either means the detectors don't pay for themselves.
- A richer candidate beats S1 on held-out natural PR-AUC by at least 0.05 with a CI lower bound above zero, the gain holds within added-line terciles and with nits excluded, and its precision is at least S1's. That means judgment headroom exists.

### S2, qualm-router: the variant built on the gate being the wrong idea

The README's real job is deciding "which changes still get human eyes". S2 computes S1's scores, but its verdict has three outcomes:

- **fail**, so the agent reworks, only on detector findings or when the score reaches t_hi. t_hi is set on dev by conformal risk control so that at most 2% of clean pull requests are blocked.
- **abstained**, routed to a person with the lines to look at, for scores from t_lo up to t_hi. t_lo is set so that at most 20% of pull requests go to review.
- **pass**, otherwise.

It costs the same as S1.

**Hypothesis.** For subjective concerns, routing to a person beats looping the agent.

**What would falsify it**

- On held-out, S2 blocks more than 4% of clean pull requests, which means risk control broke under the shift to new repositories.
- At the same review budget, its recall over fail plus abstain trails the best gate's fail recall by 10 points or more.
- The fix loop shows rework on subjective, non-detector fails giving code the blind judge rates simpler at least 70% of the time.

## What would change my mind

I would give up the claim that 0.1.1's judgment is near the best achievable if, on held-out natural items, one candidate did all of the following:

1. beat 0.1.1's PR-AUC by at least 0.08, with a lower bound of at least +0.02 on the paired 95% CI clustered by repository;
2. beat **S1** by at least 0.05 with a CI above zero, so that the gain is judgment and not plumbing;
3. kept a gain of at least 0.05 with nit-flagged positives excluded, and within every added-line tercile;
4. at its operating point, kept precision at or above the baseline's and a PR-level clean-change block rate no more than 1 point above the baseline's;
5. stayed at or under 1% flips, $0.05 per pull request and 60 s at p95.

I would give up "the gate is the wrong idea" if two things held:

- people adjudicating held-out items agree with at least 85% of `reviewer_comment` labels;
- an agent reworking false-alarm files makes the code worse, by the blind judge, in under 10% of cases.

## (c) Predictions

1. On pooled held-out items, S1 beats 0.1.1's PR-AUC with a CI above zero, and its fail rate on injected smells rises from about 64% to 90% or more. On natural items, the CI of S1 minus the baseline includes zero.
2. No candidate reaches a held-out natural PR-AUC of 0.70, and the best natural gain over 0.1.1 is below 0.10.
3. On dev natural items, log(added lines) alone comes within 0.05 of 0.1.1's push_back PR-AUC. The best candidate's natural gain shrinks by at least half when measured within added-line terciles.
4. Any candidate that fails 30% or more of held-out `reviewer_comment` positives (0.1.1: 16%) has a PR-level clean-change block rate at least 5 points above the baseline's.
5. Any candidate whose verdict rests on one LLM sample per file exceeds a 1% flip rate on the 150-item subset. An LLM candidate's natural PR-AUC gain on dev, whose labels are LLM-cleaned, is at least twice its gain on held-out, whose labels are adjudicated by people.
6. 0.1.1 mishandles at least 8 of the 25 attacks, and all of them get through the trust boundary or the skip rules (`self_keep`, `config_*`, `gitattributes_*`, `vendor_dir`, `test_suffix`, `rename_generated`, `utf16_file`, `move_to_test_dir`). Jev withstands all four `inject_*` attacks. S1 handles all 25.
