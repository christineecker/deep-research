# reference-manager.md — search, annotations, embeddings, and OKF export

Implements `REFERENCE_MANAGER_IMPLEMENTATION_PLAN.md`. Extends `pool-architecture.md`'s
`repo_root` registry into a fuller personal reference manager: keyword/facet/similarity
search, personal annotations, and a bridge from a hand-picked paper set into an OKF wiki
bundle. Nothing here forks the standalone repo into a separate product — every piece below
reads/writes the same `data/papers/registry.jsonl` and its neighbors described in
`pool-architecture.md`.

## Pieces

| Piece | Script | Storage | What it does |
|---|---|---|---|
| Search | `registry.py search` | none new — reads `registry.jsonl`, `annotations.jsonl`, snapshots | Facet filters + full-text keyword search + optional similarity ranking, all in one subcommand |
| Annotations | `annotations.py` | `data/papers/annotations.jsonl` | Personal tags/star-rating/note per `evidence_id`, kept separate from the registry and from project-scoped appraisal |
| Embeddings | `embeddings.py` | `data/papers/embeddings.jsonl` | Cached per-paper embedding vectors + cosine-similarity nearest-neighbour lookup |
| OKF export | `research.py okf-export` | a synthetic `runs/<slug>-okf-export-<ts>/` | Promotes a hand-picked evidence_id set into an existing OKF wiki bundle via `okf.py promote`, unmodified |

## Search (`registry.py search`)

```bash
python3 scripts/registry.py search --repo <path> \
  [--journal <substr>] [--year <YYYY>|<YYYY-YYYY>] \
  [--status registered|screening|included|excluded] \
  [--extraction-status not_started|in_progress|extracted] \
  [--appraisal-status not_appraised|in_progress|appraised] \
  [--tag <tag>] [--min-rating N] [--project <slug>] \
  [--q "<keyword text>"] [--similar-to <evidence-id>] [--limit N]
```

- **Facet filters** (`--journal`, `--year`, `--status`, `--extraction-status`,
  `--appraisal-status`) operate purely in-memory over fields the registry already carries
  (`pool-architecture.md` "Registry lifecycle fields") — no new storage, no index to build.
- **`--tag`/`--min-rating`** join against `data/papers/annotations.jsonl` read-only; search
  never writes to it.
- **`--q`** is a stdlib-only, lowercase AND-of-terms keyword search over title, abstract,
  journal, extraction narrative fields, appraisal rationale (scoped to `--project` when
  given), and the paper's full text resolved from the global snapshot store (`store.py`'s
  `global_read_snapshot` function). No persisted search index — it scans the pool at query
  time, which is fine at personal-library scale.
- **`--similar-to <evidence-id>`** ranks whatever the facet/keyword filters already
  surfaced by cosine similarity, delegating to `embeddings.py`'s cached vectors. It
  **requires `embeddings.py index` to have already been run** for this repo; if no
  `embeddings.jsonl` exists yet, the command says so instead of silently ignoring the flag.

## Annotations (`annotations.py`)

Personal opinion about a paper — tags, a 1-5 star rating, a free-text note — kept in its own
file, on purpose, the same way appraisal is project-scoped rather than a registry field (see
`pool-architecture.md` "Appraisal is project-scoped, on purpose"). An annotation never
requires its `evidence_id` to exist in the registry.

```bash
python3 scripts/annotations.py tag  --repo <path> --evidence-id <id> (--add <tag>|--remove <tag>)
python3 scripts/annotations.py rate --repo <path> --evidence-id <id> (--stars N|--clear)
python3 scripts/annotations.py note --repo <path> --evidence-id <id> (--set "<text>"|--clear)
python3 scripts/annotations.py show --repo <path> --evidence-id <id>
python3 scripts/annotations.py list --repo <path> [--tag <tag>] [--min-rating N] [--limit N]
```

Storage: `data/papers/annotations.jsonl`, one record per `evidence_id`, schema
`references/schema/16-annotation.md`. Same conventions as `registry.jsonl`: atomic
tmp-file + `Path.replace()` writes, tolerant skip-on-corrupt-line reads, sorted by
`evidence_id`. Guarded by `advisory_lock(repo_root, "annotations")` — a lock distinct from
`registry.py`'s own `"registry"` lock, so annotation writes never contend with registry
writes, and vice versa.

## Embeddings and similarity search (`embeddings.py`)

```bash
python3 scripts/embeddings.py index   --repo <path> [--model <name>] [--limit N] [--force]
python3 scripts/embeddings.py similar --repo <path> --evidence-id <id> [--k N]
```

