# Phase 0 benchmark: summary for the perspective agents

*Preliminary. Collection is still running; final counts will move by roughly ±25%. The rules below are final.*

## What an item is

One changed source file in one change (a Gerrit patch set, a GitLab merge-request version, a GitHub pull-request commit, or a commit), stored with the base and head text of every judged file in that change. A candidate sees the whole change as a git repository and returns a score and verdict per file. Only files qualm 0.1.1 would judge are included (its skip rules: no tests, vendored, generated, prose or data). Languages: Go, Python, Ruby, TypeScript, Java (plus a little JavaScript).

## Where the labels come from

**Real reviewer signal (primary).**
- Gerrit (go-review: Go and vscode-go TypeScript; review.opendev.org: OpenStack Python and puppet Ruby; gerrit-review: Gerrit's Java and TypeScript; chromium-review: devtools-frontend TypeScript, luci-go Go, Python tools), GitLab (gitlab-org Ruby and Go projects, through GraphQL), GitHub (review comments from 300 hours of GH Archive, code fetched over git).
- **Positive** (`reviewer_comment`): a person other than the author, not a bot and not an AI review tool, left an inline comment on a line the change adds (±3 lines) that matches a simplification pattern ("simplify", "unnecessary", "redundant", "can't happen", "duplicated code", "too complex", "is this needed?", "factor out", ...), and the author acted on it: an acknowledging reply ("Done") or a later version changed the code near that line. Tier A: an outright request (strong pattern) or an acknowledged one. Tier B: a weaker pattern where the code merely changed later; in held-out these wait for human adjudication and are not scored until then. The location is the commented line(s).
- **Negatives**: `approved_uncommented` (a file of a reviewed change that no reviewer commented on and that merged unchanged), `post_fix` (the approved, merged version of a file that had drawn a simplification request: the same change after the fix), `approved_clean` (every file of a merged, human-approved change with no inline reviewer comments at all).

**History signal.** `followup_cleanup` positive: within 30 days, a commit whose subject says simplify / refactor / clean up / dedupe / remove dead code rewrote at least 30% of the lines this change added. `survived_90d` negative: 90 days later at least 90% of the change's added lines are untouched.

**Controlled injection (exact labels).** Into the added code of clean changes from the same split: `debug_print`, `placeholder` (a TODO stub), `near_copy` (an added function duplicated with one literal changed), `impossible_guard` (a nil/null check on a value just constructed). The clean original stays in the set as a negative, so each injection is a matched pair. LLM-written injections (unit growth, single-use indirection, plus the four above) into repositories whose tests run are being prepared for the agent fix loop.

**Hard negatives.** `rename` (an identifier renamed throughout a file), `comments_only`, `formatting` (whitespace only), `move` (a file moved unchanged).

## Splits

By repository, never by commit: no repository appears in both. Held-out is sealed (hashed and encrypted) before any candidate is built, capped by a pre-registered stratified random subsample at about 600 scored items with about 35% positives. Dev keeps everything (about 1,800 items, 27% positive). Agent-written changes (head branch `copilot/`, `codex/`, `claude/`, `cursor/`, …) are tagged.

## Known label noise

- Reviewers miss things, so some negatives deserve pushback. `approved_uncommented` and `survived_90d` are the weakest.
- Pattern matching on comments has false positives ("extract the archive", "duplicate key"). The acted-on requirement and the added-line requirement cut most of them. On dev an LLM will classify each signal comment; on held-out a person adjudicates the borderline ones.
- Nit-level requests ("nit: could be simpler") count as positive; they are flagged so results can be reported with and without them.
- Reviewer positives are usually about one spot in an otherwise fine change; many are small.

## The baseline on a trial dev sample (198 items, 51 positives)

qualm 0.1.1 unmodified, gate `push_back >= 0.6`:

| | value |
|---|---|
| PR-AUC | 0.59 (prevalence 0.26) |
| ROC-AUC | 0.75 |
| Precision / recall at 0.6 | 0.71 / 0.33 |
| Brier / ECE | 0.168 / 0.128 |
| Fail rate on reviewer positives | 16% |
| Fail rate on injected smells | 64% |
| Fail rate on clean negatives | 4% (approved), 14% (survived 90 days), 0% (hard negatives) |
| Cost | about $0.0003 per change, under a second |

These are preliminary and from a small sample; treat them as orders of magnitude.

## Resources and constraints of this study

- Jev via OpenRouter (`typesafe/jev-1.13`), about $0.04 per million input tokens; budget $20 for the whole study.
- No Anthropic API key. Wherever a design needs an LLM, this study runs it as a fresh-context Claude subagent per item, and estimates what a shipped API call would cost from token counts. A shipped LLM-based tool would need `ANTHROPIC_API_KEY` at runtime.
- The fix loop: a coding agent receives only a candidate's report, gets up to two iterations, and must make the gate pass with tests still passing, behaviour preserved, the code judged simpler by a blind judge and a deterministic metric, and no gaming.
- Definition of done on held-out: PR-AUC better than qualm 0.1.1 with the 95% CI of the difference above zero and precision not lower; fix-loop success better with CI above zero; verdict flip rate ≤ 1%; ≤ $0.05 and p95 ≤ 60 s per typical PR; every attack in the red-team suite handled.

See `dev_examples.md` beside this file for sixteen real dev items.
