# Theorist: what deterministic math can say about "harder to change"

## Stance

A reviewer asks for simplification when a change says more than its behaviour needs. Call the gap excess: how long the change takes to describe, given the base file, minus the shortest description of the same behaviour. The second term can't be computed, but four parts of excess are visible without semantics:

- **Redundancy.** The added code compresses well against the rest of the file.
- **Dead weight.** Code that changes no behaviour: debug output, stubs, impossible guards, single-use indirection.
- **Concentration.** Reading cost grows faster than linearly with unit size and nesting, so where the new lines go matters as much as how many there are.
- **Idiom.** A longer form is used where a standard shorter one exists.

Everything else (library knowledge, reuse across files, intent) is beyond metrics, and that covers most reviewer comments.

## (a) Critique of qualm 0.1.1

1. **[fact] The judge can't see what two of its diagnoses ask about.** The request holds the diff with eight lines of context and nothing else (`request` in `internal/check/judge.go`). `added_copies` asks about "logic already in the file" and `grew_a_big_unit` about "an already long function", but neither the rest of the file nor the unit's length is in the request. By the data-processing inequality, no answer carries more information about them than the diff does. Both have exact deterministic definitions (clone detection, and unit size in the head file).
2. **[fact] It misses a third of mechanical smells.** The baseline fails 64% of injected items (benchmark summary). The injector (`next/collect/mechanical.py`) writes four things:
   - `print("DEBUG …")`;
   - a `TODO: implement fallback` stub;
   - a copied function with one literal changed;
   - a nil check right after a constructor.

   Each of these can be matched by a lexer rule or a fingerprint with near-perfect recall.
3. **[fact] The gate value is not a probability.** ECE is 0.128 on the trial sample. The 0.6 threshold came from blocking 2 of 99 commits of one repository, with no labels (design doc). At 0.5, 11 commits were blocked, so 9% of commits sit in a band 0.1 wide around the decision point *(the counts are fact; reading them as poor resolution is conjecture)*.
4. **[conjecture] push_back is largely a size detector.** The spike's only positive was "22 methods added to one class". Clean fail rates are 14% on survived_90d items, which need 10–300 added lines (`history_mine.py`), but 4% on approved changes. This can be checked: compute push_back's partial correlation with the label given log added lines.
5. **[fact] Localization points at the hunk, not the problem.** `gitdiff.Hunks` reads the `+c,d` range of a `-U8` diff, so every range carries up to 16 context lines. A new file is one range covering the whole file, and a lone hunk is reported without asking (`where.go`). The README's sample report gives the same "327-577" to three different diagnoses. `overlaps()` in `metrics.py` uses a slack of 2 lines and no width penalty, so wide ranges score well for the wrong reason.
6. **[fact; the arithmetic assumes independent files] The PR gate is an OR over files with a fixed per-file threshold** (contract G1). At the trial rates, a clean PR with five judged files fails 18% of the time at the approved-change rate and 53% at the survived_90d rate. Error should be controlled per PR, not per file.
7. **[fact] Some questions are paid for but are exact facts.** `comments_only`, and the hard negatives (whitespace, consistent rename, move), can be decided exactly by normalizing. Asking a model that drifts by ±0.03 adds noise and cost and gains nothing. The baseline already fails 0% of hard negatives, so the saving is in cost and determinism, not accuracy.

## What the math sees, and what it confounds

**Size.** Halstead volume and effort, and summed cyclomatic complexity, correlate with lines of code at about r≈0.9. The labels also depend on size:

- **Reviewer comments.** A comment is roughly a per-line event, so P(comment) ≈ 1−(1−p)^a grows with the number of added lines a.
- **History labels.** Both are selected partly on size. Positives need at least 3 rewritten lines making up at least 30% of the change. Negatives need 10–300 added lines, at least 90% of them surviving.

So almost any metric will look predictive. The honest bar is a **size-only model**. The size-free tests are the matched pairs the benchmark already has: an injected file against its clean twin, and a reviewer positive against its post_fix.

**Injected smells.** Lexical detectors and winnowing should catch them almost perfectly. They mirror the generator, though, so this slice measures agreement between detector and generator, not agreement with reviewers.

**Reviewer pushback.** I expect only a weak signal, from:

- growth in nesting depth;
- convex unit growth;
- near-copies;
- single-use abstractions;
- branches that only log.

