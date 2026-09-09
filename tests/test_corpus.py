from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import ROOT, load_script, run_py, write_jsonl


corpus = load_script("corpus.py")


class CorpusMergeTest(unittest.TestCase):
    def test_merge_preserves_preprint_flag_from_either_record(self):
        base = corpus.normalize_record({
            "pmid": "12345678",
            "doi": "10.1000/published",
            "title": "A validation trial",
            "journal": "Journal",
            "publication_date": "2026",
            "source": "pubmed",
            "is_preprint": False,
        }, allow_extra=True)
        preprint = corpus.normalize_record({
            "doi": "10.1000/published",
            "title": "A validation trial",
            "journal": "Preprint Server",
            "publication_date": "2026",
            "source": "preprint",
            "is_preprint": True,
        }, allow_extra=True)

        merged = corpus.merge_records(base, preprint)

        self.assertTrue(merged["is_preprint"])

    def test_merge_prefers_external_source_over_pool_seed(self):
        pooled = corpus.normalize_record({
            "pmid": "12345678",
            "title": "A validation trial",
            "source": "pool",
            "first_seen_query": "pool-seed",
        }, allow_extra=True)
        pubmed = corpus.normalize_record({
            "pmid": "12345678",
            "title": "A validation trial",
            "source": "pubmed",
            "first_seen_query": "q1",
        }, allow_extra=True)

        merged = corpus.merge_records(pooled, pubmed)

        self.assertEqual(merged["source"], "pubmed")
        self.assertEqual(merged["seen_in_queries"], ["pool-seed", "q1"])


class DeriveEvidenceIdTest(unittest.TestCase):
    def test_pmid_takes_precedence_over_doi_pmcid_and_url(self):
        eid = corpus.derive_evidence_id({
            "pmid": "12345678", "doi": "10.1000/x", "pmcid": "PMC123", "url": "https://x",
        })
        self.assertEqual(eid, "pmid:12345678")

    def test_doi_takes_precedence_over_pmcid_and_url(self):
        eid = corpus.derive_evidence_id({
            "doi": "10.1000/X", "pmcid": "PMC123", "url": "https://x",
        })
        self.assertEqual(eid, "doi:10.1000/x")  # normalized lowercase

    def test_pmcid_takes_precedence_over_url(self):
        eid = corpus.derive_evidence_id({"pmcid": "PMC123", "url": "https://x"})
        self.assertEqual(eid, "pmcid:PMC123")

    def test_url_fallback_is_a_stable_hash(self):
        eid1 = corpus.derive_evidence_id({"url": "https://example.org/paper"})
        eid2 = corpus.derive_evidence_id({"url": "https://example.org/paper"})
        self.assertTrue(eid1.startswith("url:"))
        self.assertEqual(eid1, eid2)

    def test_no_identifiers_raises(self):
        with self.assertRaises(corpus.UserError):
            corpus.derive_evidence_id({})


class CanMergeTest(unittest.TestCase):
    def test_distinct_pmids_block_merge(self):
        ok, why = corpus.can_merge({"pmid": "1"}, {"pmid": "2"}, exact_id_match=False)
        self.assertFalse(ok)
        self.assertIn("PMID", why)

    def test_distinct_dois_block_merge(self):
        ok, why = corpus.can_merge({"doi": "10.1/a"}, {"doi": "10.1/b"}, exact_id_match=False)
        self.assertFalse(ok)
        self.assertIn("DOI", why)

    def test_different_publication_years_block_merge(self):
        ok, why = corpus.can_merge(
            {"publication_date": "2020-01-01"}, {"publication_date": "2021-06-01"},
            exact_id_match=False)
        self.assertFalse(ok)
        self.assertIn("year", why)

    def test_preprint_vs_published_blocks_without_exact_id_match(self):
        ok, why = corpus.can_merge(
            {"is_preprint": True}, {"is_preprint": False}, exact_id_match=False)
        self.assertFalse(ok)
        self.assertIn("preprint", why)

    def test_preprint_vs_published_allowed_with_exact_id_match(self):
        ok, _why = corpus.can_merge(
            {"is_preprint": True}, {"is_preprint": False}, exact_id_match=True)
        self.assertTrue(ok)

    def test_compatible_records_merge(self):
        ok, why = corpus.can_merge(
            {"pmid": "1", "publication_date": "2020"},
            {"pmid": "1", "publication_date": "2020"},
            exact_id_match=True)
        self.assertTrue(ok, why)


class CorpusStoreTest(unittest.TestCase):
    def test_load_on_missing_file_is_empty(self):
        with TemporaryDirectory() as td:
            store = corpus.Corpus(Path(td)).load()
            self.assertEqual(store.list(), [])

    def test_upsert_adds_then_merges_on_exact_pmid(self):
        with TemporaryDirectory() as td:
            store = corpus.Corpus(Path(td)).load()
            first = corpus.normalize_record({
                "pmid": "12345678", "title": "A trial", "source": "pubmed",
                "first_seen_query": "q1",
            }, allow_extra=True)
            action, eid = store.upsert(first)
            self.assertEqual(action, "added")
            self.assertEqual(eid, "pmid:12345678")
            self.assertEqual(len(store.list()), 1)

            second = corpus.normalize_record({
                "pmid": "12345678", "title": "A trial", "source": "pool",
                "first_seen_query": "pool-seed",
            }, allow_extra=True)
            action, eid = store.upsert(second)
            self.assertEqual(action, "merged")
            self.assertEqual(eid, "pmid:12345678")
            self.assertEqual(len(store.list()), 1)
            merged = store.records[eid]
            # external source beats a pool-seed source, per merge_records/_prefer_source
            self.assertEqual(merged["source"], "pubmed")
            self.assertEqual(merged["seen_in_queries"], ["pool-seed", "q1"])

    def test_save_and_reload_round_trips_records_and_order(self):
        with TemporaryDirectory() as td:
            run_dir = Path(td)
            store = corpus.Corpus(run_dir).load()
            store.upsert(corpus.normalize_record(
                {"pmid": "1", "title": "First"}, allow_extra=True))
            store.upsert(corpus.normalize_record(
                {"pmid": "2", "title": "Second"}, allow_extra=True))
            store.save()

            reloaded = corpus.Corpus(run_dir).load()
            self.assertEqual(reloaded.order, ["pmid:1", "pmid:2"])
            self.assertEqual([r["title"] for r in reloaded.list()], ["First", "Second"])