Storage: `data/papers/embeddings.jsonl`, one record per `evidence_id`:
`{schema_version, evidence_id, model, dim, vector, updated_at}`. Written atomically, same
pattern as `registry.jsonl`. Embedding text per paper is title + abstract + extraction
narrative fields (`population`, `intervention`, `comparator`, `limitations`,
`extractor_notes`) when an extraction exists — the same text search's `--q` assembles.
`similar` never re-embeds; it only does cosine similarity over the cache, in pure Python
(no numpy needed at personal-library scale).

**Scoped pip-install exception.** `index` requires the third-party package
`sentence-transformers` (default model `all-MiniLM-L6-v2`), imported lazily inside the one
function that needs it — importing the module or running `--help` never requires it.
This is the **one deliberate, narrow exception** to deep-research's blanket "zero pip
installs, ever" policy, scoped to this single script; see `references/acquisition.md` §9's
"Forbidden approaches" preamble for the canonical statement of the exception (do not
duplicate that wording here beyond this cross-reference). It does not relax the
no-pip-installs policy for full-text acquisition, extraction, appraisal, or anything else
in this skill — those remain stdlib-plus-`requests`/`pdfminer` as before. If the package
isn't installed, `index` fails fast with a one-line message pointing at
`pip install sentence-transformers`; `similar` and `registry.py search --similar-to` never
need it once `embeddings.jsonl` already exists.

## OKF export (`research.py okf-export`)

```bash
python3 scripts/research.py okf-export --repo <path> \
  --evidence-id <id> [--evidence-id <id> ...] \
  --wiki <wiki-root> [--project <slug>] [--no-keep-run]
```

Lets the user pick N evidence_ids out of the registry and feed them into an existing
`.okf/`-style bundle without reshaping `okf.py promote`'s V1-V25 validator or hand-writing a
second concept-emission path. It:

1. Resolves every `--evidence-id` against the registry up front; if any lacks a usable
   `extraction_path` on disk, nothing is written and the missing ones are reported.
2. Synthesizes a throwaway run directory (`runs/<slug>-okf-export-<timestamp>/`) shaped
   exactly as `okf.py`'s `load_run()` expects: `config.json`, `corpus.jsonl` projected from
   the registry records, and the selected papers' extraction/appraisal JSON copied into
   `workspace/`.
3. Runs the existing `verify.py` over that synthetic run to produce
   `outputs/verification.json`.
4. Calls `okf.py promote` unmodified, with `--force --allow-unverified` — a registry-only
   export has no real screening/PRISMA history, so pipeline-completeness checks like
   `C-SEARCH-LOG`/`C-PRISMA` are expected to fail; that's why every concept this promotes
   lands with `status: provisional`, never `stable`. Concept-level correctness (V1-V25) is
   still enforced, unforced.
5. Leaves the synthetic run directory on disk by default (it's a legitimate run record, not
   scratch) — pass `--no-keep-run` to delete it after promotion.

See `commands/okf-export.md` for the slash-command wrapper and its prerequisite
(`/deep-research:summarize` or the full pipeline, to get an extraction on file first).

## End-to-end example

```bash
# 1. Register a paper
python3 scripts/registry.py add --repo /path/to/repo --pmid 12345678

# 2. Extract it (via the summarize command/paper.py — needed before okf-export)
python3 scripts/paper.py summarize --repo /path/to/repo --pmid 12345678

# 3. Tag and rate it
python3 scripts/annotations.py tag  --repo /path/to/repo --evidence-id pmid:12345678 --add to-read
python3 scripts/annotations.py rate --repo /path/to/repo --evidence-id pmid:12345678 --stars 4

# 4. Search for it (facet + keyword)
python3 scripts/registry.py search --repo /path/to/repo --tag to-read --q "diagnostics"

# 5. Build the similarity index, then find similar papers
python3 scripts/embeddings.py index   --repo /path/to/repo
python3 scripts/embeddings.py similar --repo /path/to/repo --evidence-id pmid:12345678 --k 5

# 6. Export a small hand-picked set into an OKF wiki bundle
python3 scripts/research.py okf-export --repo /path/to/repo \
  --evidence-id pmid:12345678 --wiki /path/to/wiki
```

## What is not new here

- No changes to `registry.py`'s own schema or save path — search and annotations only read
  it.
- No changes to `okf.py`'s validator or promotion logic — `okf-export` calls it unmodified.
- No new skill, no changes to the wiki-backed Stage 0-8 pipeline described in `SKILL.md`.
