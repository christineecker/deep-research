from __future__ import annotations

import unittest

from helpers import load_script


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


if __name__ == "__main__":
    unittest.main()
