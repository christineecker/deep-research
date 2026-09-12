"""Chunk-level full-text search over snapshot bodies.

`papers_fts` indexes bibliographic metadata; this indexes the *text* — the body of
each full-text snapshot in the global source store, split into overlapping chunks and
ranked with bm25. Both are rebuildable derived indexes (migrations/0003_chunks.sql):
the snapshots stay canonical, and `registry.py reindex --chunks` rebuilds every row.

Two properties are deliberate:

* **Chunks carry snapshot offsets.** A row is `(source_id, start_char, end_char)`
  into the snapshot's own `text`, which is exactly a schema.md §12 claim span. A
  retrieval hit can therefore be handed straight to `store.py verify_span` — chunking
  and evidence verification share one coordinate system instead of two.
* **Chunks stay under `store.MAX_SPAN_CHARS`.** A chunk longer than the span cap
  could never verify, which would defeat the point (`CHUNK_CHARS` below).

Indexing is keyed on the snapshot's `content_hash`, so re-running over an unchanged
snapshot is a no-op rather than a re-split.

Every write happens inside `db.transaction(conn)`.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone

from .. import db, identity

#: Target chunk size in characters. Kept well under `store.MAX_SPAN_CHARS` (2000) so
#: every chunk is directly verifiable as a span, with headroom for the boundary search
#: below to overshoot slightly when it extends to the end of a sentence.
CHUNK_CHARS = 1200
#: Overlap between consecutive chunks, so a claim straddling a boundary still lands
#: whole inside at least one chunk.
CHUNK_OVERLAP = 150
#: How far past `CHUNK_CHARS` to look for a paragraph/sentence boundary before giving
#: up and cutting mid-text. `CHUNK_CHARS + BOUNDARY_SEARCH` must stay under the span cap.
BOUNDARY_SEARCH = 300

MAX_LIMIT = 200

_BOUNDARY_RE = re.compile(r"\n\n|(?<=[.!?])\s")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quote(term: str) -> str:
    """One term as a literal (non-operator) FTS5 string."""
    return '"' + term.replace('"', '""') + '"'


def _escape_fts_query(query: str, *, operator: str = " ") -> str:
    """Turn arbitrary user text into a literal FTS5 MATCH string.

    Same escaping contract as `search.py`: every term is double-quoted, so FTS5
    operator characters become inert text rather than syntax. `operator` joins the
    terms — `" "` for implicit AND, `" OR "` for ranked any-term retrieval.
    """
    return operator.join(_quote(term) for term in query.split())


def split_text(text: str, *, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP,
               boundary_search: int = BOUNDARY_SEARCH) -> list[tuple[int, int]]:
    """Split `text` into overlapping `(start, end)` character ranges.

    Cuts at a paragraph break or sentence end when one falls within
    `boundary_search` characters past `size`, so a chunk rarely ends mid-sentence;
    otherwise cuts at `size`. Ranges are returned in document order and always
    satisfy `end - start <= size + boundary_search`.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")

    length = len(text)
    if length == 0:
        return []

    ranges: list[tuple[int, int]] = []
    start = 0
    while start < length:
        end = min(start + size, length)
        if end < length:
            window = text[end:min(end + boundary_search, length)]
            match = _BOUNDARY_RE.search(window)
            if match is not None:
                end += match.end()
        if text[start:end].strip():
            ranges.append((start, end))
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return ranges


class ChunkRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- indexing --------------------------------------------------------

    def index_source(self, paper_id: str, source_id: str, text: str,
                     content_hash: str) -> dict:
        """(Re)index one snapshot's text for one paper.

        Returns `{"indexed": n, "skipped": bool}` — `skipped` is True when rows for
        this `(paper_id, source_id)` already carry `content_hash`, i.e. the snapshot
        has not changed since the last pass.
        """
        if self.is_current(paper_id, source_id, content_hash):
            return {"indexed": 0, "skipped": True}

        ranges = split_text(text)
        now = _now()
        with db.transaction(self.conn):
            self._remove_source_locked(paper_id, source_id)
            for start, end in ranges:
                chunk_id = identity.new_id()
                self.conn.execute(
                    "INSERT INTO chunks (id, paper_id, source_id, snapshot_content_hash, "
                    "start_char, end_char, text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (chunk_id, paper_id, source_id, content_hash, start, end,
                     text[start:end], now),
                )
                self.conn.execute(
                    "INSERT INTO chunks_fts (chunk_id, paper_id, text) VALUES (?, ?, ?)",
                    (chunk_id, paper_id, text[start:end]),
                )
        return {"indexed": len(ranges), "skipped": False}

    def is_current(self, paper_id: str, source_id: str, content_hash: str) -> bool:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM chunks WHERE paper_id = ? AND source_id = ? "
            "AND snapshot_content_hash = ?",
            (paper_id, source_id, content_hash),
        ).fetchone()
        return row["c"] > 0

    def remove_source(self, paper_id: str, source_id: str) -> None:
        with db.transaction(self.conn):
            self._remove_source_locked(paper_id, source_id)

    def _remove_source_locked(self, paper_id: str, source_id: str) -> None:
        rows = self.conn.execute(
            "SELECT id FROM chunks WHERE paper_id = ? AND source_id = ?",
            (paper_id, source_id),
        ).fetchall()
        for row in rows:
            self.conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (row["id"],))
        self.conn.execute(
            "DELETE FROM chunks WHERE paper_id = ? AND source_id = ?",
            (paper_id, source_id),
        )

    def remove_paper(self, paper_id: str) -> None:
        with db.transaction(self.conn):
            rows = self.conn.execute(
                "SELECT id FROM chunks WHERE paper_id = ?", (paper_id,)
            ).fetchall()
            for row in rows:
                self.conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (row["id"],))
            self.conn.execute("DELETE FROM chunks WHERE paper_id = ?", (paper_id,))

    # -- search ------------------------------------------------------------

    def search(self, query: str, *, limit: int = 20,
               paper_ids: list[str] | None = None) -> list[dict]:
        """bm25-ranked chunk hits, best first. **Any** term matches; ranking sorts it out.

        This is the ranked retriever, not a filter: a natural-language question
        ("does X reduce Y in older adults?") shares only some of its words with the
        passage that answers it, so requiring every term returns nothing useful. bm25
        already discounts the words every chunk contains. Use `papers_matching_all`
        when boolean AND is what you actually want.

        Each hit carries everything needed both to show the match and to re-verify it:
        `paper_id`, `source_id`, `start`/`end` offsets, the chunk text, and an FTS5
        snippet with the matched terms marked. `paper_ids`, when given, restricts the
        search to those papers (an empty list means "no papers", not "all papers").
        """
        if not query or not query.strip():
            return []
        limit = max(1, min(int(limit), MAX_LIMIT))
        match_str = _escape_fts_query(query, operator=" OR ")

        sql = (
            "SELECT c.id AS chunk_id, c.paper_id AS paper_id, c.source_id AS source_id, "
            "c.start_char AS start, c.end_char AS end, c.text AS text, "
            "bm25(chunks_fts) AS score, "
            "snippet(chunks_fts, 2, '**', '**', '…', 20) AS snippet "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.chunk_id "
            "JOIN papers p ON p.id = c.paper_id "
            "WHERE chunks_fts MATCH ? AND p.deleted_at IS NULL"
        )
        params: list = [match_str]
        if paper_ids is not None:
            if not paper_ids:
                return []
            sql += " AND c.paper_id IN (%s)" % ",".join("?" * len(paper_ids))
            params.extend(paper_ids)
        sql += " ORDER BY score ASC, c.paper_id ASC, c.start_char ASC LIMIT ?"
        params.append(limit)

        try:
            rows = self.conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            # Defensive, mirroring SearchRepository: every term is quoted above, so
            # this should be unreachable. An unparseable query returns nothing rather
            # than taking the caller down.
            return []
        return [dict(row) for row in rows]

    def papers_matching_all(self, terms: list[str]) -> set[str]:
        """Papers whose indexed text contains **every** term, anywhere in the document.

        A single `chunks_fts MATCH "a" "b"` would require both terms inside one chunk,
        which is a proximity search, not the document-level AND that `registry.py
        search --q` has always meant. One query per term plus a set intersection keeps
        the old semantics while still reading an index instead of every snapshot body.
        """
        if not terms:
            return set()
        matched: set[str] | None = None
        for term in terms:
            rows = self.conn.execute(
                "SELECT DISTINCT c.paper_id AS paper_id FROM chunks_fts "
                "JOIN chunks c ON c.id = chunks_fts.chunk_id "
                "JOIN papers p ON p.id = c.paper_id "
                "WHERE chunks_fts MATCH ? AND p.deleted_at IS NULL",
                (_escape_fts_query(term),),
            ).fetchall()
            ids = {row["paper_id"] for row in rows}
            matched = ids if matched is None else (matched & ids)
            if not matched:
                return set()
        return matched or set()

    def coverage(self) -> dict:
        chunks = self.conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
        papers = self.conn.execute(
            "SELECT COUNT(DISTINCT paper_id) AS c FROM chunks"
        ).fetchone()["c"]
        sources = self.conn.execute(
            "SELECT COUNT(DISTINCT source_id) AS c FROM chunks"
        ).fetchone()["c"]
        return {"chunks": chunks, "papers_with_chunks": papers, "sources_indexed": sources}
