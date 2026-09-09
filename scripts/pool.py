#!/usr/bin/env python3
"""pool.py — wiki-level shared paper pool at <wiki-root>/assets/papers/pool.jsonl.

Cross-run reuse of Stage 5/6 work (`SKILL.md` "Pipeline") plus a wiki-wide BibTeX export.
Complements the PDF library (`library.py`, `<wiki>/assets/papers/index.json`): PDFs are
files, this is metadata. Extraction/appraisal JSON stays inside the run that produced it
(`<run>/workspace/extractions/`, `<run>/workspace/appraisals/`) — the pool stores only a
wiki-root-relative *pointer* to it plus enough bibliographic/corpus metadata to (a) let a
later run skip re-extracting/re-appraising a paper it has already seen and (b) emit one
consolidated `refs.bib` covering every paper ever pulled into this wiki, independent of
which run's report cites it.

Keyed by the same `evidence_id` used everywhere else (`corpus.py derive_evidence_id`,
schema.md S9): pmid > doi > pmcid > url. One record per evidence_id; `sync` is an
upsert — a field already populated is never cleared or overwritten by a later, thinner
sync, mirroring `library.py`'s enrichment-only merge for the PDF index.

Subcommands
  sync     --wiki --run-dir            upsert a run's extracted/appraised records into the pool
  lookup   --wiki --pmid|--doi|--pmcid|--evidence-id    find a reusable record
  bib      --wiki --out                refs.bib across the whole pool
  list     --wiki                      dump pool entries

Environment: python3, stdlib only. No pip installs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import utcnow  # noqa: E402  (sibling module, stdlib-only)
import corpus as _corpus  # noqa: E402  (derive_evidence_id, norm_pmid/doi/pmcid, CORPUS_FIELDS)
import library as _library  # noqa: E402  (wiki_root_for_run, read_corpus)
import render as _render  # noqa: E402  (build_entry, bib_key, atomic_write)
import store as _store  # noqa: E402  (read_snapshot, register_text — portable spans)

SCHEMA_VERSION = 1

# Corpus fields worth caching in the pool: everything bibliographic, minus fields that
# are meaningless outside the run that produced them (screening verdict, the run-local
# extraction/appraisal paths themselves, first_seen_query).
POOL_FIELDS = tuple(
    f for f in _corpus.CORPUS_FIELDS
    if f not in ("screening", "extraction_path", "appraisal_path", "first_seen_query")
) + _corpus.CORPUS_BIBLIO


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


class Pool:
    """The <wiki-root>/assets/papers/pool.jsonl store."""

    def __init__(self, wiki_root: Path):
        self.wiki_root = Path(wiki_root).expanduser().resolve()
        self.path = self.wiki_root / "assets" / "papers" / "pool.jsonl"
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
                    continue  # a corrupt pool line must never block a sync/lookup
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

    def lookup(self, *, evidence_id: str | None = None, pmid: str | None = None,
               doi: str | None = None, pmcid: str | None = None) -> dict | None:
        if evidence_id and evidence_id in self.records:
            return self.records[evidence_id]
        npmid = _corpus.norm_pmid(pmid)
        if npmid and f"pmid:{npmid}" in self.records:
            return self.records[f"pmid:{npmid}"]
        ndoi = _corpus.norm_doi(doi)
        if ndoi and f"doi:{ndoi}" in self.records:
            return self.records[f"doi:{ndoi}"]
        npmcid = _corpus.norm_pmcid(pmcid)
        if npmcid and f"pmcid:{npmcid}" in self.records:
            return self.records[f"pmcid:{npmcid}"]
        return None

    def upsert(self, corpus_rec: dict, *, run_wiki_rel: str, run_slug: str) -> tuple[dict, bool]:
        """Merge one corpus record's biblio into the pool; append/update its source pointer."""
        eid = corpus_rec.get("evidence_id") or _corpus.derive_evidence_id(corpus_rec)
        existing = self.records.get(eid)
        is_new = existing is None
        target = existing or {"schema_version": SCHEMA_VERSION, "evidence_id": eid, "sources": []}

        for field in POOL_FIELDS:
            if field in ("schema_version", "evidence_id"):
                continue
            val = corpus_rec.get(field)
            if val in (None, "", [], {}):
                continue
            if not target.get(field):
                target[field] = val

        extraction_path = corpus_rec.get("extraction_path")
        appraisal_path = corpus_rec.get("appraisal_path")
        if extraction_path or appraisal_path:
            sources = target.setdefault("sources", [])
            entry = next((s for s in sources if s.get("run") == run_slug), None)
            if entry is None:
                entry = {"run": run_slug, "run_dir": run_wiki_rel}
                sources.append(entry)
            if extraction_path:
                entry["extraction_path"] = extraction_path
            if appraisal_path:
                entry["appraisal_path"] = appraisal_path
            entry["synced_at"] = utcnow()

        target["updated_at"] = utcnow()
        self.records[eid] = target
        return target, is_new

    def resolve_source(self, entry: dict, kind: str) -> dict | None:
        """The freshest source pointer whose `{kind}_path` still resolves on disk.

        None either when the record has no such pointer, or the pointer's run directory
        has since moved or been deleted — either case is "stale", and the caller falls
        through to a fresh extraction/appraisal rather than trusting a dangling path.
        """
        sources = sorted(entry.get("sources") or [], key=lambda s: s.get("synced_at") or "",
                         reverse=True)
        for s in sources:
            rel = s.get(f"{kind}_path")
            run_dir_rel = s.get("run_dir")
            if not rel or not run_dir_rel:
                continue
            if (self.wiki_root / run_dir_rel / rel).exists():
                return s
        return None

    def resolve_ref(self, entry: dict, kind: str) -> Path | None:
        """Absolute path to a pointer's extraction/appraisal file. See `resolve_source`."""
        s = self.resolve_source(entry, kind)
        if s is None:
            return None
        return self.wiki_root / s["run_dir"] / s[f"{kind}_path"]


