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
  figure_links        figure rows whose source PDF attachment or image asset is gone
  index_staleness     papers missing from papers_fts, and chunk/term coverage

`--deep` hashes every asset (slow, exact); the default trusts a matching size and only
hashes what looks wrong, which is what makes this runnable often.
"""

from __future__ import annotations

from pathlib import Path

from .repositories.assets import _hash_file
from .repositories.chunks import CHUNKER_VERSION


def _asset_rows(conn) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT sha256, byte_size, storage_path FROM assets ORDER BY sha256")]


def check_sqlite_integrity(conn) -> dict:
    """SQLite's own structural checks: page-level corruption and dangling foreign keys.

    Both are cheap relative to hashing assets, so unlike `check_assets` there is no
    shallow/deep split here -- this always runs at full strength. A non-'ok' integrity
    row or any foreign-key violation means the database file itself is damaged, not
    merely that a derived index is stale; that distinction is why these count toward
    `problems` while index-staleness findings elsewhere do not.
    """
    integrity_rows = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
    ok = integrity_rows == ["ok"]
    fk_violations = [
        {"table": row[0], "rowid": row[1], "parent": row[2], "fkid": row[3]}
        for row in conn.execute("PRAGMA foreign_key_check").fetchall()
    ]
    return {
        "integrity_ok": ok,
        "integrity_errors": [] if ok else integrity_rows,
        "foreign_key_violations": fk_violations,
    }


def check_figure_ownership(conn) -> dict:
    """Figure rows whose source PDF or crop attachment belongs to a DIFFERENT paper
    than the figure row itself claims.

    `check_figures` (below) only checks that the referenced attachment/asset rows
    still EXIST; this checks that the reference is semantically correct. The two
    diverge after an incompletely-applied merge or a direct `attachments.reassign`
    that moved an attachment without also moving the figure rows derived from it --
    existence alone would miss that the figure now points at the right row for the
    wrong paper.
    """
    mismatched_source = [dict(row) for row in conn.execute(
        "SELECT f.id AS figure_id, f.paper_id AS figure_paper_id, "
        "at.paper_id AS attachment_paper_id FROM figures f "
        "JOIN attachments at ON at.id = f.source_attachment_id "
        "WHERE at.paper_id != f.paper_id ORDER BY f.id")]
    mismatched_figure_attachment = [dict(row) for row in conn.execute(
        "SELECT f.id AS figure_id, f.paper_id AS figure_paper_id, "
        "at.paper_id AS attachment_paper_id FROM figures f "
        "JOIN attachments at ON at.id = f.figure_attachment_id "
        "WHERE at.paper_id != f.paper_id ORDER BY f.id")]
    return {
        "figures_with_wrong_source_owner": mismatched_source,
        "figures_with_wrong_crop_owner": mismatched_figure_attachment,
    }


def check_identifier_consistency(conn) -> dict:
    """Identifier bookkeeping that should never happen if merge/reconcile stayed
    consistent, but is cheap to verify rather than assume.

    `identifiers_on_deleted_papers`: a reassign/merge step that soft-deletes a paper
    without first moving its identifiers off would leave one stranded here.
    `duplicate_primary_schemes`: `refmgr.repositories.identifiers.normalize_primaries`
    is supposed to guarantee at most one `is_primary` row per (paper_id, scheme) --
    this is the check that verifies that guarantee actually held, rather than trusting
    call sites to have remembered to call it.
    """
    on_deleted = [dict(row) for row in conn.execute(
        "SELECT i.id AS identifier_id, i.paper_id AS paper_id, i.scheme AS scheme, "
        "i.value AS value FROM identifiers i "
        "JOIN papers p ON p.id = i.paper_id "
        "WHERE p.deleted_at IS NOT NULL ORDER BY i.id")]
    duplicate_primary = [dict(row) for row in conn.execute(
        "SELECT paper_id, scheme, COUNT(*) AS primary_count FROM identifiers "
        "WHERE is_primary = 1 GROUP BY paper_id, scheme HAVING COUNT(*) > 1 "
        "ORDER BY paper_id, scheme")]
    return {
        "identifiers_on_deleted_papers": on_deleted,
        "duplicate_primary_schemes": duplicate_primary,
    }


def check_chunk_staleness(conn) -> dict:
    """How much of the chunk index was built by an earlier `CHUNKER_VERSION`.

    A stale-version row is neither corrupt nor missing -- the text it was split from
    may be perfectly current -- but `search --q` (registry.py `_record_chunk_coverage`)
    will not trust it, so a paper stuck here effectively falls back to a full scan
    until `registry.py reindex` re-chunks it. Rebuildable drift, not data loss: this
    never counts toward `problems`.
    """
    total = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
    stale = conn.execute(
        "SELECT COUNT(*) AS c FROM chunks WHERE chunker_version != ?",
        (CHUNKER_VERSION,),
    ).fetchone()["c"]
    stale_papers = conn.execute(
        "SELECT COUNT(DISTINCT paper_id) AS c FROM chunks WHERE chunker_version != ?",
        (CHUNKER_VERSION,),
    ).fetchone()["c"]
    return {
        "current_chunker_version": CHUNKER_VERSION,
        "chunks_total": total,
        "chunks_at_stale_version": stale,
        "papers_with_stale_chunks": stale_papers,
    }


def check_untracked_asset_files(conn, library_root: Path) -> dict:
    """Files under `assets/sha256/` with no corresponding `assets` row.

    Harmless retained bytes, not corruption or missing canonical data: a writer that
    staged a file and crashed before its `INSERT` (package 1 keeps filesystem staging
    outside the database write lock, so this is possible by design, not a bug), or a
    file left behind by a since-reverted operation. Never counts toward `problems` --
    an operator can delete these freely, but this never does so itself (read-only).
    """
    assets_root = Path(library_root) / "assets" / "sha256"
    if not assets_root.is_dir():
        return {"untracked_files": []}
    known = {row["sha256"] for row in _asset_rows(conn)}
    untracked = []
    for prefix_dir in sorted(p for p in assets_root.iterdir() if p.is_dir()):
        for path in sorted(prefix_dir.iterdir()):
            if not path.is_file():
                continue
            stem = path.name.split(".", 1)[0]
            if stem not in known:
                untracked.append(str(path.relative_to(library_root)))
    return {"untracked_files": untracked}


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
        "papers_with_figures": conn.execute(
            "SELECT COUNT(DISTINCT paper_id) AS c FROM figures").fetchone()["c"],
        "orphan_chunks": _orphan_count(conn, "chunks"),
        "orphan_terms": _orphan_count(conn, "paper_terms"),
        "orphan_figures": _orphan_count(conn, "figures"),
        "orphan_fts_rows": conn.execute(
            "SELECT COUNT(*) AS c FROM papers_fts f WHERE NOT EXISTS "
            "(SELECT 1 FROM papers p WHERE p.id = f.paper_id)").fetchone()["c"],
        "orphan_figure_fts_rows": conn.execute(
            "SELECT COUNT(*) AS c FROM figures_fts ff WHERE NOT EXISTS "
            "(SELECT 1 FROM figures f WHERE f.id = ff.figure_id)").fetchone()["c"],
    }


def check_figures(conn) -> dict:
    """Figure rows whose source PDF attachment or image asset has gone.

    Unlike chunks and papers_fts, figures are not rebuilt by `registry.py reindex`
    — they are cropped from PDFs by a heuristic, so recovery means re-running
    `registry.py figures --replace`, which is a much more expensive pass. Reporting
    them separately keeps that distinction visible in the advice.
    """
    figures_without_source = [dict(row) for row in conn.execute(
        "SELECT f.id AS figure_id, f.paper_id AS paper_id, "
        "f.source_attachment_id AS source_attachment_id FROM figures f "
        "WHERE NOT EXISTS (SELECT 1 FROM attachments at WHERE at.id = f.source_attachment_id) "
        "ORDER BY f.id")]
    figures_without_asset = [dict(row) for row in conn.execute(
        "SELECT f.id AS figure_id, f.paper_id AS paper_id, "
        "f.asset_sha256 AS asset_sha256 FROM figures f WHERE NOT EXISTS "
        "(SELECT 1 FROM assets a WHERE a.sha256 = f.asset_sha256) ORDER BY f.id")]
    return {"figures_without_source": figures_without_source,
            "figures_without_asset": figures_without_asset}


#: Findings that mean data is gone or wrong, as opposed to merely un-indexed. Only these
#: make `doctor` exit non-zero: a library with no PDFs attached yet is not unhealthy.
_PROBLEM_KEYS = ("missing_files", "corrupt_assets", "orphan_attachments",
                 "attachments_without_paper", "figures_without_source",
                 "figures_without_asset")

#: Nested problem lists reached via `report[section][key]` rather than top-level.
_NESTED_PROBLEM_KEYS = (
    ("integrity", "integrity_errors"),
    ("integrity", "foreign_key_violations"),
    ("figure_ownership", "figures_with_wrong_source_owner"),
    ("figure_ownership", "figures_with_wrong_crop_owner"),
    ("identifiers", "duplicate_primary_schemes"),
)


def run(service, *, deep: bool = False) -> dict:
    """Full report. `service` is a `ReferenceManagerService`.

    Every check here is read-only and inspects the library as it currently stands --
    nothing migrates, repairs, or otherwise modifies it. Findings are kept in three
    distinct buckets rather than one flat problem list, per the plan's "separate
    corruption, missing canonical data, rebuildable index drift, and harmless
    retained files": `problems` (corruption/missing canonical data -- these make
    `healthy` False), `indexes`/`chunk_staleness` (rebuildable derived-index drift --
    never a problem), and `untracked_files` (harmless retained bytes -- never a
    problem, never touched).
    """
    conn = service.conn
    report = {"library_root": str(service.library_root)}
    report.update(check_assets(conn, service.library_root, deep=deep))
    report["asset_check_mode"] = "deep" if deep else "shallow"
    report.update(check_links(conn))
    report.update(check_figures(conn))
    report["indexes"] = check_indexes(conn)
    report["integrity"] = check_sqlite_integrity(conn)
    report["figure_ownership"] = check_figure_ownership(conn)
    report["identifiers"] = check_identifier_consistency(conn)
    report["chunk_staleness"] = check_chunk_staleness(conn)
    report["untracked_files"] = check_untracked_asset_files(
        conn, service.library_root)["untracked_files"]

    problems = {key: len(report[key]) for key in _PROBLEM_KEYS if report.get(key)}
    for section, key in _NESTED_PROBLEM_KEYS:
        value = report.get(section, {}).get(key)
        if value:
            problems[f"{section}.{key}"] = len(value)
    report["problems"] = problems
    report["healthy"] = not problems

    advice: list[str] = []
    if not report["integrity"]["integrity_ok"]:
        advice.append("SQLite integrity_check reported structural corruption in the "
                      "database file itself; stop writing to this library and restore "
                      "from a backup rather than continuing to use it")
    if report["integrity"]["foreign_key_violations"]:
        advice.append("foreign key violations found — a row references a parent that "
                      "no longer exists; this should be unreachable via the repository "
                      "layer and points at direct/out-of-band database edits")
    if (report["figure_ownership"]["figures_with_wrong_source_owner"]
            or report["figure_ownership"]["figures_with_wrong_crop_owner"]):
        advice.append("figure rows reference an attachment owned by a different paper "
                      "— an incompletely-applied merge or a direct attachment reassign; "
                      "reconcile paper ownership by hand, this is not rebuildable")
    if report["identifiers"]["identifiers_on_deleted_papers"]:
        advice.append("identifiers still point at a soft-deleted paper — expected for "
                      "a plain delete (they are not freed for reuse), but worth a look "
                      "if that paper was meant to be absorbed by a merge instead")
    if report["identifiers"]["duplicate_primary_schemes"]:
        advice.append("more than one identifier is flagged primary for the same "
                      "paper/scheme — run refmgr.repositories.identifiers."
                      "normalize_primaries for the affected paper(s)")
    if report["chunk_staleness"]["chunks_at_stale_version"]:
        advice.append("some chunk rows were built by an earlier chunker version — "
                      "`registry.py reindex` will re-split them; no data is at risk")
    if report["untracked_files"]:
        advice.append("files under assets/ have no matching database row — harmless "
                      "leftovers from an interrupted import; safe to delete by hand, "
                      "doctor never deletes them itself")
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
    if report["figures_without_source"] or report["figures_without_asset"]:
        advice.append("figure rows have lost the PDF attachment or image asset they "
                      "were derived from — `registry.py reindex` does NOT rebuild "
                      "these; re-crop with `registry.py figures --replace`")
    if report["indexes"]["orphan_figures"] or report["indexes"]["orphan_figure_fts_rows"]:
        advice.append("figure rows or caption index rows point at papers that no longer "
                      "exist; they are stale derived data, not data loss")
    if report["orphan_assets"]:
        advice.append("orphan assets are stored bytes no live attachment references; "
                      "they are safe to keep and are not counted as a problem")
    report["advice"] = advice
    return report
