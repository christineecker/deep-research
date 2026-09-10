# pool-architecture.md — standalone repo, registry, and global source store

Implements `POOL_ARCHITECTURE_IMPLEMENTATION_PLAN.md`. Describes the `repo_root`-based storage
model that sits alongside the wiki-backed pipeline described in `SKILL.md`: both are supported,
neither is required by the other. `wiki-manager` is never a runtime dependency of anything in
this document.

## Two storage roots, not one migration

- `wiki_root` (`_common.py wiki_root_for_run`) — legacy: `<wiki>/outputs/deep-research/<slug>/`
  runs, wiki-level pool at `<wiki>/assets/papers/pool.jsonl` (`pool.py Pool`).
- `repo_root` (`_common.py repo_root_for_run`) — standalone: `<repo>/runs/<slug>/` runs, a
  canonical registry at `<repo>/data/papers/registry.jsonl` (`registry.py Registry`).

A run under either root works end to end without the other. `pool.py migrate --from-wiki` and
`research.py export wiki` are one-way bridges, not a required step.

## Repo layout (`research.py init <path>`)

```text
<repo>/
  data/
    sources/
      assets/            content-addressed PDFs: sha256-<hash>.pdf
      sources/            JSON snapshots: src-<64 hex>.json  (store.global_sources_root)
      events.jsonl        append-only retrieval log, global
    papers/
      registry.jsonl      canonical registry — one record per evidence_id
      pool.jsonl           GENERATED view of registry.jsonl — never hand-edited, never
                           the source of truth (regenerate with `registry.py pool`)
      extractions/         <evidence-id-slug>.json, e.g. pmid-12345678.json
      appraisals/
        <project>/          project-scoped: a paper's appraisal is per-question, not universal
  projects/<slug>/        protocol.md, synthesis.md, manuscript.qmd, refs.bib, figures/, tables/
  runs/<slug>/             same shape as a wiki-backed run (config.json, corpus.jsonl,
                           workspace/, outputs/), just rooted under the repo instead of a wiki
  templates/
  exports/{wiki,bib,html,docx,pdf}/
```

`data/sources/sources/` (not `.../snapshots/`) is deliberate reuse, not a naming slip: it is
`store.py`'s existing `<run_dir>/sources/` convention applied to `data/sources` treated as a
`run_dir` (see the `global_sources_root` docstring in `store.py`) — every write-once/hash/verify function in
`store.py` works unchanged against it, at zero duplication and zero risk to the audited
evidence-kernel code.

## Registry lifecycle fields

Every `data/papers/registry.jsonl` record carries, beyond the bibliographic fields shared with
`corpus.jsonl` (schema.md §4):

| field | values | meaning |
|---|---|---|
| `status` | registered / screening / included / excluded | not currently automated by any command; available for a project to set |
| `metadata_status` | pending / partial / complete | derived by `Registry.register` from which fields are present |
| `asset_status` | missing / available | set by `add-pdf` / `import-folder` |
| `extraction_status` | not_started / in_progress / extracted | set by `promote` |
| `appraisal_status` | not_appraised / in_progress / appraised | set by `appraise-promote` |
| `asset` | `{sha256, path, bytes, pages, added_at}` | present once `asset_status: available` |
| `extraction_path` | repo-relative path | canonical extraction, once promoted |
| `appraisals` | `{project: repo-relative path}` | project-scoped, see below |

## Commands

