#!/usr/bin/env python3
"""backup.py — versioned backup and restore for a reference-manager repo.

Covers everything the reference manager treats as canonical or as a durable ledger:
`data/papers/` (registry.jsonl, pool.jsonl, annotations.jsonl, embeddings.jsonl,
export_ledger.jsonl, extractions/, appraisals/ — whatever exists, copied wholesale so a
new state file added later is covered without this module needing to know its name),
`data/refmgr/` (the SQLite library plus its content-addressed asset store), and
`data/sources/` (the global snapshot store and its own asset/event log). Run-scoped state
(`runs/`, `projects/`, `templates/`, `exports/`) is deliberately excluded — that is
in-flight pipeline output, not the reference-manager library, and is named explicitly
in every manifest under `excluded_roots` so a reader never has to guess what "backup
the library" did and did not mean.

Consistency protocol (hardening plan package 6: "a copied database and independently
changing registry are not a consistent backup")
-------------------------------------------------------------------------------------
`create_backup` holds every repo-scoped advisory lock a mutating command in this repo
takes against `data/papers/` or the refmgr mirror -- `registry`, `annotations`,
`embeddings`, `export_ledger` -- for the ENTIRE copy window, in that fixed order. That
closes exactly the gap the plan calls out: without it, `registry.jsonl` could gain a new
`refmgr_paper_id` link (via a concurrent `commit()`) after this code copies the SQLite
database but before it copies `registry.jsonl`, producing a registry that references a
paper the backed-up database has never heard of.

`data/sources/` and `data/refmgr/assets/` are copied under the same lock window too, but
for a different reason than mutual exclusion: nothing writes them through these four
locks, so the lock buys nothing there directly. What makes them safe to copy anyway is
that both stores are content-addressed and every writer stages bytes to a temp path and
`os.replace()`s them into place (`store.py` snapshots, `refmgr.repositories.assets`) --
a file this code reads is always either completely absent or completely present, never
torn mid-write. A concurrent fetch may or may not make it into this particular backup;
that is an acceptable "as of approximately now" ambiguity, not a correctness bug.

The refmgr SQLite file itself is copied with `sqlite3.Connection.backup()` (the online
backup API), which produces a transactionally-consistent single-file snapshot even
against a WAL-mode database mid-write; the surrounding lock stops a *mirror* from
running concurrently, not the copy itself, which does not need it to be correct on its
own but benefits from it (a mirror pass and the backup can never observe each other's
uncommitted middle).

`lock_timeout` bounds how long `create_backup` will wait to acquire those locks before
raising `registry.AdvisoryLockTimeout` instead of hanging behind a stuck writer --
"explicit refusal when a consistent snapshot cannot be acquired" (plan acceptance). Pass
`None` for an unattended/scheduled backup that should simply wait.

Restore (`restore_backup`) verifies every file's recorded checksum against the manifest
BEFORE writing anything to the destination -- a truncated or tampered backup is rejected
outright, never partially restored. It refuses to write into an existing non-empty
directory, so a restore can never overwrite an active library; the caller always names a
fresh directory to restore into and inspect. After copying, it runs refmgr's own deep
`doctor` pass against the restored library so a restore's caller gets the same integrity
signal a live library would, without this module reimplementing those checks.

Subcommands
  create   --repo --out [--timeout SECONDS]         write a versioned backup
  verify   --backup                                  checksum-verify a backup in place
  restore  --backup --dest [--no-deep-doctor]        restore into a fresh directory

Environment: python3, stdlib only. No pip installs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from registry import AdvisoryLockTimeout, advisory_lock  # noqa: E402  (sibling module)

SCHEMA_VERSION = 1
BACKUP_MANIFEST_VERSION = 1
MANIFEST_FILENAME = "backup_manifest.json"

#: Repo-scoped advisory locks that guard every writer touching what this backup covers.
#: Held together, in this fixed order, for the whole copy window -- see module
#: docstring "Consistency protocol". `corpus`/`taskboard`/`guard` are run-scoped (keyed
#: by a run directory, not this repo root) and are out of scope on purpose: this backs
#: up the reference-manager library, not in-flight run state.
_COORDINATING_LOCKS = ("registry", "annotations", "embeddings", "export_ledger")

#: Directories under repo_root this backup copies wholesale.
_BACKUP_ROOTS = {"papers": "data/papers", "refmgr": "data/refmgr", "sources": "data/sources"}

_EXCLUDED_ROOTS = ["projects", "runs", "templates", "exports", ".locks"]

_CHUNK_SIZE = 1024 * 1024


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class BackupError(RuntimeError):
    """Raised for a backup/restore failure that should stop the operation cleanly."""


@contextmanager
def _hold_coordinating_locks(repo_root: Path, *, timeout: float | None):
    with ExitStack() as stack:
        for name in _COORDINATING_LOCKS:
            stack.enter_context(advisory_lock(repo_root, name, timeout=timeout))
        yield


def _refmgr_schema_migrations(db_path: Path) -> list:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        try:
            rows = conn.execute("SELECT id FROM schema_migrations ORDER BY id").fetchall()
        except sqlite3.OperationalError:
            return []
        return [row[0] for row in rows]
    finally:
        conn.close()


def _backup_sqlite_consistent(src_path: Path, dst_path: Path) -> None:
    """Copy a live SQLite database via the online backup API: a transactionally
    consistent snapshot as of one point in time, safe even under WAL mid-write."""
    src_conn = sqlite3.connect(src_path)
    try:
        dst_conn = sqlite3.connect(dst_path)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()


def _is_wal_sidecar(path: Path) -> bool:
    return path.suffix in (".sqlite3-wal", ".sqlite3-shm")


def create_backup(repo_root, dest, *, lock_timeout: float | None = 30.0) -> dict:
    """Write a versioned backup of `repo_root`'s reference-manager library to `dest`.

    `dest` must not already exist (or must be empty) -- this never merges into or
    overwrites an existing directory. Returns the manifest dict also written to
    `dest/backup_manifest.json`. Raises `BackupError` if `dest` is unusable,
    `registry.AdvisoryLockTimeout` if `lock_timeout` elapses before every coordinating
    lock is acquired.
    """
    repo_root = Path(repo_root).expanduser().resolve()
    dest = Path(dest).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()):
        raise BackupError(f"destination {dest} already exists and is not empty")

    file_checksums: dict = {}
    roots_present: dict = {}

    # `dest` is only created once every coordinating lock is held, so a timed-out
    # lock acquisition (AdvisoryLockTimeout) leaves no new directory behind for a
    # caller to clean up -- either dest never existed, or it was already there and
    # empty, which is unchanged either way.
    with _hold_coordinating_locks(repo_root, timeout=lock_timeout):
        dest.mkdir(parents=True, exist_ok=True)
        for key, rel in _BACKUP_ROOTS.items():
            src_root = repo_root / rel
            roots_present[key] = src_root.exists()
            if not src_root.exists():
                continue

            db_path = src_root / "library.sqlite3"
            if key == "refmgr" and db_path.exists():
                (dest / rel).mkdir(parents=True, exist_ok=True)
                _backup_sqlite_consistent(db_path, dest / rel / "library.sqlite3")

            for path in sorted(src_root.rglob("*")):
                if not path.is_file():
                    continue
                if key == "refmgr" and path.name == "library.sqlite3" and path.parent == src_root:
                    continue  # handled above via the backup API
                if _is_wal_sidecar(path):
                    continue  # not part of the logical database; the checkpointed
                              # copy above already captured everything committed
                relative = path.relative_to(repo_root)
                dst_path = dest / relative
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst_path)
                file_checksums[str(relative)] = {
                    "sha256": _sha256_file(dst_path), "bytes": dst_path.stat().st_size,
                }

        refmgr_db_dst = dest / "data" / "refmgr" / "library.sqlite3"
        schema_migrations = []
        if refmgr_db_dst.exists():
            relative = refmgr_db_dst.relative_to(dest)
            file_checksums[str(relative)] = {
                "sha256": _sha256_file(refmgr_db_dst), "bytes": refmgr_db_dst.stat().st_size,
            }
            schema_migrations = _refmgr_schema_migrations(refmgr_db_dst)

    manifest = {
        "backup_manifest_version": BACKUP_MANIFEST_VERSION,
        "tool": "backup.py",
        "created_at": _now(),
        "source_repo_root": str(repo_root),
        "roots": {key: rel for key, rel in _BACKUP_ROOTS.items() if roots_present.get(key)},
        "excluded_roots": list(_EXCLUDED_ROOTS),
        "refmgr_schema_migrations": schema_migrations,
        "files": file_checksums,
    }
    (dest / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _load_manifest(backup_dir: Path) -> dict:
    manifest_path = backup_dir / MANIFEST_FILENAME
    if not manifest_path.exists():
        raise BackupError(f"{backup_dir} is not a backup: no {MANIFEST_FILENAME}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BackupError(f"{manifest_path} is corrupt: {exc}") from exc
    if manifest.get("backup_manifest_version") != BACKUP_MANIFEST_VERSION:
        raise BackupError(
            f"unsupported backup_manifest_version "
            f"{manifest.get('backup_manifest_version')!r} (this tool understands "
            f"{BACKUP_MANIFEST_VERSION})"
        )
    return manifest


def _verify_files(backup_dir: Path, manifest: dict) -> list:
    """Every checksum failure found, or `[]` if the backup verifies clean."""
    failures = []
    for relative, info in manifest.get("files", {}).items():
        src = backup_dir / relative
        if not src.exists():
            failures.append({"path": relative, "reason": "missing from backup"})
            continue
        actual_bytes = src.stat().st_size
        if actual_bytes != info.get("bytes"):
            failures.append({
                "path": relative, "reason": "size mismatch",
                "expected_bytes": info.get("bytes"), "actual_bytes": actual_bytes,
            })
            continue
        actual_sha256 = _sha256_file(src)
        if actual_sha256 != info.get("sha256"):
            failures.append({
                "path": relative, "reason": "checksum mismatch",
                "expected_sha256": info.get("sha256"), "actual_sha256": actual_sha256,
            })
    return failures


def verify_backup(backup_dir) -> dict:
    """Checksum-verify a backup in place, without restoring it."""
    backup_dir = Path(backup_dir).expanduser().resolve()
    manifest = _load_manifest(backup_dir)
    failures = _verify_files(backup_dir, manifest)
    return {
        "backup_dir": str(backup_dir), "ok": not failures,
        "files_checked": len(manifest.get("files", {})), "checksum_failures": failures,
    }


def restore_backup(backup_dir, dest_repo_root, *, deep_doctor: bool = True) -> dict:
    """Restore `backup_dir` (as produced by `create_backup`) into `dest_repo_root`.

    `dest_repo_root` must not already exist (or must be empty) -- restore never
    overwrites an active library. Raises `BackupError` if the backup fails checksum
    verification (nothing is written in that case) or `dest_repo_root` is unusable.
    """
    backup_dir = Path(backup_dir).expanduser().resolve()
    dest_repo_root = Path(dest_repo_root).expanduser().resolve()

    manifest = _load_manifest(backup_dir)
    failures = _verify_files(backup_dir, manifest)
    if failures:
        raise BackupError(
            f"backup at {backup_dir} failed verification for {len(failures)} file(s); "
            f"refusing to restore any of it: {failures}"
        )

    if dest_repo_root.exists() and any(dest_repo_root.iterdir()):
        raise BackupError(
            f"destination {dest_repo_root} already exists and is not empty -- restore "
            f"only ever writes into a fresh directory, never an active library"
        )
    dest_repo_root.mkdir(parents=True, exist_ok=True)

    for relative in manifest.get("files", {}):
        src = backup_dir / relative
        dst = dest_repo_root / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    report = {
        "backup_dir": str(backup_dir),
        "restored_to": str(dest_repo_root),
        "files_restored": len(manifest.get("files", {})),
        "roots": manifest.get("roots", {}),
        "excluded_roots": manifest.get("excluded_roots", []),
    }

    refmgr_db = dest_repo_root / "data" / "refmgr" / "library.sqlite3"
    if refmgr_db.exists():
        import refmgr.doctor as _doctor
        from refmgr.service import ReferenceManagerService

        service = ReferenceManagerService(dest_repo_root / "data" / "refmgr")
        try:
            report["papers_restored"] = service.conn.execute(
                "SELECT COUNT(*) FROM papers"
            ).fetchone()[0]
            report["doctor"] = _doctor.run(service, deep=deep_doctor)
        finally:
            service.close()

    return report


def cmd_create(args) -> int:
    try:
        manifest = create_backup(args.repo, args.out, lock_timeout=args.timeout)
    except (BackupError, AdvisoryLockTimeout) as exc:
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "create",
              "error": str(exc)})
        return 1
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "create",
          "out": args.out, "files": len(manifest["files"]), "roots": manifest["roots"],
          "created_at": manifest["created_at"]})
    return 0


def cmd_verify(args) -> int:
    try:
        result = verify_backup(args.backup)
    except BackupError as exc:
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "verify",
              "error": str(exc)})
        return 1
    emit({"schema_version": SCHEMA_VERSION, "status": "ok" if result["ok"] else "error",
          "command": "verify", **result})
    return 0 if result["ok"] else 1


def cmd_restore(args) -> int:
    try:
        report = restore_backup(
            args.backup, args.dest, deep_doctor=not args.no_deep_doctor
        )
    except BackupError as exc:
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "restore",
              "error": str(exc)})
        return 1
    healthy = report.get("doctor", {}).get("healthy", True)
    emit({"schema_version": SCHEMA_VERSION,
          "status": "ok" if healthy else "error", "command": "restore", **report})
    return 0 if healthy else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="backup.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("create", help="write a versioned backup")
    s.add_argument("--repo", required=True)
    s.add_argument("--out", required=True, help="destination directory (must not exist yet)")
    s.add_argument("--timeout", type=float, default=30.0,
                   help="seconds to wait for coordinating locks (default 30; "
                        "pass a large value or use --timeout 0 to fail immediately "
                        "if already held)")
    s.set_defaults(func=cmd_create)

    s = sub.add_parser("verify", help="checksum-verify a backup in place")
    s.add_argument("--backup", required=True)
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("restore", help="restore a backup into a fresh directory")
    s.add_argument("--backup", required=True)
    s.add_argument("--dest", required=True, help="destination directory (must not exist yet)")
    s.add_argument("--no-deep-doctor", dest="no_deep_doctor", action="store_true",
                   help="skip hashing every asset during the post-restore doctor pass")
    s.set_defaults(func=cmd_restore)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