def cmd_sync(args) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    wiki = (Path(args.wiki).expanduser().resolve() if args.wiki
            else _library.wiki_root_for_run(run_dir))
    corpus_path = Path(args.corpus) if args.corpus else run_dir / "corpus.jsonl"
    records = _library.read_corpus(corpus_path)
    try:
        run_wiki_rel = str(run_dir.relative_to(wiki))
    except ValueError:
        run_wiki_rel = str(run_dir)
    run_slug = run_dir.name

    pool = Pool(wiki)
    synced, created = 0, 0
    for rec in records:
        if not (rec.get("extraction_path") or rec.get("appraisal_path")):
            continue
        _, is_new = pool.upsert(rec, run_wiki_rel=run_wiki_rel, run_slug=run_slug)
        synced += 1
        created += int(is_new)
    pool.save()
    emit({
        "schema_version": SCHEMA_VERSION, "status": "ok", "command": "sync",
        "wiki": str(wiki), "run_dir": str(run_dir), "pool": str(pool.path),
        "records_seen": len(records), "records_synced": synced, "records_new": created,
        "pool_size": len(pool.records),
    })
    return 0


def cmd_lookup(args) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    pool = Pool(wiki)
    entry = pool.lookup(evidence_id=args.evidence_id, pmid=args.pmid, doi=args.doi,
                        pmcid=args.pmcid)
    result = {"schema_version": SCHEMA_VERSION, "status": "ok", "command": "lookup",
              "matched": entry is not None, "entry": entry,
              "extraction_path": None, "appraisal_path": None}
    if entry is not None:
        extraction = pool.resolve_ref(entry, "extraction")
        appraisal = pool.resolve_ref(entry, "appraisal")
        result["extraction_path"] = str(extraction) if extraction else None
        result["appraisal_path"] = str(appraisal) if appraisal else None
    emit(result)
    return 0 if entry is not None else 1


def _iter_source_ids(obj) -> list[str]:
    """Every distinct `source_id` referenced anywhere in an extraction/appraisal record.

    Spans live at different depths (`spans[]`, `outcomes[].spans[]`, `domains[].spans[]`) —
    walking the whole structure instead of hardcoding paths survives schema additions.
    """
    seen: list[str] = []

    def walk(o) -> None:
        if isinstance(o, dict):
            sid = o.get("source_id")
            if isinstance(sid, str) and sid.startswith("src-") and sid not in seen:
                seen.append(sid)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return seen


def _carry_snapshots(origin_run_dir: Path, dest_run_dir: Path, source_ids: list[str],
                     *, reused_from: str) -> tuple[list[str], list[dict]]:
    """Re-register each snapshot into `dest_run_dir` so its spans verify there too.

    Snapshots are content-addressed (`source_id = sha256(url + text)`, store.py R11):
    writing the same url+text into a different run's store reproduces the identical
    source_id, so a copied extraction's `source_id`/`start`/`end` spans resolve locally
    without any change to `store.py`'s verification. The re-registration is a `register`
    event (never `fresh`, R22) — honest, since the text was not retrieved in this run;
    `verify.py`'s C-FRESH-FETCH reports it as non-fresh exactly like any other cached text.
    """
    carried, failed = [], []
    for sid in source_ids:
        try:
            snap = _store.read_snapshot(origin_run_dir, sid)
        except _store.StoreError as exc:
            failed.append({"source_id": sid, "error": str(exc)})
            continue
        try:
            _store.register_text(
                dest_run_dir, url=snap["url"], text=snap["text"], title=snap["title"],
                access=snap["access"], origin=snap["origin"], paper=snap["paper"],
                asset=snap.get("asset"),
                detail="reused from pool: originally retrieved in %s" % reused_from,
            )
        except _store.StoreError as exc:
            failed.append({"source_id": sid, "error": str(exc)})
            continue
        carried.append(sid)
    return carried, failed


