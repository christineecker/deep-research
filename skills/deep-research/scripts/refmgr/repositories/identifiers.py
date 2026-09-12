"""Identifier repository.

External identifiers (DOI/PMID/PMCID/URL/...) are normalized before
being written so the schema's `UNIQUE(scheme, value)` constraint catches
duplicates (see identity.normalize_identifier). Repeated imports of the
same identifier for the same paper are idempotent; an identifier that
already points at a *different* paper raises `IdentifierConflictError`
rather than being silently overwritten or merged
(REFERENCE_MANAGER_V2_PLAN.md: "Ambiguous paper matches require review").
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .. import db, identity
from .audit import AuditRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IdentifierConflictError(ValueError):
    """Raised when an identifier already points at a different paper."""

    def __init__(self, scheme: str, value: str, existing_paper_id: str, new_paper_id: str):
        self.scheme = scheme
        self.value = value
        self.existing_paper_id = existing_paper_id
        self.new_paper_id = new_paper_id
        super().__init__(
            f"identifier {scheme}:{value!r} already points at paper "
            f"{existing_paper_id!r}, cannot also link to {new_paper_id!r}"
        )


class IdentifierRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._audit = AuditRepository(conn)

    def add(
        self, paper_id: str, scheme: str, raw_value: str, is_primary: bool = False
    ) -> str:
        normalized = identity.normalize_identifier(scheme, raw_value)
        with db.transaction(self.conn):
            return self._add_locked(paper_id, scheme, normalized, is_primary=is_primary)

    def _add_locked(
        self, paper_id: str, scheme: str, normalized: str, is_primary: bool = False
    ) -> str:
        existing = self.conn.execute(
            "SELECT id, paper_id FROM identifiers WHERE scheme = ? AND value = ?",
            (scheme, normalized),
        ).fetchone()
        if existing is not None:
            if existing["paper_id"] == paper_id:
                return existing["id"]
            raise IdentifierConflictError(
                scheme, normalized, existing["paper_id"], paper_id
            )
        identifier_id = identity.new_id()
        now = _now()
        self.conn.execute(
            "INSERT INTO identifiers "
            "(id, paper_id, scheme, value, is_primary, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (identifier_id, paper_id, scheme, normalized, 1 if is_primary else 0, now),
        )
        self._audit.record(
            entity_type="identifier",
            entity_id=identifier_id,
            action="create",
            before=None,
            after={
                "id": identifier_id,
                "paper_id": paper_id,
                "scheme": scheme,
                "value": normalized,
                "is_primary": is_primary,
            },
            reversible=False,
        )
        return identifier_id

    def list_for_paper(self, paper_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM identifiers WHERE paper_id = ? ORDER BY created_at",
            (paper_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def find_paper_by_identifier(self, scheme: str, raw_value: str) -> str | None:
        normalized = identity.normalize_identifier(scheme, raw_value)
        row = self.conn.execute(
            "SELECT paper_id FROM identifiers WHERE scheme = ? AND value = ?",
            (scheme, normalized),
        ).fetchone()
        return row["paper_id"] if row is not None else None

    def reassign(self, identifier_id: str, new_paper_id: str) -> None:
        with db.transaction(self.conn):
            return self._reassign_locked(identifier_id, new_paper_id)

    def _reassign_locked(self, identifier_id: str, new_paper_id: str) -> None:
        row = self.conn.execute(
            "SELECT * FROM identifiers WHERE id = ?", (identifier_id,)
        ).fetchone()
        if row is None:
            raise KeyError(identifier_id)
        old_paper_id = row["paper_id"]
        self.conn.execute(
            "UPDATE identifiers SET paper_id = ? WHERE id = ?",
            (new_paper_id, identifier_id),
        )
        self._audit.record(
            entity_type="identifier",
            entity_id=identifier_id,
            action="reassign",
            before={"paper_id": old_paper_id},
            after={"paper_id": new_paper_id},
            reversible=True,
        )
