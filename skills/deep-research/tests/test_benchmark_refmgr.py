"""Correctness checks for benchmark_refmgr.py itself (hardening plan package 8).

These are smoke/determinism tests at a tiny scale, not the actual 10k/100k
performance runs (`python3 scripts/benchmark_refmgr.py index --counts 10000 100000`,
run by hand/CI and recorded separately -- see BENCHMARK_RESULTS.md). What belongs in
the regular suite is: the fixture generator is deterministic, the tool's subcommands
don't crash, and the `coverage` benchmark's headline finding -- a fully chunk-indexed
record triggers zero canonical snapshot reads, while an unindexed one always triggers
exactly one -- keeps holding. That property IS the correctness guarantee package 2
fixed; regressing it silently would be exactly the kind of bug this whole hardening
plan exists to catch.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import benchmark_refmgr as bench  # noqa: E402
from refmgr.service import ReferenceManagerService  # noqa: E402


class FixtureDeterminismTest(unittest.TestCase):
    def test_same_seed_and_index_produce_identical_records(self):
        a = bench._record(42)
        b = bench._record(42)
        self.assertEqual(a, b)

    def test_different_indices_produce_different_titles(self):
        titles = {bench._record(i)["title"] for i in range(50)}
        self.assertEqual(len(titles), 50)

    def test_bulk_insert_is_deterministic_across_runs(self):
        with TemporaryDirectory() as tmp_a, TemporaryDirectory() as tmp_b:
            service_a = ReferenceManagerService(Path(tmp_a))
            service_b = ReferenceManagerService(Path(tmp_b))
            try:
                bench._bulk_insert_papers(service_a.conn, 30)
                bench._bulk_insert_papers(service_b.conn, 30)
                titles_a = [r["title"] for r in service_a.conn.execute(
                    "SELECT title FROM papers ORDER BY title").fetchall()]
                titles_b = [r["title"] for r in service_b.conn.execute(
                    "SELECT title FROM papers ORDER BY title").fetchall()]
                self.assertEqual(titles_a, titles_b)
            finally:
                service_a.close()
                service_b.close()


class IndexBenchmarkSmokeTest(unittest.TestCase):
    def test_build_and_measure_at_tiny_scale(self):
        with TemporaryDirectory() as tmp:
            library_root = Path(tmp) / "refmgr"
            build = bench.build_index_fixture(library_root, 50, chunk_sample=10)
            self.assertEqual(build["papers_fts_rebuild_indexed"], 50)
            self.assertGreater(build["chunks_indexed"], 0)

            latency = bench.measure_query_latency(library_root, warm_iterations=3)
            self.assertEqual(set(latency), {s["label"] for s in bench._BENCH_QUERIES})
            for entry in latency.values():
                self.assertIn("cold_ms", entry)
                self.assertEqual(entry["warm"]["n"], 3)

            plans = bench.explain_query_plans(library_root)
            self.assertIn("papers_fts_match", plans)

            disk_mem = bench.measure_disk_and_memory(library_root)
            self.assertGreater(disk_mem["library_sqlite3_bytes"], 0)
            self.assertGreater(disk_mem["peak_rss_bytes"], 0)


class CoverageBenchmarkCorrectnessTest(unittest.TestCase):
    """The package-2 regression guard: canonical reads must track coverage exactly."""

    def test_full_coverage_means_zero_canonical_reads(self):
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            bench.build_coverage_fixture(repo_root, paper_count=6, coverage_fraction=1.0)
            workload = bench.measure_coverage_workload(repo_root, iterations=2)
            self.assertEqual(workload["canonical_reads_last_query"], 0)
            self.assertEqual(workload["results_found"], 6)

    def test_zero_coverage_means_every_snapshot_is_read(self):
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            bench.build_coverage_fixture(repo_root, paper_count=6, coverage_fraction=0.0)
            workload = bench.measure_coverage_workload(repo_root, iterations=2)
            self.assertEqual(workload["canonical_reads_last_query"], 6)
            self.assertEqual(workload["results_found"], 6)

    def test_partial_coverage_reads_only_the_uncovered_sources(self):
        with TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            fixture = bench.build_coverage_fixture(
                repo_root, paper_count=10, coverage_fraction=0.5
            )
            workload = bench.measure_coverage_workload(repo_root, iterations=2)
            uncovered = fixture["paper_count"] - fixture["chunk_cutoff"]
            self.assertEqual(workload["canonical_reads_last_query"], uncovered)
            self.assertEqual(workload["results_found"], 10)


if __name__ == "__main__":
    unittest.main()
