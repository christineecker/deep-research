-- Normalized controlled-vocabulary terms per paper (OPTIMIZATION_PLAN.md item 9).
--
-- MeSH headings, keywords, article types and authors already travel with every
-- registry record, but they were only ever stored inside `papers.metadata_json`,
-- where they can be read back but not filtered on without scanning every row and
-- parsing every blob. This table is the facet index over them.
--
-- Like papers_fts and chunks_fts, it is DERIVED AND REBUILDABLE from
-- `registry.jsonl` (`registry.py reindex`); `papers.metadata_json` remains the
-- stored form. `value` keeps the original casing for display; `value_norm` is
-- casefolded and whitespace-collapsed, and is what queries match on, so
-- "Adolescent" and "adolescent" are one facet.

CREATE TABLE IF NOT EXISTS paper_terms (
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    scheme TEXT NOT NULL,          -- mesh | keyword | article_type | author
    value TEXT NOT NULL,
    value_norm TEXT NOT NULL,
    PRIMARY KEY (paper_id, scheme, value_norm)
);

CREATE INDEX IF NOT EXISTS idx_paper_terms_lookup ON paper_terms(scheme, value_norm);
