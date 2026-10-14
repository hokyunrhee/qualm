# Review of d-cascade: the coding agent's view and the staff engineer's view

**Versions.** The fix-loop reports came from **v1**: model `raw+masked+det`, threshold 0.505, localization by diff hunk. Main moved to **v2** during this review, in commit 79e60cc at 02:30:
- the worktree's scorer, which localizes per changed function;
- a `raw+masked` model, threshold 0.509;
- `band.json` set to `[0.26, 1.01)`.

Each finding below says which version it applies to. I edited nothing under `/home/user/qualm`. My repro scripts are in `…/scratchpad/review_d/`: `exp1.py` to `exp7.py`, `det.py`, `pieces.py`, `rules.py`, `sets.py`, `nc.py`. Unit tests pass: core 33, d-cascade 13 (the same 13 in main and the worktree; none cover `pieces()` or `piece_state()`).

## Verdict

Not yet better than qualm as the report a coding agent fixes from.

- **Catching (v1, dev fix loop):** d-cascade caught 18 of 40 injected smells, qualm 21. For impossible guards it was 3/10 against 7/10. All 40 items are rows in d-cascade's dev training set, so even this is in-sample.
- **Fixing:** 12 items were caught and cleanly fixed, against 14 for qualm. "Cleanly" means the gate passes, the tests pass, the smell is gone and nothing was gamed.
- **What works:** the exact-line detector findings.
- **What fails:** the `review.*` findings. Over 21 of them the median span was 64 lines, and 12 spanned more than 60. They account for all 3 items that still failed after two rounds, and for 2 of the 3 gamed fixes. The third was caused by a perimeter bug.
- **v2's model is a real fix for detection.** On dev out-of-fold at the same 0.65 precision it catches 21/40 LLM injections against 16/40. It fails fewer approved (clean) files, 70 against 83, but more post-fix files, 147 against 131.
- **v2 still has these problems:**
  - It lists detector findings on failing files although they no longer affect the score.
  - The new function-level localization has bugs.
  - The perimeter can skip a file the pull request controls.
  - An LLM answer of `{}` passes the file.
  - The README still describes v1.

## 1. As the coding agent

1. **Whole-file `review.*` findings cannot be acted on, and they caused every two-round failure.**
   - df3d7dfb (two impossible guards at lines 91 and 114): "lines 3-127 [review.shorten] … Lines 3-127 can be written more directly", twice. Then "lines 3-107 [review.remove]". The guards were never named.
   - 20e672c2, after the agent fixed what was listed: "lines 3-92 [review.shorten]", then "lines 3-79".
   - a9ac10fa, iteration 2: "Lines 256-428 hold code that can be deleted without changing behaviour". The blind judge rated this final version simpler than the clean pull request.
   - 166b9dec: "lines 3-103 [review.added_placeholders]".
   - Verb counts across all reports: shorten 7, remove 5, unused_flexibility 5, placeholders 2, copies 2. `added_impossible_guards` was never chosen, although 10 of the 40 items are impossible guards.
   - Cause in v1: a new file, or an `-U8` diff whose hunks merge, is a single hunk. The top hunk is always reported (`if pb < 0.5 and k > 0` keeps k = 0). The verb is the argmax of raw probabilities, and shorten and remove have the highest base rates.
   - The clean pull-request code then sits just above 0.505. 20e672c2's three readings were 0.5115, 0.5032 and 0.5209, so failing it was a coin flip.
   - **Proposed change:** see section 3, change 1.

2. **Done-conditions push the agent toward golfing or deleting features.**
   - 20e672c2: the condition "Done when the same logic takes noticeably fewer lines" produced this rewrite, which keeps the debug print:
     ```ruby
     $stderr.puts "hash_public_cache: #{file} => #{cache[file] = hash_path_digest(file)}"
     ```
     The checker still recorded `smell_removed: true`, because it matches the injected lines as text. That is a benchmark bug.
   - 45e7e490, iteration 1: "Lines 187-250 add an option, hook or layer of indirection with a single user" and "Lines 295-412 … can be deleted". The agent deleted the pull request's configurable Timeout, Parallel and Errors features. The judge recorded `final_removes_functionality: true`, and the checker flagged gaming ("removed definitions … Error").
   - a9ac10fa: long_function, deep_nesting and near_copy findings on Go type-switch arms led to a 433-line generics rewrite. The next round then flagged the agent's own extraction: "medianArg … is a near copy of meanArg … 62%".
   - **Proposed change:** done-conditions tied to the named construct, not to line counts or "every added line". A standing line in the report: "do not remove options or behaviour the change introduces".

