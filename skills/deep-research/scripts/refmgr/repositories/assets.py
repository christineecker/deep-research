"""Asset repository: content-addressed storage of immutable PDF bytes.

Assets are stored at `<library_root>/assets/sha256/<prefix>/<hash><ext>`
(see REFERENCE_MANAGER_V2_PLAN.md, "Suggested runtime layout"). Import is
staged (copy to a temp file, then atomically rename into place) and
checksum-verified before and after the rename, per invariant #7
("Database/filesystem consistency uses staged asset writes, checksum
verification, and reconciliation; a database transaction alone cannot
make filesystem changes atomic") and invariant #3 ("Exact binary
duplicates are deduplicated; different versions are retained").
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from .. import db

_CHUNK_SIZE = 1024 * 1024  # 1MB


class AssetCorruptionError(RuntimeError):
    """Raised when a staged/stored asset's bytes do not match its recorded checksum."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_file(path: Path) -> str:
    digest = sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class AssetRepository:
    def __init__(self, conn: sqlite3.Connection, library_root: Path):
        self.conn = conn
        self.library_root = Path(library_root)

    def _relative_storage_path(self, sha256_hex: str, ext: str) -> str:
        prefix = sha256_hex[:2]
        return str(Path("assets") / "sha256" / prefix / f"{sha256_hex}{ext}")

    def get(self, sha256: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM assets WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return dict(row) if row is not None else None

    def verify_integrity(self, sha256: str) -> bool:
        row = self.get(sha256)
        if row is None:
            return False
        full_path = self.library_root / row["storage_path"]
        if not full_path.exists():
            return False
        try:
            actual_hash = _hash_file(full_path)
        except OSError:
            return False
        return actual_hash == sha256

    def stage_and_commit(self, source_path, mime_type: str | None = None) -> str:
        source_path = Path(source_path)
        digest = _hash_file(source_path)
        ext = source_path.suffix.lower()
        relative_path = self._relative_storage_path(digest, ext)
        final_path = self.library_root / relative_path

        existing = self.get(digest)
        if existing is not None and final_path.exists():
            byte_size = final_path.stat().st_size
            if byte_size != existing["byte_size"]:
                raise AssetCorruptionError(
                    f"asset {digest}: stored file size {byte_size} does not match "
                    f"recorded byte_size {existing['byte_size']}"
                )
            return digest

        final_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = final_path.parent / f"{final_path.name}.tmp-{uuid.uuid4().hex}"
        try:
            _copy_file(source_path, tmp_path)
            os.replace(tmp_path, final_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

        rehashed = _hash_file(final_path)
        if rehashed != digest:
            final_path.unlink(missing_ok=True)
            raise AssetCorruptionError(
                f"asset {digest}: re-hash after staging produced {rehashed}"
            )

        byte_size = final_path.stat().st_size
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT OR IGNORE INTO assets "
                "(sha256, byte_size, mime_type, storage_path, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (digest, byte_size, mime_type, relative_path, _now()),
            )
        return digest


def _copy_file(src: Path, dst: Path) -> None:
    with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
        while True:
            chunk = fsrc.read(_CHUNK_SIZE)
            if not chunk:
                break
            fdst.write(chunk)
