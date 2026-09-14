"""Saved-search repository.

Stores search query specifications (not results) so a CLI/agent caller
can re-run a named search later. `query_json` holds an opaque
JSON-serialized dict matching whatever parameters `SearchRepository.search`
accepts -- this repository has no opinion on its contents beyond "it's a
JSON-serializable dict" (REFERENCE_MANAGER_V2_PLAN.md, Phase 4 (revised):
"Stable pagination, saved searches, counts, explicit index coverage/error
reporting"). Every write happens inside `db.transaction(conn)`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .. import db, identity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "query": json.loads(row["query_json"]),
        "created_at": row["created_at"],
    }


class SavedSearchRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create(self, name: str, query: dict) -> str:
        saved_search_id = identity.new_id()
        query_json = json.dumps(query, sort_keys=True)
        now = _now()
        with db.transaction(self.conn):
            try:
                self.conn.execute(
                    "INSERT INTO saved_searches (id, name, query_json, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (saved_search_id, name, query_json, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"saved search name already exists: {name!r}") from exc
        return saved_search_id

    def _get_raw(self, saved_search_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM saved_searches WHERE id = ?", (saved_search_id,)
        ).fetchone()

    def get(self, saved_search_id: str) -> dict | None:
        row = self._get_raw(saved_search_id)
        if row is None:
            return None
        return _row_to_dict(row)

    def get_by_name(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM saved_searches WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def list(self, limit: int = 50, offset: int = 0) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM saved_searches ORDER BY created_at LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def rename(self, saved_search_id: str, new_name: str) -> None:
        with db.transaction(self.conn):
            return self._rename_locked(saved_search_id, new_name)

    def _rename_locked(self, saved_search_id: str, new_name: str) -> None:
        row = self._get_raw(saved_search_id)
        if row is None:
            raise KeyError(saved_search_id)
        if row["name"] == new_name:
            return
        try:
            self.conn.execute(
                "UPDATE saved_searches SET name = ? WHERE id = ?",
                (new_name, saved_search_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"saved search name already exists: {new_name!r}") from exc

    def update_query(self, saved_search_id: str, query: dict) -> None:
        with db.transaction(self.conn):
            return self._update_query_locked(saved_search_id, query)

    def _update_query_locked(self, saved_search_id: str, query: dict) -> None:
        row = self._get_raw(saved_search_id)
        if row is None:
            raise KeyError(saved_search_id)
        self.conn.execute(
            "UPDATE saved_searches SET query_json = ? WHERE id = ?",
            (json.dumps(query, sort_keys=True), saved_search_id),
        )

    def delete(self, saved_search_id: str) -> None:
        with db.transaction(self.conn):
            self.conn.execute(
                "DELETE FROM saved_searches WHERE id = ?", (saved_search_id,)
            )
