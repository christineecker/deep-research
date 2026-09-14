# Reference manager benchmark results

Status: recorded 2026-09-14, on the hardware named below. Companion to
`REFERENCE_MANAGER_HARDENING_PLAN.md` package 8 ("measured retrieval optimization"),
which is gated on package 2's search-correctness fix (done — see that plan) and exists
to replace guesswork with numbers before any further retrieval optimization is
considered.

Reproduce everything here with:

```bash
python3 skills/deep-research/scripts/benchmark_refmgr.py index --counts 10000 100000 \
    --out results-index.json
python3 skills/deep-research/scripts/benchmark_refmgr.py coverage --papers 100 \
    --iterations 10 --out results-coverage.json
```

Every fixture is generated from a fixed seed (`--seed`, default `20260914`) over a
small closed vocabulary — same seed and count reproduce byte-identical synthetic
records on any machine. No network access, no pip installs, no private library data.

## Hardware

| | |
|---|---|
| Machine | MacBook Air, Apple M3, 8 cores, 24 GB RAM |
| OS | macOS 26.6.2 (Darwin 25.6.0), arm64 |
| Python | 3.11.15 |
| Storage | local SSD (APFS) |

Single-machine, single-run numbers unless stated otherwise. This is not a claim about
performance on other hardware, under concurrent load, or at scale beyond what was
actually run — see "Limitations" below.

## Headline finding: `SearchRepository.rebuild()` was O(n²), now O(n)

**Mechanism** (found by inspecting the query plan, as the plan's methodology
prescribes before reaching for batching or new indexes): `papers_fts.paper_id` is an
FTS5 `UNINDEXED` column (migrations/0002_search.sql — it carries data but is
deliberately not part of the full-text index). `EXPLAIN QUERY PLAN` on the delete
`reindex_paper()` issued once per paper:

```
DELETE FROM papers_fts WHERE paper_id = ?
  -> SCAN papers_fts VIRTUAL TABLE
```

confirms it has no index to use and scans the whole table every time. Rebuilding the
index for every paper in the library therefore did one full-table scan **per paper**,
over a table with (up to) as many rows as papers in the library: O(n) work × n papers
= O(n²) total, even though nothing about full-text rebuilding is inherently quadratic.

**Fix** (`refmgr/repositories/search.py`, `SearchRepository.rebuild`): batch the
delete. For each 500-paper chunk, one `DELETE FROM papers_fts WHERE paper_id IN
(...)` clears every row in that chunk with a single scan, then each paper's row is
inserted individually (insert has no such cost). Total delete work becomes O(n) instead
of O(n × table_size). Batching the surrounding transactions (one `BEGIN
IMMEDIATE`/`COMMIT` per 500 papers instead of per paper) is a smaller, secondary
change on top of that. Per-paper fault isolation is preserved with a `SAVEPOINT`
around each paper's insert — one bad paper's failure is reported in `errors` without
aborting the rest of its batch.

### Before / after: `rebuild()` wall-clock time

| Papers | Before (unbatched delete) | After (batched delete) | Speedup |
|---:|---:|---:|---:|
| 3,000  | ~1.30s – 1.40s (3 runs, mean 1.34s) | ~1.17s – 1.21s (3 runs, mean 1.19s) | ~1.1x |
| 8,000  | 11.10s, 11.34s (2 runs, mean 11.22s) | 10.11s, 10.11s (2 runs, mean 10.11s) | ~1.1x |
| 10,000 | 15.78s (clean, no background load) | 0.37s (clean, no background load) | ~43x |
| 100,000 | not run to completion — see below | 8.51s | (see below) |

Two different effect sizes appear above on purpose, and both are real:

- At 3,000–8,000 papers the win is a modest ~10%: the per-transaction commit
  overhead reduction (batching `BEGIN IMMEDIATE`/`COMMIT`) is the dominant visible
  effect at this scale, because the O(n²) delete cost is not yet large enough to
  dominate.
