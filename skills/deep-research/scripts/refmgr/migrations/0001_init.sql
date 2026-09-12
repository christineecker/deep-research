-- Phase 1 foundation schema: papers, identifiers, assets, attachments,
-- organization (collections/tags), audit log, and merge records.
-- Entities deferred to later phases (annotations, page derivatives,
-- embeddings, jobs) are intentionally absent.

CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    paper_type TEXT NOT NULL,
    citation_key TEXT UNIQUE,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    provenance TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_papers_deleted_at ON papers(deleted_at);

CREATE TABLE IF NOT EXISTS identifiers (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id),
    scheme TEXT NOT NULL,
    value TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(scheme, value)
);

CREATE INDEX IF NOT EXISTS idx_identifiers_paper_id ON identifiers(paper_id);

CREATE TABLE IF NOT EXISTS assets (
    sha256 TEXT PRIMARY KEY,
    byte_size INTEGER NOT NULL,
    mime_type TEXT,
    storage_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id),
    asset_sha256 TEXT NOT NULL REFERENCES assets(sha256),
    role TEXT NOT NULL,
    original_filename TEXT,
    provenance TEXT,
    version_label TEXT,
    page_count INTEGER,
    is_preferred_reader INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_attachments_paper_id ON attachments(paper_id);
CREATE INDEX IF NOT EXISTS idx_attachments_asset_sha256 ON attachments(asset_sha256);

CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'collection',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS collection_members (
    collection_id TEXT NOT NULL REFERENCES collections(id),
    paper_id TEXT NOT NULL REFERENCES papers(id),
    added_at TEXT NOT NULL,
    PRIMARY KEY (collection_id, paper_id)
);

CREATE TABLE IF NOT EXISTS tags (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS paper_tags (
    paper_id TEXT NOT NULL REFERENCES papers(id),
    tag_id TEXT NOT NULL REFERENCES tags(id),
    PRIMARY KEY (paper_id, tag_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    reversible INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_entity ON audit_log(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS merges (
    id TEXT PRIMARY KEY,
    survivor_paper_id TEXT NOT NULL REFERENCES papers(id),
    absorbed_paper_id TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reverted_at TEXT
);
