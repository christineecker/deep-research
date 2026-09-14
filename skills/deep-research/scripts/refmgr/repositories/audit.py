"""Audit log repository.

`AuditRepository.record` never opens its own transaction: callers are
expected to invoke it from inside a `db.transaction(conn)` block they
already hold, so an audit row is written atomically with the change it
documents (see REFERENCE_MANAGER_V2_PLAN.md, "Audit/merge" invariant:
"never silently discard conflicting data").
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .. import identity


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def record(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        before: dict | None = None,
        after: dict | None = None,
        reversible: bool = False,
    ) -> str:
        audit_id = identity.new_id()
        before_json = json.dumps(before) if before is not None else None
        after_json = json.dumps(after) if after is not None else None
        self.conn.execute(
            "INSERT INTO audit_log "
            "(id, entity_type, entity_id, action, before_json, after_json, reversible, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id,
                entity_type,
                entity_id,
                action,
                before_json,
                after_json,
                1 if reversible else 0,
                _now(),
            ),
        )
        return audit_id

    def list_for_entity(self, entity_type: str, entity_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM audit_log WHERE entity_type = ? AND entity_id = ? "
            "ORDER BY created_at",
            (entity_type, entity_id),
        ).fetchall()
        results = []
        for row in rows:
            entry = dict(row)
            before_json = entry.pop("before_json")
            after_json = entry.pop("after_json")
            entry["before"] = json.loads(before_json) if before_json is not None else None
            entry["after"] = json.loads(after_json) if after_json is not None else None
            results.append(entry)
        return results