def cmd_reuse(args) -> int:
    dest_run_dir = Path(args.run_dir).expanduser().resolve()
    wiki = (Path(args.wiki).expanduser().resolve() if args.wiki
            else _library.wiki_root_for_run(dest_run_dir))
    pool = Pool(wiki)
    entry = pool.lookup(evidence_id=args.evidence_id, pmid=args.pmid, doi=args.doi,
                        pmcid=args.pmcid)
    result = {"schema_version": SCHEMA_VERSION, "status": "ok", "command": "reuse",
              "matched": entry is not None, "extraction": None, "appraisal": None}
    if entry is None:
        emit(result)
        return 1

    out_dirs = {
        "extraction": dest_run_dir / "workspace" / "extractions",
        "appraisal": dest_run_dir / "workspace" / "appraisals",
    }
    for kind, out_dir in out_dirs.items():
        src = pool.resolve_source(entry, kind)
        if src is None:
            continue
        origin_run_dir = wiki / src["run_dir"]
        src_path = origin_run_dir / src[f"{kind}_path"]
        record = json.loads(src_path.read_text(encoding="utf-8"))
        source_ids = _iter_source_ids(record)
        carried, failed = _carry_snapshots(origin_run_dir, dest_run_dir, source_ids,
                                           reused_from=src["run"])
        record["reused_from"] = src["run"]
        out_dir.mkdir(parents=True, exist_ok=True)
        dest_path = out_dir / Path(src[f"{kind}_path"]).name
        dest_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
        result[kind] = {
            "reused_from": src["run"], "path": str(dest_path.relative_to(dest_run_dir)),
            "snapshots_carried": carried, "snapshots_failed": failed,
        }
    emit(result)
    return 0


def cmd_bib(args) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    pool = Pool(wiki)
    records = list(pool.records.values())
    if args.select == "appraised":
        records = [r for r in records
                  if any(s.get("appraisal_path") for s in r.get("sources") or [])]
    by_key = {}
    for rec in records:
        by_key[_render.bib_key(rec["evidence_id"])] = rec
    header = (
        "% pool-refs.bib — GENERATED by scripts/pool.py. Do not hand-edit; regenerating "
        "overwrites.\n"
        f"% source: {pool.path}  entries: {len(by_key)}  selection: {args.select}\n"
        "% citation key = evidence_id with all non-alphanumerics stripped "
        "(references/schema.md R6)\n\n"
    )
    entries = [_render.build_entry(by_key[key]) for key in sorted(by_key)]
    out_path = Path(args.out)
    _render.atomic_write(out_path, header + "\n".join(entries))
    emit({
        "schema_version": SCHEMA_VERSION, "status": "ok", "command": "bib",
        "wiki": str(wiki), "out": str(out_path), "pool_size": len(pool.records),
        "entries_written": len(entries), "selection": args.select,
    })
    return 0


def cmd_list(args) -> int:
    wiki = Path(args.wiki).expanduser().resolve()
    pool = Pool(wiki)
    entries = sorted(pool.records.values(), key=lambda r: r.get("evidence_id") or "")
    emit({
        "schema_version": SCHEMA_VERSION, "status": "ok", "command": "list",
        "count": len(entries), "entries": entries[: args.limit],
    })
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pool.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="upsert a run's extracted/appraised records into the pool")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--wiki", help="wiki root (default: inferred from --run-dir)")
    s.add_argument("--corpus", help="corpus.jsonl (default: <run-dir>/corpus.jsonl)")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("lookup", help="find a paper already extracted/appraised in another run")
    s.add_argument("--wiki", required=True)
    s.add_argument("--evidence-id", dest="evidence_id")
    s.add_argument("--pmid")
    s.add_argument("--doi")
    s.add_argument("--pmcid")
    s.set_defaults(func=cmd_lookup)

    s = sub.add_parser("reuse", help="copy a pooled paper's extraction/appraisal + "
                                     "snapshots into a run (spans verify locally)")
    s.add_argument("--run-dir", required=True, dest="run_dir")
    s.add_argument("--wiki", help="wiki root (default: inferred from --run-dir)")
    s.add_argument("--evidence-id", dest="evidence_id")
    s.add_argument("--pmid")
    s.add_argument("--doi")
    s.add_argument("--pmcid")
    s.set_defaults(func=cmd_reuse)

    s = sub.add_parser("bib", help="wiki-wide BibTeX from the pool")
    s.add_argument("--wiki", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--select", choices=("all", "appraised"), default="all",
                   help="all: every pooled paper; appraised: only papers with an appraisal")
    s.set_defaults(func=cmd_bib)

    s = sub.add_parser("list", help="dump pool entries")
    s.add_argument("--wiki", required=True)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
