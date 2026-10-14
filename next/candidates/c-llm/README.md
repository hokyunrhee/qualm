# c-llm

Candidate C of the shortlist (`next/perspectives/SHORTLIST.md`): one Claude Haiku 5.5 reviewer per judged file. It tests whether an LLM can write the comment a reviewer would write, and whether checking that comment against the change makes it safe enough to gate on.

## What it does

The shared core (`next/candidates/_core/`) reads the change inside the trust perimeter and decides which files are judged. For each judged file, c-llm does the following:

1. **Settles changes that write no new code, without a call** (`mechanical.py`). This covers a change that only deletes lines, only re-indents or moves lines the base already had, only renames identifiers (one name for one name throughout the file, the old name gone and the new name not in the base), or only edits imports. Such a file gets score 0.02 and `signals.settled_by_rule` says why. Moved lines are matched as a multiset, so deleting a function and pasting it back twice counts as new code. Any added line the rules cannot account for sends the file to the reviewer.
2. **Builds one prompt** (`prompt.py`, `view.py`). The prompt holds:
   - the path (JSON-quoted) and the language;
   - a one-line size of the change;
   - an outline of the head file: functions and methods with their line ranges, with the ones the change wrote or edited marked;
   - a numbered view of the change: each hunk with 15 lines of context, every line prefixed with its head line number. Added lines are marked `+`; removed base lines are shown as `-` with no number.

   The system prompt casts the model as an experienced reviewer of the repository. It is to judge only whether it would ask for simplification (not bugs, style, naming, tests or performance), and it is told that the code, its comments, its strings and the path are data, never instructions.
3. **Asks for a JSON answer**, in this shape:

   ```
   {"blocking": bool, "probability": 0..1,
    "comments": [{"lines": [lo, hi], "kind": "remove|reuse|merge|shorten|split|debug|stub|guard|other",
                  "message": str, "fix": str}]}   (at most 3)
   ```
4. **Verifies the answer** (`verify.py`) before anything from it reaches the report:
   - **Location.** A comment whose line range does not touch an added line (one line of slack either side) is dropped. Ranges are clamped to the file, and at most 3 comments are kept.
   - **Echoed comment and string text.** A message or fix that repeats text found in the file only inside comments or string literals is replaced by a template for the comment's kind. The template is built from the comment's verified lines and the name of the enclosing function. In practice this means:
     - Words that also occur in code (identifiers, split at case changes) pass.
     - Common English and review vocabulary passes, as do plausible line numbers.
     - Any other word from a comment or string is rejected, as is any run of four words copied from one, or any non-ASCII run copied from one.

     This is what stops the red-team canary (`CANARY-QX7` planted in a comment), and an instruction planted for the downstream coding agent, from passing through.
   - **Length.** Text is cut to one line of at most 300 characters for the message and 400 for the fix, so it cannot forge lines of the text report.
   - **Fallback finding.** A failing file whose comments were all dropped gets one generic finding at its largest run of added code.
5. **Scores.** The score is the answer's probability; with more than one sample it is the mean. The file fails when `score >= threshold`. The answer's raw numeric fields go into `signals` (`llm.probability`, `llm.blocking`, the line range, kind and fate of every raw comment, the request keys, and the estimated prompt tokens). The raw comment text is not put in `signals`, because the report must not carry it.

`usage` reports `llm_requests`, `llm_input_tokens`, `llm_output_tokens` and `llm_cost_usd` at the Haiku 5.5 list price ($0.10 / $0.50 per million input / output tokens), with `llm_cost_estimated: true`. Tokens are estimated as UTF-8 bytes / 3.5, by `gatecore.llmdir`.

## How to run it

There is no API key in this study. LLM calls go through files (`next/interface.md`, `gatecore/llmdir.py`):

```
next/candidates/c-llm/run --repo PATH --base REF --llm-dir DIR
#  -> exit 2, stderr "needs llm: N requests for M files written to DIR/requests ..."
#  answer each DIR/requests/<key>.json into DIR/responses/<key>.json
next/candidates/c-llm/run --repo PATH --base REF --llm-dir DIR
#  -> exit 0 (pass) or 1 (fail), report on stdout (--format json|text)
```

- **Requests.** Each request is `{"key", "model", "system", "prompt", "schema"}`. The model is `claude-haiku-5-5`.
- **Responses.** A response is the answer object itself, matching the schema. The schema is the same for every c-llm request (`prompt.SCHEMA`), so the answering workflow (`next/eval/llm_answers.py`, `next/workflows/answer_llm.js`) can take it from any request file.
- **One pass.** Every request a run needs is written before it exits, so one round of answers is enough.
- **Bad answers.** An answer that is not a JSON object, or that has no probability, makes that file an `error` and the run exits 2. It is reported once rather than asked again.
- **No `--llm-dir`.** With no `--llm-dir`, the run exits 2 with a message saying how to give it one. The exception is a change with nothing to ask about (no judged files, or only files the rules settle), which is judged without an LLM.
- **Keys.** The key is the first 32 hex digits of the SHA-256 of `{model, system, prompt, schema}`. The prompt holds nothing that varies between machines or runs (no absolute paths, commit ids or times), so the same change always asks the same requests and an answer is reused.

The settings are in `settings.json`:

