-- Phase 4 foundation: an FTS5 lexical index over paper metadata/identifiers
-- (rebuildable, not a source of truth -- papers/identifiers remain canonical)
-- and a small saved-searches table (plan's Organization entity: "Collections,
-- membership, tags, ratings, and saved searches" -- ratings intentionally
-- absent here, deferred with the rest of the Annotation entity per
-- REFERENCE_MANAGER_V2_PLAN.md's open "Remaining decisions").

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
    paper_id UNINDEXED,
    title,
    authors,
    journal,
    abstract,
    identifiers,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS saved_searches (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    query_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
