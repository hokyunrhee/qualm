# Audit of the held-out evaluation (measurement scientist)

## Verdict

**Do not unseal yet.** Five problems block it:

1. The candidates are not frozen, and every dev result describes an older version.
2. Unencrypted held-out data sits in the shared scratchpad, where agents can read it.
3. The fix loop leaks the answer through git history.
4. Fix-loop success rewards a stricter gate, the judge never disagreed, and the LLM modes cannot run in the loop.
5. Several pre-registered choices are still open.

The scoring core is sound:
- The repository-clustered paired bootstrap is implemented correctly. In an emulation at held-out size, its 95% intervals covered the true difference in 94% to 97% of samples.
- Could-not-judge files are handled the same way for every candidate.
- b-offline computes the same features at run time as in training: 117 dev files gave identical scores (largest difference 5e-5).

Power at held-out size is reasonable for d-cascade's pooled number, but about two thirds of that expected gain comes from the 96 hard negatives. Power is weak on the natural stratum, and the fix loop cannot plausibly meet its "CI above zero" requirement.

My scripts are in `/tmp/claude-0/-home-user-qualm/2ede26c6-6287-5d82-9ba2-8868fcdef917/scratchpad/audit/` (`power.py`, `coverage.py`, `decomp.py`, `det.py`, `skew.py`, `bysource.py`, `skips.py`). I did not edit `/home/user/qualm`. Note that d-cascade v2 and the b-offline retrain were committed at 02:30–02:31, while this audit was running.

## Blocking (fix before unsealing)

### 1. Candidates are not frozen, and the dev evidence describes earlier versions (Q1, Q5, Q6)

**Evidence**
- d-cascade v2 (79e60cc) changed the model to `raw+masked`, threshold 0.509.
- Its abstain band `band.json` is now `lo 0.26, hi 1.01, auto_fail 0, in_band 1725/5139`. The band search in `train.py:140` starts at 0.90 and stops at the first failed test. When that first test fails, `hi` becomes 1.01 (`train.py:159`).
- So with `--llm-dir`, every file with p ≥ 0.26 goes to Haiku, and no file fails without the LLM. That makes it effectively "c-llm on a third of files". The README still says 0.505 and [0.25, 0.69).
- These result files were all produced by v1, before the detector change (e101819, 02:06) and before the unit change (23ae9a7, 02:26):
  - `determinism/d-cascade.json`
  - `determinism/d-cascade-llm.json`
  - `dev150_v2.json` (d-cascade-llm with the old band)
  - `fixloop/dev_v1.json`
  - `dev_oof_v1.json` (says d-cascade 0.593; the current run files give 0.601)
- b-offline was retrained at 02:31.
- e-spot's `DEV_RESULTS.md` says `metrics.py` loses 14 duplicate-id positives. That is obsolete: all 25 duplicate items are now present in every run file.
- `results/attacks` has no d-cascade or e-spot result.

**Fix**
- Tag one commit. Record the SHA-256 of every `model.json`, `band.json`, `settings.json` and the `_core`.
- Decide whether d-cascade is the model alone or the model plus LLM.
- Regenerate all dev metrics, dev150, determinism, fix-loop and attack results from that tag.

### 2. Held-out plaintext is reachable by agents (Q1)

**Evidence.** In the scratchpad:
- `assembled_v1/heldout.jsonl.gz` (14 MB, 00:31) and `heldout_reserve.jsonl.gz`.
- `assembled_test/heldout*.jsonl.gz`.
- `adjudication_items.json` and `adjudication_keymap.json`; the keymap refers to held-out 45 times.
- `fixloop/fx-held-*`: the 34 injected held-out files with their `inject.patch`.
- `inject_args_*_heldout.json` and `inject_results_*_heldout.json`.
- `next/data/heldout.key` is committed next to the archive.

Candidate code never reads these paths: grep over `candidates/`, `eval/` and `workflows/` finds none. The only scratch script that mentions held-out is the pre-seal `power.py` (23:49). But the agents that built candidates, the fix-loop agents and the LLM answerers all have filesystem access. The fix-agent prompt even points into the scratchpad (`fx_reports/`, `bench/`). The claim "stays encrypted until the final evaluation" is not true as stated.

**Fix**
- Move or encrypt these files before any more agent work.
- Grep the agent transcripts for `assembled_|heldout|fx-held|adjudication` and record the result.
- Correct `BENCHMARK.md`.

### 3. The fix loop leaks the answer through git history (Q4)