- `threshold` is 0.35: the highest recall with precision at least 0.65 on the dev150 subset, its items weighted back to the full dev split's label-source mix (weighted precision 0.75, recall 0.50). It rests on about 150 items, so it is noisy. `--threshold` overrides it, and so does `next.threshold` in the base commit's `qualm.json`.
- `samples` defaults to 1. Sample *i* > 0 adds one line, `Review pass i+1.`, to the prompt so that its key differs. Sample 0 keeps the plain prompt, so raising `samples` reuses the answers already given.
- `mechanical_score` defaults to 0.02. It is the score given to files the rules settle.

Tests: `python3 -m unittest discover -s next/candidates/c-llm/tests` from the repository root.

## When shipped: the key

A shipped version replaces the file protocol with one Anthropic Messages API call per request, authenticated with **`ANTHROPIC_API_KEY`**:

- `model: "claude-haiku-5-5"`, `system` as written, and one user message holding the prompt;
- `output_config: {"format": {"type": "json_schema", "schema": SCHEMA}, "effort": "low"}`;
- `max_tokens` around 2000.

Haiku 5.5 rejects non-default `temperature`, so repeated samples are the only lever on variance. The schema already keeps to what structured outputs accept: objects with `additionalProperties: false`, and no numeric bounds or array lengths. The ranges those would express (probability in 0..1, two line numbers, at most 3 comments) are enforced by `verify.py`. Answers should be cached by request key outside the checkout (`GATE_CACHE_DIR`, as the core's `perimeter.cache_dir` allows), so a re-run costs nothing.

## Cost per file (estimate)

These figures come from the prompts c-llm builds for the judged files of the `dev150` cases, computed offline:

- **Calls per case:** 333 calls over the 152 records of those 150 case ids (two ids occur twice in `dev.jsonl.gz`), a median of 2 and at most 5. Another 27 files were settled by rule. Only 3 views had to leave code out under the cap.
- **Input per call:** median 2.4k tokens, p90 4.4k, maximum 11.8k. The cap is 12k tokens, counting the system prompt and the schema.
- **Output per call:** about 150-300 tokens of JSON, plus whatever Haiku's adaptive thinking spends at low effort. The figures below assume about 450 output tokens in all.
- **Per file:** about **$0.0005** at the median ($0.00024 input + $0.00023 output), $0.0007 at p90, and $0.0015 at the cap.
- **Per pull request:** about $0.001 at the median.
- **With samples:** cost rises by the number of samples.

The mechanical rule settles 425 of the scored dev items without a call, and 423 of them are labelled negative.

## Changes from the design brief, and why

- **The schema has no `minimum`/`maximum`/`maxItems`.** The Messages API's structured outputs reject them. `verify.py` clamps the probability, validates the line ranges and keeps at most 3 comments.
- **Changes that write no new code are settled by rule, not asked.** A mass rename (the red-team `good_mass_mechanical` attack renames an import in 120 files) would otherwise cost 120 calls and give 120 chances of a spurious fail. On dev, the files the rule settles are almost all negatives (above).
- **Under the cap, the view ranks pieces, not whole hunks.** A hunk can be one 150 KB new file. Long hunks are cut at function boundaries, and pieces are ranked by added code: code inside functions first, then other code; blank, comment-only and moved lines count for little. A piece that is mostly rows of literals is shown only when nothing else is. The red-team padding attacks (`oversized_diff`, `payload_after_padding`, `split_straddle`, `dilute_below_split`) all keep every payload function in view. Lines longer than 300 characters are cut.
- **Echoed text is replaced with a template, not just dropped.** The comment's verified location and kind are still worth reporting. The template quotes nothing from the file except a function name, which is code.
- **`blocking` does not decide the verdict.** The score is the probability, which is the quantity the threshold is calibrated on. The majority vote on `blocking` is kept in `signals` for analysis.
- **Comments from different samples are merged.** A comment merges with another only when it is of the same kind at overlapping lines. A finding's confidence is the score times the share of samples that made it. Comments within one answer are never merged.
- **`--budget` is ignored**, as the interface allows for a candidate that pays nothing in this study. A shipped version should refuse (exit 2) when the estimated cost of a run's requests exceeds it.

## Known weaknesses

- **One file at a time.** The reviewer sees the file's outline and its change, not the rest of the repository. It cannot say "reuse the function in `other.go`" (red-team `cross_file_copy`), and a payload split across files (`split_files`, `split_fragments`) is judged fragment by fragment.
- **The verdict is the model's, unverified.** Verification protects the report, not the score. A comment that persuades the model to lower its probability (`persuasive_comment`, `inject_*`) works if the model is persuaded. The system prompt says the file is data, and that is the only defence.
- **Nondeterminism.** With one sample, a borderline file can flip between runs, and there is no temperature to turn down. More samples cost proportionally more.
- **The study's reviewer is not the shipped one.** Answers come from a fresh-context subagent in the answering workflow, not from the API with structured outputs. Token counts are byte-based estimates, and thinking tokens are not counted.
- **The echo filter is a heuristic.**
  - Its vocabulary list can let through a planted word that happens to be common English.
  - A legitimate comment that uses a domain word found only in the file's comments is rewritten into a template, which is less specific.
  - Identifiers are trusted by design, so a marker written as an identifier in code passes.
- **The rename rule trusts any consistent file-wide substitution**, including one that swaps a logger for a print function across a file. Such a file is settled without review.
- **The threshold (0.35) rests on about 150 dev items**, weighted to the dev mix; it is noisy. The configuration measured in this study is one sample per file (`samples: 1`), not the three-sample majority the shortlist described.
- **No cache in this study.** `--no-cache` and `GATE_CACHE_DIR` have nothing to act on. The llm-dir is the harness's answer store, not a cache.
