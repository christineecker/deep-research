Publish a ReadCube-importable bundle from the registry: RIS bibliography, primary PDFs,
manifest, and an import report. This is a ReadCube export target, distinct from wiki
publishing (`okf-export`) — it never touches an OKF wiki.

Do not read SKILL.md's staged pipeline narrative — call the script below directly.

Parse `$ARGUMENTS` for:
- `--repo <path>` — required.
- Exactly one selection source, required:
  - `--all` — every registered reference, including metadata-only records.
  - `--evidence-id <id>` — repeatable; explicit records. Fails closed (nothing published)
    if any id doesn't resolve.
  - `--run-dir <run>` — a run's actually-included records (`screening.decision ==
    "include"`). Fails closed if the run has no resolvable screening state.
- `--destination <local-folder>` — required; a locally accessible folder (an iCloud Drive
  folder is fine as long as it is already downloaded — do not guess an iCloud path).
- `--name <batch-name>` — optional prefix for the batch directory (default `export`).
- `--incremental` — publish only records whose exported metadata or attachment inventory
  changed since the last successful (`published`) export to this exact `--destination`
  path, per `data/papers/export_ledger.jsonl`. Unchanged records are omitted from the
  batch and counted in `unchanged_count`, not `exported_count`. If every selected record
  is unchanged, nothing is published: `status` is `"no_op"`, `batch_id`/`bundle_path` are
  `null`, and `exported_count` is `0` — no empty batch directory is created. A failed
  publish attempt is recorded in the ledger as `"failed"` and does not count as a baseline
  for the next attempt.
- `--bib` — also render `references.bib` alongside `references.ris`. Tell the user to
  import one bibliography format into ReadCube, not both.
- `--require-pdfs` — block publication (nonzero exit, nothing written) if any selected
  reference lacks a resolvable primary PDF. Without it, metadata-only references publish
  with an explicit warning instead.
- `--dry-run` — resolve selection and assets, report the same counts a real run would,
  and write nothing to `--destination`.
- `--source legacy|refmgr` — optional, defaults to `legacy` (today's real data, read from
  `--repo`'s `registry.jsonl`). `--source refmgr` reads the newer SQLite `refmgr` library
  instead (`--library <path>` then required), exports every attachment a paper has
  (primary into `PDFs/`, everything else into `Supplements/`), and supports `--all` or
  `--evidence-id` (a bare refmgr paper id, or a `scheme:value` identifier like
  `doi:10.1000/x`) — `--run-dir` is not supported against refmgr (a run's evidence_ids use
  the legacy registry's scheme, with no defined cross-resolution against refmgr
  identifiers). The export ledger (`--incremental`) is always keyed off `--repo` regardless
  of `--source`.

Steps:
1. Print, then run:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/deep-research/scripts/export.py" readcube --repo <path> (--all | --evidence-id <id> [...] | --run-dir <run>) --destination <path> [--name <batch-name>] [--incremental] [--bib] [--require-pdfs] [--dry-run] [--source legacy|refmgr] [--library <path>]
   ```
2. Print the script's own output verbatim — the structured JSON result (`status`,
   `batch_id`, `bundle_path`, selection/exported counts, `warnings`, `blocking_errors`,
   `readcube_import_status: "unknown"`).
3. If a bundle was published, point the user at `<bundle_path>/README.md` for the import
   sequence and `<bundle_path>/import-report.md` for anything that needs manual attention.

**Deviations to expect and not treat as failure**: `readcube_import_status` is always
`"unknown"` — this command only validates and writes a local bundle; it never confirms an
actual ReadCube import or iCloud sync. A record with no resolvable primary PDF still
publishes (metadata-only) unless `--require-pdfs` was passed — that is a warning, not an
error.
