-- Chunk-level full-text index over snapshot bodies (OPTIMIZATION_PLAN.md item 5).
--
-- Like papers_fts, this is a REBUILDABLE DERIVED INDEX, never a source of truth:
-- the snapshots in the global source store (`data/sources/snapshots/`) remain
-- canonical, and `registry.py reindex --chunks` rebuilds every row here from them.
--
-- `start_char`/`end_char` are offsets into the snapshot's `text`, so a chunk row is
-- directly usable as a schema.md §12 claim span -- a retrieval hit can be re-verified
-- by `store.py verify_span` without any further bookkeeping. Chunk length is capped
-- below `store.MAX_SPAN_CHARS` for exactly that reason (see chunks.py CHUNK_CHARS).
--
-- `snapshot_content_hash` is what makes reindexing cheap and staleness detectable:
-- a snapshot whose hash still matches the indexed rows needs no work.

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    snapshot_content_hash TEXT NOT NULL,
    start_char INTEGER NOT NULL,
    end_char INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_paper_id ON chunks(paper_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source_id ON chunks(source_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    paper_id UNINDEXED,
    text,
    tokenize = 'porter unicode61'
);