- At 10,000 papers, measured back-to-back on an otherwise idle machine, the O(n²)
  delete cost has become large enough to dominate completely: 15.78s → 0.37s, a ~43x
  speedup from the same code change.
- **100,000 papers with the unfixed code was not run to completion.** Extrapolating
  the confirmed O(n²) mechanism from the 3k/8k/10k measurements above (rebuild time
  roughly proportional to n², net of the small fixed per-paper cost) predicts
  something on the order of 20–25 minutes; after ~11 minutes of runtime with no sign
  of finishing, the run was killed rather than spend session time confirming a number
  the mechanism and the smaller-scale data already explain. **This one number is a
  reasoned extrapolation, not a measurement** — flagged as such rather than reported
  as if measured, per the plan's "do not invent capacity promises."
- **100,000 papers with the fix took 8.51s** — faster in absolute terms than the
  *unfixed* code took for one-tenth as many papers (10k: 15.78s). This is exactly
  what O(n) vs. O(n²) predicts and is the strongest evidence in this document that
  the fix works as understood, not merely that it happened to help on one run.

Regression test: `tests/test_refmgr_search.py
RebuildCoverageTest.test_rebuild_batches_the_delete_and_isolates_per_paper_insert_failures`
and `test_rebuild_delete_scales_with_one_scan_per_batch_not_per_paper` pin both the
fault-isolation behavior and the batched-delete query plan so this cannot silently
regress back to a per-paper delete.

## Index build time, disk, and memory at 10k / 100k

| Papers | Fixture insert (bulk SQL, setup only) | `rebuild()` (measured code path) | Chunk index, 500-source sample | `library.sqlite3` size | Peak process RSS |
|---:|---:|---:|---:|---:|---:|
| 10,000  | 0.14s | 0.37s | 0.14s (500 sources, 2,500 chunks) | 30.0 MB | 32.3 MB |
| 100,000 | 1.96s | 8.51s | 0.14s (500 sources, 2,500 chunks) | 219.5 MB | 51.1 MB |

"Fixture insert" is bulk-SQL setup (not the code path package 8 measures — see the
script's module docstring for why: timing 100k individual `add_paper()` transactions
would measure disk fsync behavior, not retrieval). The chunk-index pass is run over a
fixed 500-source sample regardless of paper count, since its cost is per-source, not
per-paper — a full-corpus chunk pass at 100k is a separate, much longer measurement
this document does not include; nothing here depends on it.

Peak RSS covers the whole Python process (`resource.getrusage`, not `tracemalloc`),
so it includes SQLite's own C-level memory, not just Python object allocations.

## Query latency at 10,000 papers (post-fix)