3. **Some findings point away from the smell and toward deleting tests, docs or notes.**
   - 33054425: `spec/plugin/params_capturing_restore.rb` was judged as code. `perimeter.classify` (perimeter.py:219-224) re-judges a test file when another judged file mentions its stem after "load", and here that was a prose comment at `params_capturing.rb:59` ("…you load the params_capturing_restore plugin"). The top finding then read "Lines 17-34 repeat lines 13-32 of spec/plugin/params_capturing_restore.rb … Done when the repeated lines exist only once". The agent deleted the spec-directory file. The gate passed with the injected copy at lines 40-81 still present (`smell_removed: false`).
   - 809b0b73: lines 14-15 were flagged as commented-out code, but they are the RDoc usage example. The agent deleted the documentation, and the guard at line 19 was never named.
   - 166b9dec: a documentation example at line 42 was flagged, while the real commented-out assignment at line 76 (`# hex = ::Digest::SHA256.file(...)`) was missed. Cause: the single-line rule at detectors.py:147.
   - b535ff89: four placeholder findings on the author's own "TODO: never reached?" notes. The agent deleted them, losing information.
   - 45e7e490: the top model feature (`max_debug_output`, +0.528) came from `fmt.Println(string(d))`, which already existed at line 250 of the base and was only moved into `Run`.

4. **A precise but partial report produces a partial fix.**
   - In 20e672c2, round 0 listed only "lines 81-82 [commented_out_code]". The `$stderr.puts` at line 75 was not detected; the detector for it landed in e101819 at 02:06, after this run. The agent fixed exactly lines 81-82.
   - qualm's "added placeholders 0.58 lines 1-98" got both leftovers fixed in one round.
   - In the d-cascade run, the third-largest model contribution was `raw_added_placeholders`, yet no finding named it.

5. **The findings that told the agent exactly what to do** were all line-precise detector hits, and each was fixed in one round:
   - 1905bd7b: "expandTabWidth (lines 202-217) is new and nothing in the repository calls it", plus commented-out code at 204-209 and the TODO at line 200.
   - 49322327: "Lines 79-87 (in _defuFn) repeat lines 38-46 … the differences are result for obj, value for val" with "See:". The agent replaced `_defuFn` with `extend(...)`.
   - b535ff89: near_copy at 495-499. Also 26477edc, aa54b6ae, 9be4ccf5 and a2d34a0d (debug output and commented-out code).
   - Overall, 10 of the 18 caught items had a line-exact finding on the injected lines. The other 8 got either spans of 61 or more lines or findings elsewhere.
   - Run offline, the detectors hit injected lines for 0/10 impossible guards, 0/6 single-use indirections and 2/10 near copies. Relaxing the token minimum from 40 to 16 adds only 33054425. For three of the five smell classes, Jev's localization is the only pointer the agent gets.

## 2. As the staff engineer (ordered by impact)

1. **The cascade can pass a file it could not judge** (`review.read`, review.py:91-93).
   - `{}` reads as `blocking=False` and passes. `{'probability': 0.9}` also passes, and shows score 0.90 for a passing file.
   - In v2, `band.json` is `[0.26, 1.01)` with "auto_fail": 0, so with `--llm-dir` Haiku decides every file at p ≥ 0.26, including p = 0.95.
   - The README still says 0.505, a band of [0.25, 0.69), and "22 answers and the detectors' counts".
   - **Fix:** a non-boolean `blocking` or an out-of-range probability raises `BadAnswer` (exit 2). Cap the band's top edge, or keep auto-fail above the threshold. Update the README.

2. **The pull request can take a file out of judgement** (perimeter.py). Each case is reproduced in `exp1.py` or `exp7.py`.
   - **Generated:** any changed `.proto` or `.graphql` makes any file with a "generated" header, or named `*_generated.*`, skip (line 201, `elif changed_sources`). This contradicts the README.
   - **Minified:** one 12 KB string line makes a modified `.py` file "minified" (line 206). That decision is not anchored to the base commit.
   - **Re-judging:** a test file is re-judged as code on prose in another file (lines 219-224).
   - **Directories:** existing `external/` directories are treated as vendored (line 18), and Python code under `testing/` is treated as tests (line 31).
   - **Fix:**
     - Trust a generated header only if the base version was generated, or its generator already appears in the base.
     - Skip as minified only if the base version was minified.
     - Re-judge only on import statements in masked code (`FileCtx.code`), never on comments.
     - Drop `external` from `VENDOR_DIRS`, or require a base attribute for it.

3. **"Added" is wrong for moved, re-indented or line-ending-changed lines** (`units.FileDiff`, units.py:159-189).
   - A CRLF-to-LF change marks all 6 lines of a file as added, and the existing print, TODO and commented-out code are reported as new. The masked diff is empty.
   - The moved print in 45e7e490 is the same bug.
   - **Fix:** `FileCtx.new_lines` = added lines whose `rstrip('\r ')`/`strip()` text is not among the removed lines. Detectors that flag leftovers use `new_lines`.

4. **Copy detection.**
   - `ChangeCtx.base_neighbours` caches by directory only (analyze.py:85). When an unrelated `.js` file in the same directory also changed, a TS copy of `src/old.ts` is missed entirely (`exp3.py`). In another run it pointed "See:" at `src/old.js` instead.
   - `near_copy` skips any counterpart later in the same file (`olo > lo`, detectors.py:278). A copied block placed above its original is never reported (`exp4.py`).
   - **Fix:** key the cache by `(dir, language)`. Skip only when the counterpart's lines are themselves mostly added.

