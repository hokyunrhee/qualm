# The shared interface

Every candidate, the baseline included, is run by one harness through this interface. It starts from qualm 0.1.1's `--format json` and adds what a coding agent needs to fix a change on the first try: where, why, what to do, and how sure.

## Invocation

Each candidate ships an executable `next/candidates/<name>/run`:

```
run --repo PATH --base REF [--format json|text] [--no-cache] [--budget DOLLARS] [--llm-dir DIR] [--include-tests]
```

- `--repo` is a git repository. The change is the working tree against the merge base of `REF` and `HEAD`, exactly as qualm reads it: committed, staged, unstaged and untracked files all count.
- `--format json` (default) prints the report below on stdout. `--format text` prints the report a coding agent reads. Both describe the same verdict.
- `--no-cache` neither reads nor writes any cache. The harness uses it for every determinism run.
- `--budget` caps paid calls for the run (Jev). A candidate that spends nothing ignores it.
- `--llm-dir` is for candidates that need an LLM at verdict or report time. There is no API key in this study, so an LLM call is made by the harness through a fresh-context subagent. The candidate writes each request it needs to `DIR/requests/<key>.json` (`{"key", "model", "system", "prompt", "schema"}`) and exits 2 with `needs llm` on stderr. The harness answers each request in `DIR/responses/<key>.json` and runs the candidate again. A shipped version replaces this with one API call per request; the key is the SHA-256 of the request, so identical requests are answered once.

Exit codes: **0** the gate passed, **1** the gate failed, **2** the candidate could not judge (no key, no network, a config mistake, a spent budget, an unanswered LLM request). A candidate never passes a change it could not judge.

## The JSON report

```json
{
  "tool": "b-offline",
  "version": "0.1.0",
  "passed": false,
  "base": "4f2a…",
  "files": [
    {
      "path": "lib/coverage_map.rb",
      "status": "judged",
      "language": "Ruby",
      "score": 0.83,
      "threshold": 0.5,
      "failed": true,
      "abstained": false,
      "findings": [
        {
          "id": "near_copy",
          "lines": [327, 361],
          "message": "Lines 327-361 repeat lines 212-246 of this file with two names changed.",
          "fix": "Extract the shared steps into one function and call it from both places.",
          "confidence": 0.91,
          "related": [{"path": "lib/coverage_map.rb", "lines": [212, 246]}]
        }
      ],
      "signals": {"jev.push_back": 0.65, "dup.max_similarity": 0.92}
    },
    {"path": "lib/version.rb", "status": "skipped", "reason": "prose or data"},
    {"path": "lib/matcher.rb", "status": "kept", "reason": "The algorithm is this complicated"}
  ],
  "warnings": [],
  "usage": {
    "jev_requests": 3, "jev_input_tokens": 9120, "jev_cost_usd": 0.00038,
    "llm_requests": 0, "llm_input_tokens": 0, "llm_output_tokens": 0, "llm_cost_usd": 0, "llm_cost_estimated": true,
    "seconds": 1.9
  }
}
```

Per file:

- `status` is `judged`, `kept`, `skipped` or `error`. A file in `error` makes the run exit 2.
- `score` is the candidate's probability that an experienced reviewer would ask for this file's change to be simplified before it merges. The harness computes PR-AUC, Brier score and calibration from it, so it should be a probability, not just a ranking.
- `threshold` is the operating point the gate used, and `failed` is `score >= threshold` unless the candidate documents another rule (for example an abstain band).
- `abstained` is true when the candidate was unsure and escalated or settled the file by a second method. It is reported, not scored.
- `findings` is what the fix loop runs on. Each one names the new-file `lines` it is about (inclusive), a one-sentence `message` saying what is wrong, a `fix` saying what to do, and a `confidence` from 0 to 1. `related` points at other code the finding depends on, such as the original of a copy. A failing file should have at least one finding. A passing file may carry findings below the gate as advice.
- `signals` is free-form: raw answers and features, for analysis only.

Per run, `usage` counts what was spent. Jev costs are what OpenRouter reported. LLM costs are estimates from token counts at the list price of the model a shipped version would call, and say so.

## The text report

What a coding agent reads. It must stand on its own: the agent in the fix loop sees this and the repository, nothing else. The first line says pass or fail. Each failing file lists its findings with line ranges, the message and the fix. It ends with what to do next. qualm 0.1.1's own text report is the baseline's.

## Benchmark cases

A case is one change, one pull request or patch set, stored as JSON (`next/data/`):

```json
{"case_id": "...", "split": "dev", "source": "gerrit:go-review.googlesource.com", "repo": "go/tools", "url": "...",
 "files": [{"path": "...", "language": "Go", "base": "<text or null>", "head": "<text>", "old_path": "<only for a move>"}],
 "items": [{"path": "...", "label": 1, "label_source": "reviewer_comment", "location": [40, 52], "smell": null}]}
```

`next/eval/materialize.py` turns a case into a git repository with a `base` branch holding the base versions and `HEAD` holding the head versions, with fixed authors and dates so every machine builds identical commits. The harness runs each candidate with `--repo <that> --base base`. Only the files listed in `items` are scored; other changed files are part of the change the candidate sees but carry no label.
