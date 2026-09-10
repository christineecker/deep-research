#!/usr/bin/env python3
"""store.py — the evidence-kernel snapshot store (library + thin CLI).

The single implementation of snapshot and span verification for the whole skill.
`scripts/source.py`, `scripts/assemble.py`, `scripts/verify.py` and `scripts/okf.py`
all import this module rather than reimplementing the checks, so there is exactly one
place where "is this evidence real?" is answered.

Contracts: `references/schema.md` §10 (snapshot record), §11 (event record),
§12 (claim span record), §13 (assembler result / reason codes), resolutions R10-R24.
Narrative: `references/evidence-kernel.md`. Design: `references/evidence-kernel.md`
Phases 1, 3 and 6.

Layout (R13 — PDFs are never duplicated per run):

    <run>/sources/src-<64 hex>.json    immutable snapshots, write-once with O_EXCL
    <run>/events.jsonl                 append-only retrieval log
    <wiki>/assets/papers/<file>.pdf    the shared PDF library; snapshots point at it

Identity (R11):

    source_id    = "src-" + sha256_hex( utf8(url) + b"\\x00" + utf8(text) )
    content_hash = "sha256:" + sha256_hex( utf8(text) )

Offsets (R10) are Python `str` indices into the snapshot's decoded `text` — Unicode code
points, NOT bytes, NOT UTF-16 units, NOT offsets into any rendered view. `text` is stored
and compared without any normalization (no NFC/NFD, no whitespace collapsing, no newline
rewriting), on write and on read; a normalizing reader would invalidate every span.

Importable API
--------------
    compute_source_id(url, text)                      -> str
    compute_content_hash(text)                        -> str
    sha256_text(text) / sha256_file(path)             -> str (bare hex)

    write_snapshot(run_dir, *, url, text, title, access, origin, paper,
                   asset=None, retrieved_at=None, event_type="fetch", fresh=False,
                   actor="main", detail=None, write_event=True)          -> snapshot
    write_snapshot_result(...)      same kwargs                          -> {snapshot, created, event}
    register_text(run_dir, *, url, text, title, access, origin, paper, ...) -> snapshot
    read_snapshot(run_dir, source_id)                                    -> snapshot (verified)
    verify_snapshot(run_dir, source_id)                                  -> result dict
    check_snapshot_integrity(snapshot)                                   -> None | raises
    list_snapshots(run_dir)                                              -> [source_id, ...]
    iter_snapshots(run_dir)                                              -> yields snapshots

    slice_span(run_dir, source_id, start, end)                           -> str
    slice_text(text, start, end)                                         -> str
    verify_span(run_dir, span_record, *, excerpt=None, strict_access=False) -> result dict

    append_event(run_dir, event)                                         -> event
    read_events(run_dir)                                                 -> [event, ...]
    fresh_event(run_dir, source_id, *, wiki_root=None)                   -> event | None
    has_fresh_retrieval(run_dir, source_id_or_url, *, wiki_root=None)    -> bool

    Store(run_dir, wiki_root=None)   caching facade with the same methods; instantiate
                                     once in assemble.py / verify.py / okf.py.

Every read path verifies. There is no unchecked read.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import errno
import hashlib
import json
import os
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import emit_json as _emit, utcnow, wiki_root_for_run  # noqa: E402  (sibling module, stdlib-only)

SCHEMA_VERSION = 1
VERSION = "deep-research/0.1"

#: Maximum characters a single claim span may cover (schema.md §12 P2, per span — R18).
MAX_SPAN_CHARS = 2000

SOURCE_ID_RE = re.compile(r"^src-[0-9a-f]{64}$")
EVENT_ID_RE = re.compile(r"^ev-(\d{4,})$")

ACCESS_VALUES = ("full_text", "abstract", "preprint", "guideline", "web")
ORIGIN_VALUES = ("pubmed", "pmc", "europepmc", "unpaywall", "oa-pdf",
                 "user-supplied-pdf", "web")
EVENT_TYPES = ("fetch", "local_pdf", "register", "read")
#: Event types that may ever carry ``fresh: true`` (R22).
FRESH_EVENT_TYPES = ("fetch", "local_pdf")

#: Weakest-first ordering of `access`, for artifacts backed by several snapshots (§13).
ACCESS_STRENGTH = {"web": 0, "abstract": 1, "preprint": 2, "guideline": 3, "full_text": 4}

#: Field order of a snapshot record on disk (schema.md §10).
SNAPSHOT_FIELDS = ("schema_version", "source_id", "content_hash", "url", "title",
                   "retrieved_at", "access", "paper", "origin", "asset", "text")
#: Field order of an event record on disk (schema.md §11).
EVENT_FIELDS = ("schema_version", "event_id", "type", "source_id", "url", "at",
                "fresh", "sha256", "actor", "detail")


# --------------------------------------------------------------------- errors --


class StoreError(Exception):
    """Base error. Carries a schema.md §13 `reason_code` where one applies."""

    reason_code: str | None = None

    def __init__(self, message: str, *, reason_code: str | None = None, **extra):
        super().__init__(message)
        self.message = message
        if reason_code is not None:
            self.reason_code = reason_code
        self.extra = extra

    def to_json(self) -> dict:
        out = {"ok": False, "error": self.message,
               "error_type": type(self).__name__, "reason_code": self.reason_code}
        out.update(self.extra)
        return out


class SchemaError(StoreError):
    """A record violated the schema (bad enum, wrong type, missing field)."""


class UnknownSourceError(StoreError):
    reason_code = "UNKNOWN_SOURCE"


class SnapshotIntegrityError(StoreError):
    """Recomputed `content_hash` or `source_id` disagrees with the stored value."""

    reason_code = "SNAPSHOT_HASH_MISMATCH"


class SpanRangeError(StoreError):
    reason_code = "SPAN_OUT_OF_RANGE"


class SpanTooLongError(StoreError):
    reason_code = "SPAN_TOO_LONG"


class ExcerptMismatchError(StoreError):
    reason_code = "EXCERPT_MISMATCH"


class AssetHashMismatchError(StoreError):
    reason_code = "ASSET_HASH_MISMATCH"


class SnapshotConflictError(StoreError):
    """A write-once violation: same source_id, different metadata (R12)."""

    reason_code = "SNAPSHOT_WRITE_CONFLICT"


# -------------------------------------------------------------------- hashing --


def sha256_text(text: str) -> str:
    """Lowercase hex sha256 of `text` encoded as UTF-8. No prefix."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path) -> str:
    """Lowercase hex sha256 of a file's bytes. No prefix."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def compute_source_id(url: str, text: str) -> str:
    """R11: "src-" + sha256_hex(utf8(url) + one literal NUL byte + utf8(text))."""
    h = hashlib.sha256()
    h.update(url.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return "src-" + h.hexdigest()


def compute_content_hash(text: str) -> str:
    """R11: "sha256:" + sha256_hex(utf8(text)). The URL is deliberately excluded."""
    return "sha256:" + sha256_text(text)

def parse_ts(value: str | None) -> _dt.datetime | None:
    """Parse an ISO-8601 Z timestamp (S2). Returns None when unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc)