My reading of the eight reviewer positives in `dev_examples.md` *(conjecture)*:

| Kind | Examples |
|---|---|
| Label noise | #1 praises a deletion; #5 asks about ID reuse |
| One-token idioms | #2 redundant parens; #6 `return await` |
| Needs knowledge outside the file | #7 callers elsewhere; #8 `java.time` |
| Structural, within reach of metrics | #3 a check that only logs; #4 a block re-indented under a new condition that duplicates a shareable path |

**Languages.**

- **Nesting from indentation.** Indentation is a sound proxy for nesting in Go (gofmt), Python and Java. In TSX, indentation is mostly JSX tree depth, so nesting must count control keywords only.
- **Go error checks.** `if err != nil { return …, err }` inflates cognitive complexity and clone counts. Example 14, a survived_90d negative, adds a dozen of them, so the idiom must be discounted.
- **Headers and imports.** Licence headers and imports inflate size (example 8).
- **Python docstrings** are strings and must be stripped like comments.
- **TODO.** A TODO on its own is not a smell. Example 12, a post_fix negative, adds an owned TODO with a bug link. A TODO inside a stub body is a smell.

**NCD.** The symmetric NCD(A, R), between the added code A and the rest of the file R, is about 1 whenever R is much larger than A, because it normalizes by C(R). The right quantity is the asymmetric novelty ν = [C(R·A) − C(R)] / C(A). Two limits:

