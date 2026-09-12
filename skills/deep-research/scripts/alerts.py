#!/usr/bin/env python3
"""alerts.py — saved PubMed searches, re-run to find what is new.

A reference manager that never tells you what appeared since last week is a batch tool.
`refmgr`'s `saved_searches` table already stored query specifications; nothing ever
executed them (OPTIMIZATION_PLAN.md item 7). This does:

    save   --repo --name --query "<pubmed query>" [--filters-json <json>]
    list   --repo
    delete --repo --name
    run    --repo [--name <name> ...] [--since <YYYY/MM/DD>] [--retmax N] [--register]

`run` re-executes each saved search restricted to records **entered into PubMed** since
the search last ran, diffs the hits against `registry.jsonl`, and reports the PMIDs the
repo has never seen. With `--register` it also registers them, so the next run's diff is
against the new baseline.

Two deliberate choices:

* **Entry date, not publication date.** The alert question is "what is new to me", and a
  paper indexed today may have a 2023 publication date. The clause added to every rerun
  is `("<since>"[edat] : "3000"[edat])`; `--filters-json`'s `years` filter still applies
  to publication date, as everywhere else in this skill.
* **No daemon, no polling loop.** This is a command the user (or a cron job they own)
  runs. Nothing here schedules itself or runs in the background.

Storage is `refmgr`'s existing `saved_searches` table — no new file. Each row's
`query_json` holds `{"kind": "pubmed", "query", "filters", "last_run"}`, where `last_run`
is this script's own bookkeeping: when it last executed and what the diff baseline was.

Environment: python3 + `requests` (via `eutils.py`, the same dependency the rest of the
search path already has). Honors `DEEP_RESEARCH_FIXTURES` for offline replay.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import utcnow  # noqa: E402  (sibling module, stdlib-only)
import corpus as _corpus  # noqa: E402  (norm_pmid)
import registry as _registry  # noqa: E402  (Registry, repo_paths, efetch->registry mapping)

SCHEMA_VERSION = 1
KIND = "pubmed"
DEFAULT_RETMAX = 200
#: The open end of an entry-date range. PubMed accepts a far-future bound; using one
#: keeps the clause a plain range rather than a special "no upper bound" case.
EDAT_OPEN_END = "3000"


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False))


def _service(repo_root: Path):
    return _registry._refmgr_service(repo_root)


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y/%m/%d")


def _as_edat(value: str) -> str:
    """Normalize an ISO-ish date to PubMed's `YYYY/MM/DD`. Passed through if already so."""
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y/%m/%d")
        except ValueError:
            continue
    # A bare year or year/month is a valid PubMed date bound on its own.
    return text.replace("-", "/")


def with_entry_date(query: str, since: str) -> str:
    """The saved query restricted to records entered into PubMed on/after `since`."""
    return f'({query}) AND ("{_as_edat(since)}"[edat] : "{EDAT_OPEN_END}"[edat])'


def registry_pmids(registry) -> set[str]:
    """Every PMID the repo already knows, normalized — the diff baseline."""
    known: set[str] = set()
    for eid, rec in registry.records.items():
        pmid = _corpus.norm_pmid(rec.get("pmid"))
        if pmid:
            known.add(pmid)
        if eid.startswith("pmid:"):
            normalized = _corpus.norm_pmid(eid.split(":", 1)[1])
            if normalized:
                known.add(normalized)
    return known


def _saved_entry(row: dict) -> dict:
    query = row.get("query") or {}
    return {
        "name": row["name"],
        "id": row["id"],
        "kind": query.get("kind"),
        "query": query.get("query"),
        "filters": query.get("filters") or {},
        "last_run": query.get("last_run"),
        "created_at": row.get("created_at"),
    }


# --------------------------------------------------------------------- commands


