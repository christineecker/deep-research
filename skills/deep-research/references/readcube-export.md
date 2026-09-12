# readcube-export.md — ReadCube import bundles

Implements `REFERENCE_MANAGER_V2_PLAN.md`'s "New Phase 2 — ReadCube export command"
(itself a merge of the former `DEEP_EXPORT_IMPLEMENTATION_PLAN.md`, now retired in favor of
this document and the plan). The user manages their library and views PDFs/figures in the
ReadCube desktop app — this command prepares a local, self-contained import bundle for it.
It does not edit ReadCube's internal database, upload anything to ReadCube, or promise
two-way synchronization: `readcube_import_status` in every result is always `"unknown"`,
because a bundle being written locally is not the same thing as it being imported.

## Pieces

| Piece | Script | Storage | What it does |
|---|---|---|---|
| Selection | `export_select.py` | reads `registry.jsonl`, or a run's `corpus.jsonl` | Resolves `--all`/`--evidence-id`/`--run-dir` into a concrete record list; fails closed on anything unresolved or ambiguous |
| Asset resolution | `export_select.py` (`LegacyRegistryAssetSource`), `export_select_refmgr.py` (`RefmgrAssetSource`) | reads `registry.jsonl`'s `fulltext` pointer, or `refmgr`'s `AttachmentRepository`/`AssetRepository` | Locates and checksum-verifies the PDF(s) for each selected record |
| RIS serialization | `ris.py` | none (pure) | Turns a resolved record into one RIS block; never fabricates a missing field |
| Bundle publication | `export.py` | writes `<destination>/<batch>/` | Staged, atomic, checksum-verified copy + manifest + reports |
| Incremental tracking | `export_ledger.py` | `data/papers/export_ledger.jsonl` | Per-destination fingerprint history, so `--incremental` skips unchanged records |

Two `AssetSource` implementations exist by design (see the plan's "Asset-resolution
bridging" section), both reachable via `--source legacy|refmgr` (default `legacy`):
`LegacyRegistryAssetSource` reads today's real data (the JSONL registry, one primary asset
pointer per record); `RefmgrExportSource` reads the newer SQLite `refmgr` layer
(`skills/deep-research/scripts/refmgr/`, `--library <path>`), which supports multiple
attachments/roles/versions per paper — every attachment a refmgr paper has is exported
(primary into `PDFs/`, everything else into `Supplements/`), not just the preferred one.
`--source refmgr` supports `--all` and `--evidence-id` (a bare paper id, or a
`scheme:value` identifier lookup); `--run-dir` is deliberately rejected for it, since a
run's evidence_ids use the legacy registry's `scheme:value` scheme with no defined
cross-resolution against refmgr identifiers yet. It becomes the natural default once real
data lives there (the CLI cutover from Phase 1 is still an open decision; see the plan).

## Command

```bash
python3 scripts/export.py readcube --repo <path> \
  (--all | --evidence-id <id> [--evidence-id <id> ...] | --run-dir <run>) \
  --destination <local-folder> \
  [--name <batch-name>] [--incremental] [--bib] [--require-pdfs] [--dry-run]
```

- **Selection** — exactly one of `--all` / `--evidence-id` (repeatable) / `--run-dir` is
  required. `--evidence-id` normalizes and resolves aliases where the registry supports it,
  and fails before publication (nothing written) if any requested id doesn't resolve —
  listing every unresolved id, not just the first. `--run-dir` selects a run's actually
  `screening.decision == "include"` records, cross-resolved against the main registry; a
  run with no resolvable screening state is rejected rather than silently exporting
  everything in it.
- **`--destination`** — any locally accessible folder, including an already-downloaded
  iCloud Drive folder. Always explicit; the command never guesses an iCloud account path.
- **`--incremental`** — compares each selected record's metadata + attachment fingerprint
  against `export_ledger.jsonl`'s last-published state for this destination. If nothing
  changed, no batch directory is created at all — the result is a structured no-op
  (`status: "no_op"`, `batch_id: null`). If some records changed, only those are published
  into a new batch; unchanged ones are reported as `unchanged_count`, not re-copied. A
  failed publish attempt records `status: "failed"` in the ledger — it does not count as a
  baseline for future incremental comparisons (only a successful publish does).
- **`--bib`** — also writes `references.bib` via the existing `render.py` BibTeX renderer,
  unmodified. RIS remains the primary import format; tell the user to import one
  bibliography file into ReadCube, not both.
- **`--require-pdfs`** — blocks publication entirely (nonzero exit, nothing written) if any
  selected record's asset doesn't resolve. Without it, metadata-only records still publish,
  with an explicit per-record warning.
- **`--dry-run`** — runs the exact same selection/resolution/manifest logic and reports the
  same counts a real run would, with zero filesystem writes to `--destination` (verified by
  test: the destination directory is untouched afterward).

## Bundle layout

```text
<destination>/<batch-name>-<unique-id>/
  references.ris
  references.bib             # only with --bib
  PDFs/
    <safe-label>--<hash-prefix>.pdf
  Supplements/
  manifest.json               # schema: references/schema/17-export-manifest.md
  import-report.md
  README.md
  COMPLETE.json                # written last; local completion only, not import confirmation
```

Publication is staged and atomic: everything is written into a `.staging-<uuid>/`
directory first, then moved into its final `<batch>/` path in one `os.replace()` — a crash
or failure partway through never leaves a half-written batch at the final path (verified by
test: killing the copy step mid-export leaves no directory at the final path at all). Every
copied file is re-hashed after the copy and compared against the source's checksum; a
mismatch is a publish-blocking error, never a silently-accepted partial copy. Filenames are
content-addressed (`<safe-label>--<sha256 prefix>.pdf`) with automatic prefix extension on
the (practically never occurring) case of a collision — the label is for human
convenience only, never identity.

## Incremental export and the ledger (`export_ledger.py`)

`data/papers/export_ledger.jsonl` tracks, per `(destination, evidence_id)` pair, the
metadata hash and attachment fingerprint of the last **published** export. Reads/writes
follow the same lock-then-read convention as `registry.jsonl` — the lock is acquired
*before* any read, not after (this is the specific bug pattern documented as a known defect
in the legacy registry itself, in `REFERENCE_MANAGER_V2_PLAN.md`'s "Known issues and
standing decisions" section; the ledger is written to avoid reintroducing it). A `failed`
ledger entry never establishes
an incremental baseline — only a `published` one does, so a failed attempt doesn't cause a
later retry to be skipped as "unchanged."

## What is not new here

- No changes to `registry.py`'s schema or save path, `render.py`'s BibTeX logic, or the
  `refmgr` repositories — this command only reads them.
- No local web server, no browser UI — see `REFERENCE_MANAGER_V2_PLAN.md`'s revision note
  for why (ReadCube is the library UI and PDF viewer going forward).
- No OKF/wiki interaction — distinct from `research.py okf-export`; see `commands/export.md`
  for the one-line distinction to give a user who might confuse the two.
