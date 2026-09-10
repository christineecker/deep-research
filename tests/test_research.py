from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import SCRIPTS, run_py


class ResearchInitTest(unittest.TestCase):
    def test_init_creates_the_full_target_layout_without_a_wiki(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            result = run_py(["scripts/research.py", "init", str(repo)])
            self.assertEqual(result.returncode, 0, result.stderr)
            for rel in (
                "data/sources/assets", "data/sources/snapshots",
                "data/papers/extractions", "data/papers/appraisals",
                "projects", "runs", "templates", "exports/wiki",
            ):
                self.assertTrue((repo / rel).is_dir(), rel)
            for rel in ("data/sources/events.jsonl", "data/papers/registry.jsonl",
                       "data/papers/pool.jsonl"):
                self.assertTrue((repo / rel).exists(), rel)

    def test_project_create_scaffolds_isolated_project_and_list_reports_it(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            run_py(["scripts/research.py", "init", str(repo)])
            create = run_py(["scripts/research.py", "project", "create", "my-manuscript",
                             "--repo", str(repo), "--title", "My Manuscript"])
            self.assertEqual(create.returncode, 0, create.stderr)
            project_dir = repo / "projects" / "my-manuscript"
            for rel in ("protocol.md", "synthesis.md", "manuscript.qmd", "refs.bib",
                       "corpus.lock.jsonl", "figures", "tables"):
                self.assertTrue((project_dir / rel).exists(), rel)

            listing = run_py(["scripts/research.py", "project", "list", "--repo", str(repo)])
            self.assertEqual(listing.returncode, 0, listing.stderr)
            self.assertIn("my-manuscript", json.loads(listing.stdout)["projects"])

    def test_project_create_refuses_to_clobber_an_existing_project_without_force(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            run_py(["scripts/research.py", "init", str(repo)])
            run_py(["scripts/research.py", "project", "create", "dup", "--repo", str(repo)])
            second = run_py(["scripts/research.py", "project", "create", "dup",
                            "--repo", str(repo)])
            self.assertEqual(second.returncode, 1)
            self.assertEqual(json.loads(second.stdout)["status"], "error")


class ResearchExportWikiTest(unittest.TestCase):
    def test_export_wiki_writes_pool_projection_and_project_bundle(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            wiki = tmp / "wiki"
            run_py(["scripts/research.py", "init", str(repo)])
            run_py(["scripts/research.py", "project", "create", "demo", "--repo", str(repo)])
            (repo / "projects" / "demo" / "refs.bib").write_text("% x\n", encoding="utf-8")

            reg_path = repo / "data" / "papers" / "registry.jsonl"
            reg_path.write_text(json.dumps({
                "schema_version": 1, "evidence_id": "pmid:1", "pmid": "1",
                "title": "Export test", "status": "registered",
                "metadata_status": "complete", "asset_status": "missing",
                "extraction_status": "not_started", "appraisal_status": "not_appraised",
                "sources": [], "created_at": "x", "updated_at": "x",
            }) + "\n", encoding="utf-8")

            result = run_py(["scripts/research.py", "export", "wiki", "--repo", str(repo),
                            "--wiki", str(wiki), "--project", "demo"])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["pool_records_exported"], 1)
            self.assertTrue((wiki / "assets" / "papers" / "pool.jsonl").exists())
            self.assertTrue((wiki / "research" / "demo" / "refs.bib").exists())


class PoolMigrateTest(unittest.TestCase):
    def test_migrate_promotes_reusable_records_and_reports_stale_pointers(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wiki = tmp / "wiki"
            run_dir = wiki / "outputs" / "deep-research" / "old-run"
            (run_dir / "workspace" / "extractions").mkdir(parents=True)
            (run_dir / "sources").mkdir(parents=True)
            (run_dir / "config.json").write_text(
                json.dumps({"created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")

            import sys
            sys.path.insert(0, str(SCRIPTS))
            import store as _store
            snap = _store.write_snapshot(
                run_dir, url="https://x/mig", text="migrated snapshot text", title="t",
                access="web", origin="web", paper=None, event_type="fetch", fresh=True)
            sid = snap["source_id"]

            (run_dir / "workspace" / "extractions" / "pmid-55555555.json").write_text(
                json.dumps({"pmid": "55555555", "source_id": sid, "summary": "x"}),
                encoding="utf-8")
            corpus_rec = {
                "schema_version": 1, "evidence_id": "pmid:55555555", "pmid": "55555555",
                "doi": None, "pmcid": None, "title": "Migration test paper",
                "journal": "J Mig", "publication_date": "2025", "authors": ["Mig A"],
                "article_types": [], "mesh_terms": [], "keywords": [],
                "retraction_status": "none", "source": "pubmed", "is_preprint": False,
                "screening": None,
                "fulltext": {"status": "missing", "source_tier": None,
                            "access_route": None, "local_path": None, "sha256": None,
                            "truncation_detected": False},
                "extraction_path": "workspace/extractions/pmid-55555555.json",
                "appraisal_path": None, "first_seen_query": None,
            }
            (run_dir / "corpus.jsonl").write_text(json.dumps(corpus_rec) + "\n",
                                                   encoding="utf-8")

            sync = run_py(["scripts/pool.py", "sync", "--wiki", str(wiki),
                          "--run-dir", str(run_dir)])
            self.assertEqual(sync.returncode, 0, sync.stderr)

            repo = tmp / "repo"
            run_py(["scripts/research.py", "init", str(repo)])
            migrate = run_py(["scripts/pool.py", "migrate", "--from-wiki", str(wiki),
                             "--repo", str(repo)])
            self.assertEqual(migrate.returncode, 0, migrate.stderr)
            payload = json.loads(migrate.stdout)
            self.assertEqual(payload["registered"], 1)
            self.assertEqual(payload["extractions_copied"], 1)
            self.assertEqual(payload["stale_pointers"], [])
            self.assertTrue(
                (repo / "data" / "papers" / "extractions" / "pmid-55555555.json").exists())
            self.assertTrue(_store.global_sources_root(repo) / "sources" / f"{sid}.json")

    def test_migrate_reports_stale_pointer_when_run_directory_is_gone(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wiki = tmp / "wiki"
            run_dir = wiki / "outputs" / "deep-research" / "old-run"
            (run_dir / "workspace" / "extractions").mkdir(parents=True)
            (run_dir / "sources").mkdir(parents=True)
            (run_dir / "config.json").write_text(
                json.dumps({"created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
            (run_dir / "workspace" / "extractions" / "pmid-99.json").write_text(
                '{"pmid":"99"}', encoding="utf-8")
            corpus_rec = {
                "schema_version": 1, "evidence_id": "pmid:99", "pmid": "99", "doi": None,
                "pmcid": None, "title": "Stale test", "journal": None,
                "publication_date": None, "authors": [], "article_types": [],
                "mesh_terms": [], "keywords": [], "retraction_status": "none",
                "source": "pubmed", "is_preprint": False, "screening": None,
                "fulltext": {"status": "missing", "source_tier": None,
                            "access_route": None, "local_path": None, "sha256": None,
                            "truncation_detected": False},
                "extraction_path": "workspace/extractions/pmid-99.json",
                "appraisal_path": None, "first_seen_query": None,
            }
            (run_dir / "corpus.jsonl").write_text(json.dumps(corpus_rec) + "\n",
                                                   encoding="utf-8")
            run_py(["scripts/pool.py", "sync", "--wiki", str(wiki), "--run-dir", str(run_dir)])

            import shutil
            shutil.rmtree(run_dir)

            repo = tmp / "repo"
            run_py(["scripts/research.py", "init", str(repo)])
            migrate = run_py(["scripts/pool.py", "migrate", "--from-wiki", str(wiki),
                             "--repo", str(repo)])
            self.assertEqual(migrate.returncode, 0, migrate.stderr)
            payload = json.loads(migrate.stdout)
            self.assertEqual(payload["extractions_copied"], 0)
            self.assertEqual(len(payload["stale_pointers"]), 1)
            self.assertEqual(payload["stale_pointers"][0]["evidence_id"], "pmid:99")


if __name__ == "__main__":
    unittest.main()
