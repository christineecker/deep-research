"""Integrity report for a refmgr library (OPTIMIZATION_PLAN.md item 10).

Content-addressed storage fails quietly: a PDF deleted out from under the database, or
bytes that no longer hash to their name, stay invisible until an export or a reader
tries to open the file. This looks for that on purpose.

**Read-only.** Nothing here deletes, moves, or rewrites anything — it reports, and the
operator decides. That is the useful half of Phase 6's recovery story
(REFERENCE_MANAGER_V2_PLAN.md) and the half that is cheap to get right; automatic repair
of a store whose canonical data lives in `registry.jsonl` and the snapshot directory
would mostly mean re-running `registry.py reindex`, which is already a command.

Checks
  missing_files       an attachment's asset row exists, the file on disk does not
  corrupt_assets      the file exists but its bytes no longer hash to its sha256
  orphan_assets       an asset no live attachment references
  orphan_attachments  an attachment whose asset row is gone
  orphan_terms        term/chunk rows for a paper that no longer exists
  index_staleness     papers missing from papers_fts, and chunk/term coverage

`--deep` hashes every asset (slow, exact); the default trusts a matching size and only
hashes what looks wrong, which is what makes this runnable often.
"""

from __future__ import annotations

from pathlib import Path

from .repositories.assets import _hash_file


def _asset_rows(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT sha256, byte_size, storage_path FROM assets ORDER BY sha256")]


def check_assets(conn, library_root: Path, *, deep: bool = False) -> dict:
    """Per-asset file existence and checksum state."""
    missing, corrupt, checked, hashed = [], [], 0, 0
    for row in _asset_rows(conn):
        checked += 1
        path = Path(library_root) / row["storage_path"]
        if not path.exists():
            missing.append({"sha256": row["sha256"], "storage_path": row["storage_path"]})
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            corrupt.append({"sha256": row["sha256"], "storage_path": row["storage_path"],
                            "detail": f"unreadable: {exc}"})
            continue
        size_ok = size == row["byte_size"]
        if size_ok and not deep:
            continue
        hashed += 1
        try:
            digest = _hash_file(path)
        except OSError as exc:
            corrupt.append({"sha256": row["sha256"], "storage_path": row["storage_path"],
                            "detail": f"unreadable: {exc}"})
            continue
        if digest != row["sha256"]:
            corrupt.append({
                "sha256": row["sha256"], "storage_path": row["storage_path"],
                "detail": f"bytes hash to {digest}"
                          + ("" if size_ok else f"; size {size} != {row['byte_size']}"),
            })
        elif not size_ok:
            corrupt.append({
                "sha256": row["sha256"], "storage_path": row["storage_path"],
                "detail": f"size {size} does not match recorded {row['byte_size']} "
                          f"although the hash matches",
            })
    return {"assets_checked": checked, "assets_hashed": hashed,
            "missing_files": missing, "corrupt_assets": corrupt}


def check_links(conn) -> dict:
    """Attachment/asset and paper/child-row referential state."""
    orphan_assets = [dict(row) for row in conn.execute(
        "SELECT a.sha256 AS sha256, a.storage_path AS storage_path FROM assets a "
        "WHERE NOT EXISTS (SELECT 1 FROM attachments at WHERE at.asset_sha256 = a.sha256 "
        "AND at.deleted_at IS NULL) ORDER BY a.sha256")]
    orphan_attachments = [dict(row) for row in conn.execute(
        "SELECT at.id AS attachment_id, at.paper_id AS paper_id, "
        "at.asset_sha256 AS asset_sha256 FROM attachments at "
        "WHERE at.deleted_at IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM assets a WHERE a.sha256 = at.asset_sha256) ORDER BY at.id")]
    attachments_without_paper = [dict(row) for row in conn.execute(
        "SELECT at.id AS attachment_id, at.paper_id AS paper_id FROM attachments at "
        "WHERE at.deleted_at IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM papers p WHERE p.id = at.paper_id) ORDER BY at.id")]
    papers_without_assets = conn.execute(
        "SELECT COUNT(*) AS c FROM papers p WHERE p.deleted_at IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM attachments at WHERE at.paper_id = p.id AND at.deleted_at IS NULL)"
    ).fetchone()["c"]
    return {
        "orphan_assets": orphan_assets,
        "orphan_attachments": orphan_attachments,
        "attachments_without_paper": attachments_without_paper,
        "papers_without_attachment": papers_without_assets,
    }


def _orphan_count(conn, table: str) -> int:
    return conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} t "
        "WHERE NOT EXISTS (SELECT 1 FROM papers p WHERE p.id = t.paper_id)"
    ).fetchone()["c"]


def check_indexes(conn) -> dict:
    """How much of the library each derived index actually covers."""
    live_papers = conn.execute(
        "SELECT COUNT(*) AS c FROM papers WHERE deleted_at IS NULL").fetchone()["c"]
    in_fts = conn.execute(
        "SELECT COUNT(*) AS c FROM (SELECT DISTINCT f.paper_id FROM papers_fts f "
        "JOIN papers p ON p.id = f.paper_id WHERE p.deleted_at IS NULL)").fetchone()["c"]
    return {
        "live_papers": live_papers,
        "papers_fts_indexed": in_fts,
        "papers_fts_missing": live_papers - in_fts,
        "papers_with_chunks": conn.execute(
            "SELECT COUNT(DISTINCT paper_id) AS c FROM chunks").fetchone()["c"],
        "papers_with_terms": conn.execute(
            "SELECT COUNT(DISTINCT paper_id) AS c FROM paper_terms").fetchone()["c"],
        "orphan_chunks": _orphan_count(conn, "chunks"),
        "orphan_terms": _orphan_count(conn, "paper_terms"),
        "orphan_fts_rows": conn.execute(
            "SELECT COUNT(*) AS c FROM papers_fts f WHERE NOT EXISTS "
            "(SELECT 1 FROM papers p WHERE p.id = f.paper_id)").fetchone()["c"],
    }


#: Findings that mean data is gone or wrong, as opposed to merely un-indexed. Only these
#: make `doctor` exit non-zero: a library with no PDFs attached yet is not unhealthy.
_PROBLEM_KEYS = ("missing_files", "corrupt_assets", "orphan_attachments",
                 "attachments_without_paper")


def run(service, *, deep: bool = False) -> dict:
    """Full report. `service` is a `ReferenceManagerService`."""
    conn = service.conn
    report = {"library_root": str(service.library_root)}
    report.update(check_assets(conn, service.library_root, deep=deep))
    report.update(check_links(conn))
    report["indexes"] = check_indexes(conn)

    problems = {key: len(report[key]) for key in _PROBLEM_KEYS if report.get(key)}
    report["problems"] = problems
    report["healthy"] = not problems

    advice: list[str] = []
    if report["missing_files"]:
        advice.append("missing asset files cannot be rebuilt from the registry — restore "
                      "them from backup, or re-import the PDFs")
    if report["corrupt_assets"]:
        advice.append("an asset whose bytes no longer match its sha256 has been modified "
                      "or damaged; originals are immutable by design, so treat this as "
                      "data loss, not drift")
    if report["indexes"]["papers_fts_missing"] or report["indexes"]["orphan_chunks"] \
            or report["indexes"]["orphan_terms"] or report["indexes"]["orphan_fts_rows"]:
        advice.append("index rows are stale or orphaned — `registry.py reindex` rebuilds "
                      "them; no data is at risk")
    if report["orphan_assets"]:
        advice.append("orphan assets are stored bytes no live attachment references; "
                      "they are safe to keep and are not counted as a problem")
    report["advice"] = advice
    return report
