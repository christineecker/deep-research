# Pool Architecture Implementation Plan

## Goal

Evolve `scripts/pool.py` from a wiki-level pointer index into the canonical paper registry
and intake layer for a standalone manuscript/research repository.

The target architecture separates:

- global source artifacts and snapshots
- reusable paper-level extractions
- project-specific screening, appraisal, synthesis, and manuscripts
- run-local execution state and audit trails

The new tool should not require a generated wiki or `wiki-manager`. It should initialize and
own its own research repository structure. Wiki import/export should remain available only as
legacy compatibility and interoperability behavior.

## Standalone Repo Initialization

The primary root concept should become `repo_root`, not `wiki_root`.

Add a higher-level command for creating a new standalone research repository:

```bash
python3 scripts/research.py init <path>
```

This command should create the base structure:

```text
<path>/
  data/
    sources/
      assets/
      snapshots/
      events.jsonl
    papers/
      registry.jsonl
      pool.jsonl
      extractions/
      appraisals/
  projects/
  runs/
  templates/
  exports/
```

Add a project creation command:

```bash
python3 scripts/research.py project create my-manuscript --repo <path>
```

This command should create:

```text
<path>/projects/my-manuscript/
  protocol.md
  corpus.lock.jsonl
  synthesis.md
  manuscript.qmd
  refs.bib
  figures/
  tables/
```

Compatibility commands can still bridge old wiki-backed layouts:

```bash
python3 scripts/research.py init --from-wiki <wiki-root> <path>
python3 scripts/research.py export wiki --project my-manuscript --repo <path> --wiki <wiki-root>
```

But the default path should be:

```text
research.py init -> project create -> ingest/search/extract -> appraise/synthesize/build
```

No default workflow should require `wiki-manager`, `<wiki>/research/`, or a pre-existing
generated wiki.

## Target Repository Layout

```text
repo/
  data/
    sources/
      assets/
        sha256-<hash>.pdf
        sha256-<hash>.html
        sha256-<hash>.txt
      snapshots/
        src-<hash>.json
      events.jsonl

    papers/
      registry.jsonl
      pool.jsonl
      extractions/
        pmid-12345678.json
        doi-10.1000-example.json
      appraisals/
        <project-or-protocol-id>/
          pmid-12345678.json

  projects/
    my-manuscript/
      protocol.md
      corpus.lock.jsonl
      synthesis.md
      manuscript.qmd
      refs.bib

  runs/
    2026-09-10-my-topic/
      config.json
      taskboard.jsonl
      corpus.jsonl
      workspace/
      outputs/

  templates/
    protocol.md
    manuscript.qmd
    report.md

  exports/
    wiki/
    bib/
    html/
    docx/
    pdf/
```

## Core Design Rules

1. Sources live globally under `data/sources/`.
2. Canonical paper extractions live globally under `data/papers/extractions/`.
3. Manuscript projects reference papers by `evidence_id`; they do not own canonical
   paper extractions.
4. Appraisal is project- or protocol-specific, not a universal paper property.
5. Runs are execution state and audit trails, not the durable home of reusable paper knowledge.
6. Every intake path uses the same normalization, dedupe, enrichment, and registry logic.
7. `repo_root` is the default storage root. `wiki_root` is legacy compatibility only.
8. `wiki-manager` is not a runtime dependency for creating, running, or building manuscripts.

## Stages And Ownership

Stages 0-5 become the reusable ingestion and extraction layer:

| Stage | Role | Durable Output |
|---|---|---|
| 0 | Configure topic/protocol/import scope | run config |
| 1 | Normalize question/search intent | project/run protocol |
| 2 | Search and seed from existing pool | run corpus candidates |
| 3 | Screen and dedupe | project/run selected corpus |
| 4 | Retrieve full text, PDFs, abstracts | `data/sources/` |
| 5 | Extract structured paper-level data | `data/papers/extractions/` |

Stages 6-8 become downstream scientific workflows:

| Stage | Role | Durable Output |
|---|---|---|
| 6 | Appraise evidence for a question/protocol | `data/papers/appraisals/<project-id>/` |
| 7 | Synthesize, compare, interpret | `projects/<slug>/synthesis.*` |
| 8 | Assemble manuscript/report/export | `projects/<slug>/`, `runs/<slug>/outputs/`, and `exports/` |