# ---------------------------------------------------------------------- paths --


def sources_dir(run_dir, *, dirname: str = "sources") -> Path:
    return Path(run_dir).expanduser() / dirname


def events_path(run_dir) -> Path:
    return Path(run_dir).expanduser() / "events.jsonl"


def snapshot_path(run_dir, source_id: str, *, dirname: str = "sources") -> Path:
    if not SOURCE_ID_RE.match(source_id or ""):
        raise SchemaError("malformed source_id: %r (want src-<64 lowercase hex>)" % (source_id,),
                          reason_code="UNKNOWN_SOURCE")
    return sources_dir(run_dir, dirname=dirname) / (source_id + ".json")


#: The legacy global snapshot dirname (kept only for backward-compatible reads).
_LEGACY_GLOBAL_SNAPSHOT_DIRNAME = "sources"

#: The current global snapshot dirname (POOL_ARCHITECTURE_OPTIMIZATION_PLAN.md priority 3).
GLOBAL_SNAPSHOT_DIRNAME = "snapshots"


def global_sources_root(repo_root) -> Path:
    """`<repo>/data/sources` treated as a logical `run_dir` for the global source store
    (POOL_ARCHITECTURE_IMPLEMENTATION_PLAN.md "Source Storage").

    Reuse, not a new path scheme: `sources_dir()`/`events_path()`/`snapshot_path()` and
    every write/read/verify function above are pure functions of a `run_dir`-shaped
    directory, parameterized by `dirname` for the snapshot subdirectory. Passing this path
    as that `run_dir` with `dirname=GLOBAL_SNAPSHOT_DIRNAME` gets snapshots at
    `data/sources/snapshots/src-*.json` and events at `data/sources/events.jsonl`, through
    the same audited write-once/hash/verify code, with zero risk to existing run-local
    behavior (which always uses the `dirname="sources"` default).
    `data/sources/assets/` is separate: hash-named binary PDFs, not JSON snapshots.
    `data/sources/sources/` is the legacy snapshot dirname; global reads fall back to it
    for compatibility, global writes never use it (see `global_read_snapshot`).
    """
    return Path(repo_root).expanduser().resolve() / "data" / "sources"


def global_snapshot_path(repo_root, source_id: str) -> Path:
    """`data/sources/snapshots/<source_id>.json` — the current global snapshot location."""
    return snapshot_path(global_sources_root(repo_root), source_id, dirname=GLOBAL_SNAPSHOT_DIRNAME)


def global_events_path(repo_root) -> Path:
    """`data/sources/events.jsonl` — shared regardless of snapshot dirname."""
    return events_path(global_sources_root(repo_root))


def global_read_snapshot(repo_root, source_id: str) -> dict:
    """Read a global snapshot: `data/sources/snapshots/` first, then the legacy
    `data/sources/sources/` dirname for compatibility with runs written before priority 3
    of POOL_ARCHITECTURE_OPTIMIZATION_PLAN.md. Writes never use the legacy dirname."""
    root = global_sources_root(repo_root)
    try:
        return read_snapshot(root, source_id, dirname=GLOBAL_SNAPSHOT_DIRNAME)
    except UnknownSourceError:
        return read_snapshot(root, source_id, dirname=_LEGACY_GLOBAL_SNAPSHOT_DIRNAME)


def global_list_snapshots(repo_root) -> list[str]:
    """Every global `source_id`, from both the current and legacy snapshot dirnames."""
    root = global_sources_root(repo_root)
    current = list_snapshots(root, dirname=GLOBAL_SNAPSHOT_DIRNAME)
    legacy = list_snapshots(root, dirname=_LEGACY_GLOBAL_SNAPSHOT_DIRNAME)
    return sorted(set(current) | set(legacy))


def global_write_snapshot_result(repo_root, **kwargs) -> dict:
    """Write a snapshot to the global store. Always writes to `data/sources/snapshots/`,
    never to the legacy `data/sources/sources/` dirname (compatibility is read-only)."""
    return write_snapshot_result(global_sources_root(repo_root), dirname=GLOBAL_SNAPSHOT_DIRNAME,
                                 **kwargs)


def run_created_at(run_dir) -> str | None:
    """The run's `created_at` from `config.json`, or None when absent/unreadable.

    Freshness (R15 condition 4) cannot be evaluated without it, and fails closed: a run
    with no `config.json` `created_at` has no fresh sources.
    """
    path = Path(run_dir).expanduser() / "config.json"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(cfg, dict):
        value = cfg.get("created_at")
        if isinstance(value, str) and value:
            return value
    return None


def ensure_run(run_dir, *, dirname: str = "sources") -> Path:
    """Create `<run>/<dirname>/` if missing. Never touches anything else."""
    run = Path(run_dir).expanduser()
    (run / dirname).mkdir(parents=True, exist_ok=True)
    return run


# ------------------------------------------------------------------ validation --


def _req_str(rec: dict, key: str, *, allow_none: bool = False) -> str | None:
    value = rec.get(key)
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        raise SchemaError("%s must be a string%s, got %r"
                          % (key, " or null" if allow_none else "", value))
    return value


def _validate_paper(paper) -> dict | None:
    if paper is None:
        return None
    if not isinstance(paper, dict):
        raise SchemaError("paper must be an object or null, got %r" % (paper,))
    out = {}
    for key in ("pmid", "doi", "pmcid"):
        value = paper.get(key)
        if value is not None and not isinstance(value, str):
            raise SchemaError("paper.%s must be a string or null, got %r" % (key, value))
        out[key] = value or None
    unknown = set(paper) - {"pmid", "doi", "pmcid"}
    if unknown:
        raise SchemaError("paper has unknown keys: %s" % ", ".join(sorted(unknown)))
    return out


def _validate_asset(asset) -> dict | None:
    if asset is None:
        return None
    if not isinstance(asset, dict):
        raise SchemaError("asset must be an object or null, got %r" % (asset,))
    path = asset.get("path")
    digest = asset.get("sha256")
    nbytes = asset.get("bytes")
    if not isinstance(path, str) or not path:
        raise SchemaError("asset.path must be a non-empty wiki-root-relative POSIX path (R13)")
    if path.startswith("/") or ".." in Path(path).parts:
        raise SchemaError("asset.path must be wiki-root-relative and contain no '..': %r" % path)
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SchemaError("asset.sha256 must be 64 lowercase hex chars, no prefix")
    if not isinstance(nbytes, int) or isinstance(nbytes, bool) or nbytes < 0:
        raise SchemaError("asset.bytes must be a non-negative integer")
    return {"path": path, "sha256": digest, "bytes": nbytes}


def _validate_enum(value, name: str, allowed) -> str:
    if value not in allowed:
        raise SchemaError("%s must be one of %s, got %r" % (name, "|".join(allowed), value))
    return value


