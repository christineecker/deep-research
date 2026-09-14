#!/usr/bin/env python3
"""annotations.py — personal annotations at `<repo>/data/papers/annotations.jsonl`.

Standalone-repo store for tags/star-rating/free-text notes, one record per `evidence_id`,
kept parallel to but never merged into `registry.jsonl` — mirrors why appraisal already
lives in its own project-scoped store rather than as registry fields
(`references/pool-architecture.md` "Appraisal is project-scoped, on purpose"): a personal
opinion about a paper (tags, rating, note) is not bibliographic or lifecycle metadata, and
annotating a paper never requires it to already be registered.

Record shape (`references/schema/16-annotation.md`):
    {"schema_version": 1, "evidence_id": "pmid:12345678",
     "tags": ["diagnostics", "to-read"], "rating": 4,
     "note": "free text", "updated_at": "2026-09-12T00:00:00Z"}

`rating` and `note` are omitted (never `null` on disk) when unset; `tags` is always a
sorted, deduped list, `[]` when none.

Subcommands
  tag    --repo --evidence-id (--add <tag> | --remove <tag>)   add/remove one tag
  rate   --repo --evidence-id (--stars N | --clear)             set/clear a 1-5 star rating
  note   --repo --evidence-id (--set "<text>" | --clear)        set/clear a free-text note
  show   --repo --evidence-id                                  print one record (never errors)
  list   --repo [--tag <tag>] [--min-rating N] [--limit N]      print matching records

Every mutating command wraps its load+mutate+save in `advisory_lock(repo_root,
"annotations")` (imported from `registry.py`, not reimplemented) — a distinct lock name
from `registry.py`'s own `"registry"` lock so annotation writes never contend with
registry writes, per the plan's explicit scoping.

Environment: python3, stdlib only. No pip installs. No dependency on `Registry` — this is
a pure evidence_id-keyed store and works even before a paper is registered.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import utcnow  # noqa: E402  (sibling module, stdlib-only)
from registry import advisory_lock, repo_paths  # noqa: E402  (reuse, do not reimplement)

SCHEMA_VERSION = 1


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def _empty_record(evidence_id: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "tags": [],
    }


class _Unset:
    def __repr__(self):
        return "UNSET"


UNSET = _Unset()  # sentinel: "leave this field unchanged" (distinct from `None` = clear)


class Annotations:
    """The `<repo>/data/papers/annotations.jsonl` store. One record per evidence_id."""

    def __init__(self, repo_root: Path):
        self.paths = repo_paths(repo_root)
        self.repo_root = self.paths["repo_root"]
        self.path = self.paths["papers"] / "annotations.jsonl"
        self.records: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a corrupt annotations line must never block tag/rate/note
                if not isinstance(rec, dict):
                    continue
                eid = rec.get("evidence_id")
                if eid:
                    self.records[eid] = rec

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for eid in sorted(self.records):
                fh.write(json.dumps(self.records[eid], ensure_ascii=False, sort_keys=False))
                fh.write("\n")
        tmp.replace(self.path)

    def get(self, evidence_id: str) -> dict:
        """Returns the stored record, or a fresh empty-shape one. Never raises — an
        unannotated paper is not an error state (plan "never errors on not annotated
        yet")."""
        rec = self.records.get(evidence_id)
        if rec is None:
            return _empty_record(evidence_id)
        # Defensive copy shape: always at least schema_version/evidence_id/tags.
        out = dict(rec)
        out.setdefault("schema_version", SCHEMA_VERSION)
        out.setdefault("evidence_id", evidence_id)
        out.setdefault("tags", [])
        return out

    def upsert(self, evidence_id: str, *, add_tag: str | None = None,
               remove_tag: str | None = None, rating=UNSET, note=UNSET) -> dict:
        """Merge fields into one record and persist it in `self.records` (caller still
        must `save()`). `add_tag`/`remove_tag` mutate the `tags` set; `rating`/`note` are
        set/cleared as a whole (pass `None` to clear, `UNSET`/omit to leave unchanged).
        """
        rec = self.records.get(evidence_id) or _empty_record(evidence_id)
        rec = dict(rec)
        rec.setdefault("schema_version", SCHEMA_VERSION)
        rec["evidence_id"] = evidence_id
        tags = set(rec.get("tags") or [])
        if add_tag:
            tags.add(add_tag)
        if remove_tag:
            tags.discard(remove_tag)
        rec["tags"] = sorted(tags)
        if rating is not UNSET:
            if rating is None:
                rec.pop("rating", None)
            else:
                rec["rating"] = rating
        if note is not UNSET:
            if note is None:
                rec.pop("note", None)
            else:
                rec["note"] = note
        rec["updated_at"] = utcnow()
        self.records[evidence_id] = rec
        return rec


def _validate_rating(value: int) -> int:
    if value < 1 or value > 5:
        raise SystemExit(f"--stars must be an integer 1-5, got {value}")
    return value


def cmd_tag(args) -> int:
    if bool(args.add) == bool(args.remove):
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "tag",
              "error": "exactly one of --add/--remove is required"})
        return 2
    with advisory_lock(args.repo, "annotations"):
        ann = Annotations(args.repo)
        rec = ann.upsert(args.evidence_id, add_tag=args.add, remove_tag=args.remove)
        ann.save()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "tag",
          "evidence_id": args.evidence_id, "record": rec})
    return 0


