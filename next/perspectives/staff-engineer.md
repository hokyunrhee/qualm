# Staff engineer and reviewer

## What reviewers actually push back on

I read the eight reviewer positives in dev_examples.md the way I read a review queue. Six of them really ask for a simpler change, and two do not. #1 ("nice catch! Great to not have duplicate logic") praises a change that removes code. #5 ("Were ids reused ... could create duplicate docs") is a correctness question, caught by the WEAK pattern `duplicat(e|ed|es|ion)`. The six real requests: redundant parentheses (#2), a redundant `return await` (#6), "use `Instant` and `Duration`" (#8), drop a check that only logs (#3), unify two branches (#4), and drop a prop that none of the 16 callers passes (#7).

Each comment is anchored on a line. Six of the eight locations are a single line, and none covers more than 16. What the comment is about varies more. It can be an expression (#2, #6, #8), a unit to delete or merge (#3, #4), or an interface decision (#7). Every real request carries a verb and a replacement. That matches my experience: a request to simplify is almost never "this file is too complex". It applies one of five verbs to one spot:

- **remove**: an unnecessary check, code that has no effect, an unused option, debug output.
- **reuse**: the language, the library or an existing helper already does this.
- **merge**: duplicated logic, or a second path for a case the first already handles.
- **shorten**: a redundant construct that an idiom replaces.
- **split**: a unit that grew too long or too deeply nested.

The evidence for the verb usually lies outside the hunk. It is the helper 300 lines up, the other branch, the callers, or the docstring that admits the check changes nothing.

## Critique of qualm 0.1.1

1. **The gate is asked per file, but reviewers answer per spot. (Fact.)** `push_back` gives one overall probability per file. Reviewer positives are "usually about one spot in an otherwise fine change" (benchmark_summary). The baseline fails 16% of reviewer positives but 64% of injected smells. *Conjecture:* the mismatch in scale explains the gap. A 60-line helper with one `return await` reads as fine taken whole.

2. **Jev never sees the context reviewers use. (Fact.)** The state is `git diff -U8` (internal/gitdiff/gitdiff.go:283-285). So `added_copies` ("already in the file") and `added_impossible_guards` ("the surrounding code shows") are asked about a file Jev mostly cannot see. The full head file is in the repository, unread.

3. **The gate question discards the comments. (Fact.)** `push_back` says to "set comments and documentation aside". In #3 the docstring is the reviewer's evidence ("the docstring says as much"). *Conjecture:* this costs recall on agent code, whose narrating comments reviewers ask to have removed.

4. **Two verbs have no question at all. (Fact.)** Nothing asks about reuse (#8) or about redundant constructs (#2, #6). Parallel paths (#4) and code with no effect (#3) are covered only by stretching `added_impossible_guards`. The spike's `pass_through_layers`, `added_tangle` and `noise_comments` did not ship.

5. **Half the questions feed neither the gate nor the fix. (Fact for the cost, conjecture for the value.)** `comments_only` and `grew_a_big_unit` can be computed deterministically, and `added_placeholders` is a job for a regex. `new_behaviour` cannot clear a change, because nearly every reviewer positive is new behaviour. `direction` and `simplified` overlap with `push_back`.

6. **The report does not say where the problem is. (Fact.)** The where pass reports the hunk where a diagnosis is strongest (where.go). The README's own example therefore sends three diagnoses to the same "lines 327-577". The baseline adapter gives every finding the same fix: "Rework the change so this no longer applies." A reviewer would write "line 49: return the promise, the await is redundant". Given a 250-line range, an agent rewrites too much.

7. **The threshold was set to limit failures, not to match reviewers. (Fact.)** 0.6 was chosen because it blocked 2 of 99 commits in one unlabelled Ruby repository. On the trial it gives precision 0.71 and recall 0.33.

8. **Per-file false alarms compound across a PR. (Conjecture, from arithmetic.)** One failing file fails the PR. The baseline fails 4-14% of clean files, so a clean ten-file PR is blocked 34-78% of the time. Reviewers do not block that often. The gate needs a per-file false-alarm rate near 2%, and it needs to separate nits from blocking requests.

9. **The labels are noisy exactly where the signal is. (Fact.)** Two of the eight sampled positives are not requests to simplify, and nits count as positives. Whether a reviewer comments on a nit depends on the project: Go reviewers flag redundant parentheses, most others do not. That randomness, not the tool, limits the PR-AUC any tool can reach against real reviewers.

## What the gate should ask

Ask about each spot, not each file, and ask which verb a reviewer would apply. Detect the mechanical verbs deterministically. Fail a file on a likely blocking request, and report nits without failing. R1 does this.

## Candidate R1: spot review (Jev and deterministic checks, no LLM)

**What it computes.**
- Spots: the units (function, method or top-level block) that enclose each hunk, found by indentation and braces as next/collect/mechanical.py does. Each is matched to its base version by name, with lengths and nesting before and after.
- Winnowing fingerprints (k=5, window 4) over identifier-normalised tokens of every head file in the change, so a copy of existing code counts.
- Detectors for debug output and TODO stubs. A TODO that carries a bug link or an owner, like #12's, is exempt.
- Flags for changes that are clean by construction: comments only, formatting, a rename, a move.

**What it asks.** One Jev request per changed unit, at most 12 per file. The state is the unit's head and base text, a one-line-per-signature outline of the file, and the hunk. The questions are:
- `would_comment`: "Would an experienced reviewer leave an inline comment asking for this code to be removed, reused from elsewhere, merged, shortened or split?"
- one yes/no question per verb, plus `impossible_guard` and `unused_option`.

Comments count as evidence.

**How it decides.**
- A logistic regression over about 15 features: the max and noisy-OR of `would_comment` across spots, the max per verb, detector hits, copy similarity, unit growth, size, and the clean-change flags.
- It is fitted with cross-validation grouped by repository, then calibrated with isotonic regression.
- The threshold gives a 2% fail rate on clean negatives.
- A close call re-asks only the spot that decides it and takes the median of three.

**What the report says.** Each finding gives the spot's own lines, or both halves of a copy through `related`. It gives the verb, a message built from facts ("lines 327-361 repeat lines 212-246 with one literal changed"), and a fix specific to the verb ("Delete the check: `x` is constructed on line 58").

**Cost.** About 10k tokens per file, roughly $0.004 for a ten-file PR, in 2-3 s.

**Hypothesis.** The baseline misses reviewer positives because it judges whole files with too little context, not because of Jev.

**What would falsify it.** On dev with repository-grouped cross-validation, any one of these:
- At the baseline's precision, R1 does not fail at least 10 points more Tier-A reviewer positives.
- Its PR-AUC on reviewer items is not at least 0.05 higher.
- The verb questions add less than 0.02 PR-AUC over spot-level `would_comment` alone. Then the scale of judgement is everything and the verbs are decoration.

## Candidate R2: an LLM reviewer for the uncertain band (R1 plus an LLM)

**How it works.**
- R1 settles every file outside an abstain band. The band is set by conformal risk control so that at most 2% of dev decisions outside it are wrong.
- Each file inside the band gets one call to a Claude Haiku-class model, with the head file (at most 8k tokens around the changed units), the diff and the outline.
- The model returns at most three inline comments, each with the lines, the verb, blocking or nit, the quoted code and a replacement.
- The tool checks each comment. The quote must appear in the added lines, and the replacement must be shorter or delete lines. Comments that fail are dropped, which also defeats instructions planted in code comments.
- The file fails on any verified blocking comment. The report is the comments themselves.

**Cost.** About $0.002 per file in the band, under $0.01 per PR if 20% of files land there.

**Determinism.** Temperature 0. If two calls disagree, the file is judged on the union of their blocking comments.

**Hypothesis.** An LLM writes the comment a person would. Keeping it to the uncertain band gets most of the gain for about a fifth of the cost.

**What would falsify it.** On the 150-item dev subset, any one of these:
- R2 beats R1 by less than 0.05 PR-AUC on reviewer items.
- Its fix-loop success is no higher than R1's.
- Its verdicts flip on more than 1% of files across repeated runs.

## Predictions

1. At least 50% of dev reviewer positives have a location of three lines or fewer. The baseline fails at most 10% of that group, against at least 25% of the wider ones.
2. LLM classification of the dev signal comments rejects at least 15% of reviewer positives as not requests to simplify.
3. The deterministic detectors alone fail at least 90% of mechanical injections while failing at most 3% of clean negatives. On reviewer items they score PR-AUC within 0.05 of prevalence.
4. In a logistic model over the baseline's ten answers, dropping `comments_only`, `new_behaviour`, `direction`, `simplified` and `added_placeholders` costs at most 0.01 PR-AUC.
5. No candidate reaches PR-AUC 0.60 on held-out reviewer items alone (reviewer_comment positives against approved negatives). Excluding nit-flagged positives at least halves the winner's margin over the baseline.
6. In the fix loop, findings that span at most 15 lines and name a verb succeed on the first iteration at least 15 points more often than the baseline's.
