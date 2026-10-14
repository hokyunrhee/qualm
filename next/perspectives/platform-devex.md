# Perspective: platform and developer experience

A team never sees per-file PR-AUC. It sees a required check, a secret, a bill, and a red X when something upstream breaks.

## (a) Critique of qualm 0.1.1

1. **Fork PRs go ungated. (Fact.)** `ci.yml` runs `--dry-run` when there is no secret, and `cli.go:195` exits 0 for a dry run, so qualm's own gate passes every fork PR without judging it. Any keyed design must block outside contributors (exit 2) or skip them.
2. **Policy comes from the PR being judged. (Fact.)** `config.Load` reads `qualm.json` from the working tree, and `git check-attr` reads `.gitattributes` from the same tree. Binary detection trusts git's "Binary files" line, and no `--text` is passed. So a PR can do any of these: raise the threshold to 1.0, redefine `push_back`, skip `**/*`, mark itself `linguist-generated` or `binary`, or keep its own change. The README's answer, CODEOWNERS, sits outside qualm. Seven red-team attacks target this.
3. **The cache is trusted where it buys least. (Fact; conjecture on typical setups.)** Cache entries in `~/.cache/qualm` are read back unsigned (`internal/jev/cache.go`). Any earlier CI step that runs PR code (tests, `npm ci`) can plant a passing `<sha>.json`. In CI it saves almost nothing: about $0.0003 a change.
4. **The README's CI recipe is the real cost. (Fact; size is conjecture.)** It uses `fetch-depth: 0` and `go install ...@latest`. On a monorepo, a full clone plus a compile takes minutes on every push. That is about a cent a runner-minute over 30,000 runs a month (10,000 PRs, three pushes each), hundreds of dollars, against about $9 of Jev. `@latest` also lets the gate change without a commit.
5. **An outage turns every check red. (Fact.)** The endpoint is `/api/alpha/decisions`. A hung endpoint holds a run for about 6 minutes before exit 2: 90 s × 4 attempts plus 7 s of backoff, against a 60 s p95 budget. That freezes merges org-wide, the likeliest week-one turn-off (conjecture).
6. **False positives compound per PR. (Fact as arithmetic.)** The baseline fails 4% of approved files and 14% of files that survived 90 days. A clean six-file PR therefore fails 22% of the time at the 4% rate (1−0.96⁶), or about 60% at the 14% rate.
7. **The findings are vague and cost extra. (Fact.)** The README's example points three diagnoses at the same lines 327-577. The message is the question's text, and the fix is one fixed sentence (`adapt.py`). Finding those lines costs one request per hunk.
8. **Strengths to keep. (Fact.)** One static binary with no dependencies, shipped as a gem, through `go install` or as release binaries. Exit codes 0/1/2 shared with exhale. The Contract. Python, cgo (tree-sitter breaks the cross-compiled release; conjecture), a model runtime or a second vendor must earn their cost.
9. **Diffs go to two third parties. (Fact; the enterprise effect is conjecture.)** Diffs go to OpenRouter and on to TypeSafe. Vendor review delays enterprises; air-gapped teams can't use it.

## (b) Candidate designs

### O+J: offline-first, two-tier gate

**What it computes.** All offline, deterministic, in Go, about 10 ms a file:
- **Exact pass classes:** whitespace-only, comments-only (a table of comment syntax per language), rename-only (the same tokens under a consistent renaming), and moves.
- **Detectors that point at exact lines:**
  - debug output on added lines (a table of calls per language);
  - placeholder stubs: `NotImplementedError`, `panic("TODO")`, a body that is only `pass`. A TODO comment alone doesn't count; dev example 12, a negative, has one;
  - near-copies: winnowing fingerprints of normalised token k-grams, comparing added units with the rest of the file and with the PR's other changed files, which 0.1.1 cannot see;
  - growth of an already long unit, with units found by indentation;
  - added nesting and branches.
- **Size and shape features.**

**What it asks.** Only when a key is present: one Jev request per file with `push_back`, `added_impossible_guards` and `added_unused_flexibility`. Those are the smells that syntax can't see. There is no hunk-by-hunk pass.

**How it decides.**
- Logistic regression with two weight sets, with Jev and without. Each is fit on repository-grouped dev folds and Platt-calibrated.
- The operating point keeps precision at or above 0.71.
- Without a key, or when Jev is down, the offline weights decide, and the report's first line says the run was offline. Never a silent pass, never an infrastructure red X.

**What the report says.** Exact lines, `related` ranges for copies (including copies in other files), the concrete thing found, and a specific fix.

**Cost.** Offline: $0. With Jev: about $0.0002 a PR, with a p95 under 3 s.

**Hypothesis.** Offline features carry the signal on injected smells and hard negatives, and Jev carries the reviewers' signal. Together they beat either alone, and the offline weights alone are good enough to gate fork and air-gapped PRs.

