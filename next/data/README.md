# Benchmark data

- `dev.jsonl.gz`: the dev split, one change (case) per line, with base and head text of every judged file and the labelled items (`next/interface.md` describes the shape). Used for building, tuning and every dev number.
- `heldout.v1.tar.enc`: the held-out split, sealed before any candidate was built. It holds `heldout.jsonl.gz` and the held-out fix-loop items (their injected files and patches). `heldout.v1.sha256.json` records the SHA-256 of the tar and of each member, made before encryption. The passphrase was committed as `heldout.key` with the archive and removed from the tree again before any held-out run (the measurement audit found it next to the archive); it is restored when the split is opened. The encryption keeps the split from being read by accident; the hash proves it never changed. **Do not decrypt before the final evaluation.**
- `manifest.json`: counts by split, language, label source and label, and which held-out items are unscored and why.
- `fixloop_items.json`: the 40 dev fix-loop items (repository, commit, file, injected smell, location, test command).
- `subsets/`: the fixed dev subsets: `dev150` for LLM-based candidates, `det100` and `det30` for determinism runs.
- `adjudication.v1.json` (later): the human adjudication of held-out and dev items, applied on top of the sealed labels.
