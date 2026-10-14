# qualm-next

qualm-next fails a pull request when an experienced reviewer would ask for the change to be simplified, and it tells the coding agent what to fix. It does qualm's job, and it was chosen over qualm 0.1.1 by a study on 125 repositories it never saw while it was built (`next/REPORT.md`).

It asks [Jev](https://openrouter.ai/blog/insights/what-is-jev/) what qualm asks, and more:
- It asks about two views of each changed file:
  - the diff as it stands;
  - the diff with its comments removed, so a comment cannot talk the verdict up or down.
- A small model trained on 5,139 labelled file changes turns the answers into one probability, and the gate is a threshold on that.
- A failing file is then asked about one function at a time, so the report names the function to rework and what to do to it.

No LLM runs inside it.

It is one Python 3.9+ script and its library, with no packages to install. The only key it needs is `OPENROUTER_API_KEY`.

## Install

Copy `next/winner/` anywhere and put `bin/` on your `PATH`, or call the script by its path:

```bash
python3 -I path/to/winner/bin/qualm-next
```

In GitHub Actions, use the action in this directory (see [In CI](#in-ci)).

## Use

```bash
export OPENROUTER_API_KEY=...
qualm-next
```

Run it in a git repository. It compares `HEAD` with the merge base of `HEAD` and the default branch, so it works before you open the pull request as well as in CI.

It exits:
- **0** when no file needs simplifying;
- **1** when the gate fails;
- **2** when it couldn't judge: no key, no network, a mistake in the config, a spent budget, a judged file over 1 MB, or `--dry-run`.

It never passes a change it couldn't judge. A change with nothing to judge passes and needs no key.

## What it asks

Each judged file gets two requests to Jev:
- **The diff** gets qualm's ten questions. The model uses nine of the answers; it found no use for `direction`.
- **The diff with comments removed** gets eight of qualm's questions and four of its own.

The masked view also turns prose strings into `<text>`. That is so an instruction or a reassurance planted in a comment or a log message reaches neither Jev nor the verdict.

The four added questions are a reviewer's verbs:

| Question | What it asks |
| --- | --- |
| `remove` | Could some added lines go without changing what the program does? |
| `reuse` | Does the added code re-implement something the language, the standard library or nearby code provides? |
| `shorten` | Is there a noticeably shorter, more direct way to write it? |
| `indirection` | Does it add a layer with only one user or one implementation? |

The exact wording is in [`lib/qualm_next/asks.py`](lib/qualm_next/asks.py). Every request tells Jev that the code's comments and strings are part of what it is judging, never instructions to it.

Alongside Jev, ten detectors read the changed lines themselves:
- debug output, placeholders and stubs;
- impossible guards;
- commented-out code and near-copies;
- verbose constructs;
- unused new functions;
- pass-through wrappers;
- long units;
- comments that narrate the change.

They point at exact lines. They don't decide the verdict.

## The gate

The model combines the answers into the probability that a reviewer would push back on this file. The run fails when any file reaches **0.506**.

That threshold was set on the training data only, as the highest recall that kept precision at 0.65 or better out of fold.

Jev's answers move a little between identical calls. When a file lands within 0.05 of the threshold, qualm-next reads it again:
- three readings;
- then seven, if the median is still within 0.02;
- then at most eleven, if it is still within 0.01.

The median decides. On 509 unseen pull requests, each run five times with every cache off, 0.55% of file verdicts changed between runs.

## Reading the report

A passing run prints one line, and any advice. A failing run names each failing file, its probability, and what to fix, as findings in this order:
1. The detectors' exact lines: debug output, stubs, copies, impossible guards, commented-out code, unused new functions.
2. Then each function a reviewer would push back on, with what to change and when it is done.

Here it is on the change that added qualm-next to this repository:

```text
FAIL: 1 of 15 changed code files should be simpler before merge.

next/winner/lib/gatecore/detectors.py  (score 0.59, gate 0.51)
  - lines 93-128 [review.added_placeholders, confidence 0.63]
    What: Lines 93-128 (in placeholder) add placeholder, debugging or unfinished code.
    Fix: Remove the debugging output and stubs, or finish them. Done when nothing in these lines is temporary.
  - lines 49-75 [review.shorten, confidence 0.50]
    What: Lines 49-75 (in debug_output) take a longer route than needed for what they compute.
    Fix: Rewrite the steps with the direct construct (a built-in, one loop instead of two, an early return instead of nesting) and keep the behaviour. Done when the construct named here is replaced, not when lines are merged.

Advice (does not fail the gate):
  - next/winner/lib/gatecore/cli.py:83-88 Lines 83-88 (in run) repeat lines 71-76 of this file (run) almost token for token.
  - next/winner/lib/gatecore/lexer.py:47-150 scan (lines 47-150) has 99 lines of code, most of them added by this change.

Next: make the fixes above, then run the gate again. Keep every behaviour, option and test the change introduces, ...
```

That verdict is wrong. `detectors.py` is the code that finds debug output and stubs, so its patterns read to Jev as the things they look for. A maintainer kept it, and the run passed. The full runs are in [`next/eval/results/winner_selfrun.json`](../eval/results/winner_selfrun.json).

Advice is everything that isn't the verdict's reason: copies and long functions in files that pass, detector findings outside the flagged functions. Advice never fails a run.

`--format json` prints the same result as one object (`next/interface.md`). `--text-out FILE` also writes the text report of the same run, which is how the action shows both.

## Keeping a change

A verdict is an opinion. When a person has looked at a failure and accepts it:

```bash
qualm-next keep lib/matcher.rb --reason "The algorithm is this complicated"
```

That records the path, a hash of the whole file, the reason and the date under `keeps` in `qualm.json`. Edit the file and the question reopens.

**A keep counts only from the base branch.** A pull request that adds a keep for its own file is still judged without it. So an agent cannot keep its own change. To accept one pull request as it stands, a maintainer grants the keep from outside the checkout:
- write `{"keeps": [{"path": "...", "hash": "...", "reason": "..."}]}` to a file the pull request can't write, for example from a workflow step that runs on a maintainer's label;
- point `GATE_KEEPS_FILE` (the action's `keeps-file`) at it.

A keeps file inside the checkout is ignored, and the report says so.

## The config

`qualm.json` at the repository root. It's optional, and it is read **from the base branch**, never from the pull request. A pull request that edits it is judged by the base branch's version, and the report names the edit.

```json
{
  "qualm-next": { "threshold": 0.506 },
  "skip": ["db/schema.rb", "**/*.generated.*"],
  "keeps": []
}
```

- `qualm-next.threshold` moves the gate.
- `skip` uses qualm's patterns:
  - a pattern with no slash matches a file name anywhere;
  - `**` matches any number of directories;
  - a pattern ending in a slash matches everything under that directory.
- `keeps` is written by `qualm-next keep`.

qualm's `gate`, `questions` and `drop` are ignored. The gate is a trained model, and a question it was not trained with has no weight in it.

## What it skips

It skips the same things qualm does:
- deleted, binary and unchanged files;
- lockfiles;
- prose and data such as Markdown, JSON and YAML;
- tests, unless you pass `--include-tests`.

What a pull request cannot do is decide its own skips:
- **Generated and vendored paths.** These are skipped only when the base branch already treats them so, through `.gitattributes`, a generated header, a vendor directory or a minified file. A pull request that marks its own files `linguist-generated` or `binary` still has them judged.
- **New vendored or generated code.** A new `vendor/` directory, or a file whose header claims it is generated, is judged like any other code. The exceptions are a dependency manifest changing with vendored code, and a generated file whose schema changes in the same pull request. Either exception is named in the report.
- **Tests that judged code imports.** A test file that judged code imports is judged.

The report counts what it skipped and why.

## What it costs

On 509 pull requests from 125 repositories, a run cost:

| | Cost |
| --- | --- |
| Median | $0.0004 |
| Mean | $0.0011 |
| p95 | $0.0042 |
| Most expensive | $0.016 |

Wall time was 4 seconds at the median and 24 seconds at p95. That is about twice qualm's cost: two views per file, more readings for close calls, and one request per function on a failing file. `--budget` caps a run, at one dollar by default.

Answers are cached in `GATE_CACHE_DIR`, or `~/.cache/gatecore/qualm-next`, never inside the checkout. Each entry is signed with a key derived from `OPENROUTER_API_KEY`, so an entry a pull request planted or edited is ignored. `--no-cache` skips the cache.

`qualm-next --dry-run` lists the files it would send and what that would cost, sends nothing, and exits 2, so a dry run in CI never passes a change.

## What leaves your machine

qualm-next sends OpenRouter, which passes it to TypeSafe:
- each judged file's diff;
- the same diff with its comments removed;
- for a failing file, the functions it changed.

Nothing else is sent. Run `--dry-run` first to see the list, and use `skip` for anything that shouldn't go.

## In CI

```yaml
# .github/workflows/qualm-next.yml
on:
  pull_request:
permissions:
  contents: read
jobs:
  simplify:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: tools4imps/qualm/next/winner@<commit-sha>
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Pin a commit, so the gate doesn't change without a commit of yours. The action:
1. checks the key, and exits 2 without it;
2. fetches the base branch;
3. runs the gate;
4. writes the report to the log and to the job summary.

Its inputs are `base`, `budget` (0.25 by default), `include-tests` and `keeps-file`. [`examples/qualm-next.yml`](examples/qualm-next.yml) is the same workflow.

**Forks.** A pull request from a fork gets no secrets, so the action fails with exit 2, by design. Run it from `pull_request_target` only if the job never runs the pull request's code. qualm-next reads the pull request's files with git and executes none of them, but other steps in the same job might.

## Flags

| Flag | What it does |
| --- | --- |
| `--base REF` | Compare with the merge base of REF and `HEAD` (default: the default branch) |
| `--threshold N` | Use this gate threshold for one run |
| `--include-tests` | Judge test files too |
| `--format text\|json` | The report's format (default text) |
| `--text-out FILE` | Also write the text report of this run to FILE |
| `--dry-run` | List what would be sent and its cost, send nothing, exit 2 |
| `--budget DOLLARS` | Stop when this much is spent (default 1.00) |
| `--no-cache` | Neither read nor write the answer cache |
| `--repo DIR` | The repository (default: here) |
| `--version` | Print the version |

## How the gate was chosen

Six alternatives were built and compared with qualm 0.1.1. Two splits were used, separated by repository:
- **dev:** 5,139 file changes from 301 repositories, for building;
- **held-out:** 634 file changes from 125 other repositories, sealed until the candidates were frozen and then opened once.

Labels came from what happened to each change:
- reviewers' comments asking for simplification;
- changes simplified by a follow-up commit;
- changes approved as they stood;
- smells injected into clean changes.

None came from the tools being judged. The plan, including the one comparison that would decide, was committed before the held-out split was opened ([`next/eval/PREREG.md`](../eval/PREREG.md)).

Against qualm 0.1.1 at its own gate (`push_back ≥ 0.6`) on held-out:

| | qualm 0.1.1 | qualm-next | Difference, 95% CI by repository |
| --- | --- | --- | --- |
| PR-AUC | 0.735 | 0.779 | **+0.043** [+0.005, +0.084] |
| Precision at the gate | 0.842 | 0.870 | +0.028 [−0.045, +0.113] |
| Recall at the gate | 0.328 | 0.385 | +0.057 [+0.006, +0.103] |
| False-alarm rate | 3.8% | 3.6% | −0.3 points [−2.6, +2.0] |

Most of that gain comes from changes that look large but are simple by construction: renames, comment-only edits, reformatting, moves. These are 25% of held-out's negatives against 2.4% of dev's, and the masked view reads them as the small changes they are. Without them the difference is −0.002 [−0.037, +0.036]. On the changes from real reviews alone, the two tools score about the same (+0.004).

**Fix loop.** A fresh coding agent fixed planted problems using each tool's report, and a blind judge scored the fixes. With qualm-next's report it succeeded on 11 of 34 held-out items, and with qualm's on 7. The difference is +0.118 [0.000, +0.242], McNemar p = 0.22. That is suggestive, not shown: 34 items can detect only a difference of about 0.26.

The numbers, and every number in `REPORT.md`, trace to files in [`next/eval/results/`](../eval/results/).

## How qualm-next holds itself to this

- **Tests.** 65 tests cover the perimeter, the lexer, the detectors, the scorer's localization and the command line. Run them with `python3 -m unittest discover -s tests`.
- **Same as what was measured.** The packaged script gives the same verdicts and findings as the measured, frozen version on all 100 dev pull requests checked (`winner_equivalence.json`).
- **Attacks.** 66 attacks were run against it: prompt injection in comments and strings, planted cache entries, generated or vendored disguises, self-granted keeps, and oversized files. The results are in `next/eval/results/attacks/`. What it doesn't handle is under Known limits.
- **Its own change.** qualm-next ran on the change that added it. It failed one file, wrongly (above), and passed with that file kept.

## Known limits in 1.0

- **Copies.** It is weak at noticing copies across files:
  - it missed a whole module copied into another module (attack `py_cross_module_copy`);
  - three other copy attacks it reported only as advice, which doesn't fail a run;
  - on its own change it did not report that `lib/gatecore/` is a copy of the study's core.
- **Code about smells.** It reads code about smells as smelly: detectors, linters and test fixtures that contain debug calls or stubs on purpose. Keep them, or put fixtures where your language's test rules skip them (`testdata/` in Go).
- **Language by extension.** It decides a file's language by extension, so an extensionless script, such as its own `bin/qualm-next`, is not judged.
- **Languages.** The model was trained on Go, Python, Java, TypeScript, Ruby and JavaScript changes (JavaScript only 163 of 5,139). Other languages are judged with the same model, untested.
- **Fails closed.** A file it couldn't judge fails the run. A Jev outage means red builds, not green ones.
- **Calibration.** Probabilities are calibrated to dev's mix, where 22% of changes drew push-back. Expect the absolute numbers to drift in a codebase with a different rate. The ranking holds better than the scale.
- **Determinism.** One in about two hundred file verdicts can differ between two runs with the cache off. With the cache on, a rerun of the same diff gives the same verdict.
- **Natural reviews.** On changes from real reviews alone, it is no better than qualm 0.1.1 yet.

## License

MIT