```bash
# repo/project lifecycle
python3 scripts/research.py init <path>
python3 scripts/research.py init <path> --from-wiki <wiki-root>   # lightweight registry seed only
python3 scripts/research.py project create <slug> --repo <path>
python3 scripts/research.py project list --repo <path>
python3 scripts/research.py export wiki --repo <path> --wiki <wiki-root> [--project <slug>]

# intake -> registry.jsonl (all converge on Registry.register)
python3 scripts/registry.py add --repo <path> --pmid <pmid> | --doi <doi>
python3 scripts/registry.py add-pdf --repo <path> --file <pdf> [--pmid/--doi/--pmcid/--title]
python3 scripts/registry.py import-bib --repo <path> --file refs.bib
python3 scripts/registry.py import-folder --repo <path> --dir <pdf-folder> [--recursive]
python3 scripts/registry.py lookup --repo <path> (--pmid|--doi|--pmcid|--evidence-id)
python3 scripts/registry.py list --repo <path>
python3 scripts/registry.py pool --repo <path>                    # regenerate pool.jsonl

# Stage 5/6 promotion: run workspace -> canonical store
python3 scripts/registry.py promote --repo <path> --run-dir <run>
python3 scripts/registry.py appraise-promote --repo <path> --run-dir <run> --project <slug>

# Stage 2/5/6 reuse, repo mode (each subcommand below branches on --repo vs --wiki)
python3 scripts/pool.py seed --run-dir <run> --repo <path> --query "..." [--include-metadata-only]
python3 scripts/pool.py lookup --repo <path> (--pmid|--doi|--pmcid) [--project <slug>]
python3 scripts/pool.py reuse --run-dir <run> --repo <path> (--pmid|--doi|--pmcid) [--project <slug>]

# legacy bridge, one-way, never required
python3 scripts/pool.py migrate --from-wiki <wiki-root> --repo <path> [--project <slug>]
```

`pool.py`'s wiki-mode commands (`sync`, `bib`, `list`, and `seed`/`lookup`/`reuse` without
`--repo`) are unchanged; `--wiki` and `--repo` are mutually exclusive per invocation, not per
script.

## Stage 4/5 write path under `--repo`

`store.py Store(run_dir, repo_root=<path>)` writes new snapshots to the global store by default
(`local=True` overrides), and falls back run-local -> global on read — so a canonical extraction
promoted from *any* run resolves its spans from any other run's `Store` constructed with the
same `repo_root`, with no snapshot-carrying step needed (contrast `pool.py reuse`'s wiki-mode
path, which must carry snapshots between isolated per-run stores because wiki runs do not share
one global store).

## Appraisal is project-scoped, on purpose

`Registry.set_appraisal(evidence_id, project, path)` writes into an `appraisals` map, not a
single field: a paper's risk-of-bias / certainty judgement is a function of the question it is
being used to answer, not an intrinsic property of the paper (plan "Appraisal Storage"). Looking
it up without a `--project` never returns one; the same paper can be `appraised` for one
manuscript and `not_appraised` for another simultaneously.

## Migration (`pool.py migrate --from-wiki`)

For every record in the legacy wiki pool: register its bibliographic fields into the registry,
then copy its freshest extraction/appraisal pointer (`Pool.resolve_source` — same
freshest-pointer-that-still-resolves-on-disk logic `pool.py reuse` uses) into the canonical
store, carrying every snapshot it cites into the global source store so spans keep verifying
with no wiki involved. A pointer whose file has moved or vanished is reported under
`stale_pointers` in the command's JSON output, never silently dropped.

## Other consumers of the canonical registry

`scripts/paper.py` (`references/single-paper-summary.md`) reads and writes this same registry,
extraction, and appraisal store for its single-paper and selected-paper summary profiles — there
is no separate summary-only store. A paper registered/extracted/appraised by a full review run is
reused as-is by a later single-paper summary request, and vice versa.

## What is not yet wired

- `SKILL.md`'s Stage 2-8 narrative still documents the wiki-backed path only; nothing above
  changes it. Running a project under `repo_root` today means using the `research.py`/
  `registry.py` commands directly rather than the SKILL.md stage prose.
- `status`/`screening` on registry records are not set by any command; they exist for a project
  to use, not because anything currently writes them.
- `okf.py promote` (SKILL.md Stage 8, publishes `<wiki>/research/reviews/<slug>.md`) is a
  separate, richer publishing mechanism from `research.py export wiki`'s plain file copy — the
  latter is a courtesy bundle, not a replacement.
