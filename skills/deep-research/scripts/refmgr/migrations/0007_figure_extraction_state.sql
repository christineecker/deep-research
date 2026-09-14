-- Processing state for figure extraction, one row per source PDF attachment
-- (hardening plan package 7: "restartable figure processing").
--
-- Figures are NOT a cheaply rebuildable index like chunks/papers_fts (see
-- migrations/0005_figures.sql): re-deriving them re-runs poppler over the PDF, a slow
-- heuristic pass. Before this table existed, "has any figure row for this attachment"
-- was the only signal of progress -- which cannot tell a complete extraction from one
-- interrupted halfway, cannot tell "genuinely zero figures" from "never attempted", and
-- cannot tell a stale DPI/extractor-version config from the current one. This table
-- makes all three distinctions explicit instead of inferring them from figures rows.
--
-- `status`: 'running' (an attempt is in flight, or died without completing -- the two
-- are indistinguishable from this row alone, and both must be retried, not skipped),
-- 'complete' (the recorded `figure_count` -- zero is a valid, final answer -- reflects
-- exactly `source_asset_sha256`/`extractor`/`options_json`), or 'failed' (the last
-- attempt raised; `error` carries why).
--
-- A source asset hash or extractor or options change is a DIFFERENT logical piece of
-- work, not a continuation of the old one: matching on all three together is what lets
-- a caller skip only a truly-finished, still-current configuration and rerun anything
-- else (hardening plan: "skip only matching completed configurations").

CREATE TABLE IF NOT EXISTS figure_extraction_state (
    source_attachment_id TEXT PRIMARY KEY REFERENCES attachments(id),
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    source_asset_sha256 TEXT NOT NULL,
    extractor TEXT NOT NULL,
    options_json TEXT NOT NULL,
    status TEXT NOT NULL,
    figure_count INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_figure_extraction_state_paper
    ON figure_extraction_state(paper_id);
