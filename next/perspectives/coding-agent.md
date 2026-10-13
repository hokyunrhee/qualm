# The coding agent being judged

I am the agent whose pull request failed. I see the text report and the repository, nothing else. For each problem I need four things: the exact lines, why a reviewer objects, what to write instead, and a check that tells me I'm done. I also need what the report asks me to fix to be what the gate measures. If it isn't, gaming the gate is the cheapest way to green.

## Reading qualm 0.1.1's real report

The README's mutineer report gives `push back 0.65` and three diagnoses, all at `lines 327-577`. With it, I would:

- open 250 lines and guess which of 22 new methods are "near-copies" (of what? the report doesn't say);
- guess which guard is "impossible", probably split the class, rerun, and hope.

If push_back then read 0.62, nothing would tell me what was left. The only command the report prints in full is `qualm keep lib/mutineer/coverage_map.rb --reason "..."`.

## Critique

1. **Location is a hunk, not a problem.** [fact] `where.go` reports the strongest hunk's range, 8 context lines included. A single-hunk diff is never asked about again, so every diagnosis gets the same range. A new file is one hunk, so its "where" is `lines 1-N`. Dev items 5, 8 and 13 are new files.
2. **The fix is the question restated.** [fact] `writeAdvice` prints each fired question and "Rework the change so they no longer apply." `added_copies` never names the original. The adapter's `fix` field is literally "Rework the change so this no longer applies."
3. **Report and gate measure different things.** [fact] Only push_back gates; diagnoses can't fail a run. Clearing every diagnosis doesn't promise push_back drops. A file failing with no diagnosis at 0.5 or above gets only "Simplify the change, then run qualm again." [conjecture] This is where fix loops stall and gaming starts.
4. **The vocabulary misses reviewers' asks.** [fact] The eight reviewer positives in `dev_examples.md` ask to:
   - drop a redundant `return await` (6) or redundant parens (2);
   - use Instant and Duration (8);
   - drop a sanity-check round trip (3);
   - use one code path for both cases (4);
   - drop an unused `sx` prop (7).

   At most three map onto the ten questions. Two aren't simplification requests: item 1 praises a removal of duplicate logic, and item 5 asks whether ids were reused. [conjecture] Even a perfect gate would send me to "fix" those.
5. **It mostly misses those asks.** [fact] It fails 16% of reviewer positives and 64% of injected smells. Reviewer positives are "one spot in an otherwise fine change". [conjecture] A whole-file question dilutes one spot.
6. **The PR controls its own judge.** [fact] `config.Load` reads `qualm.json` from the working tree under judgement. `qualm.json` is skipped as "prose or data", so editing it shows only as a count on line 2. Through it the PR can:
   - keep itself;
   - set the threshold to 1 (validation accepts ≤ 1);
   - redefine `push_back` with a same-id config question (`questions.Resolve` replaces built-ins by id);
   - add `"skip": ["*"]`, which gives "nothing to judge" and exit 0.

   `git check-attr` (`gitdiff.go:304`) reads `.gitattributes` from the working tree. CI workflows are YAML and skipped, so an edit adding `--threshold 0.99` is never judged.
7. **Skips go by name alone.** [fact] `classify` settles skip-rule files "from the listing alone", and a rename is judged by its new path. Code is never seen if it is:
   - moved under `test/`, `vendor/` or `build/`;
   - renamed to `*.generated.*`, `*.pb.go`, `*.min.js` or `*.test.ts`;
   - inlined in `.html`.
8. **A fresh score costs a trivial edit.** [conjecture] The close-call re-ask covers identical requests only. A cosmetic edit is a new request and a new draw, so a file at 0.62 can be walked under 0.6 by renaming a local. The guard sentence covers instructions, not comments that argue ("intentionally minimal"). The spike never tested this.

## What I'd be tempted to do, and what the tool must do

- **`qualm keep` my own change.** Read keeps and config from the base commit. A keep added in the PR is a blocking finding ("needs a person"). The agent's report never prints the keep command. It says: if a finding is wrong, stop and ask a person.
- **Edit `qualm.json`, `.gitattributes` or CI.** A change to any file that steers the gate blocks. Read attributes with `--source <base>`.
- **Move code out of sight.**
  - Judge a rename by the stricter of its two paths.
  - Fingerprint added lines in skipped files (winnowing) against judged files; a match of 8 or more lines fails.
  - Flag a judged file that imports a new test-path file.
- **Split across files.** Detect near-copies across the whole change and cite `related` lines. Extracting a called unit is the fix, so only duplicated or moved-unchanged code fires.
- **Injection or persuasion in comments.** Text addressed to the tool (qualm, Jev, "reviewer", "ignore", "answer no") blocks. The judge also scores the diff with comment lines stripped and keeps the higher score, so comments never lower it.
- **Cosmetic re-rolls.** Canonicalise whitespace before asking, and let deterministic features carry near-threshold calls.
- **Delete the feature or its tests.** The gate can't tell; the harness's behaviour check must. The tool bounds each fix to named lines and warns when test lines are deleted.

## Candidate C1: Pinpoint (Jev plus deterministic checks, no LLM)

**Computes.** Changed units in base and head, found by indentation and braces (`mechanical.py`'s `unit_end` already handles six languages), each with its diff slice and sizes. On those it runs:
- detectors for debug output, placeholder stubs, guards on a value constructed within 5 lines, and unit growth (40 or more lines, grown by 10 or more);
- near-copy winnowing across the change and the base file, which supplies `related`;
- the evasion detectors above.

**Asks.** Jev once per changed unit (push_back and five diagnoses), plus the baseline's file request. About $0.001 per PR, under 5 s.

**Decides.** The score is a logistic regression over:
- file push_back and the maximum unit push_back;
- units at 0.5 or above, and the top diagnosis;
- detector flags, size, and whether the file is new.

It is Platt-calibrated on dev, with the threshold set at the baseline's precision. Exact and evasion detectors block whatever the score.

**Reports.** Each finding gives:
- the exact lines, quoting the first one;
- a one-sentence why;
- a fix naming the counterpart ("a copy of lines 212-246");
- a checkable "done when": the line is gone; the blocks share under half their tokens; the unit is back to its old size; this unit scores under the threshold.

Each failing file says which findings, once resolved, bring it under the threshold (their regression features zeroed), so the report and the gate agree.

**Hypothesis.** Fix loops fail on imprecise, misaligned reports, not on the score. Asking per unit recovers the local asks that a whole-file question dilutes.

**Falsified if** first-iteration fix success isn't above the baseline's (the CI of the difference includes zero), findings hit labelled locations (±3 lines) no more often than the baseline's ranges, or removing unit features leaves reviewer-comment PR-AUC unchanged.

## Candidate C2: Reviewer's note (C1 plus one LLM call per failing file)

**Adds.** For each file C1 failed, a Haiku-class LLM gets the diff and C1's findings. For each finding it writes a reviewer-style comment and a replacement hunk of at most 30 lines that keeps behaviour, or marks the finding doubtful for a person (never a pass).

**Decides.** The verdict stays C1's, so LLM variance can't flip it. Haiku 5.5 rejects non-default temperature, so temperature 0 isn't available as a fix.

**Costs.** About 6K tokens per failing file at $0.10/$0.50 per million: under $0.002.

**Hypothesis.** A concrete patch lifts first-try fixes on idiom and redundancy findings, where a template can't say what to write.

**Falsified if** fix-loop success isn't at least 5 points above C1's, or accepted patches break tests more often than the agent's own fixes.

## Predictions

1. qualm 0.1.1 passes at least six of these eight attacks, and C1 catches all eight:
   - a self-keep;
   - threshold 1 in `qualm.json`;
   - `push_back` redefined in `qualm.json`;
   - `"skip": ["*"]`;
   - `linguist-generated` in `.gitattributes`;
   - code moved under `test/` or `vendor/`;
   - a rename to `*.generated.*`;
   - a CI workflow edit.
2. The baseline's median reported range on failing files exceeds 40 lines, and in at least 30% of them every diagnosis points at one range. C1's median finding span is 12 lines or fewer, with an equal or higher location hit rate.
3. At least half of the baseline's fix-loop failures end with tests passing and every diagnosis below 0.5 while push_back stays at or above 0.6, or with no diagnosis reported.
4. Any candidate whose verdict rests on a single LLM sample per file, with no vote or deterministic band, exceeds the 1% flip-rate limit. Report-only LLM use stays within it.
5. Exact detectors lift injected-smell fails from 64% to at least 90%, with at most 2 points more fails on clean and hard negatives. They move reviewer-comment PR-AUC by less than 0.03.
6. C2's patches raise first-iteration fix success by less than 5 points over C1. Once I know where, why and what "done" means, I write the fix myself.
