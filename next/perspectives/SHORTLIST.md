# Phase 1 synthesis: the shortlist

Seven perspectives (files beside this one, structured outputs in `structured.json`) agree on more than they disagree. This note records where they agree, where they split, and the candidates that span the design space.

## Where the perspectives agree

1. **The trust boundary is the cheapest, surest win.** qualm reads `qualm.json`, keeps and `.gitattributes` from the working tree under judgement, and skips by name alone, so a pull request can steer or hide from its own judge. The fixed attack suite confirms it: qualm 0.1.1 handles 30 of 50 attacks, and nearly every miss is a trust-boundary or skip-rule attack (`results/attacks/a-baseline.json`). Red team, coding agent, skeptic and platform all propose reading policy from the base commit, content-checked skips, a tamper finding for gate-control files, and no trusted cache.
2. **Reviewers push back on spots, not files.** Six of eight sampled reviewer locations are one line. The baseline fails 16% of reviewer positives against 64% of injected smells. Staff engineer, coding agent and theorist all move judgement to the changed unit.
3. **The report must say where, why, what to do, and when it's done.** qualm's ranges are whole hunks (three diagnoses at lines 327-577 in its own README) and its fix is the question restated.
4. **Mechanical smells should be detected mechanically.** Debug output, stub bodies, near-copies (winnowing) and guards on just-built values are lexical facts; whitespace, comment-only, rename and move changes are decidable.
5. **Calibration and the operating point need fixing.** ECE 0.128; 0.6 came from one unlabelled repository; per-file false fails compound across a PR.

## Where they split

- **Is there judgement headroom on natural labels?** The skeptic and the measurement scientist expect none that held-out can show (noise ceiling, ~0.07-0.13 minimum detectable effect); staff engineer and coding agent expect spot-level judging to lift reviewer recall.
- **Jev's role.** Keep it as a feature (measurement scientist, theorist, platform), move it to the unit level (staff engineer, coding agent), or check it can't be talked down by masking comments and strings (red team).
- **The LLM's role.** Never in the verdict (platform, coding agent), only in an abstain band (staff engineer, measurement scientist), or as the judge itself (not argued by anyone, but required by the design space).

## The candidates

All new candidates share one core (`next/candidates/_core/`): pinned git diff reading, the trust perimeter (policy and attributes from the base, tamper findings, content-checked skips, UTF-16 decoding, symlink targets, untracked files, no merge base exits 2), unit detection by indentation and braces, and the deterministic detectors with exact-line findings. The perimeter is shared so that candidates differ in how they judge; an ablation turns it off.

| | Candidate | Verdict from | Network at verdict time | Tests the hypothesis that |
|---|---|---|---|---|
| A | `a-baseline` | qualm 0.1.1 unmodified | Jev | (baseline) |
| B | `b-offline` | detectors + features (unit growth, nesting, copies, size, exact-change classes) in a calibrated, repository-grouped logistic regression; strong detectors fail a file directly | none | excess structure measured from the full files predicts pushback as well as Jev reading the diff (theorist T1, coding agent C1 offline half, platform O) |
| C | `c-llm` | one Claude Haiku 5.5 reviewer per file: numbered diff and context in, at most three located comments out, each verified against the added lines; three samples, majority vote on "blocking" | Claude | an LLM writes the comment a reviewer would, and verification plus voting makes it trustworthy and stable enough to gate |
| D | `d-cascade` | B's features plus Jev (qualm's questions on the raw and on the comment/string-masked diff) in one calibrated model; a Learn-then-Test abstain band sends only uncertain files to C's reviewer; templated findings, LLM notes for failing files optional | Jev, Claude for the band | qualm's signal is real but miscalibrated and partly discarded, and residual error sits in an uncertain band an LLM can resolve (measurement scientist M1/M2, theorist T2, red team R2, staff engineer R2) |
| E | `e-spot` | Jev asked per changed unit (unit head and base text plus a file outline) about the five verbs reviewers use (remove, reuse, merge, shorten, split), with the detectors, in a calibrated model | Jev | the baseline misses reviewer positives because it judges whole files with too little context, not because of Jev (staff engineer R1, coding agent C1) |

Yardsticks, not candidates: `n0-size` (logistic regression on log added and removed lines plus language, the measurement scientist's N0 and the theorist's size-only bar) and `a-recal` (qualm's `push_back` with Platt recalibration fitted on dev). A candidate that does not beat N0 on natural labels has learned size.

Variants measured as analyses on the winner, not as separate candidates: the skeptic's three-way router (fail / route to a person / pass) and the platform's LLM-written report (`R-LLM`), whose value the fix loop decides.

## Pre-registered analysis rules (from the measurement scientist, adopted)

- Primary scientific comparison: repository-clustered paired ΔPR-AUC on the natural stratum (reviewer and follow-up positives against approved, post-fix and survived negatives). The pooled number is the Definition of Done's and is reported beside every stratum.
- Matched pairs: P(score(injected) > score(original)) and P(score(positive) > score(post_fix)).
- Thresholds are chosen on repository-grouped out-of-fold dev scores; held-out is opened once.
- A file a candidate could not judge counts as a fail at score 1.
- Results are also reported with nit-only positives excluded, and within added-line terciles.
- Mechanically injected smells measure detector-generator agreement; LLM-written injections are reported apart.
