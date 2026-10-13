# Theorist: what deterministic math can say about "harder to change"

## Stance

A reviewer asks for simplification when a change says more than its behaviour needs: excess description length given the base file. The minimum is uncomputable, but four parts of excess are visible without semantics: redundancy (added code compresses against the file), dead weight (debug output, stubs, impossible guards, single-use indirection), concentration (reading cost is superlinear in unit size and nesting) and idiom. Library knowledge, cross-file reuse and intent are out of reach, and that is most reviewer comments.

## (a) Critique of qualm 0.1.1

1. **[fact] The judge can't see what two diagnoses ask about.** The request is the diff with eight context lines (`request` in `internal/check/judge.go`). `added_copies` asks about "logic already in the file", `grew_a_big_unit` about "an already long function"; neither the file nor the unit's length is sent. By data processing no answer knows more than the diff, yet both have exact definitions.
2. **[fact] It misses a third of mechanical smells** (64% fail rate, benchmark summary), though `next/collect/mechanical.py` writes `print("DEBUG …")`, `TODO: implement fallback` stubs, one-literal function copies and nil checks after constructors, each matchable by a lexer rule or fingerprint.
3. **[fact] The gate value is not a probability.** ECE is 0.128, and 0.6 came from blocking 2 of 99 unlabelled commits (11 at 0.5, design doc).
4. **[conjecture] push_back is largely a size detector.** The spike's one positive was "22 methods added to one class"; clean fail rates are 14% on survived_90d items (which need 10–300 added lines, `history_mine.py`) against 4% on approved changes. Check: push_back's partial correlation with the label given log added lines.
5. **[fact] Localization is the hunk, not the problem.** `gitdiff.Hunks` reads the `+c,d` range of a `-U8` diff, so ranges carry up to 16 context lines and a new file is one whole-file range (`where.go`); the README gives "327-577" to three diagnoses. `overlaps()` in `metrics.py` has no width penalty, so this scores well for the wrong reason.
6. **[fact; independence assumed] The PR gate is an OR over files** at a fixed per-file threshold (contract G1). At the trial rates a clean five-file PR fails 18% (approved rate) to 53% (survived rate) of the time. Error should be controlled per PR.
7. **[fact] Exact facts are bought from a noisy model.** `comments_only`, whitespace, renames and moves are decidable by normalizing (the baseline already passes them, so the gain is determinism and cost).

## What the math sees, and what it confounds

**Size.** Halstead volume, effort and summed cyclomatic complexity correlate with lines of code at r≈0.9. The labels depend on size too: a reviewer comment is roughly a per-line event, so P(comment) ≈ 1−(1−p)^a rises with added lines a, and both history labels are selected on size (≥3 rewritten lines and ≥30% of the change; 10–300 added lines). The honest bar is a **size-only model**; the size-free tests are the matched pairs: injected against clean twin, reviewer positive against its post_fix.

**Which measure predicts what.** Lexical detectors and winnowing should catch injected smells almost perfectly, but they mirror the generator, so that slice measures detector-generator agreement. For reviewer pushback I expect weak signal from nesting growth, convex unit growth, near-copies, single-use abstractions and log-only branches, and nothing beyond size from Halstead or change entropy. Of the eight reviewer positives in `dev_examples.md`, I read #1 and #5 as label noise (praise of a deletion; a question about ID reuse), #2 and #6 as one-token idioms, #7 and #8 as needing outside knowledge, and only #3 (a log-only check) and #4 (a block re-nested, duplicating a shareable path) as structural *(conjecture)*.

**Languages.** Indentation is a sound nesting proxy in Go, Python and Java; in TSX it is mostly JSX depth, so count control keywords only. Go's `if err != nil { return …, err }` inflates complexity and clone counts (example 14, a negative, adds a dozen) and must be discounted. Licence headers and imports inflate size (example 8); Python docstrings are comments. A bare TODO is no smell (example 12, a negative, adds one with a bug link); a TODO in a stub is.

**NCD.** Symmetric NCD(A, R) between added code A and the rest of the file R tends to 1 whenever R ≫ A, because it divides by C(R). The right quantity is novelty ν = [C(R·A) − C(R)] / C(A), noisy below about 500 bytes and needing a window covering R. Winnowing measures the same thing locally, so I expect ν to be redundant.

## Feature set (no parser)

A lexer per family (C-like, Python, Ruby) emits typed tokens with line numbers; a pinned `git diff -U0` gives added lines. Units come from header regexes, with extent by brace matching on stripped tokens, indentation (Python) or a same-indent `end` (Ruby), paired across versions by class and name. Nesting is the count of enclosing control lines in the unit.