def validate_snapshot(snapshot: dict) -> dict:
    """Structural validation of a snapshot record (schema.md §10). Returns a normalized copy.

    Does NOT check hashes — that is `check_snapshot_integrity`.
    """
    if not isinstance(snapshot, dict):
        raise SchemaError("snapshot must be a JSON object")
    if snapshot.get("schema_version") != SCHEMA_VERSION:
        raise SchemaError("snapshot missing schema_version == %d (S1)" % SCHEMA_VERSION)
    out = {
        "schema_version": SCHEMA_VERSION,
        "source_id": _req_str(snapshot, "source_id"),
        "content_hash": _req_str(snapshot, "content_hash"),
        "url": _req_str(snapshot, "url"),
        "title": _req_str(snapshot, "title", allow_none=True),
        "retrieved_at": _req_str(snapshot, "retrieved_at"),
        "access": _validate_enum(snapshot.get("access"), "access", ACCESS_VALUES),
        "paper": _validate_paper(snapshot.get("paper")),
        "origin": _validate_enum(snapshot.get("origin"), "origin", ORIGIN_VALUES),
        "asset": _validate_asset(snapshot.get("asset")),
        "text": _req_str(snapshot, "text"),
    }
    if not SOURCE_ID_RE.match(out["source_id"]):
        raise SchemaError("source_id must match src-<64 lowercase hex>, got %r" % out["source_id"])
    if not out["content_hash"].startswith("sha256:"):
        raise SchemaError("content_hash must carry the 'sha256:' prefix (R11)")
    if parse_ts(out["retrieved_at"]) is None:
        raise SchemaError("retrieved_at must be ISO-8601 UTC with a literal Z (S2)")
    return out


def check_snapshot_integrity(snapshot: dict) -> None:
    """Recompute both digests from the record's own `url`/`text`; raise on mismatch.

    Never a warning: a mismatch means the snapshot is tampered, no span resolves against
    it, `C-SNAPSHOT` fails and OKF promotion is blocked regardless of any gate flag (R20).
    """
    expect_content = compute_content_hash(snapshot["text"])
    if snapshot["content_hash"] != expect_content:
        raise SnapshotIntegrityError(
            "content_hash mismatch for %s: stored %s, recomputed %s"
            % (snapshot["source_id"], snapshot["content_hash"], expect_content),
            source_id=snapshot["source_id"])
    expect_id = compute_source_id(snapshot["url"], snapshot["text"])
    if snapshot["source_id"] != expect_id:
        raise SnapshotIntegrityError(
            "source_id mismatch: stored %s, recomputed %s"
            % (snapshot["source_id"], expect_id),
            source_id=snapshot["source_id"])


def validate_event(event: dict, *, require_ids: bool = True) -> dict:
    """Structural validation of an event record (schema.md §11). Returns a normalized copy."""
    if not isinstance(event, dict):
        raise SchemaError("event must be a JSON object")
    etype = _validate_enum(event.get("type"), "type", EVENT_TYPES)
    fresh = event.get("fresh")
    if not isinstance(fresh, bool):
        raise SchemaError("event.fresh must be a boolean")
    if fresh and etype not in FRESH_EVENT_TYPES:
        raise SchemaError("event.fresh may only be true for %s (R22), not %r"
                          % ("/".join(FRESH_EVENT_TYPES), etype))
    digest = event.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SchemaError("event.sha256 must be 64 lowercase hex chars, no prefix")
    detail = event.get("detail")
    if detail is not None and not isinstance(detail, str):
        raise SchemaError("event.detail must be a string or null")
    if isinstance(detail, str) and len(detail) > 300:
        detail = detail[:297] + "..."
    out = {
        "schema_version": SCHEMA_VERSION,
        "event_id": event.get("event_id"),
        "type": etype,
        "source_id": _req_str(event, "source_id"),
        "url": _req_str(event, "url"),
        "at": _req_str(event, "at"),
        "fresh": fresh,
        "sha256": digest,
        "actor": _req_str(event, "actor"),
        "detail": detail,
    }
    if require_ids and not EVENT_ID_RE.match(out["event_id"] or ""):
        raise SchemaError("event_id must match ev-<4+ digits> (R23), got %r" % out["event_id"])
    if not SOURCE_ID_RE.match(out["source_id"]):
        raise SchemaError("event.source_id must match src-<64 lowercase hex>")
    if parse_ts(out["at"]) is None:
        raise SchemaError("event.at must be ISO-8601 UTC with a literal Z (S2)")
    return out


# ----------------------------------------------------------------- snapshot io --


def _ordered(rec: dict, fields) -> dict:
    return {k: rec[k] for k in fields}


def write_snapshot_result(run_dir, *, url: str, text: str, title: str | None,
                          access: str, origin: str, paper: dict | None,
                          asset: dict | None = None, retrieved_at: str | None = None,
                          event_type: str = "fetch", fresh: bool = False,
                          actor: str = "main", detail: str | None = None,
                          write_event: bool = True, dirname: str = "sources") -> dict:
    """Write a snapshot write-once and append its event. Returns the full write result.

    Returns ``{"snapshot": dict, "created": bool, "event": dict | None,
    "path": str, "source_id": str}``.

    * `source_id` and `content_hash` are computed here per R11; callers never supply them.
    * The file is created with `O_EXCL` (R12/§10). Because the id covers url+text, an
      identical re-write lands on the identical path: that is a **no-op**, not an error —
      `created` comes back `False`. A same-id write whose *metadata* differs is a
      write-once violation and raises `SnapshotConflictError`.
    * The snapshot is written **before** its event, so an event never names a source_id
      that does not yet exist (R23).
    * `fresh` is only ever honoured for `fetch` / `local_pdf` (R22); `register` and `read`
      are forced to `fresh: false`.
    """
    if not isinstance(url, str) or not url:
        raise SchemaError("url must be a non-empty string")
    if not isinstance(text, str):
        raise SchemaError("text must be a string (may be \"\")")
    _validate_enum(access, "access", ACCESS_VALUES)
    _validate_enum(origin, "origin", ORIGIN_VALUES)
    _validate_enum(event_type, "event_type", EVENT_TYPES)
    if event_type == "read":
        raise SchemaError("a snapshot write cannot emit a 'read' event")

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "source_id": compute_source_id(url, text),
        "content_hash": compute_content_hash(text),
        "url": url,
        "title": title if (title is None or isinstance(title, str)) else str(title),
        "retrieved_at": retrieved_at or utcnow(),
        "access": access,
        "paper": _validate_paper(paper),
        "origin": origin,
        "asset": _validate_asset(asset),
        "text": text,
    }
    snapshot = validate_snapshot(snapshot)
    check_snapshot_integrity(snapshot)

    ensure_run(run_dir, dirname=dirname)
    path = snapshot_path(run_dir, snapshot["source_id"], dirname=dirname)
    payload = json.dumps(_ordered(snapshot, SNAPSHOT_FIELDS),
                         indent=2, ensure_ascii=False) + "\n"
    created = True
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise StoreError("cannot create snapshot %s: %s" % (path, exc)) from exc
        created = False
        existing = read_snapshot(run_dir, snapshot["source_id"], dirname=dirname)
        diff = [k for k in SNAPSHOT_FIELDS
                if k != "retrieved_at" and existing.get(k) != snapshot.get(k)]
        if diff:
            raise SnapshotConflictError(
                "snapshot %s already exists with different %s; snapshots are write-once, "
                "a correction is a new source_id (R12)"
                % (snapshot["source_id"], ", ".join(diff)),
                source_id=snapshot["source_id"], fields=diff)
        snapshot = existing
    else:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)

    event = None
    if write_event:
        event = append_event(run_dir, {
            "type": event_type,
            "source_id": snapshot["source_id"],
            "url": snapshot["url"],
            "at": utcnow(),
            "fresh": bool(fresh) and event_type in FRESH_EVENT_TYPES,
            "sha256": (snapshot["asset"]["sha256"] if event_type == "local_pdf" and snapshot["asset"]
                       else sha256_text(snapshot["text"])),
            "actor": actor,
            "detail": detail,
        }, dirname=dirname)
    return {"snapshot": snapshot, "created": created, "event": event,
            "path": str(path), "source_id": snapshot["source_id"]}