def cmd_save(args) -> int:
    repo_root = _registry.repo_paths(args.repo)["repo_root"]
    try:
        filters = json.loads(args.filters_json) if args.filters_json else {}
    except json.JSONDecodeError as exc:
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "save",
              "error": f"--filters-json is not valid JSON: {exc}"})
        return 2
    if not isinstance(filters, dict):
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "save",
              "error": "--filters-json must be a JSON object"})
        return 2

    # Fail on a bad query/filter now, at save time, rather than on every future rerun.
    import eutils as _eutils
    try:
        translated_preview = _eutils.build_query(args.query, filters)
    except _eutils.EutilsError as exc:
        emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "save",
              "error": str(exc)})
        return 2

    payload = {"kind": KIND, "query": args.query, "filters": filters, "last_run": None}
    service = _service(repo_root)
    try:
        existing = service.saved_searches.get_by_name(args.name)
        if existing is not None and not args.force:
            emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "save",
                  "error": f"saved search {args.name!r} already exists (pass --force to "
                           f"replace its query)"})
            return 1
        if existing is not None:
            # Replacing the query keeps the row's identity and its last_run baseline:
            # a reworded query on the same topic should not re-report years of backlog.
            payload["last_run"] = (existing.get("query") or {}).get("last_run")
            service.saved_searches.update_query(existing["id"], payload)
            saved_id = existing["id"]
        else:
            saved_id = service.saved_searches.create(args.name, payload)
        emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "save",
              "repo": str(repo_root), "name": args.name, "id": saved_id,
              "query": args.query, "filters": filters,
              "translated_preview": translated_preview,
              "replaced": existing is not None})
    finally:
        service.close()
    return 0


def cmd_list(args) -> int:
    repo_root = _registry.repo_paths(args.repo)["repo_root"]
    service = _service(repo_root)
    try:
        rows = [_saved_entry(row) for row in service.saved_searches.list(limit=args.limit)]
    finally:
        service.close()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "list",
          "repo": str(repo_root), "count": len(rows), "saved_searches": rows})
    return 0


def cmd_delete(args) -> int:
    repo_root = _registry.repo_paths(args.repo)["repo_root"]
    service = _service(repo_root)
    try:
        row = service.saved_searches.get_by_name(args.name)
        if row is None:
            emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "delete",
                  "error": f"no saved search named {args.name!r}"})
            return 1
        service.saved_searches.delete(row["id"])
    finally:
        service.close()
    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "delete",
          "repo": str(repo_root), "name": args.name})
    return 0


def _run_one(entry: dict, *, registry, known_pmids: set[str], since: str | None,
             retmax: int, email: str | None) -> dict:
    """Re-run one saved search and diff its hits against the registry."""
    import eutils as _eutils

    effective_since = since or (entry.get("last_run") or {}).get("at_date") \
        or _as_edat(entry.get("created_at") or _today())
    term = with_entry_date(entry["query"], effective_since)

    result = {"name": entry["name"], "since": _as_edat(effective_since)}
    try:
        search = _eutils.esearch(term, filters=entry.get("filters") or {},
                                 query_id=f"alert:{entry['name']}", retmax=retmax,
                                 email=email)
    except _eutils.EutilsError as exc:
        result.update(status="error", error=str(exc))
        return result

    hits = [p for p in (search.get("retrieved_pmids") or []) if p]
    new_pmids = [p for p in hits if _corpus.norm_pmid(p) not in known_pmids]
    result.update(status="ok", translated_query=search.get("translated_query"),
                  total_hits=search.get("count"), returned=len(hits),
                  new_count=len(new_pmids), new_pmids=new_pmids)
    if search.get("count") is not None and len(hits) < (search.get("count") or 0):
        result["truncated"] = (f"{search['count']} hits, {len(hits)} returned — raise "
                               f"--retmax to see the rest")
    return result


def _fetch_records(pmids: list[str], email: str | None) -> tuple[list[dict], str | None]:
    import eutils as _eutils
    try:
        fetched = _eutils.efetch(pmids=pmids, email=email)
    except _eutils.EutilsError as exc:
        return [], str(exc)
    return fetched.get("records") or [], None


