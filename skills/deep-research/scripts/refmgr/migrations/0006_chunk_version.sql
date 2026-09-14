-- Tracks which chunker version produced each row, on top of the existing
-- content-hash staleness check (migrations/0003_chunks.sql).
--
-- `snapshot_content_hash` alone answers "has this source's text changed
-- since it was indexed" -- but chunks.py's own splitting logic (CHUNK_CHARS,
-- CHUNK_OVERLAP, the boundary search) can change too, producing different
-- spans over the SAME unchanged text. Without a version marker, a chunker
-- upgrade would leave old-shaped rows in place forever, silently reported as
-- "current" coverage even though they no longer match what the current code
-- would produce. `chunker_version` closes that: a row only counts as
-- covering a source when both its content hash AND its chunker version
-- match what search is about to trust it for (see chunks.py CHUNKER_VERSION).
--
-- DEFAULT 1 backfills every already-indexed row as version 1, matching the
-- CHUNKER_VERSION this migration ships alongside -- no existing row becomes
-- spuriously stale on upgrade.

ALTER TABLE chunks ADD COLUMN chunker_version INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_chunks_paper_source_version
    ON chunks(paper_id, source_id, chunker_version);
