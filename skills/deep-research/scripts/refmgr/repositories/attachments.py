"""Attachment repository: links assets to papers.

Attachments are append-only per invariant #2 ("Adding a second PDF never
overwrites the first attachment reference") — `link()` always inserts a
new row, never updates an existing one. Multiple attachments may point at
the same `asset_sha256` (shared, immutable bytes) with different roles or
version labels. `soft_delete` only sets `deleted_at`; the underlying
asset is never touched, since it may be referenced by other attachments.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .. import db, identity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AttachmentRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def link(
        self,
        paper_id: str,
        asset_sha256: str,
        role: str,
        original_filename: str | None = None,
        provenance: str | None = None,
        version_label: str | None = None,
        page_count: int | None = None,
        preferred: bool = False,
    ) -> str:
        attachment_id = identity.new_id()
        with db.transaction(self.conn):
            return self._link_locked(
                attachment_id,
                paper_id,
                asset_sha256,
                role,
                original_filename,
                provenance,
                version_label,
                page_count,
                preferred,
            )

    def _link_locked(
        self,
        attachment_id: str,
        paper_id: str,
        asset_sha256: str,
        role: str,
        original_filename: str | None,
        provenance: str | None,
        version_label: str | None,
        page_count: int | None,
        preferred: bool,
    ) -> str:
        if preferred:
            self.conn.execute(
                "UPDATE attachments SET is_preferred_reader = 0 "
                "WHERE paper_id = ? AND deleted_at IS NULL",
                (paper_id,),
            )
        self.conn.execute(
            "INSERT INTO attachments "
            "(id, paper_id, asset_sha256, role, original_filename, provenance, "
            "version_label, page_count, is_preferred_reader, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attachment_id,
                paper_id,
                asset_sha256,
                role,
                original_filename,
                provenance,
                version_label,
                page_count,
                1 if preferred else 0,
                _now(),
            ),
        )
        return attachment_id

    def list_for_paper(self, paper_id: str, include_deleted: bool = False) -> list[dict]:
        if include_deleted:
            query = (
                "SELECT * FROM attachments WHERE paper_id = ? ORDER BY created_at"
            )
        else:
            query = (
                "SELECT * FROM attachments WHERE paper_id = ? AND deleted_at IS NULL "
                "ORDER BY created_at"
            )
        rows = self.conn.execute(query, (paper_id,)).fetchall()
        return [dict(row) for row in rows]

    def set_preferred(self, attachment_id: str) -> None:
        row = self.conn.execute(
            "SELECT paper_id FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no attachment with id {attachment_id!r}")
        paper_id = row["paper_id"]
        with db.transaction(self.conn):
            return self._set_preferred_locked(attachment_id, paper_id)

    def _set_preferred_locked(self, attachment_id: str, paper_id: str) -> None:
        self.conn.execute(
            "UPDATE attachments SET is_preferred_reader = 0 "
            "WHERE paper_id = ? AND id != ?",
            (paper_id, attachment_id),
        )
        self.conn.execute(
            "UPDATE attachments SET is_preferred_reader = 1 WHERE id = ?",
            (attachment_id,),
        )

    def reassign(self, attachment_id: str, new_paper_id: str) -> None:
        with db.transaction(self.conn):
            return self._reassign_locked(attachment_id, new_paper_id)

    def _reassign_locked(self, attachment_id: str, new_paper_id: str) -> None:
        row = self.conn.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
        if row is None:
            raise KeyError(attachment_id)
        self.conn.execute(
            "UPDATE attachments SET paper_id = ? WHERE id = ?",
            (new_paper_id, attachment_id),
        )

    def soft_delete(self, attachment_id: str) -> None:
        with db.transaction(self.conn):
            return self._soft_delete_locked(attachment_id)

    def _soft_delete_locked(self, attachment_id: str) -> None:
        self.conn.execute(
            "UPDATE attachments SET deleted_at = ? "
            "WHERE id = ? AND deleted_at IS NULL",
            (_now(), attachment_id),
        )