| id | definition |
|---|---|
| size | log1p added, deleted and head code lines; hunk count. Controls only. |
| regret | Σ over grown units of [(s+g)^1.5 − s^1.5 − g^1.5] / 50^1.5, with s the base size and g the growth in code lines. Zero when new code is its own unit: the cost of not extracting, under a convex load. |
| class_growth | Most methods added to one class that already had ≥15. |
| dcog | Σ over touched units of max(0, cognitive_head − cognitive_base), SonarSource rules with indent nesting and Go's error-return idiom counted 0; also dcog per added line. |
| dnest | Deepest nesting among added lines minus the unit's deepest in base, floored at 0. |
| copy | Winnowing, k=12 normalized tokens (identifiers → I, literals → L), w=8: for each added unit or ≥6-line block, highest containment in another head span, fingerprints IDF-weighted by a per-language table from dev base files so idioms count little. Returns both spans. |
| single_use | New unexported units used at most once (in MDL terms, one use pays for a header and a call and saves nothing); pass_through: a body that is one forwarding call. |
| log_only | Added conditional whose body only logs. |
| lexical | Debug output of a kind absent from the base; stub (TODO/FIXME in a unit with at most one trivial statement); null test of a name assigned a literal or constructor ≤3 lines above; commented-out code; a short per-language list of redundant idioms. |
| exact | Whitespace-only, comment-only, bijective rename or pure move scores 0. |
| ablation only | ν, change entropy over units, Halstead difficulty. |

**Tree-sitter** would earn its weight only where unit boundaries are ambiguous (TSX/JS arrow functions and object literals, Ruby blocks). Measure, don't ship: compare boundaries on dev offline and adopt it only if it moves PR-AUC by at least 0.01.

## (b) Candidates

### T1: metric gate, offline

**Computes** the features above from the full base and head text; asks nothing. **Decides** in two layers. A lexical detector, or copy above 0.8, fails the file directly if its dev precision is at least 0.9; weaker ones feed a logistic regression with non-negative smell coefficients (adding a smell can't lower the score), language intercepts and size controls, fitted with repository-grouped 5-fold CV and Platt-calibrated out of fold. The threshold is the lowest with out-of-fold precision ≥ 0.75; a conformal check on clean negatives keeps per-PR false fails ≤ 10%.

**Reports** each detector or top feature with the exact added lines (never context), the related span for a copy, a message with numbers ("handleRequest grew from 182 to 261 lines; lines 340–418 are one block at depth 3"), a fix ("move lines 340–418 into a function of their own") and dev precision as confidence. Escape routes trip other features: single-use helpers trip single_use; a renamed copy keeps its fingerprints.

**Costs** $0, under a second, flip rate 0 by construction; no model reads the text, so prompt injection has nothing to act on.

**Hypothesis:** excess structure measured from the full files predicts pushback as well as Jev reading the diff. **Falsified if**, on the dev items that aren't injected, T1's PR-AUC is below the baseline's with the bootstrap CI of the difference wholly below 0, or T1 beats the size-only model by less than 0.02 (its "excess" is then size).

### T2: Jev plus metrics

**Asks** one Jev request per file and enters all ten answers as logits beside T1's features in the same constrained model; T1's lexical layer is a floor no text in the diff can lower. **Localizes** from T1's spans, with no hunk pass; Jev-only diagnoses attach to the unit with the largest regret or dcog. **Re-asks** only within 3·β_jev·σ_jev of the threshold (Jev's coefficient times its logit spread on identical calls). **Costs** about $0.0003 per PR.

**Hypothesis:** Jev (idiom, intent) and the metrics (the whole file, exactly) make complementary errors. **Falsified if** T2 does not beat the baseline on items that aren't injected (CI includes 0), or Jev's coefficient falls to about 0 beside the metrics (then drop Jev).

**For the harness:** add the size-only model as a reference, report paired AUC on the matched pairs, and score localization by IoU as well as by the slack-2 overlap.

## (c) Predictions

1. On mechanical injections T1 fails at least 95% of injected files and at most 3% of their clean twins; the baseline stays near 64%.
2. A size-only logistic model reaches ROC-AUC of at least 0.65 on the dev items that aren't injected, and among negatives push_back has a Spearman correlation of at least 0.3 with log added code lines.
3. On those items T1 does not beat the baseline's PR-AUC; T2 beats it by at least 0.03, but on reviewer_comment items alone its gain is under 0.03 and the CI includes 0.
4. On pairs of a reviewer positive and its post_fix, no deterministic feature reaches a paired AUC of 0.60, and neither does Jev's push_back.
5. The ablation keeps lexical, copy, regret and dnest; removing Halstead, change entropy or ν changes grouped-CV PR-AUC by less than 0.005, and ν correlates with copy at 0.7 or more on blocks of at least 500 bytes.
6. T1's and T2's median finding width is at most a fifth of the baseline's. Under the slack-2 rule the baseline's hit rate is within 10 points of theirs; under IoU ≥ 0.2 they beat it by at least 20 points. Tree-sitter moves PR-AUC by less than 0.01.
