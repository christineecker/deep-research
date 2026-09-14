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
            "SELECT id, paper_id, is_primary FROM identifiers WHERE scheme = ? AND value = ?",
            (scheme, normalized),
        ).fetchone()
        if existing is not None:
            if existing["paper_id"] == paper_id:
                if is_primary and not existing["is_primary"]:
                    self._set_primary_locked(paper_id, scheme, existing["id"])
                return existing["id"]
            raise IdentifierConflictError(
                scheme, normalized, existing["paper_id"], paper_id
            )

        # The first identifier ever attached for a (paper_id, scheme) pair becomes
        # that scheme's primary by default -- there is no ambiguity yet to decide
        # between. A later same-scheme identifier (an alias: a preprint DOI added
        # alongside a published one, say) defaults to non-primary so it does not
        # silently displace an already-settled primary; `is_primary=True` overrides
        # that default to promote it explicitly instead.
        is_first_for_scheme = self.conn.execute(
            "SELECT 1 FROM identifiers WHERE paper_id = ? AND scheme = ? LIMIT 1",
            (paper_id, scheme),
        ).fetchone() is None
        make_primary = is_primary or is_first_for_scheme

        identifier_id = identity.new_id()
        now = _now()
        if make_primary:
            self.conn.execute(
                "UPDATE identifiers SET is_primary = 0 WHERE paper_id = ? AND scheme = ?",
                (paper_id, scheme),
            )
        self.conn.execute(
            "INSERT INTO identifiers "
            "(id, paper_id, scheme, value, is_primary, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (identifier_id, paper_id, scheme, normalized, 1 if make_primary else 0, now),
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
                "is_primary": make_primary,
            },
            reversible=False,
        )
        return identifier_id

    def set_primary(self, paper_id: str, identifier_id: str) -> None:
        """Promote one identifier to be its scheme's primary for `paper_id`.

        Every other identifier of the same (paper_id, scheme) is demoted, never
        deleted -- they remain attached and readable as historical aliases
        (`list_for_paper`), just no longer the one `primary_for_scheme` returns.
        """
        row = self.conn.execute(
            "SELECT paper_id, scheme FROM identifiers WHERE id = ?", (identifier_id,)
        ).fetchone()
        if row is None:
            raise KeyError(identifier_id)
        if row["paper_id"] != paper_id:
            raise ValueError(
                f"identifier {identifier_id!r} belongs to paper {row['paper_id']!r}, "
                f"not {paper_id!r}"
            )
        with db.transaction(self.conn):
            self._set_primary_locked(paper_id, row["scheme"], identifier_id)

    def _set_primary_locked(self, paper_id: str, scheme: str, identifier_id: str) -> None:
        self.conn.execute(
            "UPDATE identifiers SET is_primary = 0 WHERE paper_id = ? AND scheme = ?",
            (paper_id, scheme),
        )
        self.conn.execute(
            "UPDATE identifiers SET is_primary = 1 WHERE id = ?", (identifier_id,)
        )

    def primary_for_scheme(self, paper_id: str, scheme: str) -> dict | None:
        """The identifier `export`/display should treat as canonical for one scheme.

        Prefers the row flagged `is_primary`; falls back to the earliest-created row
        of that scheme when none is flagged yet (a paper mirrored before primary
        tracking existed, say), so callers never have to special-case "no primary
        set" themselves.
        """
        row = self.conn.execute(
            "SELECT * FROM identifiers WHERE paper_id = ? AND scheme = ? "
            "AND is_primary = 1",
            (paper_id, scheme),
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM identifiers WHERE paper_id = ? AND scheme = ? "
                "ORDER BY created_at LIMIT 1",
                (paper_id, scheme),
            ).fetchone()
        return dict(row) if row is not None else None

    def normalize_primaries(self, paper_id: str) -> None:
        with db.transaction(self.conn):
            self._normalize_primaries_locked(paper_id)

    def _normalize_primaries_locked(self, paper_id: str) -> None:
        """Ensure at most one `is_primary` row per scheme for `paper_id`.

        A merge can move an already-primary alias onto a paper that already has its
        own primary for that scheme (two papers, each with a settled primary DOI,
        merged together): rather than pick a winner opaquely, this keeps the
        earliest-created row as primary and demotes the rest, which is deterministic
        and matches the tie-break `primary_for_scheme` itself falls back to.
        """
        schemes = self.conn.execute(
            "SELECT DISTINCT scheme FROM identifiers WHERE paper_id = ?", (paper_id,)
        ).fetchall()
        for row in schemes:
            scheme = row["scheme"]
            primaries = self.conn.execute(
                "SELECT id FROM identifiers WHERE paper_id = ? AND scheme = ? "
                "AND is_primary = 1 ORDER BY created_at",
                (paper_id, scheme),
            ).fetchall()
            if len(primaries) <= 1:
                continue
            keep = primaries[0]["id"]
            self.conn.execute(
                "UPDATE identifiers SET is_primary = 0 "
                "WHERE paper_id = ? AND scheme = ? AND id != ?",
                (paper_id, scheme, keep),
            )

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
