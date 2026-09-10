from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, run_py

registry = load_script("registry.py")
research = load_script("research.py")
store = load_script("store.py")


def _corpus_rec(evidence_id: str, **overrides) -> dict:
    rec = {
        "schema_version": 1, "evidence_id": evidence_id,
        "pmid": evidence_id.split(":")[1] if evidence_id.startswith("pmid:") else None,
        "doi": evidence_id.split(":", 1)[1] if evidence_id.startswith("doi:") else None,
        "pmcid": None, "title": "A registry test paper", "journal": "J Registry",
        "publication_date": "2026", "authors": ["Reg A"], "article_types": [],
        "mesh_terms": [], "keywords": [], "retraction_status": "none", "source": "pubmed",
        "is_preprint": False, "screening": None,
        "fulltext": {"status": "missing", "source_tier": None, "access_route": None,
                    "local_path": None, "sha256": None, "truncation_detected": False},
        "extraction_path": None, "appraisal_path": None, "first_seen_query": None,
    }
    rec.update(overrides)
    return rec


class RegistryCoreTest(unittest.TestCase):
    def test_register_dedupes_by_evidence_id_and_generates_pool_view(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            reg = registry.Registry(repo)
            rec, is_new = reg.register({"pmid": "111", "title": "First title",
                                        "journal": "J X", "publication_date": "2024"})
            self.assertTrue(is_new)
            self.assertEqual(rec["evidence_id"], "pmid:111")
            self.assertEqual(rec["metadata_status"], "complete")

            rec2, is_new2 = reg.register({"pmid": "111", "title": "First title"})
            self.assertFalse(is_new2)
            self.assertEqual(len(reg.records), 1)

            reg.save()
            entries = reg.generate_pool()
            self.assertEqual(len(entries), 1)
            pool_path = reg.paths["pool"]
            self.assertTrue(pool_path.exists())
            lines = pool_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["evidence_id"], "pmid:111")

    def test_lookup_matches_by_pmid_doi_pmcid(self):
        with TemporaryDirectory() as tmp:
            reg = registry.Registry(Path(tmp) / "repo")
            # pmcid-only record: no pmid/doi, so evidence_id derives from pmcid itself
            # (same key-only lookup semantics as the legacy `pool.py Pool.lookup`: it
            # matches an *identity key*, not a field scan across every record).
            reg.register({"pmcid": "PMC999", "title": "Lookup paper"})
            reg.register({"doi": "10.1000/reglookup", "title": "Another lookup paper"})
            self.assertIsNotNone(reg.lookup(doi="10.1000/reglookup"))
            self.assertIsNotNone(reg.lookup(pmcid="pmc999"))
            self.assertIsNone(reg.lookup(pmid="000"))

    def test_extraction_slug_matches_plan_examples(self):
        self.assertEqual(registry.extraction_slug("pmid:12345678"), "pmid-12345678")
        self.assertEqual(registry.extraction_slug("doi:10.1000/example"),
                         "doi-10.1000-example")


