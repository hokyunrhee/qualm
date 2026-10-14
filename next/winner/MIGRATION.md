# Moving from qualm 0.1.1 to qualm-next

qualm-next asks Jev the same ten questions about the same diff that qualm does, plus twelve about a second view of the change with its comments removed. A model trained on 5,139 labelled files weighs the answers. Most of what you run stays the same. The differences below are the ones that need you.

## The key and the command

| qualm 0.1.1 | qualm-next |
| --- | --- |
| `OPENROUTER_API_KEY` | the same key, nothing else |
| `qualm` | `qualm-next` (one Python 3.9+ script, no packages to install) |
| `qualm --base REF` | `qualm-next --base REF` (default: the default branch, as before) |
| `--format text\|json` | the same; the JSON adds `findings`, `advice`, `warnings` and `usage` (see `next/interface.md`) |
| `--budget DOLLARS` (default 1.00) | the same |
| `--no-cache` | the same; `--cache DIR` becomes `GATE_CACHE_DIR=DIR` |
| `--include-tests` | the same |
| `--threshold N` | the same flag, on a different scale (below) |
| `--dry-run` (exits 0) | `--dry-run` exits **2**, so a dry run in CI can never pass a change |
| `--jobs N` | gone; files are judged eight at a time |

## Exit codes

They are unchanged: **0** passed, **1** failed, **2** could not judge.

Two more cases now exit 2 instead of 0:
- `--dry-run`, as above.
- A judged file over 1 MB. qualm judged its first megabyte; qualm-next does not judge part of a file.

A change with nothing to judge still passes, and needs no key.

## The config

`qualm.json` keeps its name and its place at the top of the repository. **qualm-next reads it from the base branch, never from the pull request.** A pull request that edits `qualm.json` is judged by the base branch's version, and the report names the edit. So raising the threshold, adding a `skip` or recording a keep inside a pull request has no effect on that pull request. It takes effect once a maintainer merges it.

| qualm.json key | qualm-next |
| --- | --- |
| `skip` | honoured, same patterns |
| `keeps` | honoured, with a new hash (below) |
| `gate.threshold` | ignored: it is a threshold on `push_back`, not on qualm-next's probability |
| `qualm-next.threshold` | new: the gate on qualm-next's calibrated probability (default 0.506) |
| `gate.question`, `questions`, `drop` | ignored: the gate is the trained model, not one question. Custom questions have no place in a model trained without them. |

qualm's `push_back >= 0.6` and qualm-next's `0.506` are not the same scale. On dev, qualm's gate ran at precision 0.61 and qualm-next's at 0.65 (`next/REPORT.md`).

`.gitattributes` is also read from the base branch. A pull request cannot mark its own files `linguist-generated` or `binary` to skip them.

## Keeps

`qualm-next keep PATH --reason "..."` records a keep in `qualm.json`, as `qualm keep` did. Two things change:

- **The hash binds the whole file**, normalized for line endings and trailing spaces. qualm's hash covered the lines the change added and removed. So qualm 0.1.1's keeps do not match, and those files are judged again. Re-record the ones you still want.
- **A keep counts only from the base branch.** It does not unblock the pull request that adds it. To accept one pull request as it stands, a maintainer grants a keep outside the checkout:
  - put `{"keeps": [{"path": "...", "hash": "...", "reason": "..."}]}` in a file the pull request cannot write, for example one written by a workflow step that runs on a maintainer's label;
  - point `GATE_KEEPS_FILE` (the action's `keeps-file` input) at it.

  A keeps file inside the checkout is ignored, and the report says so.

## What is skipped

The skip rules are qualm's, with one difference: the pull request cannot decide them for itself.
- **Vendored, generated and built paths** are skipped only when the base branch already treated them that way.
- **A new `vendor/` directory**, or a file whose header claims it is generated, is judged like any other code. Two exceptions: the header's own schema (for example `foo.proto` for `foo.pb.go`) changes in the same pull request, or a dependency manifest changes with vendored code. Either exception is named in the report.

Tests are skipped unless you pass `--include-tests`, as before. A test file that judged code imports is judged.

## Cache

Answers are cached in `GATE_CACHE_DIR` or `~/.cache/gatecore/qualm-next`, never inside the checkout. Each entry is signed with a key derived from `OPENROUTER_API_KEY`, so an entry a pull request planted or rewrote is ignored. The CI cache saves little; `--no-cache` is fine there.

## Reports

A failing file lists findings in this order:
1. The exact lines of the detectors' findings: debug output, stubs, copies, impossible guards, commented-out code, unused new functions.
2. Then each function a reviewer would push back on, with what to change and when it is done.

Everything else is listed as advice, and advice never fails a run.