**What would falsify it.**
- The Jev weights fail to beat the baseline's PR-AUC with the CI of the difference above zero.
- Removing the offline features costs less than 0.01 PR-AUC. In that case, ship Jev plus the platform fixes only.
- The offline weights fall below 0.71 precision at a recall of 0.2 or more, or fail more than 1% of hard negatives.

### R-LLM: the LLM writes the report, never the verdict

An add-on to whichever verdict wins.
- **What it runs.** Each failing file (about 15% of files) gets one Haiku 5.5 call. The prompt holds the diff, the units the deterministic findings point at, and those findings. The reply is at most three `{lines, message, fix}`.
- **Validation.** A finding off the added lines is dropped; the templated one stays.
- **What it leaves alone.** The verdict is untouched, so the flip rate is unchanged. Without `ANTHROPIC_API_KEY`, the report is templated.
- **Cost.** About $0.0012 per failing file (`pricing.json`, roughly 10k tokens in and 400 out), or about $0.001 a PR.
- **Hypothesis.** The reader is itself an LLM, so precise templated findings fix as well as LLM prose does, and the shipped tool needs no second key.
- **What would falsify it.** LLM reports raise fix-loop success by 10 or more points on the same failing items, with the CI above zero. Then ship R-LLM behind an optional key.

### Running cost by class

Assumptions: 10,000 PRs a month, about 30,000 runs, six judged files each. Estimates.

| Class | Per run | Per month | Burden |
|---|---|---|---|
| Offline | $0 | $0 | none; forks and air-gapped work |
| Jev | ~$0.0003 | ~$9 | a secret, an outage policy, vendor review of OpenRouter and TypeSafe |
| Haiku 5.5 per file | ~$0.007 | ~$220 | a second secret and vendor, nondeterminism |
| Sonnet 5.5 per file | ~$0.14 | >$4,000 | breaks the $0.05 cap |

## Minimum shippable form

1. qualm 0.2, the same single binary and gem, with the model weights embedded. Nothing new to install.
2. **`--policy-from base`.** Config, skip rules and attributes come from the merge base, using `git --attr-source` (git 2.40+) and `core.attributesFile=/dev/null`. Policy edits inside the PR are reported, not applied.
3. **Skipped additions are reported.** A skipped new file with more than 50 added lines (skipped by path, encoding or symlink) is listed as a warning.
4. **Keeps in CI.** A keep counts only when a maintainer with write access applies a `qualm-keep` label. The Action checks who applied it. `qualm keep` still works locally.
5. **No key or no endpoint.** The run falls back to the offline weights. Base config sets `on_unavailable: offline|fail`. Requests time out after 15 s, with at most two attempts.
6. **Cache.** None in CI. The local cache stays.
7. **`qualm-action@v1`.**
   - Runs on `pull_request_target` with `persist-credentials: false`, and never runs PR code.
   - Uses a pinned release binary and checks its checksum.
   - Gets the merge base from the compare API, then makes a blobless, shallow fetch of the merge base and the head.
   - Posts annotations on the finding lines and the report as the job summary.
   - Takes under 20 s.
8. **The agent loop.** `qualm --offline` runs as a pre-push or Stop hook: instant, free and deterministic. CI with Jev is the authority.
9. **exhale.** One annotation format shared with exhale. A Ruby team that already runs exhale can drop qualm's near-copy finding.

**Week-one turn-off triggers, ranked:**
1. Red checks caused by outages or the budget.
2. Clean PRs blocked at 10% or more with no cheap override.
3. Verdicts that flip on re-run.
4. Hunk-wide findings.
5. Blocked forks.
6. Minutes of added CI time.

## (c) Predictions

1. **Cost.** Every Jev-based candidate costs under $0.002 per typical PR. A Sonnet-class LLM judging every file exceeds $0.05 on a six-file PR, while Haiku-class stays under $0.01.
2. **Clean PRs.** On dev cases where every scored file is negative and at least four files are judged, qualm 0.1.1 fails at least 15% of the cases. That is 3–5× its per-file false-positive rate.
3. **Red team.** The baseline fails open on at least 10 of the 21 should-fail attacks. At least 8 of those are trust-boundary attacks (config, attributes, keeps, cache, path skips, encoding). `--policy-from base` plus skip warnings closes them without changing a score.
4. **Offline only.** An offline-only ablation:
   - fails at least 90% of `debug_print`, `placeholder` and `near_copy` injections;
   - fails at most 1% of hard negatives;
   - fails at most 50% of `impossible_guard` injections;
   - scores no better PR-AUC than the baseline on reviewer-comment items.
5. **Flip rate.** Any candidate whose verdict depends on a Claude answer flips at least 2 of the 150 subset items across three cache-off runs, unless it votes over three or more samples. A verdict from offline features plus Jev with the close-call rule stays at or below 1%.
6. **Fix loop.** Templated exact-line reports come within 10 points of LLM-written ones (R-LLM's null holds).
