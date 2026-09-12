# schema §17 — `export manifest`

Shared rules: `references/schema/00-shared.md`. Index: `references/schema.md`.

## 17. `export manifest`

`<destination>/<batch-name>-<unique-id>/manifest.json`, one file per published export
batch. Written and read by `scripts/export.py readcube` only. Describes exactly what a
ReadCube export bundle contains — see `references/readcube-export.md` for the full
command contract and `REFERENCE_MANAGER_V2_PLAN.md`'s "New Phase 2 — ReadCube export
command" for the design this implements. Not related to `registry.jsonl` (bibliographic +
intake lifecycle), `references/schema/16-annotation.md` (personal opinion), or an OKF wiki
bundle (`okf-export`) — this schema describes a one-shot, immutable export artifact, never
a live/authoritative store.

```json
{
  "schema_version": 1,
  "batch_id": "smoketest-565d0bbe",
  "created_at": "2026-09-12T09:30:00Z",
  "exporter_version": "export.py/0.1",
  "selection": {"source": "all"},
  "destination": "/Users/example/Library/Mobile Documents/com~apple~CloudDocs/refs",
  "counts": {
    "records": 1,
    "attachments": 1,
    "primary_pdf": 1,
    "metadata_only": 0,
    "supplements": 0
  },
  "records": [
    {
      "evidence_id": "doi:10.1000/smoke-1",
      "doi": "10.1000/smoke-1",
      "pmid": null,
      "pmcid": null,
      "type": "JOUR",
      "citation_key": "doi101000smoke1",
      "metadata_hash": "sha256:...",
      "warnings": [],
      "primary_pdf_available": true,
      "attachments": [
        {"path": "PDFs/A-Smoke-Test--10238c10.pdf", "role": "primary"}
      ]
    }
  ],
  "attachments": [
    {
      "path": "PDFs/A-Smoke-Test--10238c10.pdf",
      "role": "primary",
      "sha256": "10238c10...",
      "bytes": 41,
      "page_count": null,
      "provenance": {"source_path": "data/sources/paper1.pdf"},
      "associations": ["doi:10.1000/smoke-1"]
    }
  ]
}
```

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schema_version` | int | yes | Always `1`. |
| `batch_id` | string | yes | `<name>-<unique-id>`; also the batch's directory name. |
| `created_at` | string | yes | ISO-8601 UTC, batch creation time. |
| `exporter_version` | string | yes | `export.py`'s own tool identifier. |
| `selection` | object | yes | What was selected: `{"source": "all"}`, `{"source": "evidence-id", "ids": [...]}`, or `{"source": "run-dir", "run_dir": "..."}`. |
| `destination` | string | yes | The resolved `--destination` path this batch was published under. |
| `counts.records` | int | yes | Number of selected references in this batch. |
| `counts.attachments` | int | yes | Number of distinct files in the bundle (deduplicated by sha256 — one file shared by multiple records is counted once). |
| `counts.primary_pdf` | int | yes | Records with an available, resolved primary PDF. |
| `counts.metadata_only` | int | yes | Records published with no resolvable asset. |
| `counts.supplements` | int | yes | Non-primary attachments — always `0` for `LegacyRegistryAssetSource` (only ever reports `role="primary"`); nonzero for a `RefmgrExportSource` (`--source refmgr`) paper with more than one attachment. |
| `records[].evidence_id` | string | yes | The source evidence_id (legacy `scheme:value` form, or a `refmgr` paper id once that source is wired in — the two are not comparable to each other). |
| `records[].doi` / `.pmid` / `.pmcid` | string \| null | no | Whichever identifiers the record had; absent ones are `null`, never fabricated. |
| `records[].type` | string \| null | no | The RIS `TY` value used for this record (`JOUR`, `BOOK`, `CHAP`, `GEN`, `UNPB`). |
| `records[].citation_key` | string | yes | From `render.py bib_key` — R6: `evidence_id` with non-alphanumerics stripped. |
| `records[].metadata_hash` | string | yes | `sha256:<hex>` over the canonical (sorted-key, no whitespace) JSON of the record's exported metadata fields — the basis for Phase 2.3 incremental-export comparison (see `references/export-ledger.md` once written, or `scripts/export_ledger.py`'s docstring). |
| `records[].warnings` | string[] | yes | Per-record issues (e.g. "no resolvable primary PDF") — `[]` when none, never omitted. |
| `records[].primary_pdf_available` | bool | yes | Whether asset resolution succeeded for this record. |
| `records[].attachments[].path` | string | yes | Bundle-relative path (`PDFs/...` or `Supplements/...`). |
| `records[].attachments[].role` | string | yes | `"primary"`, `"supplement"`, `"version"`, or `"unknown"`. |
| `attachments[]` | object[] | yes | One entry per distinct file in the bundle (by full sha256), independent of the per-record `records[].attachments` associations above — a file referenced by multiple records appears once here with multiple entries in `associations`. |
| `attachments[].sha256` | string | yes | Full SHA-256 of the bundled file, verified against the source at copy time. |
| `attachments[].bytes` | int | yes | File size in bytes. |
| `attachments[].page_count` | int \| null | no | Only populated when the source reports it — never populated by `LegacyRegistryAssetSource`; `RefmgrExportSource` doesn't populate it yet either, though the underlying `refmgr` attachment record has a `page_count` field this could read from in a later increment. |
| `attachments[].provenance.source_path` | string | yes | Where the file was copied from, for reconciliation — never a credential or unrelated config value. |
| `attachments[].associations` | string[] | yes | `evidence_id`s of every record this file is attached to. |

### Invariants

- Immutable after publication: a batch's `manifest.json` is written once, during the
  staged publish, and never edited in place. A changed export produces a new batch (or,
  under `--incremental`, is reported as a structured no-op with no new batch at all — see
  `scripts/export_ledger.py`).
- Absent/unsupported fields are reported as `null`/omitted or as a `records[].warnings`
  entry — never silently represented as transferred (mirrors `references/schema/16-annotation.md`'s
  and `render.py`'s "absent means omitted, never invented" convention).
- `manifest.json` is written before `COMPLETE.json`; `COMPLETE.json`'s presence is what
  marks a batch as a locally validated, complete export — its absence means the batch was
  interrupted mid-publish and must not be treated as valid (the staged-directory + atomic
  rename publish mechanism in `scripts/export.py` is designed so an interrupted publish
  never reaches the final batch path at all, but a reader encountering a batch directory
  without `COMPLETE.json` by some other means — e.g. a copied `.staging-*` directory —
  must still treat it as incomplete).
- `attachments[].sha256` is independently re-verified against the copied file at publish
  time, not merely trusted from the source's recorded checksum (hash-mismatch is a
  publish-blocking error, not a manifest annotation).

---
