# d-cascade

Decides, for each file a pull request changes, whether an experienced reviewer would ask for that change to be simplified before it merges, and tells the coding agent where and what to fix.

It keeps qualm 0.1.1's signal and uses all of it. qualm asks Jev ten questions about each file's diff and gates on one of them, `push_back >= 0.6`. d-cascade asks the same ten questions, asks twelve more about a second view of the change with the comments removed, and lets a model trained on 5,139 labelled files weigh every answer.

## What it does

1. **Reads the change inside a trust perimeter** (`../_core`). The change is the working tree against the merge base, untracked files included. Configuration, keeps and git attributes come from the base commit, so a pull request cannot loosen its own gate. A new `vendor/` directory or a file that only claims to be generated is judged like any other code. Edits to the gate's configuration or to a CI workflow that runs it are named in the report.
2. **Asks Jev about two views of each judged file.**
   - **Raw:** qualm's request exactly: the same diff (`-U8`, Myers), the same ten questions, the same 60 KB pieces.
   - **Masked:** every comment removed and every prose string replaced by `"<text>"`. Jev is asked eight of qualm's questions plus four that name what reviewers ask for: *remove*, *reuse*, *shorten* and *indirection*. Text in the change cannot address the judge through this view.
3. **Combines the answers.** A logistic model turns them into a calibrated probability, using the 22 answers and the core detectors' counts. Dev folds were grouped by repository. A file whose probability lands within 0.05 of the threshold is asked twice more, and the median decides.
4. **Fails a file** when its probability is at least **0.505**. That is the highest out-of-fold recall on dev with precision at least 0.65, where qualm runs at 0.61.
5. **Localizes the findings.** For a failing file, it asks about each of its larger hunks on its own and writes the hunks reviewers would push back on as findings, with the verb that fits. The detectors' exact-line findings come first: debug output, stubs, copies, guards with "can't happen" comments, commented-out code and unused new functions.
6. **Optional cascade.** With `--llm-dir` (and, when shipped, an Anthropic key), files whose probability falls in the abstain band **[0.25, 0.69)** go to an LLM reviewer (`review.py`, Claude Haiku 5.5). The reviewer's verdict decides them, and its located comments are verified before they reach the report. The band comes from Learn-then-Test on out-of-fold dev probabilities. Outside it, fails keep precision ≥ 0.75 and passes keep ≤ 10% positives, jointly at 95% confidence. On dev about 30% of files fall inside it.

## Running it

```
OPENROUTER_API_KEY=... next/candidates/d-cascade/run --repo . --base origin/main [--format text] [--budget 0.05] [--no-cache] [--llm-dir DIR]
```

Exit 0 passes, 1 fails, 2 could not judge (no key, Jev failing after retries, a spent budget, an unanswered LLM request). It never passes a change it could not judge.

The cache lives in `$GATE_CACHE_DIR` or `~/.cache/gatecore/d-cascade`, never inside the checkout. Entries are signed with a key derived from `OPENROUTER_API_KEY`, so a rewritten entry is ignored.

## Keys

- `OPENROUTER_API_KEY` for Jev: required.
- For the cascade, a shipped version calls the Anthropic Messages API (`claude-haiku-5-5`) with `ANTHROPIC_API_KEY` for each file in the band. In this study those calls go through `--llm-dir` and are answered by fresh-context subagents. Without either, the model's threshold decides every file.

## Cost

About 2 Jev requests per file: raw and masked, plus a few hunk requests for a failing file and two extra readings on a close call. On dev this measured about $0.0006 per change (`next/eval/results/`). The cascade's Haiku calls are estimated at about $0.0005 per band file from token counts (an estimate, list price $0.10 / $0.50 per million tokens).

## Training

`collect_dev.py` asks Jev the masked questions for every scored dev file (cached). `train.py` reads:

- those answers;
- the raw-view answers from qualm 0.1.1's own dev run, since its request is the raw view;
- the detector features from b-offline's feature cache.

It compares feature sets on out-of-fold PR-AUC, refits the best on all of dev with a Platt step, and picks the threshold and band. Nothing from the held-out split is used.

## Known weaknesses

- It inherits Jev's view of one file at a time. A copy of code in another file is caught only by the copy detector, which reports but does not fail the file on its own: on dev, no detector alone had the precision to fail a file.
- Jev's answers move a little between identical requests. The median of three steadies close calls but does not remove the noise.
- On natural reviewer labels the gain over qualm is real but modest. Reviewer comments are themselves noisy: a fresh LLM and the label regex agree at kappa 0.45.