def cmd_rate(args) -> int:
    if bool(args.stars is not None) == bool(args.clear):
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "rate",
              "error": "exactly one of --stars/--clear is required"})
        return 2
    rating = None if args.clear else _validate_rating(args.stars)
    with advisory_lock(args.repo, "annotations"):
        ann = Annotations(args.repo)
        rec = ann.upsert(args.evidence_id, rating=rating)
        ann.save()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "rate",
          "evidence_id": args.evidence_id, "record": rec})
    return 0


def cmd_note(args) -> int:
    if bool(args.set is not None) == bool(args.clear):
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "note",
              "error": "exactly one of --set/--clear is required"})
        return 2
    note = None if args.clear else args.set
    with advisory_lock(args.repo, "annotations"):
        ann = Annotations(args.repo)
        rec = ann.upsert(args.evidence_id, note=note)
        ann.save()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "note",
          "evidence_id": args.evidence_id, "record": rec})
    return 0


def cmd_show(args) -> int:
    ann = Annotations(args.repo)
    rec = ann.get(args.evidence_id)
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "show",
          "evidence_id": args.evidence_id, "record": rec})
    return 0


def cmd_list(args) -> int:
    ann = Annotations(args.repo)
    entries = sorted(ann.records.values(), key=lambda r: r.get("evidence_id") or "")
    if args.tag:
        entries = [r for r in entries if args.tag in (r.get("tags") or [])]
    if args.min_rating is not None:
        entries = [r for r in entries if (r.get("rating") or 0) >= args.min_rating]
    if args.limit is not None:
        entries = entries[: args.limit]
    emit(entries)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="annotations.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("tag", help="add or remove one tag")
    s.add_argument("--repo", required=True)
    s.add_argument("--evidence-id", dest="evidence_id", required=True)
    s.add_argument("--add", help="tag to add")
    s.add_argument("--remove", help="tag to remove")
    s.set_defaults(func=cmd_tag)

    s = sub.add_parser("rate", help="set or clear a 1-5 star rating")
    s.add_argument("--repo", required=True)
    s.add_argument("--evidence-id", dest="evidence_id", required=True)
    s.add_argument("--stars", type=int)
    s.add_argument("--clear", action="store_true")
    s.set_defaults(func=cmd_rate)

    s = sub.add_parser("note", help="set or clear a free-text note")
    s.add_argument("--repo", required=True)
    s.add_argument("--evidence-id", dest="evidence_id", required=True)
    s.add_argument("--set", dest="set")
    s.add_argument("--clear", action="store_true")
    s.set_defaults(func=cmd_note)

    s = sub.add_parser("show", help="print one annotation record (empty shape if none yet)")
    s.add_argument("--repo", required=True)
    s.add_argument("--evidence-id", dest="evidence_id", required=True)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("list", help="list annotation records, optionally filtered")
    s.add_argument("--repo", required=True)
    s.add_argument("--tag")
    s.add_argument("--min-rating", dest="min_rating", type=int)
    s.add_argument("--limit", type=int)
    s.set_defaults(func=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
