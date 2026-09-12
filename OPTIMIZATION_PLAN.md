# Optimization plan — reference manager as a question-answering library

Companion backlog to `REFERENCE_MANAGER_V2_PLAN.md`. That file describes the
storage layer v2 and what remains of its phases; this one is scoped to a
single goal:

> make the papers we already find, fetch and extract **searchable and usable
> to answer scientific questions**, without running the full eight-stage
> pipeline.

Code is the source of truth. Where this file names a file/line, read the code
rather than trusting the description here.

---

## Current shape

```
discover   eutils.py (esearch/efetch/elink) + PubMed MCP + Europe PMC
acquire    fulltext.py, 8-rung ladder (references/acquisition.md)
store      store.py — content-addressed snapshots, spans, freshness
extract    subagents -> workspace/extractions/*.json (claims + char spans)
persist    registry.jsonl (repo mode) / pool.jsonl (wiki mode)  <- biblio truth
           refmgr/*.sqlite                                      <- assets only
retrieve   registry.py search --q (linear scan) | embeddings.py similar
answer     full pipeline run, or paper.py summarize (one paper)
```

Discovery and the evidence kernel are the strong parts and are not touched by
this plan. The weak link is everything between "text is on disk" and "here is
an answer with citations": storage is split across two systems, retrieval is
an unranked linear scan, the semantic layer cannot accept a question, and
there is no entry point that answers from the existing library at all.

---

## Problems, in dependency order

### P1 — `Registry` read-modify-write race (known, unfixed)

`Registry.__init__` (`skills/deep-research/scripts/registry.py`) calls
`self._load()` before any call site takes `advisory_lock`, at every mutating
call site. Concurrent writers — pool sync, parallel subagent receipts — can
silently clobber each other. `refmgr`'s `db.transaction` does not have this
bug. Already recorded in `REFERENCE_MANAGER_V2_PLAN.md` as a known defect;
it is listed first here because everything below writes more often, not less.

### P2 — Two stores, no bridge

`refmgr` SQLite is populated only through the PDF path (`add-pdf` /
`import-folder` / `paper.py --pdf`). Papers registered by PMID or DOI never
reach `papers` / `identifiers`, so `papers_fts` is near-empty for most of the
library and `SearchRepository` / `SavedSearchRepository` are unreachable in
practice. Meanwhile the user-facing `registry.py search` does not consult
`refmgr` at all.

This is "Open decision 1" in the v2 plan. It is no longer deferrable: it is
the reason the shipped FTS5 and saved-search work has no effect on any real
query.

### P3 — Keyword search is an unranked linear scan

`registry.py`'s `_keyword_filter` / `_search_text_pieces` re-read every
record's extraction JSON and every referenced snapshot body from disk on each
query, concatenate them, and test `all(term in haystack)`. Consequences:

- cost grows with total corpus bytes per query, not with result count;
- substring matching, so `cell` matches `excellent`;
- no ranking, no phrase or proximity, no field weighting;
- the snippet is simply the first textual hit.

FTS5 with `bm25` already exists (`refmgr/repositories/search.py`) but indexes
only title / authors / journal / abstract / identifiers — full text, page
text and notes are explicitly out of scope there.

### P4 — Extraction claims are not indexed

`_search_text_pieces` covers `population`, `intervention`, `comparator`,
`limitations`, `extractor_notes` — but not `spans[].claim` or
`outcomes[].name` (`references/schema/07-extraction.md` §7), which are the
most answer-relevant text the pipeline produces.

### P5 — Semantic layer is document-level and staleness-blind

`embeddings.py` builds one vector per paper from title + abstract + narrative
fields (never full text), stores vectors as JSON float arrays, keys freshness
on model *name* only, and exposes `similar --evidence-id` only. There is no
way to embed a free-text question — the one operation a question-answering
library needs most.

### P6 — No "answer from what I already have" path

Every answering route is either the gated eight-stage run or a single-paper
summary. Nothing takes a question and answers it from the pool, despite the
pool holding extracted, span-verified, appraised content.

### P7 — Saved searches never re-run

`SavedSearchRepository` stores query specs; nothing executes them. `watch.py`
is a read-only TUI for one run, not a literature monitor. A reference manager
that never tells you what is new is a batch tool.

### P8 — Flat facets

`refmgr` keeps bibliographic detail in a `metadata_json` blob, so there is no
`--mesh`, `--author` or `--article-type` facet — even though `pool.jsonl`
already carries `mesh_terms`, `keywords` and `article_types`.

### P9 — Tests never run in CI; no declared dev environment