- ν is noisy below about 500 bytes, because of DEFLATE overhead.
- It needs a compression window that covers R (DEFLATE's is 32 KB).

Winnowing measures the same thing more locally, so I expect ν to add nothing.

## Feature set (no parser)

**Inputs.**

- **Tokens.** A lexer per language family (C-like: Go, Java, TS, JS; Python; Ruby) emits tokens with a kind (keyword, identifier, literal, string, comment, operator) and a line number.
- **Added lines.** A pinned `git diff -U0` gives the added lines A in new-file coordinates.
- **Units.** Header regexes find units. Their extent comes from brace matching on comment- and string-stripped tokens (C-like), from indentation (Python), or from an `end` at the same indent (Ruby).
- **Matching.** Base and head units are paired by enclosing class and name.
- **Nesting.** A line's nesting is the number of enclosing control lines in its unit, judged by indent containment.

| id | definition |
|---|---|
| size | log1p of added and of deleted code lines, log1p of head code lines, and the hunk count. Used only as controls. |
| regret | Σ over grown units of [(s+g)^1.5 − s^1.5 − g^1.5] / 50^1.5, where s is the unit's base size in code lines and g its growth. It is 0 when the new code is a unit of its own: the cost of not extracting, under a convex load. |
| class_growth | The largest number of methods added to any class that already had at least 15. |
| dcog | Σ over touched units of max(0, cognitive_head − cognitive_base), by SonarSource's rules with indent nesting and Go's error-return idiom counted as 0. Also its density, dcog divided by added lines. |
| dnest | The deepest nesting among added lines, minus that unit's deepest nesting in base, floored at 0. |
| copy | Winnowing with k=12 normalized tokens and w=8 (identifiers become I, literals become L). For each added unit, or each added block of at least 6 lines: the highest containment in any other span of the head file. Each fingerprint is weighted by IDF from a per-language table of common fingerprints built from dev base files, so idioms count for little. Returns both spans. |
| single_use | New unexported units used at most once in the head file. In MDL terms, a single use pays for a header and a call and saves nothing. Also pass_through: a body that is one call forwarding its parameters. |
| log_only | An added conditional whose body is only logging calls. |
| lexical | debug output (a print-like call of a kind absent from the base); stub (TODO or FIXME in a unit whose body is at most one trivial statement); guard-after-construct (a null test of a name assigned a literal or constructor no more than 3 lines above); commented-out code; and a short list of redundant idioms per language. |
| exact | Whitespace-only, comment-only, a consistent rename (a bijective identifier map) or a pure move scores 0. |
| ablation only | ν, change entropy over units (Hassan), and Halstead difficulty of the added code. |

**Tree-sitter** would earn its weight only where unit boundaries are ambiguous: arrow functions and object literals in TSX and JS, and Ruby blocks. Measure it rather than ship it. Compare unit boundaries with tree-sitter's on dev offline, and adopt it only if using it moves PR-AUC by at least 0.01.

## (b) Candidates

### T1: metric gate, offline

- **Computes** the features above from the base and head text of each judged file.
- **Decides in two layers.**
  - *Layer 1.* A lexical detector, or a copy above 0.8, fails the file directly, provided its precision on dev clean negatives is at least 0.9. Detectors below that precision feed layer 2.
  - *Layer 2.* A logistic regression. Every smell coefficient is constrained to be at least 0, so adding a smell can't lower the score. It has language intercepts and the size controls, is fitted with 5-fold cross-validation grouped by repository on dev, and is Platt-calibrated on out-of-fold scores.
  - *Threshold.* The lowest one with out-of-fold precision of at least 0.75. A conformal check on clean negatives keeps the false-fail rate per PR (n files at α/n each) at 10% or below.
- **Report.** Each finding is a detector, or the feature contributing most. It gives:
  - the exact added lines (from the first to the last added line in the unit or block, never context lines);
  - the related span, for a copy;
  - a message with numbers ("handleRequest grew from 182 to 261 lines; lines 340–418 are one block at depth 3");
  - a fix ("move lines 340–418 into a function of their own");
  - a confidence, set to the detector's dev precision.

  Each escape route trips another feature. Splitting code into single-use helpers trips single_use. Renaming a copy leaves its normalized fingerprints unchanged.
- **Cost.** $0, under a second, and a flip rate of 0 by construction. No model reads the text, so prompt injection has nothing to act on.
- **Hypothesis.** Excess structure, measured from the full files, predicts pushback as well as Jev reading the diff.
- **Falsified if,** on the dev items that are not injected, either:
  - T1's PR-AUC is below the baseline's with the bootstrap CI of the difference entirely below 0; or
  - T1 beats the size-only model by less than 0.02 PR-AUC. In that case its "excess" is just size.

### T2: Jev plus metrics

- **Asks** one Jev request per file, as qualm does now. All ten answers enter as logits alongside T1's features, in the same constrained logistic regression. T1's lexical layer is unchanged, and it puts a floor under the score that no text in the diff can lower.
- **Localizes** without the hunk-by-hunk pass, from T1's spans. When only Jev's diagnoses fire, they are attached to the unit with the largest regret or dcog.
- **Re-asks a close call** only when the combined score falls within 3·β_jev·σ_jev of the threshold. Here β_jev is Jev's coefficient in the model and σ_jev is the spread of Jev's logit over repeated identical calls.
- **Cost.** One request per file, the baseline's main request (about $0.0003 per PR), without the where-pass requests.
- **Hypothesis.** Jev and the metrics make complementary errors: Jev sees idiom and intent, while the metrics see the whole file exactly.
- **Falsified if** either:
  - T2 does not beat the baseline on the items that are not injected (the CI of the difference includes 0); the gain would then be only the injected smells that T1 alone catches. Or:
  - Jev's coefficient falls to about 0 once the metrics are in; then Jev should be dropped.

### For the harness

- Add a size-only logistic model as a reference.
- Report paired AUC on the matched pairs (injected against clean twin, reviewer positive against post_fix).
- Score localization by IoU as well as by the slack-2 overlap.

## (c) Predictions

1. On mechanical injections, T1 fails at least 95% of injected files and at most 3% of their clean twins. The baseline stays near 64%.
2. A size-only logistic model reaches ROC-AUC of at least 0.65 on the dev items that are not injected. Among negatives, push_back has a Spearman correlation of at least 0.3 with log added code lines.
3. On the items that are not injected, T1 does not beat the baseline's PR-AUC. T2 beats it by at least 0.03, but on reviewer_comment items alone its gain is under 0.03 and the CI includes 0.
4. On pairs of a reviewer positive and its post_fix, no deterministic feature reaches a paired AUC of 0.60, and neither does Jev's push_back.
5. The ablation keeps lexical, copy, regret and dnest. Removing Halstead, change entropy or ν changes grouped-CV PR-AUC by less than 0.005. ν correlates with copy at 0.7 or more on blocks of at least 500 bytes.
6. T1's and T2's median finding width is at most a fifth of the baseline's. Under the slack-2 rule, the baseline's hit rate is within 10 points of theirs. Under IoU ≥ 0.2, they beat it by at least 20 points. Tree-sitter moves PR-AUC by less than 0.01.
