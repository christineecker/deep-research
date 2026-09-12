"""Paper repository.

Papers have an immutable id and created_at; metadata enrichment must
never touch either (REFERENCE_MANAGER_V2_PLAN.md, "Metadata enrichment
never changes paper identity or disconnects annotations/citations").
Every write happens inside `db.transaction(conn)`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .. import db, identity
from .audit import AuditRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    paper = dict(row)
    metadata_json = paper.pop("metadata_json")
    paper["metadata"] = json.loads(metadata_json) if metadata_json is not None else {}
    return paper


class PaperRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._audit = AuditRepository(conn)

    def create(
        self,
        title: str,
        paper_type: str,
        metadata: dict | None = None,
        provenance: str | None = None,
    ) -> str:
        paper_id = identity.new_id()
        now = _now()
        metadata_json = json.dumps(metadata or {})
        with db.transaction(self.conn):
            return self._create_locked(
                paper_id, title, paper_type, metadata_json, provenance, now
            )

    def _create_locked(
        self,
        paper_id: str,
        title: str,
        paper_type: str,
        metadata_json: str,
        provenance: str | None,
        now: str,
    ) -> str:
        self.conn.execute(
            "INSERT INTO papers "
            "(id, title, paper_type, metadata_json, provenance, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (paper_id, title, paper_type, metadata_json, provenance, now, now),
        )
        row = self.conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        self._audit.record(
            entity_type="paper",
            entity_id=paper_id,
            action="create",
            before=None,
            after=dict(row),
            reversible=False,
        )
        return paper_id

    def get(self, paper_id: str, include_deleted: bool = False) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()
        if row is None:
            return None
        if row["deleted_at"] is not None and not include_deleted:
            return None
        return _row_to_dict(row)

    def _get_raw(self, paper_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM papers WHERE id = ?", (paper_id,)
        ).fetchone()

    def update_metadata(self, paper_id: str, patch: dict) -> None:
        with db.transaction(self.conn):
            return self._update_metadata_locked(paper_id, patch)

    def _update_metadata_locked(self, paper_id: str, patch: dict) -> None:
        row = self._get_raw(paper_id)
        if row is None:
            raise KeyError(paper_id)
        before_metadata = json.loads(row["metadata_json"])
        after_metadata = dict(before_metadata)
        after_metadata.update(patch)
        now = _now()
        self.conn.execute(
            "UPDATE papers SET metadata_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(after_metadata), now, paper_id),
        )
        self._audit.record(
            entity_type="paper",
            entity_id=paper_id,
            action="update_metadata",
            before=before_metadata,
            after=after_metadata,
            reversible=True,
        )

    def set_citation_key(self, paper_id: str, key: str, force: bool = False) -> None:
        """Set a paper's citation_key.

        `citation_key` is UNIQUE in the schema. If another paper already
        holds `key`, this raises `ValueError` regardless of `force` --
        there is no sane way to force two rows to share a UNIQUE column.
        `force` only affects re-setting the paper's own current key: without
        it, re-setting the same value is still validated (a no-op update);
        with it, the same short-circuit applies. Kept simple and correct
        rather than clever.
        """
        with db.transaction(self.conn):
            return self._set_citation_key_locked(paper_id, key, force=force)

    def _set_citation_key_locked(self, paper_id: str, key: str, force: bool = False) -> None:
        row = self._get_raw(paper_id)
        if row is None:
            raise KeyError(paper_id)
        existing = self.conn.execute(
            "SELECT id FROM papers WHERE citation_key = ?", (key,)
        ).fetchone()
        if existing is not None and existing["id"] != paper_id:
            raise ValueError(
                f"citation_key {key!r} already used by paper {existing['id']!r}"
            )
        before = {"citation_key": row["citation_key"]}
        now = _now()
        self.conn.execute(
            "UPDATE papers SET citation_key = ?, updated_at = ? WHERE id = ?",
            (key, now, paper_id),
        )
        self._audit.record(
            entity_type="paper",
            entity_id=paper_id,
            action="set_citation_key",
            before=before,
            after={"citation_key": key},
            reversible=True,
        )

    def soft_delete(self, paper_id: str) -> None:
        with db.transaction(self.conn):
            return self._soft_delete_locked(paper_id)

    def _soft_delete_locked(self, paper_id: str) -> None:
        row = self._get_raw(paper_id)
        if row is None:
            raise KeyError(paper_id)
        if row["deleted_at"] is not None:
            return
        now = _now()
        self.conn.execute(
            "UPDATE papers SET deleted_at = ?, updated_at = ? WHERE id = ?",
            (now, now, paper_id),
        )
        self._audit.record(
            entity_type="paper",
            entity_id=paper_id,
            action="soft_delete",
            before={"deleted_at": None},
            after={"deleted_at": now},
            reversible=True,
        )

    def restore(self, paper_id: str) -> None:
        with db.transaction(self.conn):
            return self._restore_locked(paper_id)

    def _restore_locked(self, paper_id: str) -> None:
        row = self._get_raw(paper_id)
        if row is None:
            raise KeyError(paper_id)
        if row["deleted_at"] is None:
            return
        before_deleted_at = row["deleted_at"]
        now = _now()
        self.conn.execute(
            "UPDATE papers SET deleted_at = NULL, updated_at = ? WHERE id = ?",
            (now, paper_id),
        )
        self._audit.record(
            entity_type="paper",
            entity_id=paper_id,
            action="restore",
            before={"deleted_at": before_deleted_at},
            after={"deleted_at": None},
            reversible=True,
        )

    def list(
        self, limit: int = 50, offset: int = 0, include_deleted: bool = False
    ) -> list[dict]:
        if include_deleted:
            rows = self.conn.execute(
                "SELECT * FROM papers ORDER BY created_at LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM papers WHERE deleted_at IS NULL "
                "ORDER BY created_at LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]