def write_snapshot(run_dir, *, url: str, text: str, title: str | None,
                   access: str, origin: str, paper: dict | None,
                   asset: dict | None = None, **kwargs) -> dict:
    """Write a snapshot write-once, append its event, and return the snapshot record.

    Thin wrapper over `write_snapshot_result`; see it for the full semantics and for the
    extra keyword arguments (`retrieved_at`, `event_type`, `fresh`, `actor`, `detail`,
    `write_event`).
    """
    return write_snapshot_result(run_dir, url=url, text=text, title=title, access=access,
                                 origin=origin, paper=paper, asset=asset, **kwargs)["snapshot"]


def register_text(run_dir, *, url: str, text: str, title: str | None, access: str,
                  origin: str, paper: dict | None, asset: dict | None = None,
                  actor: str = "main", detail: str | None = None,
                  retrieved_at: str | None = None, dirname: str = "sources") -> dict:
    """Fold text obtained elsewhere in the pipeline into the store (R22).

    For `eutils.py` abstracts, `fulltext.py` acquisitions and `library.py` cache hits from
    a previous run: the text becomes span-addressable without pretending it was retrieved
    in this run. Emits a `register` event, which is **never** fresh, so a run whose sources
    are all registered validates structurally and still fails `C-FRESH-FETCH`.
    """
    return write_snapshot(run_dir, url=url, text=text, title=title, access=access,
                          origin=origin, paper=paper, asset=asset,
                          retrieved_at=retrieved_at, event_type="register", fresh=False,
                          actor=actor, dirname=dirname,
                          detail=detail or "registered from pipeline, not re-retrieved")


def read_snapshot(run_dir, source_id: str, *, dirname: str = "sources") -> dict:
    """Load a snapshot and verify it. Raises on anything short of intact.

    Recomputes `content_hash` and `source_id` from the file's own `url` and `text` on
    **every** read (§10) — there is no unchecked read path in this module.

    Raises `UnknownSourceError` (UNKNOWN_SOURCE), `SnapshotIntegrityError`
    (SNAPSHOT_HASH_MISMATCH) or `SchemaError`.
    """
    path = snapshot_path(run_dir, source_id, dirname=dirname)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise UnknownSourceError("no snapshot %s under %s"
                                 % (source_id, sources_dir(run_dir, dirname=dirname)),
                                 source_id=source_id) from exc
    except OSError as exc:
        raise StoreError("cannot read snapshot %s: %s" % (path, exc)) from exc
    try:
        rec = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnapshotIntegrityError("snapshot %s is not valid JSON: %s" % (source_id, exc),
                                     source_id=source_id) from exc
    snapshot = validate_snapshot(rec)
    if snapshot["source_id"] != source_id:
        raise SnapshotIntegrityError(
            "snapshot file %s carries source_id %s" % (path.name, snapshot["source_id"]),
            source_id=source_id)
    check_snapshot_integrity(snapshot)
    return snapshot


def verify_snapshot(run_dir, source_id: str) -> dict:
    """Structured integrity result for one snapshot; never raises for a bad snapshot.

    Returns ``{"ok": bool, "source_id": str, "reason_code": str | None, "detail": str,
    "chars": int | None, "access": str | None, "origin": str | None}``.
    """
    try:
        snap = read_snapshot(run_dir, source_id)
    except StoreError as exc:
        return {"ok": False, "source_id": source_id,
                "reason_code": exc.reason_code or "SNAPSHOT_HASH_MISMATCH",
                "detail": exc.message, "chars": None, "access": None, "origin": None}
    return {"ok": True, "source_id": source_id, "reason_code": None,
            "detail": "content_hash and source_id recompute", "chars": len(snap["text"]),
            "access": snap["access"], "origin": snap["origin"]}


def list_snapshots(run_dir, *, dirname: str = "sources") -> list[str]:
    """Every `source_id` with a file under `<run>/<dirname>/`, sorted. Does not verify."""
    d = sources_dir(run_dir, dirname=dirname)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("src-*.json") if SOURCE_ID_RE.match(p.stem))


def iter_snapshots(run_dir):
    """Yield every verified snapshot under `<run>/sources/`. Raises on the first bad one."""
    for source_id in list_snapshots(run_dir):
        yield read_snapshot(run_dir, source_id)


# --------------------------------------------------------------------- spans ---