**Evidence**
- `fixloop.py:90-101` checks out the clean upstream commit, overwrites the file, and commits "Add the change" on top.
- In workspace `fx-dev-BurntSushi_toml-1905bd7b`, `git log` shows `97b3068 Add the change` above `1905bd7 Correct error position…`. `git diff 1905bd7 97b3068` is exactly the 21-line injection.
- The fix_id (part of the workspace path in the agent's prompt) contains the clean commit's SHA prefix.
- `SP/fixloop/<id>/inject.patch` is readable.
- The agent prompt forbids commit, stash, checkout and reset, but not `git show`, `git diff` or `git apply -R`.

**Fix**
- Build each workspace as a fresh repository whose history is the base plus one commit containing the clean change and the injection together.
- Use opaque workspace paths.
- Put the injection material out of the agents' reach.

### 4. Fix-loop success measures strictness, the judge never disagreed, and the LLM modes cannot run (Q4)

**Evidence**
- `fixloop_summary.py:73-86` makes success require `caught`, meaning the gate failed at iteration 0. All 40 items are positives and there are no clean controls.
- Counterexample: a gate that fails every change at iteration 0 and passes at the next iteration whenever the diff shrank gets caught = 40/40. Its success then depends only on the agent.
- On dev, success equals 12 for both candidates, but the composition differs: baseline 12 of 21 caught, d-cascade 12 of 18.
- The blind judge said the final version was simpler in **39 of 39** items. Position was balanced (final shown as X 22 times, Y 17 times), so this is not position bias.
- That includes `expr-lang_expr-bbcbc530`, where 4 of 5 injected lines remained and the change grew from 38 to 40 added lines.
- `judge_stage/index.json`, which maps each hash to the candidate and which side is final, sits in the same directory as the staged patches (`fixloop_judge.py:21,41`).
- `prepare()` (`fixloop.py:104`) and `gate_round()` (`fixloop_round.py:48`) never pass `--llm-dir`. So c-llm and d-cascade with the LLM exit 2 at iteration 0 and count as not caught.
- One d-cascade item was re-run in round 1 (`fx_d_r1b.json`, `unjs_ufo-f7d47110`), and nothing shows the same retry was offered to baseline items.

**Fix**
- Report `caught` and `success | caught` separately.
- Add the 40 clean commits as controls, and penalise a gate that fails them.
- Validate the judge on known pairs (clean vs injected, and an unchanged version) and require it to beat chance.
- Move the judge index out of the judge-readable directory.
- Pass `--llm-dir` in `prepare` and `gate`.
- Write down one retry policy that applies to every candidate.

### 5. Pre-registration gaps that must close before unsealing (Q3, Q6)

**Evidence.** These pre-registered analyses are not in `metrics.py`:
- one named confirmatory candidate, with Holm adjustment for the rest;
- added-line terciles;
- matched natural-negative false-positive rate (FPR) operating point;
- equal-mass ECE with intervals, the Brier decomposition, and log loss.

There is also no code that applies `adjudication.v1.json`. The 12 tier-B items, and the human adjudication of 30 held-out comments and 15 held-out items, may change labels after scores exist.

**Fix**
- Name the confirmatory candidate and mode, and the Holm family.
- Freeze the adjudication before unsealing, and report results with and without it.
- Fix the tercile cutpoints from dev.
- Implement recall at the dev out-of-fold threshold matched to the baseline's FPR on natural negatives.
- Add a second natural analysis without `approved_agent` (see finding 6).

## Should fix

### 6. Power, and how much the expected gain depends on the held-out mix (Q3)

**Method.** I emulated the held-out set 1,500 times: 125 dev repositories drawn with replacement, then items sampled to the held-out label-source mix (634 items, 244 positives; about 462 natural items with 168 positives). Scores were the dev out-of-fold or real-run scores.

**Spread of the paired PR-AUC difference and minimum detectable effect** (80% power, two-sided 5%):

| Comparison | Pooled SD | Pooled MDE | Natural SD | Natural MDE |
|---|---|---|---|---|
| d-cascade − baseline | 0.023 | 0.065 | 0.027 | 0.076 |
| e-spot − baseline | 0.035 | 0.097 | 0.041 | 0.115 |
| b-offline − baseline | 0.039 | 0.110 | 0.047 | 0.130 |
| n0-size − baseline | 0.046 | 0.130 | 0.052 | 0.145 |

With Bonferroni over 4 candidates, multiply by about 1.19 (pooled MDE 0.078–0.155).

**What to expect if the dev effects transfer**
- d-cascade pooled +0.065: the CI is above zero in about 79% of emulated samples.
- d-cascade natural +0.030: about 20% power.
- e-spot pooled +0.02: about 11% power.

**Why the composition matters**
- `cap()` keeps every hard negative and every agent-written item (`assemble.py:295`).
- So held-out negatives are 25% hard negatives (2.4% on dev), and natural negatives are 48% `approved_agent` (7% on dev).
- Dropping the 96 hard negatives cuts d-cascade's emulated pooled gain from +0.065 to +0.021.
- The natural gain is +0.069 at the dev mix but +0.030 at the held-out mix. On dev, d-cascade separates reviewer positives from `approved_agent` (ROC 0.742) little better than the baseline (0.725), while its edge over `approved_clean` (0.879 vs 0.787) is matched by size alone (n0-size 0.864).

**Fix:** pre-register these predictions, and report pooled results with and without hard negatives, and natural results with and without `approved_agent`.

### 7. Operating points are not matched, and calibration will meet a prevalence shift (Q1, Q2)

**Evidence**
- Thresholds use different rules:
  - b-offline and d-cascade: precision floor 0.65;
  - e-spot: floor 0.61;
  - n0-size: best F1;
  - c-llm: a provisional 0.5, never tuned;
  - a-recal: qualm's 0.6, which maps to 0.469.
- Platt steps were fitted at 22% prevalence; held-out is 38%.
- d-cascade's band rule ("≤10% positives among passes") depends on prevalence, so it will not hold on held-out.

**Fix**
- Make the precision and recall comparison at the matched-FPR point from finding 5, in addition to each candidate's own gate.
- Report Brier and ECE with a note on the prior shift; only paired differences transfer.

### 8. The flip rates look better than they are (Q5)

**Evidence**
- d-cascade (v1, 5 runs × 100 cases, no cache):
  - reported 1 of 193 scored items (0.5%);
  - all judged files: 4 of 421 (0.95%);
  - gate verdicts (exit codes): 2 of 100 PRs (2%).
- Baseline:
  - reported 3 of 192 (1.6%);
  - `determinism.py:27` drops items a run could not judge; counting them as fails gives 4 of 193 (2.1%);
  - PRs: 3 of 100.
- d-cascade-llm (3 × 30) ran with the Jev cache on: 15, 16 and 9 Jev requests per 30-case run, against about 980 per 100 cases uncached. So only the LLM's noise was measured. Its PR flip rate was 4 of 30 (13%).
- c-llm: PR flips 6 of 30 (20%).
- e-spot had only 2 cold runs, and there is no `determinism/e-spot.json`.
- det30 comes from cases with at most 5 files.
- "Not unanimous over k runs" grows with k, and d-cascade had 5 runs where the LLM runs had 3. Pairwise flip rates are 0.21% for d-cascade and 0.83% for the baseline.
- No design here can show ≤1%. Zero flips in 193 items gives an upper bound of 1.54%; showing ≤1% at 95% needs at least 299 items with 0 flips, or 474 with 1.
- The definition of done says the flip rate is measured on held-out, but it is measured on dev.

**Fix**
- Re-run on the frozen versions with all caches off (Jev included).
- Count could-not-judge as a fail.
- Report flips per file across all judged files, per PR, and pairwise, with exact upper bounds.
- Use at least 300 files, or state that ≤1% cannot be demonstrated.

### 9. Some dev numbers are in-sample, or chosen on the same out-of-fold data they report (Q1)

**Evidence**
- These real runs were made with models trained on all of dev (the ledger tags them "insample"):
  - `runs/dev/b-offline.jsonl`
  - `runs/dev/d-cascade.jsonl`
  - `runs/dev/d-cascade-llm.jsonl`
- The 40 dev fix-loop items are the `injection_llm` training items, so the iteration-0 catches of b-offline, d-cascade and e-spot are in-sample. The v2 commit even chose features on them ("40% vs 53%").
- d-cascade compared 7 feature sets × 4 values of C, and switched its selection rule from pooled to natural after seeing both:
  - `d_cv_v4` picked `raw+masked+det` (natural 0.4670);
  - `d_cv_v5` picked `raw+masked` (natural 0.4778).
- In every trainer, the Platt step, the threshold and the band are fitted on the same out-of-fold predictions that are reported. So out-of-fold Brier, ECE and precision at the threshold are in-sample. The PR-AUC optimism is small (about 0.003–0.011).
- a-recal's out-of-fold scores use a separate Platt fit per fold, so ranks are not monotone across folds. That produced the artificial ROC −0.012 [−0.024, −0.004] in `dev_oof_v1`. Its out-of-fold file gives the 24 skipped items a score of 0.0, which `train.py` cannot produce, so the file is not reproducible from the committed script.
- e-spot's rarity table is built from all dev, so its dev localization numbers are in-sample.

**Fix:** label these as in-sample or selected, regenerate a-recal from one global Platt fit, and take only held-out numbers as unbiased.

### 10. Could-not-judge is decided per case, and missing files pass (Q2)

**Evidence**
- `metrics.py:60` discards the whole report when the exit code is 2. One file error therefore scores every file of the change at 1.0.
- Candidates that make more requests per file (e-spot per unit, d-cascade at least two per file) are more exposed to API failures.
- A file missing from a report scores as a pass at 0 (`metrics.py:71`), which contradicts the fail-closed rule.
- The docstring (`metrics.py:12-13`) still says could-not-judge items are left out.
- `run_candidate` re-runs failed cases only when it is invoked again.

**Fix**
- Use the per-file status when a report exists.
- Treat a file missing from the report as could-not-judge.
- Pre-register N retries of exit-2 and timeout cases for every candidate.
- Correct the docstring.

### 11. The hybrid and LLM candidates are not what the shortlist describes (Q2)

**Evidence**
- In d-cascade with the LLM, the LLM's probability replaces the model's probability inside the band (`scorer.py:255-256`). The ranking mixes a calibrated model with coarse LLM probabilities: c-llm used only 25 distinct values over 355 files.
- c-llm's `settings.json` has `samples: 1` and a threshold of 0.5. The shortlist says three samples with a majority vote.

**Fix:** describe the configurations actually measured, or change them before freezing. Report d-cascade with the LLM separately from d-cascade alone.

### 12. The fix loop is underpowered (Q4)

**Evidence**
- Dev has 40 items in 7 repositories; held-out has 34 in 8 (`FIXLOOP_HELDOUT`).
- With the dev discordance (12 of 40), SE ≈ √(0.30/34) ≈ 0.094. The minimum detectable difference at 80% power is about 0.26, roughly 9 net successes out of 34, before accounting for clustering.
- The dev interval was [−0.13, +0.16].
- A percentile bootstrap over 7–8 clusters is unreliable.
- `run_gate` runs the candidate twice (JSON, then text), so the agent may read a report from a different run than the recorded verdict.

**Fix**
- Use an exact paired test (McNemar or a sign-flip test), and report the clustering as a caveat.
- Treat the definition-of-done fix-loop requirement as unlikely to be met unless the effect is at least 0.25.
- Write the text report from the same run as the verdict.

## Note in the report

13. **The injected and hard-negative strata measure agreement with the generator.**
    - The templates in `collect/mechanical.py` ("DEBUG", "TODO: implement fallback", `V2` and `(copy)`, "must not be None") and the LLM smell prompts in `inject_fixloop.js` produced both splits.
    - The stderr debug detector (e101819) was added after the dev fix-loop runs.
    - Masking comments handles the comment-only and formatting hard negatives by construction. On dev, d-cascade separates reviewer positives from hard negatives with ROC 0.977, against the baseline's 0.753.

14. **Out-of-fold scores against a real baseline run** are fair enough for ranking metrics, with three caveats:
    - d-cascade's raw-view features are the baseline's own dev Jev answers, so the noise is shared and the dev SDs are slightly understated.
    - Out-of-fold runs cannot fail (0 could-not-judge vs the baseline's 3).
    - They lack the close-call re-reading rule.

    The core skips 63 test files the baseline judges (3 reviewer positives among them). That changes PR-AUC by at most 0.003.

15. **Bootstrap.** Clustering by repository is correct: injected/original and positive/post-fix pairs stay together, and repository names map one-to-one to repo keys. The paired resampling is correct. Percentile intervals are adequate at 125 clusters.

16. **Duplicate case ids.** The 11 ids (25 lines) are now handled in every run file. `run_candidate.case_repo` can race when two jobs materialize the same id at once; serialize it per case id.

17. **The "LLM" in this study is a Claude Code subagent, not the API.** Determinism and cost do not transfer to a shipped version. p95 latency is unmeasured for the LLM modes, because the recorded seconds exclude answer time.

18. **Other caveats for the report**
    - The held-out projects are public (go, openstack, gerrit), so Jev and Haiku may have seen these reviews in pretraining.
    - Repository identity is by name. Dev has a fork pair (`44madfire/oh-my-pi` and `can1357/oh-my-pi`). Check held-out repositories against dev by root commit at unseal.
    - Held-out positives are tier A only; dev positives include tier B.
    - The attack suite was visible to the people building candidates, and there is no held-out attack set.