## Paper Intake Paths

New papers should be addable through runs and by hand. All entry points should converge on
the same internal paper registry API.

Planned commands:

```bash
python3 scripts/pool.py add --pmid 12345678 --repo <root>
python3 scripts/pool.py add --doi 10.1000/example --repo <root>
python3 scripts/pool.py add-pdf --file paper.pdf --repo <root>
python3 scripts/pool.py import-bib --file refs.bib --repo <root>
python3 scripts/pool.py import-folder --dir papers/ --repo <root>
python3 scripts/pool.py ingest-run --run-dir <dir> --repo <root>
python3 scripts/pool.py extract --evidence-id pmid:12345678 --repo <root>
```

Internal flow for every entry point:

```text
input
  -> identify paper
  -> normalize identifiers
  -> derive evidence_id
  -> dedupe against registry
  -> enrich metadata
  -> attach source assets if available
  -> optionally retrieve full text
  -> optionally extract structured data
  -> update registry and pool projection
```

Manual adds should register papers without forcing extraction. This allows large reference
sets to be staged cheaply before deeper processing.

Example lifecycle fields:

```json
{
  "evidence_id": "doi:10.1000/example",
  "status": "registered",
  "metadata_status": "complete",
  "asset_status": "missing",
  "extraction_status": "not_started",
  "appraisal_status": "not_appraised"
}
```

## Registry And Pool Model

Introduce a canonical registry:

```text
data/papers/registry.jsonl
```

One record per `evidence_id`.

Example:

```json
{
  "schema_version": 1,
  "evidence_id": "pmid:12345678",
  "pmid": "12345678",
  "doi": null,
  "pmcid": null,
  "title": "Example paper title",
  "journal": "J Example",
  "publication_date": "2024",
  "authors": ["Smith JA"],
  "status": "registered",
  "metadata_status": "complete",
  "asset_status": "available",
  "extraction_status": "extracted",
  "created_at": "2026-09-10T00:00:00Z",
  "updated_at": "2026-09-10T00:00:00Z",
  "sources": [],
  "extraction_path": "data/papers/extractions/pmid-12345678.json"
}
```

`pool.jsonl` can remain as either:

- a compatibility projection generated from `registry.jsonl`
- a search-optimized view used by seeding
- a deprecated alias during migration

Prefer one canonical registry to avoid split-brain state.

## Source Storage

Update `scripts/store.py` to support both old and new source locations.

Old run-local mode:

```text
<run>/sources/
<run>/events.jsonl
```

New global mode:

```text
data/sources/snapshots/
data/sources/events.jsonl
```

Recommended compatibility behavior:

```text
1. Look for a run-local snapshot.
2. Fall back to the global snapshot store.
3. Verify hash and span integrity through the same evidence-kernel checks.
```

Possible API:

```python
Store(run_dir, repo_root=None)
```

When `repo_root` is provided, writes should go to the global source store unless explicitly
requested otherwise.

Rename helper concepts as part of this transition:

```text
wiki_root_for_run() -> repo_root_for_run()
--wiki             -> --repo
<wiki>/assets/...  -> <repo>/data/...
<wiki>/research/   -> optional export target only
```

## Stage 5 Promotion

Change Stage 5 completion flow from pointer sync to canonical promotion:

```text
extract into run workspace
verify spans
copy/promote extraction to data/papers/extractions/
update registry
update run corpus extraction_path
```

The run may keep a workspace copy for auditability:

```text
runs/<run>/workspace/extractions/
```

But future reuse should prefer:

```text
data/papers/extractions/<evidence-id>.json
```

The old model, where `pool.jsonl` points to another run's workspace, should become a
backward-compatibility path only.

## Appraisal Storage

Do not store a single canonical appraisal per paper. Store appraisals under a project,
review, or protocol identity:

```text
data/papers/appraisals/<project-id-or-protocol-hash>/<evidence-id>.json
```

