"""Figure repository: crops taken out of a paper's PDF, plus their captions.

A figure row sits on top of the ordinary blob path -- the PNG's bytes are an
`assets` row, linked to the paper by an `attachments` row with role='figure'
-- and adds the meaning that path cannot carry: which figure of which paper
this is, what its caption says, and which PDF attachment it was cropped from.

Recording is idempotent on `(source_attachment_id, asset_sha256)`. Rendering
the same region of the same PDF is deterministic, so a second extraction pass
produces identical bytes and `record()` returns the existing row instead of a
duplicate. That is what makes re-running extraction over a whole library safe,
without violating the append-only rule for attachments themselves (invariant
#2) -- the guard lives here, where the derived meaning lives, not there.

Captions are indexed in `figures_fts` rather than `chunks_fts`; see
migrations/0005_figures.sql for why that separation is deliberate.

Every write happens inside `db.transaction(conn)`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .. import db, identity

MAX_LIMIT = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _escape_fts_query(query: str, *, operator: str = " ") -> str:
    """Arbitrary user text -> a literal FTS5 MATCH string (same contract as chunks.py)."""
    return operator.join(_quote(term) for term in query.split())


class FigureRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- writes ----------------------------------------------------------

    def record(
        self,
        paper_id: str,
        source_attachment_id: str,
        figure_attachment_id: str,
        asset_sha256: str,
        kind: str,
        extractor: str,
        label: str | None = None,
        number: str | None = None,
        caption: str | None = None,
        page: int | None = None,
        bbox: list | None = None,
    ) -> str:
        """Record one figure, returning its id. Existing rows are returned as-is."""
        existing = self.conn.execute(
            "SELECT id FROM figures WHERE source_attachment_id = ? AND asset_sha256 = ?",
            (source_attachment_id, asset_sha256),
        ).fetchone()
        if existing is not None:
            return existing["id"]

        figure_id = identity.new_id()
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO figures (id, paper_id, source_attachment_id, "
                "figure_attachment_id, asset_sha256, kind, label, number, caption, "
                "page, bbox_json, extractor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (figure_id, paper_id, source_attachment_id, figure_attachment_id,
                 asset_sha256, kind, label, number, caption, page,
                 json.dumps(bbox) if bbox is not None else None, extractor, _now()),
            )
            if caption:
                self.conn.execute(
                    "INSERT INTO figures_fts (figure_id, paper_id, caption) "
                    "VALUES (?, ?, ?)",
                    (figure_id, paper_id, caption),
                )
        return figure_id

    def remove_for_attachment(self, source_attachment_id: str) -> int:
        """Drop the figure rows derived from one PDF attachment.

        The cropped PNGs' own assets and attachments are left alone: they are
        immutable blobs that may be referenced elsewhere, and dropping them here
        would be the silent overwrite invariant #2 exists to prevent. Re-running
        extraction after this re-records rows pointing at those same assets.
        """
        rows = self.conn.execute(
            "SELECT id FROM figures WHERE source_attachment_id = ?",
            (source_attachment_id,),
        ).fetchall()
        with db.transaction(self.conn):
            for row in rows:
                self.conn.execute(
                    "DELETE FROM figures_fts WHERE figure_id = ?", (row["id"],))
            self.conn.execute(
                "DELETE FROM figures WHERE source_attachment_id = ?",
                (source_attachment_id,))
        return len(rows)

    def remove_paper(self, paper_id: str) -> int:
        rows = self.conn.execute(
            "SELECT id FROM figures WHERE paper_id = ?", (paper_id,)).fetchall()
        with db.transaction(self.conn):
            for row in rows:
                self.conn.execute(
                    "DELETE FROM figures_fts WHERE figure_id = ?", (row["id"],))
            self.conn.execute("DELETE FROM figures WHERE paper_id = ?", (paper_id,))
        return len(rows)

    # -- reads -----------------------------------------------------------

    def get(self, figure_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM figures WHERE id = ?", (figure_id,)).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def list_for_paper(self, paper_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM figures WHERE paper_id = ? "
            "ORDER BY page, id", (paper_id,)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def list_for_attachment(self, source_attachment_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM figures WHERE source_attachment_id = ? ORDER BY page, id",
            (source_attachment_id,)).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def has_figures_for_attachment(self, source_attachment_id: str) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM figures WHERE source_attachment_id = ?",
            (source_attachment_id,)).fetchone()
        return row["c"] > 0

    def search(self, query: str, *, limit: int = 20,
               paper_ids: list[str] | None = None) -> list[dict]:
        """bm25-ranked caption hits, best first. Any term matches, as in chunks.py.

        Deleted papers are excluded. `paper_ids`, when given, restricts the search
        to those papers; an empty list means "no papers", not "all papers".
        """
        if not query or not query.strip():
            return []
        limit = max(1, min(int(limit), MAX_LIMIT))
        sql = (
            "SELECT f.*, bm25(figures_fts) AS score, "
            "snippet(figures_fts, 2, '**', '**', '…', 20) AS snippet "
            "FROM figures_fts JOIN figures f ON f.id = figures_fts.figure_id "
            "JOIN papers p ON p.id = f.paper_id "
            "WHERE figures_fts MATCH ? AND p.deleted_at IS NULL"
        )
        params: list = [_escape_fts_query(query, operator=" OR ")]
        if paper_ids is not None:
            if not paper_ids:
                return []
            sql += " AND f.paper_id IN (%s)" % ",".join("?" * len(paper_ids))
            params.extend(paper_ids)
        sql += " ORDER BY score ASC, f.paper_id ASC, f.page ASC LIMIT ?"
        params.append(limit)
        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # Defensive, mirroring ChunkRepository.search: every term is quoted
            # above, so an unparseable query returns nothing rather than raising.
            return []
        return [self._row_to_dict(row) for row in rows]

    def coverage(self) -> dict:
        figures = self.conn.execute("SELECT COUNT(*) AS c FROM figures").fetchone()["c"]
        papers = self.conn.execute(
            "SELECT COUNT(DISTINCT paper_id) AS c FROM figures").fetchone()["c"]
        attachments = self.conn.execute(
            "SELECT COUNT(DISTINCT source_attachment_id) AS c FROM figures"
        ).fetchone()["c"]
        captioned = self.conn.execute(
            "SELECT COUNT(*) AS c FROM figures WHERE caption IS NOT NULL AND caption != ''"
        ).fetchone()["c"]
        return {"figures": figures, "papers_with_figures": papers,
                "pdfs_extracted": attachments, "captioned": captioned}

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        out = dict(row)
        raw = out.pop("bbox_json", None)
        try:
            out["bbox"] = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            out["bbox"] = None
        return out