5. **`is_script()` matches name prefixes** (analyze.py:58). `client.go`, `client.py`, `maintenance.ts`, `commander_helpers.rb` and `main_menu.py` are all treated as entry points, so their prints are not reported. **Fix:** match the file stem exactly.

6. **`commented_out_code` flags documentation examples.** Go `//\t` examples, JSDoc `@example` blocks and RDoc `#   ` examples are all flagged at confidence 0.7. **Fix:** skip code-like lines in a comment block that also has prose and indents its code deeper, or that follows `@example` or `Example:`.

7. **v2 function localization** (scorer.py:102-124 and 207-212).
   - `piece_state` drops removed lines and writes a malformed header (`@@ -1 +1,7 @@`). The `pieces.py` repro shows `s += i.cost` presented as pure addition, without the `s += i.price` it replaced.
   - There is no size cap (`PIECE_LIMIT`) on a place.
   - Places are the top 6 by added lines, so small new helpers are dropped. 86352aed's injected `_warn_classmethod_call` (lines 17-24) is not among its places.
   - The "covered" rule counts function-wide findings. In a9ac10fa, `long_function [256-369] 0.62` would suppress the place for `minMax`, which holds the guard.
   - The verb is still the argmax of raw probabilities.

8. **Report.**
   - `findings[:8]` truncates silently; a9ac10fa had 10 findings.
   - Advice is hidden on a PASS (report.py:44). The `cross_file_copy` and `fifth_copy` attacks pass with near_copy findings at confidence 0.95 that nobody sees.
   - A copy between two changed files is reported from both sides.
   - v2 lists detector findings at confidence ≥ 0.3 on failing files (scorer.py:257), but fixing them cannot change the score.

9. **Lexer and units** (`exp5.py`).
   - `s.replace(/"/g, "")` makes function `a` vanish as a unit.
   - Java text blocks: `// not a comment` inside the block is lexed as a comment.
   - Ruby endless `def a = 1` extends to the next method's `end` and folds `b` into `a`.
   - TS generic arrow functions are not units.
   - Dead condition at lexer.py:87 (`len(q) == 3 or True`).

10. **Maintenance.**
    - The model has no version stamp for the feature code. v1 was trained at 01:40; the detectors changed at 02:06. Training features came from `offline.MemoryCtx`, which has no neighbours and searches only the changed files' base text, while a real run searches the whole repository.
    - Platt scaling is fitted on out-of-fold logits but applied to the full-data fit.
    - The selected C (0.003) sits at the edge of the grid.
    - Fix-loop Jev cost was 4.4 times qualm's ($0.0211 against $0.0048).
    - The masked view still passes strings of fewer than six words and all identifiers. The raw view, which carries the largest weight, includes comments.

11. **Smaller issues.**
    - Files over 1 MB are truncated and judged on partial text (gitio.py:126).
    - `--threshold 0` is ignored.
    - A pull request with no judged files still exits 2 when there is no API key.
    - Language scope is narrower than qualm's: `.pyi`, `.erb`, Rust, Kotlin and others pass unjudged.
    - `GATE_WORDS` does not include `d-cascade`, so edits to a workflow that runs d-cascade are not named.
    - Exit codes, the signed cache, and budget handling behave correctly on every path I traced.

## 3. Three changes

None of these changes the probability, so the rate of failing clean code stays where v2 has it.

1. **Make localization verdict-aligned and function-sized** (`scorer.pieces`, `piece_state`, `localize`, `score`).
   - Places: every new unit plus every changed top unit, with new units first, capped at 60 KB in total.
   - Show the removed lines inside each place, under a valid hunk header.
   - Count only line-precise findings (span ≤ 8 lines) toward "covered".
   - Choose the verb by z-score: `(logit(a[q]) - mean_q) / scale_q`, using the dev means and scales that `model.json` stored for `masked_*` in v1.
   - Report every place with push_back ≥ 0.5 and nothing below it. Drop the `k > 0` exception, and never report a span wider than one function.
   - On a failing file, list a detector finding only if it lies inside a reported place or is a precise leftover finding. Move the rest to advice.
   - Name any top-contributing Jev question that no finding covers.
   - This targets a9ac10fa, df3d7dfb, 20e672c2, 45e7e490 and 86352aed.

2. **Replace the golfable and deleting done-conditions in `VERB_TEXT`, and in the fix text for long_function and deep_nesting.** For example, shorten becomes "replace the named construct with …", and remove becomes "delete the lines that are dead or never read; keep every option the change introduces". Add a standing line forbidding deletion of tests, documentation examples or features. This targets 20e672c2, 45e7e490 and a9ac10fa.

3. **Fix "new code" and test/doc misdirection in the core.**
   - `FileCtx.new_lines` for moved, re-indented and CRLF lines.
   - Skip documentation examples in `commented_out_code`.
   - Match exact stems in `is_script`.
   - Re-judge test files on code imports only.
   - Key `base_neighbours` by language.
   - This targets 33054425, 809b0b73, b535ff89, 166b9dec and 45e7e490.

**Caveat:** single-use indirection is 0/6 for qualm, v1 and every v2 feature set. I found no detection change I could verify offline that adds catches without more fails on dev.
