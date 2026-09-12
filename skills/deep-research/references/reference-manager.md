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
| Search | `registry.py search` | reads `registry.jsonl`, `annotations.jsonl`, refmgr's indexes | Facet filters + full-text keyword search + optional similarity ranking, all in one subcommand |
| Index | `registry.py reindex` | `data/refmgr/library.sqlite3` (`papers_fts`, `chunks_fts`, `paper_terms`) | Rebuilds every derived index from the registry and the snapshot store |
| Facets | `registry.py facets` | `paper_terms` | What MeSH headings / keywords / article types / authors the library actually holds, with counts |
| Figures | `registry.py figures` | `figures` + `figures_fts`, images as role=`figure` attachments | Crops captioned figures out of stored PDFs, and searches those captions |
| Health | `registry.py doctor` | none — read-only | Missing or corrupt assets, dangling attachments, stale index rows, figures that lost their source |
| Ask | `ask.py retrieve` | none new | Hybrid retrieval over the indexes, every hit re-verified as a span; the answering command writes prose from the result |
| Alerts | `alerts.py` | refmgr's `saved_searches` table | Saved PubMed queries, re-run on demand to report (or register) what the repo has not seen |
| Annotations | `annotations.py` | `data/papers/annotations.jsonl` | Personal tags/star-rating/note per `evidence_id`, kept separate from the registry and from project-scoped appraisal |
| Embeddings | `embeddings.py` | `data/papers/embeddings.jsonl` | Cached per-paper embedding vectors + cosine-similarity nearest-neighbour lookup |
| OKF export | `research.py okf-export` | a synthetic `runs/<slug>-okf-export-<ts>/` | Promotes a hand-picked evidence_id set into an existing OKF wiki bundle via `okf.py promote`, unmodified |

## Storage split: truth vs index

`data/papers/registry.jsonl` is the **source of truth** for bibliographic metadata and
lifecycle. `data/refmgr/library.sqlite3` is an **index built from it** — every mutating
registry command mirrors its changed records into refmgr's `papers`/`identifiers` tables
and reindexes them (`Registry.commit`), and the full-text `chunks_fts` index is built from
the snapshots in `data/sources/snapshots/`.

Consequences worth knowing:

- **Nothing is lost if the database is.** `registry.py reindex --repo <path>` rebuilds
  both indexes from scratch. It is also the backfill path for a repo whose records were
  registered before the mirror existed, and it is safe to re-run: an unchanged snapshot
  is skipped rather than re-split.
- **Search degrades, never fails.** No database, an empty chunk table, or a corrupt file
  makes `--q` fall back to scanning snapshot bodies exactly as it did before the index
  existed — slower, same answers, with a note on stderr.
- **`refmgr_paper_id` on a registry record is the link** between the two stores, and the
  idempotency key for records with no identifier refmgr can match on.
- **Figures are the exception to "nothing is lost".** Every other derived table is
  rebuilt from canonical text by `reindex`; figure crops are derived from PDF *layout*
  by a heuristic, so rebuilding them means re-running poppler over every asset. That is
  a separate command (`registry.py figures`), not part of `reindex`, and the cropped
  PNGs are real assets in the store rather than rows that can be regenerated for free.

## Figures (`registry.py figures`)

Journal figures are mostly vector charts, so pulling embedded images out of a PDF
misses them entirely. Extraction instead anchors on captions: `pdftotext -bbox` gives
every word's rectangle, a line *starting* with `Figure N` is a caption, the figure is
the whitespace band above it within that column, and `pdftoppm` renders exactly that
region (`library.py`, "figures" section). Each crop is stored as an ordinary asset with
a role=`figure` attachment; the `figures` table records which figure of which paper it
is and what its caption says.

```
registry.py figures --repo <path>                  # backfill; already-done PDFs skipped
registry.py figures --repo <path> --replace        # re-crop (after an extractor change)
registry.py figures --repo <path> --query "forest plot"
registry.py add-pdf --repo <path> --file x.pdf --figures   # opt in at import time
```

Two limits worth knowing before relying on it:

- **Scanned PDFs yield nothing.** No text layer means no caption anchors. OCR first.
- **Tables are deliberately excluded.** Their body is text, which the chunk index
  already holds in a form that keeps the cell values searchable; a crop would not.

Captions are indexed in `figures_fts`, not `chunks_fts`. A chunk row is a verifiable
claim span into a snapshot (`store.verify_span`); a caption read out of PDF layout
has no such offsets, so filing it as a chunk would put unverifiable rows into an index
whose contract is that its rows verify.

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
- **Controlled-vocabulary filters** (`--mesh`, `--author`, `--article-type`) match a
  case-insensitive *substring* of a term, because MeSH headings are long and people type
  fragments: `--mesh depress` finds "Depressive Disorder, Major". They are served by the
  `paper_terms` index and fall back to the record's own metadata lists when it is absent,
  with identical results either way. `registry.py facets --scheme mesh|keyword|
  article_type|author` lists what is available to filter on, with paper counts.
- **`--tag`/`--min-rating`** join against `data/papers/annotations.jsonl` read-only; search
  never writes to it.