class RegistryBibTexTest(unittest.TestCase):
    def test_parse_bibtex_extracts_pmid_from_note_and_splits_authors(self):
        text = (
            "@article{k1,\n"
            "  title = {A bib paper},\n"
            "  author = {Smith, Jane A and Doe, John},\n"
            "  journal = {J Bib},\n"
            "  year = {2020},\n"
            "  doi = {10.1000/bib1},\n"
            "  note = {PMID: 88888888}\n"
            "}\n"
        )
        entries = registry.parse_bibtex(text)
        self.assertEqual(len(entries), 1)
        raw = registry._bibtex_to_registry_raw(entries[0]["fields"])
        self.assertEqual(raw["pmid"], "88888888")
        self.assertEqual(raw["doi"], "10.1000/bib1")
        self.assertEqual(raw["authors"], ["Smith, Jane A", "Doe, John"])

    def test_import_bib_registers_entries_and_skips_missing_title(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bib = tmp / "refs.bib"
            bib.write_text(
                "@article{k1, title = {Has title}, doi = {10.1000/hastitle}}\n"
                "@article{k2, doi = {10.1000/notitle}}\n",
                encoding="utf-8",
            )
            repo = tmp / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            args = _ns(repo=str(repo), file=str(bib))
            registry.cmd_import_bib(args)
            reg = registry.Registry(repo)
            self.assertEqual(len(reg.records), 1)
            self.assertIn("doi:10.1000/hastitle", reg.records)


class RegistryPromoteTest(unittest.TestCase):
    def test_promote_copies_extraction_and_repoints_corpus_extraction_path(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            run_dir = repo / "runs" / "r1"
            (run_dir / "workspace" / "extractions").mkdir(parents=True)
            (run_dir / "workspace" / "extractions" / "pmid-42.json").write_text(
                json.dumps({"pmid": "42", "summary": "x"}), encoding="utf-8")
            corpus_path = run_dir / "corpus.jsonl"
            corpus_path.write_text(json.dumps(_corpus_rec(
                "pmid:42", extraction_path="workspace/extractions/pmid-42.json")) + "\n",
                encoding="utf-8")

            registry.cmd_promote(_ns(repo=str(repo), run_dir=str(run_dir), corpus=None,
                                     dry_run=False))

            reg = registry.Registry(repo)
            rec = reg.records["pmid:42"]
            self.assertEqual(rec["extraction_status"], "extracted")
            self.assertEqual(rec["extraction_path"],
                             "data/papers/extractions/pmid-42.json")
            self.assertTrue((repo / rec["extraction_path"]).exists())

            corpus_after = json.loads(corpus_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(corpus_after["extraction_path"],
                             "data/papers/extractions/pmid-42.json")

    def test_promote_reports_skipped_when_extraction_file_missing(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            run_dir = repo / "runs" / "r2"
            run_dir.mkdir(parents=True)
            corpus_path = run_dir / "corpus.jsonl"
            corpus_path.write_text(json.dumps(_corpus_rec(
                "pmid:43", extraction_path="workspace/extractions/missing.json")) + "\n",
                encoding="utf-8")
            # exercise via the CLI to check the emitted skipped_records payload
            result = run_py(["scripts/registry.py", "promote", "--repo", str(repo),
                             "--run-dir", str(run_dir)])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["promoted"], 0)
            self.assertEqual(payload["skipped"], 1)

    def test_appraise_promote_is_isolated_per_project(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            run_dir = repo / "runs" / "r3"
            (run_dir / "workspace" / "appraisals").mkdir(parents=True)
            (run_dir / "workspace" / "appraisals" / "pmid-44.json").write_text(
                json.dumps({"tool": "rob2"}), encoding="utf-8")
            corpus_path = run_dir / "corpus.jsonl"
            corpus_path.write_text(json.dumps(_corpus_rec(
                "pmid:44", appraisal_path="workspace/appraisals/pmid-44.json")) + "\n",
                encoding="utf-8")

            registry.cmd_appraise_promote(_ns(repo=str(repo), run_dir=str(run_dir),
                                              project="proj-a", corpus=None, dry_run=False))
            reg = registry.Registry(repo)
            rec = reg.records["pmid:44"]
            self.assertEqual(rec["appraisal_status"], "appraised")
            self.assertIn("proj-a", rec["appraisals"])
            self.assertNotIn("proj-b", rec["appraisals"])


class GlobalSourceStoreTest(unittest.TestCase):
    def test_store_writes_read_and_lists_via_global_fallback_when_run_local_misses(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            run_dir = repo / "runs" / "r1"
            run_dir.mkdir(parents=True)
            (run_dir / "config.json").write_text(
                json.dumps({"created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")

            s = store.Store(run_dir, repo_root=repo)
            snap = s.write_snapshot(url="https://x/global", text="global text", title=None,
                                    access="web", origin="web", paper=None,
                                    event_type="fetch", fresh=True)
            sid = snap["source_id"]
            self.assertFalse((run_dir / "sources" / f"{sid}.json").exists())
            self.assertTrue(
                (store.global_sources_root(repo) / "sources" / f"{sid}.json").exists())

            s2 = store.Store(run_dir, repo_root=repo)
            self.assertEqual(s2.read_snapshot(sid)["text"], "global text")
            self.assertIn(sid, s2.list_snapshots())

    def test_store_without_repo_root_is_unchanged_run_local_behavior(self):
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()
            (run_dir / "config.json").write_text(
                json.dumps({"created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
            s = store.Store(run_dir)
            snap = s.write_snapshot(url="https://x/local", text="local text", title=None,
                                    access="web", origin="web", paper=None)
            self.assertTrue((run_dir / "sources" / f"{snap['source_id']}.json").exists())


class RepoModeCliTest(unittest.TestCase):
    def test_seed_and_lookup_work_against_a_standalone_repo(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            init = run_py(["scripts/research.py", "init", str(repo)])
            self.assertEqual(init.returncode, 0, init.stderr)

            reg = registry.Registry(repo)
            reg.register({
                "pmid": "77", "title": "Adolescent depression exercise remission trial",
                "journal": "J Test", "publication_date": "2026",
                "abstract": "Exercise therapy for adolescent depression remission.",
            })
            reg.set_extraction("pmid:77", "data/papers/extractions/pmid-77.json")
            reg.save()
            reg.generate_pool()

            lookup = run_py(["scripts/pool.py", "lookup", "--repo", str(repo), "--pmid", "77"])
            self.assertEqual(lookup.returncode, 0, lookup.stderr)
            payload = json.loads(lookup.stdout)
            self.assertTrue(payload["matched"])

            run_dir = repo / "runs" / "newrun"
            run_dir.mkdir(parents=True)
            (run_dir / "corpus.jsonl").touch()
            (run_dir / "taskboard.jsonl").touch()
            (run_dir / ".locks").mkdir()
            seed = run_py(["scripts/pool.py", "seed", "--run-dir", str(run_dir),
                          "--repo", str(repo), "--query",
                          "adolescent depression exercise remission",
                          "--min-score", "0.1", "--include-metadata-only"])
            self.assertEqual(seed.returncode, 0, seed.stderr)
            seed_payload = json.loads(seed.stdout)
            self.assertEqual(seed_payload["seeded"], 1)


def _ns(**kwargs):
    class NS:
        pass
    ns = NS()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


if __name__ == "__main__":
    unittest.main()