No `pyproject.toml`, `pytest.ini` or `conftest.py`. `pytest` is not installed
in the uv-managed 3.11.15 interpreter the v2 plan names as the target, and
`__pycache__` directories exist for 3.11, 3.12 and 3.14 — three interpreters
have run this tree. `.github/workflows/pages.yml` only deploys docs. For a
~46k-line evidence system with 39 test files, this is the largest process
risk.

### P10 — No asset integrity check

Content-addressed PDFs with no orphan or missing-file detection; a lost file
surfaces first at export time. Phase 6 of the v2 plan owns the full recovery
story, but the minimal check should not wait for it.

---

## Work items

Ordered so each item is useful on its own and later items depend only on
earlier ones. Sizes are relative: XS ≈ under an hour, S ≈ half a day,
M ≈ one to two days.

### 1. Fix the `Registry` lock race — XS — **done**

Shipped as `Registry.reload()` + `Registry.locked()` in `registry.py`; every mutating
call site in `registry.py`, `paper.py`, `pool.py` and `research.py` goes through
`locked()` instead of taking `advisory_lock(..., "registry")` directly. Regression
tests: `tests/test_concurrency.py::RegistryLostUpdateTest`.

Original scope:

Load inside the lock (or re-load immediately after acquiring it) at every
mutating call site in `registry.py`. Add a concurrency test alongside
`tests/test_concurrency.py`, which already exercises the `refmgr` side.

**Done when**: two concurrent `registry.py add` processes both survive in
`registry.jsonl` under the existing concurrency-test harness.

### 2. Index extraction claims and outcome names — XS — **done**

Shipped as `_extraction_claim_texts` in `registry.py`, searched ahead of the narrative
fields and snapshot bodies so snippets prefer a claim. Tests:
`tests/test_registry_search.py::KeywordSearchTest`.

Original scope:

Extend `_search_text_pieces` to include `spans[].claim` and
`outcomes[].name` / `outcomes[].effect_measure` from the extraction record.
Order them ahead of snapshot bodies so snippets prefer them.

**Done when**: a query matching only a claim sentence returns that paper with
the claim as its snippet.

### 3. `embeddings.py query --text` — S — **done**

Shipped as `embeddings.py query`, with `_cached_model` refusing cross-model rankings
rather than returning a meaningless one. Tests:
`tests/test_embeddings.py::{CachedModelSelectionTest,QueryCommandTest}`.

Original scope:

Add a subcommand that embeds free text and ranks the existing vectors against
it, reusing `cosine_similarity` and the `similar` output shape. Same lazy
`sentence-transformers` import, same documented carve-out — no new dependency
and no change to the policy in `references/acquisition.md` §9.

**Done when**: `embeddings.py query --repo <p> --text "<question>" -k 10`
returns ranked evidence_ids, and `/deep-research:embed` documents it.

### 4. Mirror registry writes into `refmgr` — M — **done**

Shipped as `Registry.mirror_to_refmgr` + `Registry.commit` (`registry.py`), which every
mutating command now calls in place of `save()` + `generate_pool()`, plus
`registry.py reindex` for backfill and repair. `PaperRepository.update_title` was added
so a corrected title reaches `papers_fts`. Resolved as **mirror**: `registry.jsonl`
stays truth, refmgr is the index. Tests: `tests/test_registry_mirror.py`.

Original scope:

Route every `Registry` mutation that creates or updates a paper through
`refmgr`'s `papers` / `identifiers` repositories, reusing the helpers already
present in `registry.py` (`_refmgr_service`, `_refmgr_identifiers`,
`_refmgr_paper_id`) rather than adding a second mapping. `registry.jsonl`
stays the durable append log and export format; `refmgr` becomes the index.

Backfill: a one-shot `registry.py reindex --repo <p>` that walks
`registry.jsonl` and populates `refmgr` + `papers_fts` for an existing repo.

Resolves "Open decision 1" in the v2 plan in favour of *mirror*, not cutover —
no existing consumer of `registry.jsonl` has to change.

**Done when**: a paper added by PMID is findable via
`SearchRepository.search`, and `reindex` is idempotent over a repo built
before this change.

### 5. `chunks_fts` — full text, ranked — M — **done**

Shipped as `migrations/0003_chunks.sql` + `refmgr/repositories/chunks.py`, wired into
`registry.py search --q` through `_chunk_index_hits`. Two query paths, deliberately
different: `search()` matches any term and ranks by bm25 (what a question needs),
`papers_matching_all()` intersects per-term hits to preserve `--q`'s document-level AND.
No index, an empty one, or a corrupt file falls back to the old snapshot scan with a
note on stderr. Tests: `tests/test_refmgr_chunks.py`,
`tests/test_registry_mirror.py::SearchThroughTheChunkIndexTest`.