- **`--q`** is a stdlib-only, lowercase AND-of-terms keyword search over title, abstract,
  journal, the extraction's claim sentences and outcome names (`spans[].claim`,
  `outcomes[].name`/`timepoint`/`effect_measure`/`direction`), extraction narrative
  fields, appraisal rationale (scoped to `--project` when given), and the paper's full
  text. Pieces are searched in that order, so a snippet quotes an extracted claim
  in preference to raw full text whenever both match. Metadata and claims are matched in
  memory; **full text is served by refmgr's `chunks_fts` index** (bm25, one query per
  term intersected so document-level AND still means AND), falling back to reading every
  snapshot body via `store.py`'s `global_read_snapshot` when no index is available. No persisted search index — it scans the pool at query
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
python3 scripts/embeddings.py query   --repo <path> --text "<question>" [--k N] [--model <name>]
```

`similar` starts from a paper already in the registry; `query` starts from arbitrary text —
a question, a paragraph, an abstract — which is what asking the library something requires.
Both compare only vectors produced by the same model: a ranking across two embedding spaces
is meaningless, so `query` refuses (naming the models it found) rather than returning one.
`--model` is therefore only needed when the cache mixes models.

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
need it once `embeddings.jsonl` already exists. `query` does need it — it has to embed the
question before it can rank anything.

## Asking the library a question (`ask.py`)

```bash
python3 scripts/ask.py retrieve --repo <path> --question "<question>" \
  [--k N] [--passages-per-paper N] [--project <slug>] [--no-semantic]
```

The third answering path in this skill, alongside the full pipeline and
`paper.py summarize`: it answers *from what the repo already holds*, with no run, no
network, and no new extraction. It cannot find a paper the repo has never seen — when
the library is thin, the honest answer is that the library is thin, and the pipeline is
what goes looking.

**The script retrieves and verifies; it does not write prose.** It returns candidate
claims and passages, each with its `(source_id, start, end)` span and the result of
re-verifying that span against the snapshot store; `commands/ask.md` is the contract the
agent follows when turning that into an answer. No model is called anywhere in the
script — that split is why the evidence bundle is auditable on its own.

Retrieval is hybrid and fused by reciprocal rank:

| Leg | Source | When it is skipped |
|---|---|---|
| lexical | `chunks_fts` bm25 over full-text snapshots | no chunk index — reported in `notes` |
| semantic | `embeddings.jsonl` vectors, queried with the question | `--no-semantic`, no embeddings file, mixed models, or `sentence-transformers` not installed |

Both legs are optional in the sense that missing one degrades the result and says so;
neither is an error. Ranking uses rank positions, not raw scores: bm25 and cosine are
not on a comparable scale and normalizing them would invent a relationship.

**Verification is the point.** Every span goes back through `store.py`'s `verify_span`
(`references/evidence-kernel.md`) at retrieval time. A span whose snapshot no longer
hashes, whose offsets no longer fit, or whose text no longer matches is moved to
`unverified` with its `reason_code` and must not be cited. This is the same gate the
assembler applies to a run, pointed at a retrieval result instead of a corpus.

## Health checks (`registry.py doctor`)

```bash
python3 scripts/registry.py doctor --repo <path> [--deep]
```

Read-only. Content-addressed storage fails quietly — a PDF deleted out from under the
database stays invisible until an export or a reader tries to open it — so this looks for
that on purpose: missing asset files, assets whose bytes no longer hash to their name,
attachments pointing at absent assets or papers, orphan assets, and stale/orphaned index
rows.

Two distinctions the output depends on:

- **Exit code 1 means data loss, not staleness.** Missing files, corrupt assets and
  dangling attachments make the report unhealthy; un-indexed papers and orphan index rows
  do not, because `registry.py reindex` fixes those and nothing is at risk. A library with
  no PDFs attached yet is healthy.
- **`--deep` re-hashes every asset**; the default trusts a matching file size and only
  hashes what already looks wrong. A shallow clean result means "nothing obviously wrong",
  not "every byte verified" — which is the trade that makes it cheap enough to run often.

Nothing here repairs anything. A missing or corrupt original cannot be rebuilt from the
registry (originals are immutable by design), so the response is a restore or a
re-import, and that is the user's call, not the script's.

## Saved searches and alerts (`alerts.py`)

```bash
python3 scripts/alerts.py save   --repo <path> --name <name> --query "<pubmed query>" \
  [--filters-json '<json>'] [--force]
python3 scripts/alerts.py list   --repo <path>
python3 scripts/alerts.py delete --repo <path> --name <name>
python3 scripts/alerts.py run    --repo <path> [--name <name>] [--since YYYY/MM/DD] \
  [--retmax N] [--register] [--dry-run]
```

Storage is refmgr's existing `saved_searches` table — no new file. Each row's
`query_json` holds `{kind: "pubmed", query, filters, last_run}`; `last_run` is the
script's own bookkeeping (when it ran, what floor it used, how many were new).

Three things are worth being precise about:

- **Entry date, not publication date.** A rerun appends
  `AND ("<since>"[edat] : "3000"[edat])` — records *entered into PubMed* since the search
  last ran. A 2019 paper indexed last week is new to you and is reported as such.
  `--filters-json`'s `years` still means publication date, as everywhere else.
- **"New" means absent from `registry.jsonl`**, checked against both the `pmid` field and
  `pmid:` evidence_ids. Without `--register` nothing is added, so the same papers appear
  again on the next run from a later floor; `--register` adds them (through
  `Registry.commit`, so they are mirrored and indexed like any other record) and moves
  the baseline.
- **No daemon.** `run` is a command the user or their own cron invokes. Nothing here
  schedules itself, and the run is not recorded in a run's query log — the query log is
  run-scoped (`corpus.py query-register`) and an alert has no run.

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
4. Calls `okf.py promote` unmodified except for a narrow exemption — a registry-only export
   has no real screening/PRISMA history, so pipeline-completeness checks
   `C-SEARCH-LOG`/`C-PRISMA` are expected to fail. Rather than a blanket `--force`, only
   those two check_ids are passed via `--exempt-check`; every other verifier check (evidence
   identity, span integrity, source/citation consistency, evidence-kernel tamper checks) stays
   enforced, so a real failure there still blocks promotion. Every concept this promotes lands
   with `status: provisional`, never `stable`. Concept-level correctness (V1-V25) is still
   enforced, unforced.
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
