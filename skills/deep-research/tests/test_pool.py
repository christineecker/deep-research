from __future__ import annotations

import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from helpers import make_run, run_py, write_jsonl


class PoolSeedTest(unittest.TestCase):
    def test_seed_adds_matching_pooled_record_as_unscreened_candidate(self):
        with TemporaryDirectory() as tmp:
            wiki, old_run = make_run(Path(tmp), "old-run")
            extraction = old_run / "workspace" / "extractions" / "pmid-12345678.json"
            extraction.write_text(json.dumps({
                "schema_version": 1,
                "pmid": "12345678",
                "source_id": "src-test",
                "summary": "Exercise improved remission.",
            }), encoding="utf-8")
            write_jsonl(old_run / "corpus.jsonl", [{
                "schema_version": 1,
                "evidence_id": "pmid:12345678",
                "pmid": "12345678",
                "doi": "10.1000/exercise",
                "pmcid": None,
                "title": "Exercise therapy for adolescent depression remission",
                "journal": "Journal of Validation",
                "publication_date": "2026",
                "authors": ["Smith JA"],
                "article_types": ["Randomized Controlled Trial"],
                "mesh_terms": ["Depression", "Exercise Therapy", "Adolescent"],
                "keywords": ["remission"],
                "retraction_status": "none",
                "source": "pubmed",
                "is_preprint": False,
                "screening": {"decision": "include", "reason": "old run"},
                "fulltext": {
                    "status": "fulltext",
                    "source_tier": 0,
                    "access_route": "library",
                    "local_path": "assets/papers/pmid-12345678.pdf",
                    "sha256": "abc",
                    "truncation_detected": False,
                },
                "extraction_path": "workspace/extractions/pmid-12345678.json",
                "appraisal_path": None,
                "first_seen_query": "q1",
            }])

            sync = run_py(["scripts/pool.py", "sync", "--wiki", str(wiki),
                           "--run-dir", str(old_run)])
            self.assertEqual(sync.returncode, 0, sync.stderr + sync.stdout)

            _, new_run = make_run(Path(tmp), "new-run")
            seed = run_py([
                "scripts/pool.py", "seed", "--wiki", str(wiki), "--run-dir", str(new_run),
                "--query", "adolescent depression exercise remission", "--min-score", "0.1",
            ])
            self.assertEqual(seed.returncode, 0, seed.stderr + seed.stdout)
            payload = json.loads(seed.stdout)
            self.assertEqual(payload["added"], 1)

            records = [
                json.loads(line)
                for line in (new_run / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source"], "pool")
            self.assertIsNone(records[0]["screening"])
            self.assertIsNone(records[0]["extraction_path"])
            self.assertEqual(records[0]["first_seen_query"], "pool-seed")

    def test_seed_dry_run_does_not_write_corpus(self):
        with TemporaryDirectory() as tmp:
            wiki, old_run = make_run(Path(tmp), "old-run")
            (old_run / "workspace" / "extractions" / "pmid-1.json").write_text(
                "{}", encoding="utf-8"
            )
            write_jsonl(old_run / "corpus.jsonl", [{
                "schema_version": 1,
                "evidence_id": "pmid:1",
                "pmid": "1",
                "title": "Ketamine and depression response",
                "source": "pubmed",
                "extraction_path": "workspace/extractions/pmid-1.json",
            }])
            self.assertEqual(run_py(["scripts/pool.py", "sync", "--wiki", str(wiki),
                                     "--run-dir", str(old_run)]).returncode, 0)
            _, new_run = make_run(Path(tmp), "new-run")
            seed = run_py(["scripts/pool.py", "seed", "--wiki", str(wiki),
                           "--run-dir", str(new_run), "--query", "ketamine depression",
                           "--dry-run"])
            self.assertEqual(seed.returncode, 0, seed.stderr + seed.stdout)
            self.assertFalse((new_run / "corpus.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