Original scope:

New migration adding a chunk index over snapshot text:

```
chunks(paper_id, source_id, start, end, text)     -- offsets into the snapshot
chunks_fts                                        -- FTS5 over chunks.text
```

Rebuildable from snapshots, never a source of truth — the same contract
`papers_fts` already states. Chunk boundaries carry snapshot char offsets, so
every hit is already a span the evidence kernel can verify; that is the point
of storing offsets rather than chunk-local text alone.

Route `registry.py search --q` through `chunks_fts` when `refmgr` has the
paper, falling back to the existing linear scan otherwise, so the command
keeps working on un-mirrored repos.

**Done when**: `--q` returns bm25-ranked results with snapshot offsets, and a
corpus-scale fixture shows query cost independent of total corpus bytes.

### 6. `ask` — answer from the library — M — **done**

Shipped as `scripts/ask.py retrieve` + `commands/ask.md`. One design point worth
recording, because it differs from the sketch below: the **script retrieves and
verifies, the agent writes the answer**. `ask.py` calls no model — it returns a bundle
of claims and passages with their spans re-verified through `store.py`'s `verify_span`,
and `commands/ask.md` carries the answering contract (cite by `evidence_id`, quote the
verified bytes, never cite `unverified[]`, say "nothing here" rather than answering from
memory). That keeps it consistent with the rest of the skill, where scripts do state and
network work and the agent does the reasoning. Tests: `tests/test_ask.py`.

Original scope:

New command, the actual deliverable of this plan:

```
ask --repo <path> "does X reduce Y in Z?"
  1. hybrid retrieve: chunks_fts bm25  ∪  embeddings top-k,
     fused by reciprocal rank
  2. group hits by evidence_id, carry (source_id, start, end)
  3. answer restricted to the retrieved spans
  4. re-verify every cited span through store.py's excerpt-hash check
  5. refuse any claim that carries no verified span
```

No new trust machinery: steps 4 and 5 are the assembler gate described in
`references/evidence-kernel.md`, applied to a retrieval result instead of a
run corpus. Output carries the same citation shape as the pipeline's, so an
`ask` answer can be promoted into a run or a wiki bundle later.

Deliberately out of scope: appraisal, GRADE, synthesis across conflicting
results. `ask` reports what the library says and who says it; the pipeline
remains the only thing that adjudicates.

**Done when**: `ask` answers from a seeded fixture repo with every sentence
carrying a verified span, and refuses (visibly) when retrieval returns
nothing relevant.

### 7. `search-rerun` — new-hits alerting — S — **done**

Shipped as `scripts/alerts.py` (`save`/`list`/`delete`/`run`) + `commands/alerts.md`,
storing specs in refmgr's existing `saved_searches` table. Two deviations from the
sketch below, both deliberate:

- **Entry date, not `mindate`/`reldate`.** A rerun appends
  `AND ("<since>"[edat] : "3000"[edat])` to the saved query. The alert question is "what
  is new *to me*", which is an indexing event, not a publication date.
- **Not registered in the query log.** `corpus.py query-register` is run-scoped and an
  alert has no run, so the baseline lives in the saved search's own `last_run` state
  instead. Forcing a synthetic run directory just to log a rerun would have been
  bookkeeping for its own sake.

Tests: `tests/test_alerts.py` (network patched, never live).

Original scope:

Execute stored saved searches: esearch with `mindate` / `reldate` plus the
normalized query hash the query log already keeps (`corpus.py query-register`
/ `query-check`), diff against the registry, report new PMIDs. No polling
daemon; one command the user or a cron runs.

**Done when**: re-running a saved search over a fixture reports only records
absent from the registry, and registers the query in the log like any other.

### 8. Test infrastructure and CI — S — **done**

Shipped as `pyproject.toml` (test configuration only — no `[project]` table, because
this repo is a plugin and not an installable package), `requirements-dev.txt`, and
`.github/workflows/tests.yml` running the suite on push and pull request against
Python 3.11. `pytest` now needs no arguments from the repo root; verified from a clean
copy with a fresh environment. `sentence-transformers` stays out of CI — the tests that
need it skip rather than pulling a torch stack into every run.

Original scope:

- `pyproject.toml` (or a minimal `requirements-dev.txt`) declaring `pytest`
  and the interpreter, matching the uv-managed 3.11.15 target the v2 plan
  names.
- `pytest.ini` / `[tool.pytest]` with `testpaths` set, so the suite runs from
  the repo root without arguments.
- A GitHub Actions job running the suite on push and PR, alongside the
  existing docs deploy.

**Done when**: a clean checkout runs the full suite with two documented
commands, and CI fails on a failing test.