Reason: risk of bias and certainty judgments are often outcome- and question-specific.
A paper can be useful for one manuscript question and weak or irrelevant for another.

## Run Seeding And Reuse

Stage 2 should seed from the canonical registry or pool projection:

```text
data/papers/registry.jsonl
```

Seeded records are still only candidates. They must pass the current project's screening
criteria before extraction, appraisal, or synthesis.

Stage 5 reuse should check:

```text
1. canonical extraction in data/papers/extractions/
2. legacy pool pointer to a previous run
3. fresh extraction if neither exists
```

This ensures full deep-research runs combine newly ingested papers with the existing general
paper pool before proceeding into appraisal and synthesis.

## Backward Compatibility And Migration

Add migration/import commands:

```bash
python3 scripts/pool.py migrate --from-wiki <wiki-root> --repo <repo-root>
python3 scripts/research.py init --from-wiki <wiki-root> <repo-root>
```

Migration should:

1. Read existing `<wiki>/assets/papers/pool.jsonl`.
2. Resolve run-local extraction and appraisal pointers.
3. Copy reusable extractions into `data/papers/extractions/`.
4. Copy or re-register snapshots into `data/sources/snapshots/`.
5. Write `data/papers/registry.jsonl`.
6. Preserve or regenerate old `pool.jsonl` as a compatibility output.
7. Report stale pointers that could not be resolved.

Old runs should continue to verify and assemble through run-local snapshots.
New runs should prefer global source and extraction stores.

Wiki export should be an adapter, not part of the core pipeline:

```bash
python3 scripts/research.py export wiki --repo <repo-root> --project <slug> --wiki <wiki-root>
```

The export adapter may write OKF/wiki-manager-compatible files if desired, but manuscript
creation, project storage, source storage, extraction reuse, appraisal, synthesis, and report
building must work without it.

## Implementation Milestones

1. Document target architecture and schemas.
2. Add `research.py init <path>` to create standalone repo structure.
3. Add `research.py project create <slug> --repo <path>`.
4. Rename internal root concept from `wiki_root` to `repo_root`, keeping `--wiki` only where
   compatibility explicitly needs it.
5. Add canonical `data/papers/registry.jsonl` support without changing existing pipeline behavior.
6. Add manual `pool.py add` and `pool.py lookup` against the registry.
7. Add bulk import commands for BibTeX and folders.
8. Change `pool.py sync` so run outputs are promoted into canonical extractions.
9. Change Stage 2 seeding to use the canonical registry or generated pool projection.
10. Change Stage 5 reuse to prefer canonical extractions over run-local pointers.
11. Add global source snapshot support to `store.py`.
12. Add migration from legacy pool records.
13. Add project/protocol-specific appraisal storage.
14. Add optional wiki export adapter.

## Test Plan

Add tests in this order:

1. `research.py init` creates a complete standalone repo without a wiki.
2. `research.py project create` creates an isolated project structure.
3. `evidence_id` normalization and dedupe.
4. Manual add by PMID.
5. Manual add by DOI.
6. Manual add of PDF with hash-based asset dedupe.
7. Run sync promotes extraction to canonical store.
8. Seed uses canonical registry records.
9. Reuse works after the original run directory is unavailable.
10. Legacy pool-pointer records still resolve during migration.
11. Span verification works against global snapshots.
12. Project-specific appraisal storage does not overwrite appraisal from another project.
13. Wiki export works from a standalone repo but is not required by any core workflow.

## Open Decisions

1. Whether `pool.jsonl` remains a persisted file or becomes a generated view from
   `registry.jsonl`.
2. Whether global source snapshots should be mandatory for all new runs immediately or enabled
   behind a compatibility flag first.
3. How to derive stable project/protocol IDs: project slug, protocol hash, or both.
4. Which manual imports should be supported first: PMID/DOI, PDF folder, or BibTeX.
5. Whether run-local extraction copies should remain required for auditability after canonical
   promotion is implemented.
6. Whether to implement `research.py` as a thin orchestration wrapper around existing scripts or
   gradually move root/project/run concerns out of individual scripts.
7. Whether old wiki-backed command names should remain indefinitely as aliases or be deprecated
   with warnings after migration support is stable.