def cmd_run(args) -> int:
    repo_root = _registry.repo_paths(args.repo)["repo_root"]
    registry = _registry.Registry(repo_root)
    known = registry_pmids(registry)

    service = _service(repo_root)
    try:
        rows = [_saved_entry(row) for row in service.saved_searches.list(limit=500)]
        selected = [e for e in rows if e["kind"] == KIND]
        if args.name:
            wanted = set(args.name)
            selected = [e for e in selected if e["name"] in wanted]
            missing = wanted - {e["name"] for e in selected}
            if missing:
                emit({"schema_version": SCHEMA_VERSION, "status": "error", "command": "run",
                      "error": f"no saved search named: {', '.join(sorted(missing))}"})
                return 1
        if not selected:
            emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "run",
                  "repo": str(repo_root), "results": [],
                  "notes": ["no saved PubMed searches — create one with `alerts.py save`"]})
            return 0

        results = []
        registered_total = 0
        for entry in selected:
            result = _run_one(entry, registry=registry, known_pmids=known,
                              since=args.since, retmax=args.retmax, email=args.email)
            if result["status"] == "ok" and result["new_pmids"]:
                records, fetch_error = _fetch_records(result["new_pmids"], args.email)
                if fetch_error:
                    result["metadata_error"] = fetch_error
                result["new_records"] = [
                    {"pmid": r.get("pmid"), "title": r.get("title"),
                     "publication_date": r.get("publication_date")}
                    for r in records
                ]
                if args.register and records:
                    with registry.locked():
                        for record in records:
                            registry.register(_registry._efetch_to_registry_raw(record))
                        registry.commit()
                    registered = [r.get("pmid") for r in records if r.get("pmid")]
                    result["registered"] = registered
                    registered_total += len(registered)
                    # Later searches in this same run must not re-report what this one
                    # just registered.
                    known |= {_corpus.norm_pmid(p) for p in registered if p}

            if result["status"] == "ok" and not args.dry_run:
                payload = {"kind": KIND, "query": entry["query"],
                           "filters": entry["filters"],
                           "last_run": {"at": utcnow(), "at_date": _today(),
                                        "since": result["since"],
                                        "new_count": result["new_count"],
                                        "registered": bool(args.register)}}
                service.saved_searches.update_query(entry["id"], payload)
            results.append(result)
    finally:
        service.close()

    notes = []
    if args.dry_run:
        notes.append("--dry-run: the next run will use the same `since` date as this one")
    if not args.register and any(r.get("new_count") for r in results):
        notes.append("new hits are reported only; pass --register to add them to the "
                     "registry (and to move the baseline forward)")

    emit({"schema_version": SCHEMA_VERSION, "status": "ok", "command": "run",
          "repo": str(repo_root), "searches": len(results),
          "new_total": sum(r.get("new_count") or 0 for r in results),
          "registered_total": registered_total, "results": results, "notes": notes})
    return 1 if any(r["status"] == "error" for r in results) else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="alerts.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("save", help="save a PubMed query to re-run later")
    s.add_argument("--repo", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--query", required=True, help="PubMed query string")
    s.add_argument("--filters-json", dest="filters_json",
                   help="eutils.py filter object (years/journals/article_types/...)")
    s.add_argument("--force", action="store_true", help="replace an existing saved search")
    s.set_defaults(func=cmd_save)

    s = sub.add_parser("list", help="list saved searches and when they last ran")
    s.add_argument("--repo", required=True)
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("delete", help="delete a saved search")
    s.add_argument("--repo", required=True)
    s.add_argument("--name", required=True)
    s.set_defaults(func=cmd_delete)

    s = sub.add_parser("run", help="re-run saved searches and report what is new")
    s.add_argument("--repo", required=True)
    s.add_argument("--name", action="append",
                   help="only this saved search (repeatable; default: all)")
    s.add_argument("--since", help="entry-date floor (YYYY/MM/DD); default: when the "
                                   "search last ran")
    s.add_argument("--retmax", type=int, default=DEFAULT_RETMAX)
    s.add_argument("--register", action="store_true",
                   help="register new hits into the registry as well as reporting them")
    s.add_argument("--dry-run", dest="dry_run", action="store_true",
                   help="do not move the saved search's baseline forward")
    s.add_argument("--email", help="contact email for NCBI (or $DEEP_RESEARCH_EMAIL)")
    s.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