### 9. Term and facet tables — M — **done**

Shipped as `migrations/0004_terms.sql` + `refmgr/repositories/terms.py`, populated by the
same mirror as everything else, with `--mesh`/`--author`/`--article-type` on
`registry.py search` and a `registry.py facets` command for "what is actually in this
library". Matching is case-insensitive substring on a normalized value — MeSH headings
are long and people type fragments — and falls back to the record's own metadata lists
when the index is absent, with identical results. Tests: `tests/test_refmgr_terms.py`.

Original scope:

Normalized `paper_terms(paper_id, scheme, value)` for MeSH terms, keywords,
article types and authors, populated from the same source as the mirror in
item 4. Adds `--mesh`, `--author`, `--article-type` facets to
`registry.py search` and makes hedge recall auditable against what was
actually indexed.

### 10. `refmgr doctor` — S — **done**

Shipped as `refmgr/doctor.py` + `registry.py doctor` + `commands/doctor.md`, read-only as
planned. Two decisions worth recording:

- **Exit code distinguishes loss from staleness.** Missing files, corrupt assets and
  dangling attachments exit 1; un-indexed papers and orphan index rows do not, since
  `reindex` fixes those. That makes it usable from cron without crying wolf over a
  library that simply has no PDFs yet.
- **`--deep` is opt-in.** The default trusts a matching file size and hashes only what
  already looks wrong, so it stays cheap enough to run often; a shallow clean result is
  reported as "nothing obviously wrong", not "verified".

Tests: `tests/test_refmgr_doctor.py`, including a read-only assertion over file mtimes.

Original scope:

Minimal integrity check pulled out of Phase 6: verify every attachment's
recorded hash against the file on disk, list assets with no paper and papers
with a missing asset. Read-only, no repair — reporting first.

---

## Sequencing

| Step | Items | Rationale |
|---|---|---|
| ~~Now~~ done | 1, 2, 3 | Independent, small, each immediately useful |
| ~~Next~~ done | 4, 5, 6 | One arc: make `refmgr` the index, index the text, answer from the index |
| ~~Now~~ done | 7, 8 | Ongoing use, and the CI that keeps 1–7 honest |
| ~~Later~~ done | 9, 10 | Polish and integrity |

Every item in this plan is now shipped. What remains is in "Open questions" below, plus
the paused Phase 4 scope in `REFERENCE_MANAGER_V2_PLAN.md`, which this plan deliberately
did not restart.

Item 8 is placed after the first arc only because the arc is what makes the
suite worth gating on; moving it first is a defensible reordering.

## Explicitly not in scope

- Anything in the paused Phase 4 scope of `REFERENCE_MANAGER_V2_PLAN.md`:
  ANN retrieval benchmarking, bounded OCR/indexing workers, the 10k/100k
  latency benchmark. The corpus is nowhere near the size that justifies them,
  and items 4–6 change what would be benchmarked anyway.
- Everything the v2 plan puts permanently out of scope: a browser library UI,
  an embedded PDF reader, in-app annotation, any local HTTP service. ReadCube
  remains the human library UI; `ask` is a CLI capability, not a front end.
- New third-party dependencies. `sentence-transformers` stays the single
  documented exception, and item 3 does not widen it.

## Open questions

1. **Chunking strategy for item 5** — *settled for now*: fixed-size (1200 chars) with
   150-char overlap, cut at a paragraph or sentence boundary when one falls within 300
   chars of the target. Section-aware chunking off JATS structure would be better for
   `ask` but only helps papers that arrived via ladder rung 3. Revisit once `ask` has
   real queries to evaluate against.
2. **Vector storage** — still open, and now the main scaling limit of `ask`'s semantic
   leg: vectors remain JSON float arrays in `embeddings.jsonl`, loaded and scored in
   pure Python on every query. Moving them to float32 BLOBs in `refmgr` would cut size
   roughly tenfold and load time more, still with no numpy. Deferred because it changes
   `embeddings.py`'s file contract; item 5's migration shipped without it.
3. **Wiki mode parity** — *settled*: items 4–6 are repo-mode only. Wiki mode's
   `pool.jsonl` has no refmgr database, so `search --q` there still scans snapshots and
   `ask.py` requires `--repo`. Mirroring the wiki pool into a wiki-local refmgr database
   remains possible; nothing in items 4–6 forecloses it.
4. **Semantic retrieval is still paper-level** (new). The chunk index gives `ask` passage
   granularity on the lexical leg, but the semantic leg still ranks whole papers from a
   single title+abstract+narrative vector. Chunk-level embeddings — the rest of the
   paused Phase 4 embedding rework — would make the two legs symmetric. Not scheduled.
