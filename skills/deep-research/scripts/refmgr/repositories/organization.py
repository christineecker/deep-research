"""Organization repository: collections/projects, membership, and tags.

A "project" is a collection with kind="project" -- no separate table
(REFERENCE_MANAGER_V2_PLAN.md, "Organization" row: "Projects have explicit
paper membership."). Ratings and saved searches are out of scope for this
phase; they land with the later annotation-related work.

Every write happens inside `db.transaction(conn)`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .. import db, identity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrganizationRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- collections / projects -------------------------------------

    def create_collection(self, name: str, kind: str = "collection") -> str:
        collection_id = identity.new_id()
        now = _now()
        with db.transaction(self.conn):
            return self._create_collection_locked(collection_id, name, kind, now)

    def _create_collection_locked(
        self, collection_id: str, name: str, kind: str, now: str
    ) -> str:
        self.conn.execute(
            "INSERT INTO collections (id, name, kind, created_at) "
            "VALUES (?, ?, ?, ?)",
            (collection_id, name, kind, now),
        )
        return collection_id

    def get_collection(self, collection_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM collections WHERE id = ?", (collection_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def list_collections(self, kind: str | None = None) -> list[dict]:
        if kind is None:
            rows = self.conn.execute(
                "SELECT * FROM collections ORDER BY created_at"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM collections WHERE kind = ? ORDER BY created_at",
                (kind,),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- membership ----------------------------------------------------

    def add_to_collection(self, collection_id: str, paper_id: str) -> None:
        with db.transaction(self.conn):
            return self._add_to_collection_locked(collection_id, paper_id)

    def _add_to_collection_locked(self, collection_id: str, paper_id: str) -> None:
        exists = self.conn.execute(
            "SELECT 1 FROM collections WHERE id = ?", (collection_id,)
        ).fetchone()
        if exists is None:
            raise KeyError(collection_id)
        self.conn.execute(
            "INSERT OR IGNORE INTO collection_members "
            "(collection_id, paper_id, added_at) VALUES (?, ?, ?)",
            (collection_id, paper_id, _now()),
        )

    def remove_from_collection(self, collection_id: str, paper_id: str) -> None:
        with db.transaction(self.conn):
            return self._remove_from_collection_locked(collection_id, paper_id)

    def _remove_from_collection_locked(self, collection_id: str, paper_id: str) -> None:
        self.conn.execute(
            "DELETE FROM collection_members "
            "WHERE collection_id = ? AND paper_id = ?",
            (collection_id, paper_id),
        )

    def list_members(self, collection_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT paper_id FROM collection_members "
            "WHERE collection_id = ? ORDER BY added_at",
            (collection_id,),
        ).fetchall()
        return [row["paper_id"] for row in rows]

    def list_collections_for_paper(self, paper_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT c.* FROM collections c "
            "JOIN collection_members m ON m.collection_id = c.id "
            "WHERE m.paper_id = ? ORDER BY c.created_at",
            (paper_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # -- tags ------------------------------------------------------------

    def _get_or_create_tag_id(self, tag_name: str) -> str:
        row = self.conn.execute(
            "SELECT id FROM tags WHERE name = ?", (tag_name,)
        ).fetchone()
        if row is not None:
            return row["id"]
        tag_id = identity.new_id()
        self.conn.execute(
            "INSERT INTO tags (id, name) VALUES (?, ?)", (tag_id, tag_name)
        )
        return tag_id

    def add_tag(self, paper_id: str, tag_name: str) -> None:
        with db.transaction(self.conn):
            return self._add_tag_locked(paper_id, tag_name)

    def _add_tag_locked(self, paper_id: str, tag_name: str) -> None:
        tag_id = self._get_or_create_tag_id(tag_name)
        self.conn.execute(
            "INSERT OR IGNORE INTO paper_tags (paper_id, tag_id) VALUES (?, ?)",
            (paper_id, tag_id),
        )

    def remove_tag(self, paper_id: str, tag_name: str) -> None:
        with db.transaction(self.conn):
            return self._remove_tag_locked(paper_id, tag_name)

    def _remove_tag_locked(self, paper_id: str, tag_name: str) -> None:
        row = self.conn.execute(
            "SELECT id FROM tags WHERE name = ?", (tag_name,)
        ).fetchone()
        if row is None:
            return
        self.conn.execute(
            "DELETE FROM paper_tags WHERE paper_id = ? AND tag_id = ?",
            (paper_id, row["id"]),
        )

    def list_tags(self, paper_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT t.name FROM tags t "
            "JOIN paper_tags pt ON pt.tag_id = t.id "
            "WHERE pt.paper_id = ? ORDER BY t.name",
            (paper_id,),
        ).fetchall()
        return [row["name"] for row in rows]

    def list_papers_with_tag(self, tag_name: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT pt.paper_id FROM paper_tags pt "
            "JOIN tags t ON t.id = pt.tag_id "
            "WHERE t.name = ?",
            (tag_name,),
        ).fetchall()
        return [row["paper_id"] for row in rows]
