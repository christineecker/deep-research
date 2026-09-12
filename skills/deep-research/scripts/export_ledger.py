#!/usr/bin/env python3
"""export_ledger.py — incremental-export ledger for `export.py readcube --incremental`.

Implements the "Incremental export and consistency" section of Phase 2.3 in
`REFERENCE_MANAGER_V2_PLAN.md`'s "New Phase 2 — ReadCube export command". Tracks, per
`(destination_identity, evidence_id)` pair, the last export attempt of that record to
that destination, so a subsequent `--incremental` run publishes only what changed.

Ledger file: `<repo>/data/papers/export_ledger.jsonl`, one JSON line per
`(destination_identity, evidence_id)` pair::

    {"schema_version": 1, "destination_identity": "...", "evidence_id": "...",
     "metadata_hash": "...", "attachment_fingerprint": "...", "batch_id": "...",
     "published_at": "...", "status": "published" | "failed"}

Only a `"published"` entry establishes an incremental baseline (plan: "Only published
bundles establish incremental baselines") — a `"failed"` entry keeps the ledger
informative about the attempt but is never treated as a prior successful export when
diffing.

`destination_identity` is the resolved absolute path of `--destination` (plan: "a
resolved absolute path is the documented-sufficient approach for this phase" — no
cross-device/iCloud-identity resolution is attempted).

Locking
-------
Mirrors `registry.py`'s own convention: `advisory_lock(repo_root, name)` for cross-process
exclusion, tmp-file + `os.replace` for atomic writes (same pattern as `Registry.save` /
`Annotations.save`). Unlike the registry's own known defect (documented in
`REFERENCE_MANAGER_V2_PLAN.md`'s "Known issues and standing decisions" section:
`Registry.__init__` loads state *before* any call site takes the lock, a real
read-modify-write race), every operation
here acquires the lock *first* and only loads ledger state once it is held — there is no
load-then-lock window anywhere in this module.

`load`/`record_published`/`record_failed` each take the lock for the span of one
self-contained operation. `diff`, used on its own, is a read-only lock-and-release too.
The one place true cross-operation atomicity matters — "diff at the start" through
"record_published/record_failed at the end of publish", so no concurrent export process
can publish conflicting state in between — is `ExportLedger.locked()`, a context manager
that holds a single lock acquisition across the whole diff -> publish -> record window
and yields a `_LockedLedgerHandle` operating on one in-memory snapshot. `export.py`'s
real (non-dry-run) `--incremental` path must use `locked()`, not the individual
lock-per-call methods, to get that guarantee.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from registry import advisory_lock, repo_paths  # noqa: E402  (reuse, do not reimplement)

SCHEMA_VERSION = 1
LEDGER_LOCK_NAME = "export_ledger"


def attachment_fingerprint(assets: list[tuple[str, str]]) -> str:
    """Hash over the ordered list of `(role, sha256)` pairs for one record's resolved
    assets (plan: "a hash over the ordered list of (role, sha256) pairs for that record's
    resolved assets"). `[]` (a metadata-only record) hashes to a stable, distinct value —
    not an empty string — so it still round-trips through equality checks."""
    canonical = json.dumps(list(assets), sort_keys=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ledger_path(repo_root) -> Path:
    return repo_paths(repo_root)["papers"] / "export_ledger.jsonl"


def _key(destination_identity: str, evidence_id: str) -> str:
    return f"{destination_identity}\x00{evidence_id}"


def _entries_for_destination(all_entries: dict[str, dict], destination_identity: str) -> dict[str, dict]:
    return {
        rec["evidence_id"]: rec
        for rec in all_entries.values()
        if rec.get("destination_identity") == destination_identity
    }


def _diff_against(current: dict[str, dict], candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """A candidate is "unchanged" only against a prior entry whose own `status` is
    `"published"` and whose `metadata_hash`/`attachment_fingerprint` both match exactly —
    a `"failed"`-only history (or no history) is always `changed_or_new`."""
    changed_or_new: list[dict] = []
    unchanged: list[dict] = []
    for cand in candidates:
        prior = current.get(cand["evidence_id"])
        if (prior is not None and prior.get("status") == "published"
                and prior.get("metadata_hash") == cand["metadata_hash"]
                and prior.get("attachment_fingerprint") == cand["attachment_fingerprint"]):
            unchanged.append(cand)
        else:
            changed_or_new.append(cand)
    return changed_or_new, unchanged


class ExportLedger:
    """The `<repo>/data/papers/export_ledger.jsonl` store, keyed by
    `(destination_identity, evidence_id)`."""

    def __init__(self, repo_root):
        self.repo_root = repo_paths(repo_root)["repo_root"]
        self.path = ledger_path(self.repo_root)

    # ---- lock-held primitives; callers below always hold the lock first ----

    def _load_all_locked(self) -> dict[str, dict]:
        entries: dict[str, dict] = {}
        if not self.path.exists():
            return entries
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a corrupt ledger line must never block export
                if not isinstance(rec, dict):
                    continue
                dest = rec.get("destination_identity")
                eid = rec.get("evidence_id")
                if dest and eid:
                    entries[_key(dest, eid)] = rec
        return entries

    def _save_all_locked(self, entries: dict[str, dict]) -> None:
        """Atomic tmp-file + `os.replace`, mirroring `registry.py Registry.save`."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for key in sorted(entries):
                fh.write(json.dumps(entries[key], ensure_ascii=False, sort_keys=False))
                fh.write("\n")
        tmp.replace(self.path)

    # ---- public single-operation methods (each takes the lock itself) ----

    def load(self, destination_identity: str) -> dict[str, dict]:
        """`{evidence_id: entry}` for this destination, `{}` if none yet.

        Takes and releases `advisory_lock(repo_root, "export_ledger")` for the read
        itself. Safe to call standalone for a read-only peek; a caller that intends to
        act on this snapshot and then write back must instead use `locked()` so the lock
        spans both the read and the later write.
        """
        with advisory_lock(self.repo_root, LEDGER_LOCK_NAME):
            all_entries = self._load_all_locked()
        return _entries_for_destination(all_entries, destination_identity)

    def record_published(self, destination_identity: str, evidence_id: str,
                          metadata_hash: str, attachment_fingerprint: str,
                          batch_id: str, published_at: str) -> None:
        """Upsert one entry with `status="published"`."""
        with advisory_lock(self.repo_root, LEDGER_LOCK_NAME):
            entries = self._load_all_locked()
            entries[_key(destination_identity, evidence_id)] = {
                "schema_version": SCHEMA_VERSION,
                "destination_identity": destination_identity,
                "evidence_id": evidence_id,
                "metadata_hash": metadata_hash,
                "attachment_fingerprint": attachment_fingerprint,
                "batch_id": batch_id,
                "published_at": published_at,
                "status": "published",
            }
            self._save_all_locked(entries)

    def record_failed(self, destination_identity: str, evidence_id: str,
                       batch_id: str | None, published_at: str, error: str) -> None:
        """Upsert one entry with `status="failed"` — informative, never a valid
        incremental baseline (see `_diff_against`)."""
        with advisory_lock(self.repo_root, LEDGER_LOCK_NAME):
            entries = self._load_all_locked()
            entries[_key(destination_identity, evidence_id)] = {
                "schema_version": SCHEMA_VERSION,
                "destination_identity": destination_identity,
                "evidence_id": evidence_id,
                "metadata_hash": None,
                "attachment_fingerprint": None,
                "batch_id": batch_id,
                "published_at": published_at,
                "status": "failed",
                "error": error,
            }
            self._save_all_locked(entries)

    def diff(self, destination_identity: str,
             candidates: list[dict]) -> tuple[list[dict], list[dict]]:
        """`candidates`: `[{evidence_id, metadata_hash, attachment_fingerprint}, ...]` for
        the currently-selected records. Returns `(changed_or_new, unchanged)`. Read-only —
        takes and releases the lock for the read; use `locked()` when the diff must be
        atomic with a later write."""
        current = self.load(destination_identity)
        return _diff_against(current, candidates)

    @contextlib.contextmanager
    def locked(self, destination_identity: str):
        """Hold one `advisory_lock(repo_root, "export_ledger")` acquisition across an
        entire diff -> publish -> record window, so no concurrent export process can
        publish conflicting state in between (the correctness property the plan requires:
        no read-then-later-write-without-holding-the-lock-continuously window). Yields a
        `_LockedLedgerHandle` exposing `diff`/`record_published`/`record_failed` against
        one in-memory snapshot taken at entry and flushed to disk once at exit.
        """
        with advisory_lock(self.repo_root, LEDGER_LOCK_NAME):
            all_entries = self._load_all_locked()
            handle = _LockedLedgerHandle(destination_identity, all_entries)
            yield handle
            self._save_all_locked(handle.all_entries)


class _LockedLedgerHandle:
    """Operates on an in-memory snapshot taken under a lock already held by
    `ExportLedger.locked()`. Never acquires or releases the lock itself; `locked()`
    persists `self.all_entries` once, after the `with` block body finishes."""

    def __init__(self, destination_identity: str, all_entries: dict[str, dict]):
        self.destination_identity = destination_identity
        self.all_entries = all_entries

    def diff(self, candidates: list[dict]) -> tuple[list[dict], list[dict]]:
        current = _entries_for_destination(self.all_entries, self.destination_identity)
        return _diff_against(current, candidates)

    def record_published(self, evidence_id: str, metadata_hash: str,
                          attachment_fingerprint: str, batch_id: str,
                          published_at: str) -> None:
        self.all_entries[_key(self.destination_identity, evidence_id)] = {
            "schema_version": SCHEMA_VERSION,
            "destination_identity": self.destination_identity,
            "evidence_id": evidence_id,
            "metadata_hash": metadata_hash,
            "attachment_fingerprint": attachment_fingerprint,
            "batch_id": batch_id,
            "published_at": published_at,
            "status": "published",
        }

    def record_failed(self, evidence_id: str, batch_id: str | None,
                       published_at: str, error: str) -> None:
        self.all_entries[_key(self.destination_identity, evidence_id)] = {
            "schema_version": SCHEMA_VERSION,
            "destination_identity": self.destination_identity,
            "evidence_id": evidence_id,
            "metadata_hash": None,
            "attachment_fingerprint": None,
            "batch_id": batch_id,
            "published_at": published_at,
            "status": "failed",
            "error": error,
        }
