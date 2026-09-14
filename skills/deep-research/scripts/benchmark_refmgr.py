#!/usr/bin/env python3
"""benchmark_refmgr.py — reproducible performance benchmark for the reference-manager
library (hardening plan package 8: "measured retrieval optimization").

Package 2 already fixed the correctness bugs in search-under-incomplete-coverage
(nonzero global chunk count no longer masks a specific paper's missing coverage, AND
terms match across metadata+body). This script is the separate, later step the plan
gates on that fix: build deterministic synthetic fixtures, measure what the current
code actually does, and record numbers -- not guess at them, not invent capacity
promises, and not reach for an ANN dependency before evidence says the lexical index
needs one.

What this measures
-------------------
1. **Index build time** at N synthetic papers (`--counts`, default `10000 100000`):
   how long `SearchRepository.rebuild()` (papers_fts) and `ChunkRepository.index_source`
   (a representative snapshot corpus) actually take against the real repository code
   paths -- not a bulk-SQL shortcut. Paper/identifier rows themselves ARE inserted via
   a bulk-SQL fixture step first (see "Fixture construction" below): that part is setup,
   not something package 8 is measuring, and doing it through 100k individual
   `add_paper()` transactions would only measure disk fsync overhead irrelevant to
   retrieval.
2. **Query latency**, p50/p95, cold and warm, for a fixed set of representative queries
   (`papers.search` keyword+filter combinations, `chunks.search`/`papers_matching_all`).
   "Cold" closes and reopens the SQLite connection between measured queries (resets
   SQLite's own page cache); it does NOT drop the OS filesystem cache, which this
   script has no portable, unprivileged way to do -- documented here rather than
   silently overclaiming a true cold-disk number.
3. **Incomplete-index workload cost**: the package-2 fallback path (scan a record's own
   full text when its chunk coverage is stale/missing) has a real cost distinct from
   the fully-indexed case. `bench_incomplete_coverage` builds a SMALL registry.py-level
   repo with real snapshot files on disk (a "small snapshot corpus", per the plan --
   this path's cost scales with snapshot I/O, not paper count, so a large corpus here
   would not answer a different question, just take longer) at 0%/50%/100% chunk
   coverage and measures `registry.py search --q` latency plus how many snapshot files
   get read per query.
4. **Peak memory** (`resource.getrusage().ru_maxrss`, whole-process RSS -- covers
   SQLite's C-level memory, unlike `tracemalloc`) and **disk usage** (library.sqlite3
   file size after a WAL checkpoint, plus the WAL/SHM sidecars if still present).

Reproducibility
---------------
Every fixture is generated from a fixed seed (`--seed`, default 20260914) with a small
closed vocabulary -- same seed, same count, same rows, byte-for-byte, every run, on any
machine. No network, no pip installs, no private library data.

Usage
-----
    python3 scripts/benchmark_refmgr.py index --counts 10000 100000 --out results.json
    python3 scripts/benchmark_refmgr.py coverage --papers 200 --out coverage.json
    python3 scripts/benchmark_refmgr.py regression --repo <repo-with-real-data> \\
        --queries "adolescent depression" "zebra biomarker" --out regression.json

`regression` is the "before/after" harness: run it before a change to capture current
result sets and latencies, then again after, and diff the two JSON files by hand or
with `regression --diff before.json after.json` -- unchanged `evidence_id` result sets
prove retrieval did not regress; latency deltas are the number an optimization has to
justify itself against.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refmgr import db  # noqa: E402
from refmgr.service import ReferenceManagerService  # noqa: E402

SEED = 20260914

#: Small closed vocabulary -> fully deterministic, human-legible synthetic text.
_TOPICS = ["depression", "diabetes", "hypertension", "asthma", "arthritis", "migraine",
           "insomnia", "obesity", "anemia", "psoriasis"]
_INTERVENTIONS = ["exercise therapy", "cognitive behavioral therapy", "metformin",
                  "physical rehabilitation", "dietary counseling", "acupuncture",
                  "mindfulness training", "statin therapy", "vitamin supplementation",
                  "graded exposure"]
_POPULATIONS = ["adolescents", "older adults", "postmenopausal women", "children",
                "veterans", "pregnant women", "athletes", "shift workers",
                "primary care patients", "outpatients"]
_JOURNALS = ["Journal of Clinical Studies", "Annals of Applied Medicine",
             "International Review of Therapeutics", "Clinical Practice Quarterly"]
_FILLER = ("Background information precedes the main finding in every synthetic "
           "record so chunk boundaries behave like real prose. ")


def _now_iso() -> str:
    # Fixed, not wall-clock: fixtures must be byte-identical across runs/machines.
    return "2026-01-01T00:00:00+00:00"


def _record(i: int) -> dict:
    topic = _TOPICS[i % len(_TOPICS)]
    intervention = _INTERVENTIONS[(i * 7) % len(_INTERVENTIONS)]
    population = _POPULATIONS[(i * 13) % len(_POPULATIONS)]
    journal = _JOURNALS[(i * 3) % len(_JOURNALS)]
    title = f"{intervention.title()} for {topic} in {population}: record {i:07d}"
    abstract = (
        f"This study evaluates {intervention} for {topic} among {population}. "
        f"{_FILLER * 3}Synthetic identifier {i:07d}."
    )
    body = (
        f"{_FILLER * 20}Results: {intervention} was associated with a measurable "
        f"change in {topic} outcomes among {population}, record {i:07d}. "
        f"{_FILLER * 20}Discussion follows with no further findings of note."
    )
    return {
        "title": title, "abstract": abstract, "journal": journal, "body": body,
        "doi": f"10.9999/synthetic-{i:07d}", "topic": topic,
    }


def _peak_rss_bytes() -> int:
    """Whole-process peak RSS. macOS reports bytes; Linux reports KiB."""
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw if platform.system() == "Darwin" else raw * 1024


def _percentiles(samples: list) -> dict:
    ordered = sorted(samples)
    return {
        "p50_ms": round(statistics.median(ordered) * 1000, 3),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)] * 1000, 3),
        "min_ms": round(ordered[0] * 1000, 3),
        "max_ms": round(ordered[-1] * 1000, 3),
        "n": len(ordered),
    }


def _hardware_info() -> dict:
    info = {
        "platform": platform.platform(), "processor": platform.processor() or None,
        "python": platform.python_version(),
    }
    try:
        import os as _os
        info["cpu_count"] = _os.cpu_count()
    except Exception:
        info["cpu_count"] = None
    return info


def _bulk_insert_papers(conn, count: int, *, seed: int = SEED) -> None:
    """Insert `count` synthetic papers + DOI identifiers directly via SQL.

    This is fixture SETUP, not something package 8 measures: real per-paper import
    (`ReferenceManagerService.add_paper`) is one write transaction per paper, and at
    100k papers that transaction overhead alone would dominate the clock -- measuring
    disk fsync behavior, not retrieval. Deterministic given a fixed `seed`/`count`.
    """
    import refmgr.identity as identity

    with db.transaction(conn):
        for i in range(count):
            rec = _record(i)
            paper_id = identity.new_id()
            metadata = json.dumps({
                "journal": rec["journal"], "abstract": rec["abstract"],
                "publication_date": "2020-01-01",
            })
            conn.execute(
                "INSERT INTO papers (id, title, paper_type, metadata_json, "
                "provenance, created_at, updated_at) VALUES (?, ?, 'article', ?, "
                "'benchmark', ?, ?)",
                (paper_id, rec["title"], metadata, _now_iso(), _now_iso()),
            )
            identifier_id = identity.new_id()
            conn.execute(
                "INSERT INTO identifiers (id, paper_id, scheme, value, is_primary, "
                "created_at) VALUES (?, ?, 'doi', ?, 1, ?)",
                (identifier_id, paper_id, rec["doi"], _now_iso()),
            )


def build_index_fixture(library_root: Path, count: int, *, chunk_sample: int = 500,
                        seed: int = SEED) -> dict:
    """Build `count` synthetic papers (bulk SQL) then time the REAL index paths:
    `SearchRepository.rebuild()` over all of them, and `ChunkRepository.index_source`
    over a `chunk_sample`-sized subset (a full-corpus chunk pass is a separate,
    optional `--full-chunks` measurement -- see `cmd_index`)."""
    service = ReferenceManagerService(library_root)
    try:
        t0 = time.perf_counter()
        _bulk_insert_papers(service.conn, count, seed=seed)
        fixture_seconds = time.perf_counter() - t0

        t0 = time.perf_counter()
        rebuild_report = service.search.rebuild()
        papers_fts_seconds = time.perf_counter() - t0

        paper_ids = [r["id"] for r in service.conn.execute(
            "SELECT id FROM papers ORDER BY id LIMIT ?", (chunk_sample,)).fetchall()]
        t0 = time.perf_counter()
        chunks_indexed = 0
        for i, paper_id in enumerate(paper_ids):
            rec = _record(i)
            content_hash = "sha256:" + hashlib.sha256(rec["body"].encode()).hexdigest()
            result = service.chunks.index_source(
                paper_id, f"src-bench-{i:07d}", rec["body"], content_hash)
            chunks_indexed += result["indexed"]
        chunks_seconds = time.perf_counter() - t0

        return {
            "count": count, "fixture_build_seconds": round(fixture_seconds, 3),
            "papers_fts_rebuild_seconds": round(papers_fts_seconds, 3),
            "papers_fts_rebuild_indexed": rebuild_report["indexed"],
            "chunk_sample_size": len(paper_ids),
            "chunk_index_seconds": round(chunks_seconds, 3),
            "chunks_indexed": chunks_indexed,
        }
    finally:
        service.close()


#: Representative queries exercising different code paths in search.py/chunks.py.
_BENCH_QUERIES = [
    {"label": "keyword_common_term", "kind": "papers", "query": "therapy",
     "filters": {}},
    {"label": "keyword_with_year_filter", "kind": "papers", "query": "diabetes",
     "filters": {"year_from": 2020, "year_to": 2020}},
    {"label": "keyword_with_journal_filter", "kind": "papers", "query": "exercise",
     "filters": {"journal": "Clinical"}},
    {"label": "no_query_listing", "kind": "papers", "query": None, "filters": {}},
    {"label": "chunk_bm25_search", "kind": "chunks", "query": "measurable change"},
    {"label": "chunk_papers_matching_all", "kind": "chunks_all",
     "query": "measurable change"},
]


def _run_query(service, spec: dict):
    if spec["kind"] == "papers":
        service.search.search(spec["query"], limit=20, **spec["filters"])
    elif spec["kind"] == "chunks":
        service.chunks.search(spec["query"], limit=20)
    elif spec["kind"] == "chunks_all":
        service.chunks.papers_matching_all(spec["query"].split())


def measure_query_latency(library_root: Path, *, warm_iterations: int = 30) -> dict:
    results = {}
    for spec in _BENCH_QUERIES:
        service = ReferenceManagerService(library_root)
        try:
            t0 = time.perf_counter()
            _run_query(service, spec)
            cold_seconds = time.perf_counter() - t0
        finally:
            service.close()

        service = ReferenceManagerService(library_root)
        try:
            warm_samples = []
            for _ in range(warm_iterations):
                t0 = time.perf_counter()
                _run_query(service, spec)
                warm_samples.append(time.perf_counter() - t0)
        finally:
            service.close()

        results[spec["label"]] = {
            "cold_ms": round(cold_seconds * 1000, 3),
            "warm": _percentiles(warm_samples),
        }
    return results


def explain_query_plans(library_root: Path) -> dict:
    """`EXPLAIN QUERY PLAN` for the two hot inner queries in search.py/chunks.py --
    inspecting the plan is the plan's own prescribed first step, before reaching for
    batching or a new index."""
    service = ReferenceManagerService(library_root)
    try:
        conn = service.conn
        plans = {}
        plans["papers_fts_match"] = [
            dict(row) for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT papers_fts.paper_id FROM papers_fts "
                "JOIN papers p ON p.id = papers_fts.paper_id "
                "WHERE papers_fts MATCH ? AND p.deleted_at IS NULL",
                ('"therapy"',),
            ).fetchall()
        ]
        plans["papers_year_filter_no_query"] = [
            dict(row) for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT p.id FROM papers p WHERE p.deleted_at IS "
                "NULL AND json_extract(p.metadata_json, '$.publication_date') "
                "IS NOT NULL"
            ).fetchall()
        ]
        plans["chunks_fts_match"] = [
            dict(row) for row in conn.execute(
                "EXPLAIN QUERY PLAN SELECT c.id FROM chunks_fts "
                "JOIN chunks c ON c.id = chunks_fts.chunk_id "
                "JOIN papers p ON p.id = c.paper_id "
                "WHERE chunks_fts MATCH ? AND p.deleted_at IS NULL",
                ('"measurable" OR "change"',),
            ).fetchall()
        ]
        return plans
    finally:
        service.close()


def measure_disk_and_memory(library_root: Path) -> dict:
    service = ReferenceManagerService(library_root)
    try:
        service.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        service.close()
    db_path = library_root / "library.sqlite3"
    total_bytes = sum(
        p.stat().st_size for p in library_root.glob("library.sqlite3*") if p.is_file()
    )
    return {
        "library_sqlite3_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "total_refmgr_dir_bytes": total_bytes,
        "peak_rss_bytes": _peak_rss_bytes(),
    }


def cmd_index(args) -> int:
    report = {"benchmark": "index", "seed": args.seed, "hardware": _hardware_info(),
              "note": "cold = fresh SQLite connection, not a dropped OS page cache "
                      "(no portable unprivileged way to force that)",
              "runs": []}
    for count in args.counts:
        with tempfile.TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "refmgr"
            build = build_index_fixture(library_root, count, seed=args.seed)
            latency = measure_query_latency(library_root)
            disk_mem = measure_disk_and_memory(library_root)
            plans = explain_query_plans(library_root) if count == args.counts[0] else None
            run = {"build": build, "latency": latency, "disk_and_memory": disk_mem}
            if plans is not None:
                run["query_plans"] = plans
            report["runs"].append(run)
    text = json.dumps(report, indent=2, sort_keys=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def build_coverage_fixture(repo_root: Path, *, paper_count: int, coverage_fraction: float,
                           seed: int = SEED) -> dict:
    """A SMALL registry.py-level repo with real snapshot files on disk -- this path's
    cost is per-snapshot-read, not per-paper, so a large corpus here answers nothing
    a small one does not, and would just take longer to build."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import store
    registry_mod = sys.modules.get("registry")
    if registry_mod is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "registry", Path(__file__).resolve().parent / "registry.py")
        registry_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(registry_mod)

    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "data" / "sources").mkdir(parents=True, exist_ok=True)
    (repo_root / "data" / "papers" / "extractions").mkdir(parents=True, exist_ok=True)

    reg = registry_mod.Registry(repo_root)
    chunk_cutoff = int(paper_count * coverage_fraction)
    with reg.locked():
        for i in range(paper_count):
            rec = _record(i)
            reg.register({"doi": rec["doi"], "title": rec["title"],
                         "journal": rec["journal"], "publication_date": "2020-01-01",
                         "abstract": rec["abstract"]})
        reg.commit()

    reg = registry_mod.Registry(repo_root)
    service = registry_mod._refmgr_service(repo_root)
    try:
        for i in range(paper_count):
            rec = _record(i)
            write_result = store.global_write_snapshot_result(
                repo_root, url=f"https://example.org/synthetic-{i:07d}",
                text=rec["body"], title=rec["title"], access="full_text",
                origin="web", paper=None, event_type="fetch", fresh=True,
                actor="benchmark",
            )
            source_id = write_result["source_id"]
            evidence_id = f"doi:{rec['doi']}"
            extraction_path = (
                repo_root / "data" / "papers" / "extractions"
                / f"{registry_mod.extraction_slug(evidence_id)}.json"
            )
            extraction_path.write_text(json.dumps({
                "evidence_id": evidence_id,
                "spans": [{"claim": f"measurable change record {i:07d}",
                          "evidence_id": evidence_id, "source_id": source_id,
                          "start": 0, "end": 10, "access": "full_text"}],
            }), encoding="utf-8")
            with reg.locked():
                reg.set_extraction(evidence_id, str(extraction_path.relative_to(repo_root)))
                reg.commit()

            if i < chunk_cutoff:
                content_hash = "sha256:" + hashlib.sha256(rec["body"].encode()).hexdigest()
                paper_id = reg.records[evidence_id]["refmgr_paper_id"]
                service.chunks.index_source(paper_id, source_id, rec["body"], content_hash)
    finally:
        service.close()

    return {"paper_count": paper_count, "coverage_fraction": coverage_fraction,
            "chunk_cutoff": chunk_cutoff}


