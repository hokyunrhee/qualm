# d-cascade

Decides, for each file a pull request changes, whether an experienced reviewer would ask for that change to be simplified before it merges. When it fails a file, it tells the coding agent where and what to fix.

It keeps qualm 0.1.1's signal and uses all of it:
- qualm asks Jev ten questions about each file's diff and gates on one of them, `push_back >= 0.6`;
- d-cascade asks the same ten questions;
- it asks twelve more about a second view of the change, with the comments removed;
- a model trained on 5,139 labelled files weighs all the answers.

This is version 3. The name is historical: the measured gate is the model alone (see "The LLM band").

## What it does

1. **Reads the change inside a trust perimeter** (`../_core`).
   - The change is the working tree against the merge base, untracked files included.
   - Configuration, keeps and git attributes come from the base commit, so a pull request cannot loosen its own gate.
   - A file is skipped as generated only if its base version was generated, or if its own schema (`foo.proto` for `foo.pb.go`) changes in the pull request; the second case is named in the report.
   - Only JavaScript and TypeScript can be skipped as minified, and only when most lines are very long. A file the base did not have as minified is never skipped as minified.
   - Code under an existing `vendor/` directory is judged unless a dependency manifest changes too.
   - A judged file over 1 MB is not judged in part: the run exits 2.
   - Edits to the gate's configuration, to `.gitattributes` or to a CI workflow that runs the gate are named in the report.
2. **Asks Jev about two views of each judged file.**
   - **Raw:** exactly qualm's request: the same diff (`-U8`, Myers), the same ten questions, the same 60 KB pieces.
   - **Masked:** every comment removed and every prose string replaced by `"<text>"`. Jev is asked eight of qualm's questions plus four that name what reviewers ask for: *remove*, *reuse*, *shorten* and *indirection*. Text in the change cannot address the judge through this view.
3. **Combines the answers.**
   - A logistic model turns the 22 answers into a calibrated probability. Dev folds were grouped by repository, and a Platt step calibrates the result.
   - A close call is a file within 0.05 of the threshold. It is read again: 3 readings, then 7 if the median is within 0.02, then at most 11 if it is within 0.01. The median decides.
4. **Fails a file** when its probability is at least **0.506**. That is the highest out-of-fold recall on dev with precision at least 0.65; qualm runs at 0.61.
5. **Localizes the findings of a failing file, one function at a time.**
   - **Which functions are asked about:** every function the change wrote (small helpers included), then every existing function it changed, then runs of added code outside any function. Each is asked on its own, with the lines it removed shown.
   - **Which become findings:** each function a reviewer would push back on (`push_back >= 0.5`).
   - **The verb:** the question whose answer stands out most against its usual level on dev (`verbs.json`).
   - **Exact lines first:** the detectors' findings come first (debug output, stubs, copies, guards with "can't happen" comments, commented-out code, unused new functions). A function finding is dropped when the detectors already name that kind of problem inside the function.
   - **Advice:** detector findings outside a flagged function are listed as advice, which does not fail the gate.
   - **Every report ends with the same rule:** keep every behaviour, option, test and documentation example the change introduces; simplify how the code is written, not what it does.

## Running it

```
OPENROUTER_API_KEY=... next/candidates/d-cascade/run --repo . --base origin/main [--format text] [--budget 0.05] [--no-cache] [--text-out FILE]
```

Exit codes:
- 0: passes.
- 1: fails.
- 2: could not judge. Causes include no key, Jev failing after retries, a spent budget and a judged file over 1 MB.

It never passes a change it could not judge. A change with no judged file passes without a key.

The cache lives in `$GATE_CACHE_DIR` or `~/.cache/gatecore/d-cascade`, never inside the checkout. Entries are signed with a key derived from `OPENROUTER_API_KEY`, so a rewritten entry is ignored.

## Keys

`OPENROUTER_API_KEY` for Jev. Nothing else.

## Cost

On dev, a pull request cost about $0.0008 in Jev requests at the mean (`next/eval/results/`). That covers:
- two requests per file;
- per-function requests for a failing file;
- extra readings for a close call.

## Training

`collect_dev.py` asks Jev the masked questions for every scored dev file (cached).

`train.py` reads those answers and the raw-view answers from qualm 0.1.1's own dev run (its request is the raw view). It fits the model on the raw and masked answers and picks the threshold. Version 2 chose that feature set by out-of-fold PR-AUC on the natural stratum; version 3 refits it without choosing again (`--set raw+masked`).

Nothing from the held-out split is used.

## The LLM band

`--llm-dir` with `band.json` sends files whose probability falls inside an abstain band to an LLM reviewer (`review.py`, Claude Haiku 5.5). The band was fitted by Learn-then-Test on out-of-fold dev probabilities. On dev, no upper edge kept fail precision at 0.75 with 95% confidence, so the band became [0.26, 1.01): the LLM would decide every file above 0.26. Its answers flip between runs (6.4% of files on dev).

So the band is not part of the measured gate. The code path stays for study. An answer without a yes/no verdict, or with a probability outside [0, 1], exits 2.

## Known weaknesses

- **Copies of code in another file.** It reads Jev's view of one file at a time, so such a copy is found only by the copy detector. That finding is advice and does not fail the file: on dev no copy rule had the precision to fail a file alone. The red-team copy attacks (`cross_file_copy`, `fifth_copy`, `cross_dir_copy`, `py_cross_module_copy`) pass with an advice line.
- **Jev noise.** Its answers move a little between identical requests. More readings on close calls steady the verdict but do not remove the noise.
- **Natural reviewer labels.** The gain over qualm there is real but modest. Reviewer comments are themselves noisy: a fresh LLM and the label regex agree at kappa 0.45.
