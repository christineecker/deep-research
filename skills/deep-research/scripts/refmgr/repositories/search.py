"""Lexical search repository over the `papers_fts` FTS5 index.

`papers_fts` (see migrations/0002_search.sql) is a rebuildable derived index,
not a source of truth -- `papers`/`identifiers` remain canonical. This
repository works directly on `self.conn` against those tables (like its
sibling repositories do), rather than composing PaperRepository/
IdentifierRepository internally.

Notes/page-text/captions are explicitly out of scope here (blocked on the
unresolved Annotation/Page-derivative entity decision -- see
REFERENCE_MANAGER_V2_PLAN.md "Remaining decisions"). Saved searches and the
embeddings rework are separate work.

Every write (indexing) happens inside `db.transaction(conn)`.
"""

from __future__ import annotations

import base64
import json
import sqlite3

from .. import db

MAX_LIMIT = 200


class SearchQueryError(ValueError):
    """Raised when a search query cannot be safely run against `papers_fts`.

    In practice this should be unreachable: every user-supplied query term is
    wrapped in double quotes (embedded `"` doubled to `""`) before being
    handed to FTS5's MATCH, which turns any FTS5 operator/syntax characters
    ("-", "*", stray `"`, ...) into inert literal text rather than syntax.
    This is kept as a defensive fallback in case some input defeats that
    escaping in a way not anticipated here.
    """


def _escape_fts_query(query: str) -> str:
    """Turn arbitrary user text into a literal (non-operator) FTS5 MATCH string."""
    terms = query.split()
    return " ".join('"' + term.replace('"', '""') + '"' for term in terms)


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> dict:
    raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
    return json.loads(raw.decode("utf-8"))


class SearchRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- indexing --------------------------------------------------------

    def reindex_paper(self, paper_id: str) -> None:
        with db.transaction(self.conn):
            self._reindex_paper_locked(paper_id)

    def _reindex_paper_locked(self, paper_id: str) -> None:
        row = self.conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        # Always clear any existing row(s) first -- covers both the
        # re-index case and the deleted/missing case below.
        self.conn.execute("DELETE FROM papers_fts WHERE paper_id = ?", (paper_id,))
        if row is None or row["deleted_at"] is not None:
            return

        metadata_json = row["metadata_json"]
        metadata = json.loads(metadata_json) if metadata_json else {}
        title = row["title"] or ""
        authors = metadata.get("authors") or []
        if not isinstance(authors, list):
            authors = [str(authors)]
        authors_str = " ".join(str(a) for a in authors)
        journal = metadata.get("journal") or ""
        abstract = metadata.get("abstract") or ""

        identifier_rows = self.conn.execute(
            "SELECT scheme, value FROM identifiers WHERE paper_id = ?", (paper_id,)
        ).fetchall()
        identifiers_str = " ".join(
            f"{r['scheme']}:{r['value']}" for r in identifier_rows
        )

        self.conn.execute(
            "INSERT INTO papers_fts "
            "(paper_id, title, authors, journal, abstract, identifiers) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (paper_id, title, authors_str, journal, abstract, identifiers_str),
        )

    def remove_paper(self, paper_id: str) -> None:
        with db.transaction(self.conn):
            self.conn.execute("DELETE FROM papers_fts WHERE paper_id = ?", (paper_id,))

    def rebuild(self, paper_ids: list[str] | None = None) -> dict:
        if paper_ids is None:
            rows = self.conn.execute(
                "SELECT id FROM papers WHERE deleted_at IS NULL"
            ).fetchall()
            ids = [r["id"] for r in rows]
        else:
            ids = list(paper_ids)

        indexed = 0
        errors: list[dict] = []
        for paper_id in ids:
            try:
                # A nonexistent/already-deleted id just removes a (possibly
                # nonexistent) fts row -- a legitimate no-op, not an error.
                self.reindex_paper(paper_id)
                indexed += 1
            except Exception as exc:  # defensive: one bad paper must not abort the rest
                errors.append({"paper_id": paper_id, "error": str(exc)})
        return {"indexed": indexed, "errors": errors}

    def coverage(self) -> dict:
        total = self.conn.execute(
            "SELECT COUNT(*) AS c FROM papers WHERE deleted_at IS NULL"
        ).fetchone()["c"]
        indexed = self.conn.execute(
            "SELECT COUNT(*) AS c FROM ("
            "  SELECT DISTINCT papers_fts.paper_id AS pid FROM papers_fts "
            "  JOIN papers p ON p.id = papers_fts.paper_id "
            "  WHERE p.deleted_at IS NULL"
            ")"
        ).fetchone()["c"]
        return {
            "total_papers": total,
            "indexed_papers": indexed,
            "stale_or_missing": total - indexed,
        }

    # -- filter clause builder --------------------------------------------

    def _build_filters(
        self,
        paper_type: str | None,
        journal: str | None,
        tag: str | None,
        collection_id: str | None,
        year_from: int | None,
        year_to: int | None,
    ) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []

        if paper_type is not None:
            clauses.append("p.paper_type = ?")
            params.append(paper_type)

        if journal is not None:
            escaped = (
                journal.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            clauses.append(
                "LOWER(COALESCE(json_extract(p.metadata_json, '$.journal'), '')) "
                "LIKE LOWER(?) ESCAPE '\\'"
            )
            params.append(f"%{escaped}%")

        if tag is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM paper_tags pt JOIN tags t ON t.id = pt.tag_id "
                "WHERE pt.paper_id = p.id AND t.name = ?)"
            )
            params.append(tag)

        if collection_id is not None:
            clauses.append(
                "EXISTS (SELECT 1 FROM collection_members cm "
                "WHERE cm.paper_id = p.id AND cm.collection_id = ?)"
            )
            params.append(collection_id)

        if year_from is not None or year_to is not None:
            # A missing/malformed publication_date must be excluded, not
            # crash the query -- GLOB requires exactly 4 leading digits
            # before we CAST for the actual bound comparison.
            pubdate_expr = "json_extract(p.metadata_json, '$.publication_date')"
            clauses.append(
                f"{pubdate_expr} IS NOT NULL AND "
                f"substr({pubdate_expr}, 1, 4) GLOB '[0-9][0-9][0-9][0-9]'"
            )
            if year_from is not None:
                clauses.append(f"CAST(substr({pubdate_expr}, 1, 4) AS INTEGER) >= ?")
                params.append(year_from)
            if year_to is not None:
                clauses.append(f"CAST(substr({pubdate_expr}, 1, 4) AS INTEGER) <= ?")
                params.append(year_to)

        clause_str = "".join(f" AND {c}" for c in clauses)
        return clause_str, params

    # -- search ------------------------------------------------------------

    def search(
        self,
        query: str | None = None,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
        paper_type: str | None = None,
        journal: str | None = None,
        tag: str | None = None,
        collection_id: str | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> dict:
        limit = max(1, min(int(limit), MAX_LIMIT))
        filter_clause, filter_params = self._build_filters(
            paper_type, journal, tag, collection_id, year_from, year_to
        )
        cursor_data = _decode_cursor(cursor) if cursor else None
        has_query = query is not None and query.strip() != ""

        if has_query:
            match_str = _escape_fts_query(query)
            try:
                results, next_cursor, total = self._search_with_query(
                    match_str, filter_clause, filter_params, limit, cursor_data
                )
            except sqlite3.OperationalError as exc:
                raise SearchQueryError(f"invalid search query: {query!r}") from exc
        else:
            results, next_cursor, total = self._search_without_query(
                filter_clause, filter_params, limit, cursor_data
            )

        return {
            "results": results,
            "next_cursor": next_cursor,
            "total_matched": total,
        }

    def _search_with_query(
        self,
        match_str: str,
        filter_clause: str,
        filter_params: list,
        limit: int,
        cursor_data: dict | None,
    ) -> tuple[list[dict], str | None, int]:
        inner_sql = (
            "SELECT papers_fts.paper_id AS paper_id, p.title AS title, "
            "bm25(papers_fts) AS score, "
            "snippet(papers_fts, -1, '**', '**', '...', 10) AS snippet "
            "FROM papers_fts JOIN papers p ON p.id = papers_fts.paper_id "
            "WHERE papers_fts MATCH ? AND p.deleted_at IS NULL" + filter_clause
        )
        count_sql = (
            "SELECT COUNT(*) AS c FROM papers_fts "
            "JOIN papers p ON p.id = papers_fts.paper_id "
            "WHERE papers_fts MATCH ? AND p.deleted_at IS NULL" + filter_clause
        )
        base_params = [match_str] + filter_params
        total = self.conn.execute(count_sql, base_params).fetchone()["c"]

        keyset_clause = "1=1"
        keyset_params: list = []
        if cursor_data is not None:
            keyset_clause = "(sub.score > ? OR (sub.score = ? AND sub.paper_id > ?))"
            keyset_params = [
                cursor_data["score"],
                cursor_data["score"],
                cursor_data["paper_id"],
            ]

        outer_sql = (
            f"SELECT * FROM ({inner_sql}) sub WHERE {keyset_clause} "
            "ORDER BY sub.score ASC, sub.paper_id ASC LIMIT ?"
        )
        rows = self.conn.execute(
            outer_sql, base_params + keyset_params + [limit + 1]
        ).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        results = [
            {
                "paper_id": r["paper_id"],
                "title": r["title"],
                "snippet": r["snippet"],
                "score": r["score"],
            }
            for r in rows
        ]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(
                {"score": last["score"], "paper_id": last["paper_id"]}
            )
        return results, next_cursor, total

    def _search_without_query(
        self,
        filter_clause: str,
        filter_params: list,
        limit: int,
        cursor_data: dict | None,
    ) -> tuple[list[dict], str | None, int]:
        inner_sql = (
            "SELECT p.id AS paper_id, p.title AS title, p.created_at AS created_at "
            "FROM papers p WHERE p.deleted_at IS NULL" + filter_clause
        )
        count_sql = (
            "SELECT COUNT(*) AS c FROM papers p WHERE p.deleted_at IS NULL"
            + filter_clause
        )
        total = self.conn.execute(count_sql, filter_params).fetchone()["c"]

        keyset_clause = "1=1"
        keyset_params: list = []
        if cursor_data is not None:
            keyset_clause = (
                "(sub.created_at < ? OR (sub.created_at = ? AND sub.paper_id < ?))"
            )
            keyset_params = [
                cursor_data["created_at"],
                cursor_data["created_at"],
                cursor_data["paper_id"],
            ]

        outer_sql = (
            f"SELECT * FROM ({inner_sql}) sub WHERE {keyset_clause} "
            "ORDER BY sub.created_at DESC, sub.paper_id DESC LIMIT ?"
        )
        rows = self.conn.execute(
            outer_sql, filter_params + keyset_params + [limit + 1]
        ).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        results = [{"paper_id": r["paper_id"], "title": r["title"]} for r in rows]
        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = _encode_cursor(
                {"created_at": last["created_at"], "paper_id": last["paper_id"]}
            )
        return results, next_cursor, total