def measure_coverage_workload(repo_root: Path, *, iterations: int = 20) -> dict:
    """Latency and canonical-file-read count for `registry.py search --q` at whatever
    coverage level `repo_root` was built with -- run once per coverage fraction and
    compare, per the plan's "include incomplete-index workloads"."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "registry_bench", Path(__file__).resolve().parent / "registry.py")
    registry_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(registry_mod)
    import store

    read_count = {"n": 0}
    original_read = store.global_read_snapshot

    def counting_read(*args, **kwargs):
        read_count["n"] += 1
        return original_read(*args, **kwargs)

    registry_mod._store.global_read_snapshot = counting_read
    try:
        registry = registry_mod.Registry(repo_root)
        records = sorted(registry.records.values(), key=lambda r: r.get("evidence_id") or "")

        samples = []
        for _ in range(iterations):
            read_count["n"] = 0
            t0 = time.perf_counter()
            out, _snippets = registry_mod._keyword_filter(
                records, argparse.Namespace(q="measurable change", repo=repo_root, project=None)
            )
            samples.append(time.perf_counter() - t0)
        return {
            "latency": _percentiles(samples),
            "canonical_reads_last_query": read_count["n"],
            "results_found": len(out),
        }
    finally:
        registry_mod._store.global_read_snapshot = original_read


def cmd_coverage(args) -> int:
    report = {"benchmark": "coverage", "seed": args.seed, "hardware": _hardware_info(),
              "paper_count": args.papers, "runs": []}
    for fraction in args.fractions:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            build_t0 = time.perf_counter()
            fixture = build_coverage_fixture(
                repo_root, paper_count=args.papers, coverage_fraction=fraction,
                seed=args.seed,
            )
            fixture["build_seconds"] = round(time.perf_counter() - build_t0, 3)
            workload = measure_coverage_workload(repo_root, iterations=args.iterations)
            report["runs"].append({"fixture": fixture, "workload": workload})
    text = json.dumps(report, indent=2, sort_keys=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def cmd_diff(args) -> int:
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    print(json.dumps({"before": before, "after": after}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="benchmark_refmgr.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("index", help="index-build time + query latency at N papers")
    s.add_argument("--counts", type=int, nargs="+", default=[10000, 100000])
    s.add_argument("--seed", type=int, default=SEED)
    s.add_argument("--out")
    s.set_defaults(func=cmd_index)

    s = sub.add_parser("coverage", help="incomplete-index workload cost")
    s.add_argument("--papers", type=int, default=200)
    s.add_argument("--fractions", type=float, nargs="+", default=[0.0, 0.5, 1.0])
    s.add_argument("--iterations", type=int, default=20)
    s.add_argument("--seed", type=int, default=SEED)
    s.add_argument("--out")
    s.set_defaults(func=cmd_coverage)

    s = sub.add_parser("diff", help="print two benchmark JSON reports side by side")
    s.add_argument("--before", required=True)
    s.add_argument("--after", required=True)
    s.set_defaults(func=cmd_diff)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