class BuildPrismaTest(unittest.TestCase):
    def test_counts_over_a_small_fixture_corpus(self):
        with TemporaryDirectory() as td:
            run_dir = Path(td)
            included = corpus.normalize_record({
                "pmid": "1", "title": "Included, full text", "source": "pubmed",
                "screening": {"decision": "include", "reason": "eligible"},
                "fulltext": {"status": "fulltext"},
                "extraction_path": "workspace/extractions/pmid-1.json",
            }, allow_extra=True)
            included["merged_from"] = ["url:deadbeef00000000"]
            abstract_only = corpus.normalize_record({
                "pmid": "2", "title": "Included, abstract only", "source": "pubmed",
                "screening": {"decision": "include", "reason": "eligible"},
                "fulltext": {"status": "abstract_only"},
            }, allow_extra=True)
            excluded = corpus.normalize_record({
                "pmid": "3", "title": "Excluded", "source": "pubmed",
                "screening": {"decision": "exclude", "reason": "wrong population"},
            }, allow_extra=True)
            unscreened = corpus.normalize_record({
                "pmid": "4", "title": "Not yet screened", "source": "pubmed",
            }, allow_extra=True)
            write_jsonl(run_dir / "corpus.jsonl",
                       [included, abstract_only, excluded, unscreened])

            prisma = corpus.build_prisma(run_dir)

            self.assertEqual(prisma["identification"]["records_after_dedupe"], 4)
            self.assertEqual(prisma["identification"]["duplicates_removed"], 1)
            self.assertEqual(prisma["identification"]["records_identified"], 5)
            self.assertEqual(prisma["screening"]["records_screened"], 3)
            self.assertEqual(prisma["screening"]["not_yet_screened"], 1)
            self.assertEqual(prisma["screening"]["excluded"], 1)
            self.assertEqual(prisma["screening"]["included_after_screening"], 2)
            self.assertEqual(prisma["retrieval"]["fulltext_sought"], 2)
            self.assertEqual(prisma["retrieval"]["fulltext_obtained"], 2)
            self.assertEqual(prisma["retrieval"]["abstract_only"], 1)
            self.assertEqual(prisma["included"]["studies_included"], 2)
            self.assertEqual(prisma["included"]["with_extraction"], 1)

            markdown = corpus.prisma_markdown(prisma)
            self.assertIn("Records identified", markdown)
            self.assertIn("| 5 |", markdown)


class QueryGuardTest(unittest.TestCase):
    def test_norm_query_and_hash_are_stable_and_case_insensitive(self):
        h1 = corpus.query_hash("Diabetes AND Metformin")
        h2 = corpus.query_hash("diabetes and metformin")
        self.assertEqual(h1, h2)
        self.assertTrue(h1.startswith("sha256:"))

    def test_register_then_check_reports_duplicate_and_fails_on_duplicate_flag(self):
        with TemporaryDirectory() as td:
            run_dir = Path(td)
            (run_dir / "workspace" / "search").mkdir(parents=True, exist_ok=True)
            registered = run_py([
                "scripts/corpus.py", "query-register", "--run-dir", str(run_dir),
                "--query", "diabetes AND metformin", "--query-id", "q1",
            ], cwd=ROOT)
            self.assertEqual(registered.returncode, 0, registered.stderr + registered.stdout)

            checked = run_py([
                "scripts/corpus.py", "query-check", "--run-dir", str(run_dir),
                "--query", "Diabetes and Metformin",
            ], cwd=ROOT)
            self.assertEqual(checked.returncode, 0, checked.stderr + checked.stdout)
            import json as _json
            payload = _json.loads(checked.stdout)
            self.assertTrue(payload["duplicate"])

            blocked = run_py([
                "scripts/corpus.py", "query-check", "--run-dir", str(run_dir),
                "--query", "Diabetes and Metformin", "--fail-on-duplicate",
            ], cwd=ROOT)
            self.assertEqual(blocked.returncode, 1)

            fresh = run_py([
                "scripts/corpus.py", "query-check", "--run-dir", str(run_dir),
                "--query", "an entirely different query", "--fail-on-duplicate",
            ], cwd=ROOT)
            self.assertEqual(fresh.returncode, 0, fresh.stderr + fresh.stdout)
            payload = _json.loads(fresh.stdout)
            self.assertFalse(payload["duplicate"])

    def test_register_conflicting_query_id_for_same_query_is_rejected(self):
        with TemporaryDirectory() as td:
            run_dir = Path(td)
            (run_dir / "workspace" / "search").mkdir(parents=True, exist_ok=True)
            first = run_py([
                "scripts/corpus.py", "query-register", "--run-dir", str(run_dir),
                "--query", "asthma", "--query-id", "q1",
            ], cwd=ROOT)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            second = run_py([
                "scripts/corpus.py", "query-register", "--run-dir", str(run_dir),
                "--query", "asthma", "--query-id", "q2",
            ], cwd=ROOT)
            self.assertEqual(second.returncode, 1)


if __name__ == "__main__":
    unittest.main()