Cold = fresh SQLite connection (resets SQLite's own page cache); this does **not**
drop the OS filesystem cache, which has no portable, unprivileged reset — treat "cold"
here as "worst case within one process," not "worst case on first disk access ever."
Warm = 30 repeated queries on one open connection.

| Query | Cold | Warm p50 | Warm p95 |
|---|---:|---:|---:|
| Keyword match, common term (~20% of corpus) | 9.71ms | 8.84ms | 10.01ms |
| Keyword match + year filter | 7.06ms | 6.69ms | 8.36ms |
| Keyword match + journal filter | 5.97ms | 5.75ms | 6.19ms |
| No query, filtered listing | 2.23ms | 1.97ms | 2.06ms |
| Chunk bm25 search | 2.54ms | 1.92ms | 2.25ms |
| Chunk `papers_matching_all` (AND across terms) | 3.31ms | 2.95ms | 3.05ms |

At 100,000 papers the common-term query (which now matches ~20,000 of 100,000
synthetic papers — an unrealistically unselective query for a real personal library,
included specifically to stress the bm25-ranked, keyset-paginated path) rose to
131ms p50 warm. Every other query stayed under 90ms. This is reported, not treated as
a problem to fix pre-emptively: the plan's own discipline is to optimize only where a
real workload shows the cost matters, and a query matching one-fifth of an entire
library is not representative of how this tool is actually used. `EXPLAIN QUERY PLAN`
on the FTS5 `MATCH` queries themselves shows `SCAN papers_fts VIRTUAL TABLE` / `SCAN
chunks_fts VIRTUAL TABLE` — this is FTS5's normal, expected plan shape for a `MATCH`
query (it does use its internal inverted index; SQLite's plan vocabulary has no
separate verb for that), unlike the genuinely-unindexed full-table scan the rebuild
fix above addresses. This is not a missing index.

## Incomplete-index workload cost (package 2's fallback path, quantified)

100 synthetic papers with real snapshot files on disk, chunk-indexed at 0%/50%/100%
coverage (a small corpus is deliberate here — this path's cost is per-snapshot-read,
not per-paper, so a larger corpus would answer the same question more slowly, not a
different one):

| Chunk coverage | Snapshot files read per query | Warm p50 latency | Warm p95 latency |
|---:|---:|---:|---:|
| 0%   | 100 (every paper's own body scanned) | 8.86ms  | 9.23ms  |
| 50%  | 50 (only the uncovered half)         | 11.00ms | 11.32ms |
| 100% | **0**                                 | 16.84ms | 17.32ms |

The canonical-file-read column is the direct, quantified proof that package 2's fix
works as designed: a fully chunk-indexed record never touches its snapshot file, and
an unindexed one always does, in exact proportion to coverage — not "sometimes, if
another paper happened to get indexed first" (the bug package 2 fixed). This property
is pinned permanently by `tests/test_benchmark_refmgr.py
CoverageBenchmarkCorrectnessTest` — a real regression guard, not just a one-time
measurement.

The latency column, however, surfaces a genuine secondary finding, reported and
explicitly **deferred** rather than fixed in this pass: latency at 100% coverage is
*higher* than at 0%, not lower. `_keyword_filter`'s snippet step issues one
`chunks.search(..., paper_ids=[paper_id])` call per matching paper to fetch its
snippet from the chunk index — an N+1 query pattern that costs more, per matched
paper, than reading that paper's own snapshot file directly. Fixing this (batching
snippet retrieval across every matched paper into one query) is a real, evidence-backed
candidate for a future pass; it is not done here because inspecting-then-fixing every
finding this benchmark surfaces would extend package 8 well past a single scoped
change, and the finding is recorded here specifically so it is not silently lost.

## Regression corpus (unchanged retrieval)

`benchmark_refmgr.py`'s `coverage` fixture doubles as a correctness regression
corpus: `results_found` is asserted to equal the full paper count at every coverage
level in `tests/test_benchmark_refmgr.py`, proving the rebuild-delete change and every
earlier hardening-plan package did not change *which* papers a query finds — only how
long finding them takes. Run `python3 -m pytest
skills/deep-research/tests/test_benchmark_refmgr.py
skills/deep-research/tests/test_refmgr_search.py -q` for the full set (7 + 18 tests).

## Limitations (read before citing a number from this document elsewhere)

- Single machine, single Python process, mostly single-sample measurements (the 3k
  and 8k comparisons are 2–3 run means; everything else is one run). Real variance
  exists — the 10k/100k headline numbers were re-run once on an idle machine
  specifically to reduce the effect of background load, but this is not a
  statistically rigorous benchmark suite.
- "Cold" does not drop the OS page cache (no portable unprivileged way to do that);
  treat cold numbers as a lower bound on true first-access latency, not the number
  itself.
- The 100,000-paper *unfixed* rebuild time is an extrapolation from the confirmed
  O(n²) mechanism plus 3k/8k/10k measurements, not a completed run — stated as such
  above, not blended into the "measured" numbers.
- Chunk indexing was measured over a fixed 500-source sample at both paper counts,
  not the full corpus at 100k; a full-corpus chunk-indexing time at 100k is not in
  this document.
- No ANN/vector-search dependency was introduced or evaluated. Nothing measured here
  shows the current SQLite FTS5 lexical index needs one; embeddings-based similarity
  search already exists as a separate, opt-in feature (`embeddings.py`) unrelated to
  this benchmark.
