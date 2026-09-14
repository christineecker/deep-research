-- Figures cropped out of an attachment's PDF, with their captions.
--
-- The image bytes live where every other blob lives: staged into `assets` and
-- linked by an `attachments` row with role='figure'. This table is the layer
-- above that -- what the crop MEANS. It answers "which figure of which paper is
-- this, what does its caption say, and which PDF did it come from", none of
-- which an attachment row can express.
--
-- Captions are indexed separately from `chunks_fts` on purpose. A chunk row is a
-- verifiable claim span: `(source_id, start_char, end_char)` into a snapshot's
-- text, which `store.py verify_span` can re-check. A caption read out of a PDF's
-- layout has no such span -- it is not a substring of any snapshot at a known
-- offset -- so filing captions as chunks would put unverifiable rows into an
-- index whose whole contract is that its rows verify. figures_fts keeps caption
-- retrieval without weakening that.
--
-- Unlike chunks and papers_fts, this is NOT a cheaply rebuildable index: the
-- crops are derived from PDFs by a heuristic (see library.py "figures"), so a
-- rebuild re-runs poppler over every asset rather than re-reading canonical
-- text. `source_attachment_id` plus `extractor` record what produced each row so
-- a later extractor version can be told apart from an earlier one.

CREATE TABLE IF NOT EXISTS figures (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    source_attachment_id TEXT NOT NULL REFERENCES attachments(id),
    figure_attachment_id TEXT NOT NULL REFERENCES attachments(id),
    asset_sha256 TEXT NOT NULL REFERENCES assets(sha256),
    kind TEXT NOT NULL,
    label TEXT,
    number TEXT,
    caption TEXT,
    page INTEGER,
    bbox_json TEXT,
    extractor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    -- Re-running extraction over the same PDF re-derives the same crops. The
    -- rendered bytes are deterministic, so their hash is the natural idempotency
    -- key: a second pass collides here instead of doubling every figure.
    UNIQUE(source_attachment_id, asset_sha256)
);

CREATE INDEX IF NOT EXISTS idx_figures_paper_id ON figures(paper_id);
CREATE INDEX IF NOT EXISTS idx_figures_source_attachment
    ON figures(source_attachment_id);
CREATE INDEX IF NOT EXISTS idx_figures_asset_sha256 ON figures(asset_sha256);

CREATE VIRTUAL TABLE IF NOT EXISTS figures_fts USING fts5(
    figure_id UNINDEXED,
    paper_id UNINDEXED,
    caption,
    tokenize = 'porter unicode61'
);