def _check_offsets(start, end, length: int) -> None:
    for name, value in (("start", start), ("end", end)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise SpanRangeError("%s must be an integer character offset (R10), got %r"
                                 % (name, value))
    if start < 0 or start >= end or end > length:
        raise SpanRangeError(
            "span [%d, %d) out of range for a %d-character snapshot "
            "(need 0 <= start < end <= len(text); end is exclusive)" % (start, end, length))
    if end - start > MAX_SPAN_CHARS:
        raise SpanTooLongError("span [%d, %d) is %d characters, over the %d-character cap "
                               "(never truncated, always a failure)"
                               % (start, end, end - start, MAX_SPAN_CHARS))


def slice_text(text: str, start: int, end: int) -> str:
    """`text[start:end]` with R10/P1/P2 enforced. Offsets are Python `str` indices."""
    _check_offsets(start, end, len(text))
    return text[start:end]


def slice_span(run_dir, source_id: str, start: int, end: int) -> str:
    """The excerpt for a span: verified snapshot, in range, `end` exclusive, ≤2000 chars.

    Offsets are character offsets into the snapshot's decoded `text` — Python `str`
    indices, i.e. Unicode code points, not bytes (R10). Slicing mid-grapheme is legal and
    reproduces exactly what any other re-slice produces.
    """
    snap = read_snapshot(run_dir, source_id)
    return slice_text(snap["text"], start, end)


def verify_span(run_dir, span_record: dict, *, excerpt: str | None = None,
                strict_access: bool = False, snapshot: dict | None = None) -> dict:
    """The single span checker: snapshot integrity, range, cap, excerpt equality.

    `span_record` is a schema.md §12 claim span record (at minimum `source_id`, `start`,
    `end`). `excerpt` is an agent-supplied excerpt to test for exact equality against the
    re-slice; when it is None and `span_record` carries an `excerpt`/`text` key, that value
    is used instead. `snapshot` may be passed to avoid a re-read — it is still integrity
    checked. `strict_access` turns an `access` disagreement between the span record and the
    snapshot into a failure rather than a warning.

    Returns a structured result, never a bare bool::

        {"ok": bool, "reason_code": str | None, "detail": str,
         "source_id": str | None, "start": int | None, "end": int | None,
         "length": int | None, "excerpt": str | None, "access": str | None,
         "warnings": [str, ...]}

    `reason_code` is one of the schema.md §13 codes: UNKNOWN_SOURCE,
    SNAPSHOT_HASH_MISMATCH, SPAN_OUT_OF_RANGE, SPAN_TOO_LONG, EXCERPT_MISMATCH — or a
    SchemaError for a malformed span record.
    """
    result = {"ok": False, "reason_code": None, "detail": "", "source_id": None,
              "start": None, "end": None, "length": None, "excerpt": None,
              "access": None, "warnings": []}
    if not isinstance(span_record, dict):
        result.update(reason_code="SCHEMA_ERROR", detail="span record must be a JSON object")
        return result
    source_id = span_record.get("source_id")
    start = span_record.get("start")
    end = span_record.get("end")
    result.update(source_id=source_id if isinstance(source_id, str) else None,
                  start=start if isinstance(start, int) and not isinstance(start, bool) else None,
                  end=end if isinstance(end, int) and not isinstance(end, bool) else None)
    if excerpt is None:
        for key in ("excerpt", "text"):
            if isinstance(span_record.get(key), str):
                excerpt = span_record[key]
                break

    try:
        snap = snapshot if snapshot is not None else read_snapshot(run_dir, source_id)
        if snapshot is not None:
            snap = validate_snapshot(snap)
            check_snapshot_integrity(snap)
            if source_id and snap["source_id"] != source_id:
                raise UnknownSourceError(
                    "span names %s but the supplied snapshot is %s" % (source_id, snap["source_id"]),
                    source_id=source_id)
        result["access"] = snap["access"]
        sliced = slice_text(snap["text"], start, end)
    except StoreError as exc:
        result.update(reason_code=exc.reason_code or "SCHEMA_ERROR", detail=exc.message)
        return result

    result.update(excerpt=sliced, length=len(sliced))
    span_access = span_record.get("access")
    if span_access is not None and span_access != snap["access"]:
        msg = ("span access %r disagrees with snapshot access %r; the snapshot wins (§13)"
               % (span_access, snap["access"]))
        if strict_access:
            result.update(reason_code="SCHEMA_ERROR", detail=msg)
            return result
        result["warnings"].append(msg)

    if excerpt is not None and excerpt != sliced:
        result.update(reason_code="EXCERPT_MISMATCH",
                      detail=("agent-supplied excerpt (%d chars) differs from %s[%d:%d] "
                              "(%d chars); excerpts are re-sliced, never authored (P6)"
                              % (len(excerpt), source_id, start, end, len(sliced))))
        return result

    result.update(ok=True, detail="span in range, within the %d-character cap%s"
                  % (MAX_SPAN_CHARS, ", excerpt matches" if excerpt is not None else ""))
    return result


# -------------------------------------------------------------------- events ---


def read_events(run_dir) -> list[dict]:
    """Every event line in file order — the authoritative ordering (R23).

    Unparseable or structurally invalid lines are returned with `"_invalid"` set rather
    than dropped, so a corrupt log is visible instead of silently shorter.
    """
    path = events_path(run_dir)
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                out.append(validate_event(rec))
            except (json.JSONDecodeError, SchemaError) as exc:
                out.append({"_invalid": str(exc), "_line": lineno, "_raw": line[:300]})
    return out


def _next_event_id(events: list[dict]) -> str:
    highest = 0
    for ev in events:
        m = EVENT_ID_RE.match(str(ev.get("event_id") or ""))
        if m:
            highest = max(highest, int(m.group(1)))
    return "ev-%04d" % (max(highest, len(events)) + 1)


#: Guards the read -> allocate -> dup-check -> append sequence in `append_event`.
#:
#: R23 requires `event_id`s to be unique within a run and allocated in append order. The
#: `flock` below only makes the *write* atomic; without this lock two threads can both
#: `read_events()`, both compute the same `ev-000N`, both pass the duplicate check (each
#: having read before the other wrote), and both append it. `fulltext.py acquire --workers`
#: registers snapshots from a thread pool, so that race is reachable in normal operation.
#: Re-entrant because the snapshot existence check below re-enters store code.
_EVENT_LOCK = threading.RLock()


def append_event(run_dir, event: dict, *, dirname: str = "sources") -> dict:
    """Append one line to `<run>/events.jsonl`. Append is the only mutation (§11).

    Allocates `event_id` when absent (R23: `ev-` + a zero-padded counter of at least four
    digits, unique within the run, allocated in append order). For `fetch`, `local_pdf`
    and `register` the snapshot must already exist — writing the snapshot always precedes
    appending its event.

    Thread-safe: the whole allocate-and-append sequence is serialised by `_EVENT_LOCK`, and
    the write additionally takes an `flock` so a concurrent *process* cannot interleave a
    partial line. Threads within one process are ordered by the lock; separate processes
    writing the same run concurrently are not, and never were.
    """
    ensure_run(run_dir, dirname=dirname)
    rec = dict(event)
    rec.setdefault("schema_version", SCHEMA_VERSION)
    rec.setdefault("at", utcnow())
    rec.setdefault("actor", "main")
    rec.setdefault("detail", None)
    rec.setdefault("fresh", False)
    with _EVENT_LOCK:
        existing = read_events(run_dir)
        if not rec.get("event_id"):
            rec["event_id"] = _next_event_id(existing)
        rec = validate_event(rec)
        if rec["type"] in ("fetch", "local_pdf", "register"):
            # R23: the snapshot exists before its event names it.
            read_snapshot(run_dir, rec["source_id"], dirname=dirname)
        seen = {e.get("event_id") for e in existing}
        if rec["event_id"] in seen:
            raise SchemaError("duplicate event_id %s in this run (R23)" % rec["event_id"])

        line = json.dumps(_ordered(rec, EVENT_FIELDS), ensure_ascii=False,
                          separators=(",", ":")) + "\n"
        path = events_path(run_dir)
        with open(path, "a", encoding="utf-8") as fh:
            try:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    return rec


# ----------------------------------------------------------------- freshness ---


def _asset_digest_ok(snapshot: dict, wiki_root) -> tuple[bool, str]:
    asset = snapshot.get("asset")
    if not asset:
        return False, "local_pdf event on a snapshot with asset: null"
    path = Path(wiki_root).expanduser() / asset["path"]
    if not path.is_file():
        return False, "asset file missing: %s" % asset["path"]
    actual = sha256_file(path)
    if actual != asset["sha256"]:
        return False, ("asset %s hashes to %s, recorded %s"
                       % (asset["path"], actual[:16], asset["sha256"][:16]))
    return True, "asset bytes still hash to asset.sha256"


def fresh_event(run_dir, source_id: str, *, wiki_root=None,
                events: list[dict] | None = None,
                created_at: str | None = None) -> dict | None:
    """The event that makes `source_id` fresh for this run, or None (R15, §11).

    All five conditions must hold on one line: matching `source_id`; `type` is `fetch` or
    `local_pdf`; `fresh == true`; `at >=` this run's `config.json` `created_at`; and the
    digest recomputed **now** still matches — the snapshot's `text` digest for `fetch`,
    the asset file's byte digest for `local_pdf` (the user-supplied-PDF exception).

    A `register`, a `read`, a cache-only fetch, an event copied in from another run, a
    search snippet or an existing wiki note is never fresh. Fails closed: with no
    `created_at` in `config.json`, condition 4 cannot be met and nothing is fresh.
    """
    created_at = created_at if created_at is not None else run_created_at(run_dir)
    run_start = parse_ts(created_at)
    if run_start is None:
        return None
    if events is None:
        events = read_events(run_dir)
    wiki_root = Path(wiki_root) if wiki_root else wiki_root_for_run(run_dir)

    snapshot = None
    for ev in events:
        if ev.get("_invalid"):
            continue
        if ev.get("source_id") != source_id:
            continue
        if ev.get("type") not in FRESH_EVENT_TYPES:
            continue                                   # condition 2 (R22)
        if ev.get("fresh") is not True:
            continue                                   # condition 3
        at = parse_ts(ev.get("at"))
        if at is None or at < run_start:
            continue                                   # condition 4
        if snapshot is None:
            try:
                snapshot = read_snapshot(run_dir, source_id)
            except StoreError:
                return None                            # tampered/unknown: never fresh
        if ev["type"] == "fetch":
            if ev.get("sha256") == sha256_text(snapshot["text"]):
                return ev                              # condition 5
            continue
        ok, _detail = _asset_digest_ok(snapshot, wiki_root)
        if ok and ev.get("sha256") == snapshot["asset"]["sha256"]:
            return ev                                  # condition 5, PDF exception
    return None


def freshness(run_dir, source_id: str, *, wiki_root=None, events=None,
              created_at: str | None = None) -> dict:
    """Structured freshness verdict: `{fresh, fresh_event_id, reason_code, detail}`."""
    created_at = created_at if created_at is not None else run_created_at(run_dir)
    if created_at is None:
        return {"fresh": False, "fresh_event_id": None, "reason_code": "NO_FRESH_FETCH",
                "detail": "no created_at in config.json; freshness fails closed (R15)"}
    ev = fresh_event(run_dir, source_id, wiki_root=wiki_root, events=events,
                     created_at=created_at)
    if ev is not None:
        return {"fresh": True, "fresh_event_id": ev["event_id"], "reason_code": None,
                "detail": "%s event %s at %s" % (ev["type"], ev["event_id"], ev["at"])}
    # Distinguish the PDF-asset failure so callers can raise ASSET_HASH_MISMATCH.
    try:
        snap = read_snapshot(run_dir, source_id)
    except StoreError as exc:
        return {"fresh": False, "fresh_event_id": None,
                "reason_code": exc.reason_code or "UNKNOWN_SOURCE", "detail": exc.message}
    if snap["origin"] == "user-supplied-pdf":
        has_local = any(e.get("source_id") == source_id and e.get("type") == "local_pdf"
                        for e in (events if events is not None else read_events(run_dir)))
        if has_local:
            ok, detail = _asset_digest_ok(snap, Path(wiki_root) if wiki_root
                                          else wiki_root_for_run(run_dir))
            if not ok:
                return {"fresh": False, "fresh_event_id": None,
                        "reason_code": "ASSET_HASH_MISMATCH", "detail": detail}
    return {"fresh": False, "fresh_event_id": None, "reason_code": "NO_FRESH_FETCH",
            "detail": "no fetch/local_pdf event in this run satisfies all five conditions (R15)"}


def has_fresh_retrieval(run_dir, source_id_or_url: str, *, wiki_root=None) -> bool:
    """True iff a source has a fresh retrieval logged in this run (R15).

    Accepts a `source_id` (`src-...`) or a URL; a URL is resolved to every snapshot in the
    run that recorded it byte-for-byte, and is fresh if any of them is.
    """
    if SOURCE_ID_RE.match(source_id_or_url or ""):
        return fresh_event(run_dir, source_id_or_url, wiki_root=wiki_root) is not None
    events = read_events(run_dir)
    candidates = {ev["source_id"] for ev in events
                  if not ev.get("_invalid") and ev.get("url") == source_id_or_url}
    for source_id in list_snapshots(run_dir):
        if source_id in candidates:
            continue
        try:
            if read_snapshot(run_dir, source_id)["url"] == source_id_or_url:
                candidates.add(source_id)
        except StoreError:
            continue
    created_at = run_created_at(run_dir)
    return any(fresh_event(run_dir, sid, wiki_root=wiki_root, events=events,
                           created_at=created_at) is not None
               for sid in sorted(candidates))


# --------------------------------------------------------------------- Store ---


class Store:
    """Caching facade over one run directory.

    Instantiate once in `assemble.py`, `verify.py` and `okf.py promote`: snapshots are
    verified on first touch and the verified record is reused, so a run with hundreds of
    spans hashes each snapshot once instead of once per span. Every method mirrors the
    module-level function of the same name.
    """

    def __init__(self, run_dir, wiki_root=None, repo_root=None):
        self.run_dir = Path(run_dir).expanduser()
        self.wiki_root = Path(wiki_root).expanduser() if wiki_root else wiki_root_for_run(run_dir)
        self.repo_root = Path(repo_root).expanduser().resolve() if repo_root else None
        self._snapshots: dict[str, dict] = {}
        self._errors: dict[str, StoreError] = {}
        self._events: list[dict] | None = None
        self._created_at: str | None | bool = False

    # -- snapshots --
    def read_snapshot(self, source_id: str) -> dict:
        """Run-local snapshot first, falling back to the global store when `repo_root`
        is set and the run has no local copy (plan "Source Storage" compatibility
        behavior 1-2). A run-local *integrity* failure is never swallowed by the
        fallback — only "not found" falls through."""
        if source_id in self._errors:
            raise self._errors[source_id]
        snap = self._snapshots.get(source_id)
        if snap is None:
            try:
                snap = read_snapshot(self.run_dir, source_id)
            except UnknownSourceError:
                if self.repo_root is None:
                    raise
                try:
                    snap = global_read_snapshot(self.repo_root, source_id)
                except StoreError as exc:
                    self._errors[source_id] = exc
                    raise
            except StoreError as exc:
                self._errors[source_id] = exc
                raise
            self._snapshots[source_id] = snap
        return snap

    def verify_snapshot(self, source_id: str) -> dict:
        try:
            snap = self.read_snapshot(source_id)
        except StoreError as exc:
            return {"ok": False, "source_id": source_id,
                    "reason_code": exc.reason_code or "SNAPSHOT_HASH_MISMATCH",
                    "detail": exc.message, "chars": None, "access": None, "origin": None}
        return {"ok": True, "source_id": source_id, "reason_code": None,
                "detail": "content_hash and source_id recompute", "chars": len(snap["text"]),
                "access": snap["access"], "origin": snap["origin"]}

    def list_snapshots(self) -> list[str]:
        local = list_snapshots(self.run_dir)
        if self.repo_root is None:
            return local
        glob = global_list_snapshots(self.repo_root)
        return sorted(set(local) | set(glob))

    def slice_span(self, source_id: str, start: int, end: int) -> str:
        return slice_text(self.read_snapshot(source_id)["text"], start, end)

    def verify_span(self, span_record: dict, *, excerpt: str | None = None,
                    strict_access: bool = False) -> dict:
        source_id = span_record.get("source_id") if isinstance(span_record, dict) else None
        snapshot = None
        if isinstance(source_id, str):
            try:
                snapshot = self.read_snapshot(source_id)
            except StoreError as exc:
                start = span_record.get("start") if isinstance(span_record, dict) else None
                end = span_record.get("end") if isinstance(span_record, dict) else None
                return {
                    "ok": False,
                    "reason_code": exc.reason_code or "SCHEMA_ERROR",
                    "detail": exc.message,
                    "source_id": source_id,
                    "start": start if isinstance(start, int) and not isinstance(start, bool) else None,
                    "end": end if isinstance(end, int) and not isinstance(end, bool) else None,
                    "length": None,
                    "excerpt": None,
                    "access": None,
                    "warnings": [],
                }
        return verify_span(self.run_dir, span_record, excerpt=excerpt,
                           strict_access=strict_access, snapshot=snapshot)

    # -- events / freshness --
    @property
    def events(self) -> list[dict]:
        if self._events is None:
            self._events = read_events(self.run_dir)
        return self._events

    def append_event(self, event: dict) -> dict:
        rec = append_event(self.run_dir, event)
        self._events = None
        return rec

    @property
    def created_at(self) -> str | None:
        if self._created_at is False:
            self._created_at = run_created_at(self.run_dir)
        return self._created_at

    def freshness(self, source_id: str) -> dict:
        """R15 freshness is inherently per-run (a source is fresh only if *this* run fetched
        it), so a source reused from another run/the global store is correctly never fresh —
        that is not a bug. But the module-level check below only ever looks the snapshot up
        run-locally to pick a reason_code, so a source that resolves fine through the global
        store (just not freshly, here) would misreport as UNKNOWN_SOURCE instead of
        NO_FRESH_FETCH. Downgrade that specific case; a source that is genuinely missing or
        tampered (self.read_snapshot also fails) still reports UNKNOWN_SOURCE/
        SNAPSHOT_HASH_MISMATCH untouched."""
        result = freshness(self.run_dir, source_id, wiki_root=self.wiki_root,
                           events=self.events, created_at=self.created_at)
        if not result["fresh"] and result["reason_code"] == "UNKNOWN_SOURCE":
            try:
                self.read_snapshot(source_id)
            except StoreError:
                pass
            else:
                result = dict(result, reason_code="NO_FRESH_FETCH",
                             detail="resolves via the global source store, but no fetch/"
                                    "local_pdf event in this run satisfies all five "
                                    "conditions (R15)")
        return result

    def has_fresh_retrieval(self, source_id_or_url: str) -> bool:
        if SOURCE_ID_RE.match(source_id_or_url or ""):
            return self.freshness(source_id_or_url)["fresh"]
        return has_fresh_retrieval(self.run_dir, source_id_or_url, wiki_root=self.wiki_root)

    # -- writes --
    def write_snapshot(self, *, local: bool = False, **kwargs) -> dict:
        """Writes go to the global store when `repo_root` is set (plan "Source Storage":
        "writes should go to the global source store unless explicitly requested
        otherwise"); pass `local=True` to force a run-local write regardless. Global writes
        always land under `data/sources/snapshots/` (never the legacy dirname)."""
        if local or self.repo_root is None:
            out = write_snapshot_result(self.run_dir, **kwargs)
            self._events = None
        else:
            out = global_write_snapshot_result(self.repo_root, **kwargs)
        self._snapshots[out["source_id"]] = out["snapshot"]
        self._errors.pop(out["source_id"], None)
        return out["snapshot"]

    def stats(self) -> dict:
        return stats(self.run_dir, wiki_root=self.wiki_root)


def stats(run_dir, *, wiki_root=None) -> dict:
    """Run-level tallies: snapshots (with integrity), events by type, fresh sources."""
    ids = list_snapshots(run_dir)
    events = read_events(run_dir)
    created = run_created_at(run_dir)
    by_type: dict[str, int] = {t: 0 for t in EVENT_TYPES}
    invalid = 0
    for ev in events:
        if ev.get("_invalid"):
            invalid += 1
        else:
            by_type[ev["type"]] = by_type.get(ev["type"], 0) + 1
    intact, tampered, chars = 0, [], 0
    fresh_ids = []
    for sid in ids:
        res = verify_snapshot(run_dir, sid)
        if res["ok"]:
            intact += 1
            chars += res["chars"] or 0
            if fresh_event(run_dir, sid, wiki_root=wiki_root, events=events,
                           created_at=created) is not None:
                fresh_ids.append(sid)
        else:
            tampered.append({"source_id": sid, "reason_code": res["reason_code"],
                             "detail": res["detail"]})
    return {"ok": not tampered, "run_dir": str(Path(run_dir).expanduser()),
            "created_at": created, "snapshots": len(ids), "intact": intact,
            "tampered": tampered, "chars": chars, "events": len(events),
            "events_by_type": by_type, "invalid_event_lines": invalid,
            "fresh_sources": len(fresh_ids), "fresh_source_ids": fresh_ids}


# ----------------------------------------------------------------------- cli ---

def _read_text_arg(args) -> str:
    if getattr(args, "text", None) is not None:
        return args.text
    if getattr(args, "text_file", None):
        if args.text_file == "-":
            return sys.stdin.read()
        return Path(args.text_file).expanduser().read_text(encoding="utf-8")
    raise SchemaError("one of --text or --text-file (use '-' for stdin) is required")


def cmd_write(args) -> int:
    text = _read_text_arg(args)
    asset = None
    if args.asset_path:
        asset = {"path": args.asset_path, "sha256": args.asset_sha256,
                 "bytes": args.asset_bytes}
        if not args.asset_sha256 or args.asset_bytes is None:
            raise SchemaError("--asset-path requires --asset-sha256 and --asset-bytes")
    out = write_snapshot_result(
        args.run_dir, url=args.url, text=text, title=args.title, access=args.access,
        origin=args.origin,
        paper=None if args.no_paper else {"pmid": args.pmid, "doi": args.doi,
                                          "pmcid": args.pmcid},
        asset=asset, retrieved_at=args.retrieved_at, event_type=args.event_type,
        fresh=args.fresh, actor=args.actor, detail=args.detail,
        write_event=not args.no_event)
    snap = out["snapshot"]
    return _emit({"ok": True, "created": out["created"], "source_id": snap["source_id"],
                  "content_hash": snap["content_hash"], "chars": len(snap["text"]),
                  "path": os.path.relpath(out["path"], str(Path(args.run_dir).expanduser())),
                  "event": out["event"]})


def cmd_read(args) -> int:
    snap = read_snapshot(args.run_dir, args.source_id)
    meta = {k: snap[k] for k in SNAPSHOT_FIELDS if k != "text"}
    meta["chars"] = len(snap["text"])
    if args.start is None and args.end is None:
        return _emit({"ok": True, "snapshot": meta, "start": 0, "end": len(snap["text"]),
                      "text": snap["text"]})
    start = args.start or 0
    end = args.end if args.end is not None else min(len(snap["text"]), start + MAX_SPAN_CHARS)
    return _emit({"ok": True, "snapshot": meta, "start": start, "end": end,
                  "text": slice_text(snap["text"], start, end)})


def cmd_verify(args) -> int:
    if args.span_file or (args.source_id and args.start is not None):
        if args.span_file:
            span = json.loads(Path(args.span_file).expanduser().read_text(encoding="utf-8"))
        else:
            span = {"source_id": args.source_id, "start": args.start, "end": args.end}
        excerpt = args.excerpt
        if args.excerpt_file:
            excerpt = Path(args.excerpt_file).expanduser().read_text(encoding="utf-8")
        res = verify_span(args.run_dir, span, excerpt=excerpt,
                          strict_access=args.strict_access)
        return _emit(res, code=0 if res["ok"] else 3)
    ids = [args.source_id] if args.source_id else list_snapshots(args.run_dir)
    results = [verify_snapshot(args.run_dir, sid) for sid in ids]
    bad = [r for r in results if not r["ok"]]
    return _emit({"ok": not bad, "checked": len(results), "failed": len(bad),
                  "results": results}, code=0 if not bad else 3)


def cmd_events(args) -> int:
    events = read_events(args.run_dir)
    if args.type:
        events = [e for e in events if e.get("type") == args.type]
    if args.source_id:
        events = [e for e in events if e.get("source_id") == args.source_id]
    if args.limit:
        events = events[-args.limit:]
    return _emit({"ok": True, "count": len(events), "events": events})


def cmd_fresh(args) -> int:
    target = args.source_id or args.url
    if not target:
        raise SchemaError("one of --source-id or --url is required")
    if args.source_id:
        res = freshness(args.run_dir, args.source_id, wiki_root=args.wiki)
        res.update(ok=True, source_id=args.source_id)
        return _emit(res, code=0 if res["fresh"] else 3)
    fresh = has_fresh_retrieval(args.run_dir, target, wiki_root=args.wiki)
    return _emit({"ok": True, "fresh": fresh, "url": target,
                  "fresh_event_id": None,
                  "detail": "resolved via snapshots recording this exact URL"},
                 code=0 if fresh else 3)


def cmd_stats(args) -> int:
    res = stats(args.run_dir, wiki_root=args.wiki)
    return _emit(res, code=0 if res["ok"] else 3)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="store.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("write", help="write a snapshot write-once and log its event")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--url", required=True)
    s.add_argument("--text", help="snapshot text (prefer --text-file for anything large)")
    s.add_argument("--text-file", dest="text_file", help="file holding the text; '-' = stdin")
    s.add_argument("--title", default=None)
    s.add_argument("--access", required=True, choices=list(ACCESS_VALUES))
    s.add_argument("--origin", required=True, choices=list(ORIGIN_VALUES))
    s.add_argument("--pmid")
    s.add_argument("--doi")
    s.add_argument("--pmcid")
    s.add_argument("--no-paper", action="store_true", dest="no_paper",
                   help="record paper: null (origin web only)")
    s.add_argument("--asset-path", dest="asset_path", help="wiki-root-relative PDF path (R13)")
    s.add_argument("--asset-sha256", dest="asset_sha256")
    s.add_argument("--asset-bytes", dest="asset_bytes", type=int)
    s.add_argument("--retrieved-at", dest="retrieved_at")
    s.add_argument("--event-type", dest="event_type", default="fetch",
                   choices=["fetch", "local_pdf", "register"])
    s.add_argument("--fresh", action="store_true",
                   help="mark the event as a fresh retrieval (fetch/local_pdf only, R22)")
    s.add_argument("--actor", default="main")
    s.add_argument("--detail", default=None)
    s.add_argument("--no-event", action="store_true", dest="no_event")
    s.set_defaults(func=cmd_write)

    s = sub.add_parser("read", help="read a verified snapshot, whole or windowed")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--source-id", required=True, dest="source_id")
    s.add_argument("--start", type=int)
    s.add_argument("--end", type=int, help="exclusive; defaults to start+%d" % MAX_SPAN_CHARS)
    s.set_defaults(func=cmd_read)

    s = sub.add_parser("verify", help="verify snapshots, or one span (with --start/--end)")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--source-id", dest="source_id")
    s.add_argument("--start", type=int)
    s.add_argument("--end", type=int, help="exclusive")
    s.add_argument("--span-file", dest="span_file", help="a schema.md §12 span record JSON file")
    s.add_argument("--excerpt", help="agent-supplied excerpt to compare against the re-slice")
    s.add_argument("--excerpt-file", dest="excerpt_file")
    s.add_argument("--strict-access", action="store_true", dest="strict_access")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("events", help="dump events.jsonl")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--type", choices=list(EVENT_TYPES))
    s.add_argument("--source-id", dest="source_id")
    s.add_argument("--limit", type=int, default=0, help="keep only the last N")
    s.set_defaults(func=cmd_events)

    s = sub.add_parser("fresh", help="the R15 five-condition freshness test")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--source-id", dest="source_id")
    s.add_argument("--url")
    s.add_argument("--wiki", help="wiki root (default: inferred from --run-dir)")
    s.set_defaults(func=cmd_fresh)

    s = sub.add_parser("stats", help="snapshot/event/freshness tallies for a run")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--wiki")
    s.set_defaults(func=cmd_stats)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except StoreError as exc:
        return _emit(exc.to_json(), code=2)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return _emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__,
                      "reason_code": None}, code=2)


if __name__ == "__main__":
    sys.exit(main())
