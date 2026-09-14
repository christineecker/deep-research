"""Processing state for figure extraction (migrations/0007_figure_extraction_state.sql).

One row per source PDF attachment, tracking which `(source_asset_sha256, extractor,
options)` configuration its figures currently reflect and whether that attempt is
still running, complete, or failed. `FigureRepository` (`figures.py`) owns the derived
figure rows themselves; this repository owns only whether extraction has actually
finished for the configuration a caller is about to run, and lets a caller distinguish
"never attempted" from "attempted and failed" from "attempted and found zero figures" --
none of which "does a figures row exist" can tell apart.

Every write happens inside `db.transaction(conn)`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


from .. import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["options"] = json.loads(out.pop("options_json"))
    return out


class FigureExtractionStateRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, source_attachment_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM figure_extraction_state WHERE source_attachment_id = ?",
            (source_attachment_id,),
        ).fetchone()
        return _row_to_dict(row) if row is not None else None

    def begin_attempt(
        self,
        *,
        paper_id: str,
        source_attachment_id: str,
        source_asset_sha256: str,
        extractor: str,
        options: dict,
        force: bool = False,
    ) -> dict:
        """Decide whether extraction should run, and record that an attempt is starting.

        Returns `{"should_run": False, "figure_count": N}` when a prior attempt already
        completed this EXACT `(source_asset_sha256, extractor, options)` configuration
        and `force` is not set -- the caller's cue to skip. Otherwise marks the row
        'running' (creating it if this attachment has never been attempted) and returns
        `{"should_run": True, "attempt_count": N}`. A configuration change resets
        `attempt_count` to 1 for the new configuration; repeating the same one increments
        it, so a retry loop is visible in the row rather than only in log lines.
        """
        options_json = json.dumps(options, sort_keys=True)
        with db.transaction(self.conn):
            return self._begin_attempt_locked(
                paper_id, source_attachment_id, source_asset_sha256, extractor,
                options_json, force,
            )

    def _begin_attempt_locked(
        self, paper_id: str, source_attachment_id: str, source_asset_sha256: str,
        extractor: str, options_json: str, force: bool,
    ) -> dict:
        row = self.conn.execute(
            "SELECT * FROM figure_extraction_state WHERE source_attachment_id = ?",
            (source_attachment_id,),
        ).fetchone()

        same_config = (
            row is not None
            and row["source_asset_sha256"] == source_asset_sha256
            and row["extractor"] == extractor
            and row["options_json"] == options_json
        )
        if same_config and row["status"] == "complete" and not force:
            return {"should_run": False, "figure_count": row["figure_count"]}

        now = _now()
        attempt_count = (row["attempt_count"] + 1) if same_config else 1
        if row is None:
            self.conn.execute(
                "INSERT INTO figure_extraction_state "
                "(source_attachment_id, paper_id, source_asset_sha256, extractor, "
                "options_json, status, figure_count, attempt_count, error, "
                "started_at, completed_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'running', NULL, ?, NULL, ?, NULL, ?)",
                (source_attachment_id, paper_id, source_asset_sha256, extractor,
                 options_json, attempt_count, now, now),
            )
        else:
            self.conn.execute(
                "UPDATE figure_extraction_state SET paper_id = ?, "
                "source_asset_sha256 = ?, extractor = ?, options_json = ?, "
                "status = 'running', figure_count = NULL, attempt_count = ?, "
                "error = NULL, started_at = ?, completed_at = NULL, updated_at = ? "
                "WHERE source_attachment_id = ?",
                (paper_id, source_asset_sha256, extractor, options_json,
                 attempt_count, now, now, source_attachment_id),
            )
        return {"should_run": True, "attempt_count": attempt_count}

    def complete_attempt(self, source_attachment_id: str, figure_count: int) -> None:
        """Record a successful attempt. `figure_count=0` is a valid, final answer --
        it is what makes a genuinely figure-less PDF stop being re-attempted forever."""
        with db.transaction(self.conn):
            now = _now()
            self.conn.execute(
                "UPDATE figure_extraction_state SET status = 'complete', "
                "figure_count = ?, error = NULL, completed_at = ?, updated_at = ? "
                "WHERE source_attachment_id = ?",
                (figure_count, now, now, source_attachment_id),
            )

    def fail_attempt(self, source_attachment_id: str, error: str) -> None:
        with db.transaction(self.conn):
            now = _now()
            self.conn.execute(
                "UPDATE figure_extraction_state SET status = 'failed', error = ?, "
                "completed_at = ?, updated_at = ? WHERE source_attachment_id = ?",
                (error, now, now, source_attachment_id),
            )

    def remove_for_attachment(self, source_attachment_id: str) -> None:
        with db.transaction(self.conn):
            self.conn.execute(
                "DELETE FROM figure_extraction_state WHERE source_attachment_id = ?",
                (source_attachment_id,),
            )
