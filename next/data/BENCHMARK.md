# The benchmark (Phase 0)

One question per item: would an experienced reviewer ask for this file's change to be simplified before it merges? An item is one changed file of one change (a pull request, a Gerrit patch set, a GitLab merge request, or a commit). Cases and items are in `dev.jsonl.gz` and the sealed `heldout.v1.tar.enc`; counts are in `manifest.json`.

## Splits

Split by repository: no repository has items in both splits (`manifest.json`: `repo_overlap: []`). The held-out split was sealed (tar SHA-256 `6cd58e78…4993`, recorded in `heldout.v1.sha256.json` at 2026-10-14T00:47:52Z) before any candidate was built, and stays encrypted until the final evaluation.

| | dev | held-out (scored) |
|---|---|---|
| cases | 2845 | 509 |
| scored items | 5139 | 634 |
| positives | 1133 (22%) | 244 (38%) |
| repositories | 301 | 125 |
| written by a coding agent | 354 | 148 |

Held-out was capped at about 600 scored items by a pre-registered stratified subsample (seed 13) and balanced to at least 30% positives. Unscored held-out items: 327 dropped by balancing, 231 by the size cap, and 12 weak reviewer-comment positives (tier B) that wait for human adjudication before they count.

## Where labels come from

Held-out labels come only from real reviewer signal, controlled injection, or human adjudication. LLM labels are used on dev only, never on held-out.

| source | label | dev | held-out | what it is |
|---|---|---|---|---|
| `reviewer_comment` | 1 | 923 | 165 | A human reviewer asked for a simplification on this file (strong wording, or weak wording that the author then acted on), and the comment is not AI-styled or praise. Tier A: strong or acknowledged; tier B: weak and only followed by a change. |
| `followup_cleanup` | 1 | 26 | 3 | History mining: the same author's next commit within days removed or reworked most of these added lines (at least 30%), with a cleanup subject. |
| `injection_mechanical` | 1 | 144 | 42 | A known smell inserted into a real approved file by code (debug print, placeholder, near-copy, impossible guard). |
| `injection_llm` | 1 | 40 | 34 | A smell inserted by a fresh LLM agent into a real tested commit, keeping the tests green (also the fix-loop items). |
| `approved_uncommented` | 0 | 1234 | 58 | A file in a merged change that reviewers commented on elsewhere but not here. |
| `approved_clean` | 0 | 1616 | 54 | A file in a change merged without any review comment asking for changes. |
| `approved_agent` | 0 | 280 | 141 | A file written by a coding agent and merged without simplification requests. |
| `post_fix` | 0 | 644 | 37 | The version of a reviewer-flagged file after the author addressed the comment. |
| `survived_90d` | 0 | 136 | 4 | Added lines that survived 90 days of history untouched. |
| `hard_negative` | 0 | 96 | 96 | Changes that look large but are simple by construction: a rename, comment-only edits, reformatting, a move. |

The **natural stratum** (reviewer comments and follow-up cleanups against approved, post-fix and survived files) is where the primary scientific comparison is made; injected smells measure agreement with known defects and are reported apart.

## Agreement between label sources

- **Regex vs fresh LLM, on dev reviewer comments** (`eval/results/labels/label_agreement_regex_vs_llm.json`): Cohen's kappa 0.449 on the sample (agreement 0.715) and 0.305 weighted to the population (agreement 0.86). The LLM (one fresh agent per comment, `eval/results/labels/dev_comment_classification.json`) read 75% of strong-wording and 57% of weak-wording comments as simplification requests (tier A 71%, tier B 54%). So reviewer-comment labels are noisy; the ceiling on any classifier is well below 1.
- **Human vs reviewer signal and human vs LLM**: from the human adjudication of 60 items (30 held-out comments, 15 blind held-out items, 15 dev items). Recorded in `adjudication.v1.json` once the answers are in; until then the 12 tier-B held-out items stay unscored.

## Fix-loop items

Real repositories at a real commit, with a smell injected by a fresh agent into a real file while the test suite stays green: 40 on dev (Go 12, Python 12, TypeScript 8, Ruby 8) in `fixloop_items.json`, and 34 held-out (Go 12, Python 12, TypeScript 8, Ruby 2) inside the sealed archive. Each records its test command; the environments are described in `../collect/fixrepos.json`.

## Known quirks

- 11 dev case ids appear twice: history-mined commits recorded once per flagged file. The two records hold the same files and different scored items, so per-item metrics count each item once; `eval/metrics.py` reads one report per case id.
- Everything in this data was collected from public repositories and is untrusted: it is stored and measured, never executed or followed (fix-loop repositories run only their own test suites, inside throwaway workspaces).
