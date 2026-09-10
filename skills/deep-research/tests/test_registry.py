from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, minimal_corpus_record, run_py, write_json, write_jsonl

registry = load_script("registry.py")
research = load_script("research.py")
store = load_script("store.py")
render = load_script("render.py")


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
            # pmcid-only record: no pmid/doi, so evidence_id derives from pmcid itself.
            reg.register({"pmcid": "PMC999", "title": "Lookup paper"})
            reg.register({"doi": "10.1000/reglookup", "title": "Another lookup paper"})
            self.assertIsNotNone(reg.lookup(doi="10.1000/reglookup"))
            self.assertIsNotNone(reg.lookup(pmcid="pmc999"))
            self.assertIsNone(reg.lookup(pmid="000"))

    def test_lookup_finds_record_by_alias_identifier_not_its_key(self):
        with TemporaryDirectory() as tmp:
            reg = registry.Registry(Path(tmp) / "repo")
            # Keyed pmid:111 (pmid wins evidence_id derivation) but also carries a DOI
            # and a PMCID — those must be reachable too, not just the primary key.
            reg.register({"pmid": "111", "doi": "10.1000/alias", "pmcid": "PMC555",
                          "title": "Alias paper"})
            by_doi = reg.lookup(doi="10.1000/alias")
            by_pmcid = reg.lookup(pmcid="PMC555")
            self.assertIsNotNone(by_doi)
            self.assertEqual(by_doi["evidence_id"], "pmid:111")
            self.assertIsNotNone(by_pmcid)
            self.assertEqual(by_pmcid["evidence_id"], "pmid:111")

    def test_register_merges_alias_identifiers_and_rekeys_to_stronger_id(self):
        with TemporaryDirectory() as tmp:
            reg = registry.Registry(Path(tmp) / "repo")
            doi_rec, doi_new = reg.register({"doi": "10.1000/alias-merge",
                                             "title": "Alias merge paper"})
            self.assertTrue(doi_new)
            self.assertEqual(doi_rec["evidence_id"], "doi:10.1000/alias-merge")

            pmid_rec, pmid_new = reg.register({
                "pmid": "222", "doi": "10.1000/alias-merge",
                "title": "Alias merge paper", "journal": "J Merge",
            })
            self.assertFalse(pmid_new)
            self.assertEqual(pmid_rec["evidence_id"], "pmid:222")
            self.assertEqual(len(reg.records), 1)
            self.assertNotIn("doi:10.1000/alias-merge", reg.records)
            self.assertIs(reg.lookup(doi="10.1000/alias-merge"), pmid_rec)

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
                                     dry_run=False, strict=False, no_verify=False))

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


class RegistryPromoteVerificationTest(unittest.TestCase):
    """Priority 6, POOL_ARCHITECTURE_OPTIMIZATION_PLAN.md: promotion must verify before
    copying an extraction into the canonical store."""

    def _repo_with_snapshot(self, tmp: Path) -> tuple[Path, Path, str, str, int, int]:
        repo = tmp / "repo"
        research.cmd_init(_ns(path=str(repo), from_wiki=None))
        run_dir = repo / "runs" / "r1"
        (run_dir / "config.json").parent.mkdir(parents=True, exist_ok=True)
        write_json(run_dir / "config.json", {"created_at": "2026-01-01T00:00:00Z"})
        text = "Methods. The trial enrolled 42 adults. Results were reported."
        s = store.Store(run_dir, repo_root=repo)
        snap = s.write_snapshot(url="https://example.org/fulltext", text=text, title="t",
                                access="full_text", origin="pubmed", paper=None,
                                event_type="fetch", fresh=True)
        start = text.index("trial enrolled")
        end = text.index(". Results")
        return repo, run_dir, snap["source_id"], text, start, end

    def test_promotion_succeeds_with_verified_spans(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo, run_dir, sid, text, start, end = self._repo_with_snapshot(tmp)
            (run_dir / "workspace" / "extractions").mkdir(parents=True)
            write_json(run_dir / "workspace" / "extractions" / "pmid-1.json", {
                "evidence_id": "pmid:1",
                "spans": [{"claim": "x", "source_id": sid, "start": start, "end": end,
                          "text": text[start:end]}],
            })
            write_jsonl(run_dir / "corpus.jsonl", [_corpus_rec(
                "pmid:1", extraction_path="workspace/extractions/pmid-1.json")])

            result = run_py(["scripts/registry.py", "promote", "--repo", str(repo),
                             "--run-dir", str(run_dir)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["promoted"], 1)
            self.assertEqual(payload["skipped"], 0)

    def test_promotion_skips_extraction_with_bad_source_id(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo, run_dir, sid, text, start, end = self._repo_with_snapshot(tmp)
            (run_dir / "workspace" / "extractions").mkdir(parents=True)
            write_json(run_dir / "workspace" / "extractions" / "pmid-2.json", {
                "evidence_id": "pmid:2",
                "spans": [{"claim": "x", "source_id": "src-" + "0" * 64, "start": 0, "end": 5}],
            })
            write_jsonl(run_dir / "corpus.jsonl", [_corpus_rec(
                "pmid:2", extraction_path="workspace/extractions/pmid-2.json")])

            result = run_py(["scripts/registry.py", "promote", "--repo", str(repo),
                             "--run-dir", str(run_dir)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["promoted"], 0)
            self.assertEqual(payload["skipped"], 1)
            self.assertIn("UNKNOWN_SOURCE", payload["skipped_records"][0]["reason"])
            self.assertFalse(
                (repo / "data" / "papers" / "extractions" / "pmid-2.json").exists())

            strict = run_py(["scripts/registry.py", "promote", "--repo", str(repo),
                             "--run-dir", str(run_dir), "--strict"])
            self.assertEqual(strict.returncode, 1)

    def test_promotion_skips_extraction_with_mismatched_evidence_id(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo, run_dir, sid, text, start, end = self._repo_with_snapshot(tmp)
            (run_dir / "workspace" / "extractions").mkdir(parents=True)
            write_json(run_dir / "workspace" / "extractions" / "pmid-3.json", {
                "evidence_id": "pmid:999",
                "spans": [{"claim": "x", "source_id": sid, "start": start, "end": end,
                          "text": text[start:end]}],
            })
            write_jsonl(run_dir / "corpus.jsonl", [_corpus_rec(
                "pmid:3", extraction_path="workspace/extractions/pmid-3.json")])

            result = run_py(["scripts/registry.py", "promote", "--repo", str(repo),
                             "--run-dir", str(run_dir)])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["promoted"], 0)
            self.assertEqual(payload["skipped"], 1)
            self.assertIn("disagrees with corpus evidence_id",
                         payload["skipped_records"][0]["reason"])

    def test_no_verify_preserves_lightweight_copy_only_behavior(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo, run_dir, sid, text, start, end = self._repo_with_snapshot(tmp)
            (run_dir / "workspace" / "extractions").mkdir(parents=True)
            write_json(run_dir / "workspace" / "extractions" / "pmid-4.json", {
                "evidence_id": "pmid:4",
                "spans": [{"claim": "x", "source_id": "src-" + "0" * 64, "start": 0, "end": 5}],
            })
            write_jsonl(run_dir / "corpus.jsonl", [_corpus_rec(
                "pmid:4", extraction_path="workspace/extractions/pmid-4.json")])

            result = run_py(["scripts/registry.py", "promote", "--repo", str(repo),
                             "--run-dir", str(run_dir), "--no-verify"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["promoted"], 1)
            self.assertEqual(payload["skipped"], 0)
            self.assertTrue(
                (repo / "data" / "papers" / "extractions" / "pmid-4.json").exists())


class RegistryBibExportTest(unittest.TestCase):
    """Priority 7, POOL_ARCHITECTURE_OPTIMIZATION_PLAN.md: repo-mode BibTeX export."""

    def _repo_with_three_papers(self, tmp: Path) -> Path:
        repo = tmp / "repo"
        research.cmd_init(_ns(path=str(repo), from_wiki=None))
        reg = registry.Registry(repo)
        reg.register({"pmid": "1", "title": "Extracted and appraised paper",
                      "journal": "J1", "publication_date": "2026"})
        reg.set_extraction("pmid:1", "data/papers/extractions/pmid-1.json")
        reg.set_appraisal("pmid:1", "proj-a", "data/papers/appraisals/proj-a/pmid-1.json")
        reg.register({"pmid": "2", "title": "Extracted only paper",
                      "journal": "J2", "publication_date": "2026"})
        reg.set_extraction("pmid:2", "data/papers/extractions/pmid-2.json")
        reg.register({"pmid": "3", "title": "Registered only paper",
                      "journal": "J3", "publication_date": "2026"})
        reg.save()
        return repo

    def test_select_all_exports_every_registry_record(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = self._repo_with_three_papers(tmp)
            out = tmp / "refs.bib"
            result = run_py(["scripts/registry.py", "bib", "--repo", str(repo),
                             "--out", str(out), "--select", "all"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["entries_written"], 3)
            text = out.read_text(encoding="utf-8")
            self.assertIn("@article{pmid1,", text)
            self.assertIn("@article{pmid2,", text)
            self.assertIn("@article{pmid3,", text)

    def test_select_extracted_excludes_registered_only(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = self._repo_with_three_papers(tmp)
            out = tmp / "refs.bib"
            result = run_py(["scripts/registry.py", "bib", "--repo", str(repo),
                             "--out", str(out), "--select", "extracted"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["entries_written"], 2)
            text = out.read_text(encoding="utf-8")
            self.assertIn("@article{pmid1,", text)
            self.assertIn("@article{pmid2,", text)
            self.assertNotIn("@article{pmid3,", text)

    def test_select_appraised_is_project_scoped(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = self._repo_with_three_papers(tmp)
            out = tmp / "refs.bib"
            result = run_py(["scripts/registry.py", "bib", "--repo", str(repo),
                             "--out", str(out), "--select", "appraised",
                             "--project", "proj-a"])
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["entries_written"], 1)
            text = out.read_text(encoding="utf-8")
            self.assertIn("@article{pmid1,", text)
            self.assertNotIn("@article{pmid2,", text)

            other_project = run_py(["scripts/registry.py", "bib", "--repo", str(repo),
                                    "--out", str(out), "--select", "appraised",
                                    "--project", "proj-b"])
            self.assertEqual(other_project.returncode, 0)
            self.assertEqual(json.loads(other_project.stdout)["entries_written"], 0)

            missing_project = run_py(["scripts/registry.py", "bib", "--repo", str(repo),
                                      "--out", str(out), "--select", "appraised"])
            self.assertEqual(missing_project.returncode, 2)

    def test_citation_keys_match_render_bib_key(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = self._repo_with_three_papers(tmp)
            out = tmp / "refs.bib"
            run_py(["scripts/registry.py", "bib", "--repo", str(repo), "--out", str(out)])
            text = out.read_text(encoding="utf-8")
            for eid in ("pmid:1", "pmid:2", "pmid:3"):
                self.assertIn(render.bib_key(eid), text)


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
                (store.global_sources_root(repo) / "snapshots" / f"{sid}.json").exists())
            self.assertFalse(
                (store.global_sources_root(repo) / "sources" / f"{sid}.json").exists())

            s2 = store.Store(run_dir, repo_root=repo)
            self.assertEqual(s2.read_snapshot(sid)["text"], "global text")
            self.assertIn(sid, s2.list_snapshots())

    def test_global_store_reads_legacy_dirname_but_never_writes_it(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            run_dir = repo / "runs" / "r1"
            run_dir.mkdir(parents=True)
            (run_dir / "config.json").write_text(
                json.dumps({"created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")

            legacy_result = store.write_snapshot_result(
                store.global_sources_root(repo), dirname="sources",
                url="https://x/legacy", text="legacy text", title=None,
                access="web", origin="web", paper=None,
                event_type="fetch", fresh=True)
            legacy_sid = legacy_result["source_id"]

            s = store.Store(run_dir, repo_root=repo)
            self.assertEqual(s.read_snapshot(legacy_sid)["text"], "legacy text")
            self.assertIn(legacy_sid, s.list_snapshots())

            new_snap = s.write_snapshot(url="https://x/new", text="new text", title=None,
                                        access="web", origin="web", paper=None,
                                        event_type="fetch", fresh=True)
            self.assertTrue(
                (store.global_sources_root(repo) / "snapshots"
                 / f"{new_snap['source_id']}.json").exists())
            self.assertFalse(
                (store.global_sources_root(repo) / "sources"
                 / f"{new_snap['source_id']}.json").exists())

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
    def test_pool_repo_and_wiki_flags_are_mutually_exclusive(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            wiki = tmp / "wiki"
            run_dir = repo / "runs" / "newrun"
            run_dir.mkdir(parents=True)
            result = run_py([
                "scripts/pool.py", "seed", "--run-dir", str(run_dir),
                "--repo", str(repo), "--wiki", str(wiki), "--query", "x",
            ])
            self.assertEqual(result.returncode, 2)
            self.assertIn("not allowed with argument", result.stderr)

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


class RepoModeAssembleVerifyTest(unittest.TestCase):
    def test_assembler_and_verifier_resolve_globally_promoted_extraction_with_repo_flag(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            init = run_py(["scripts/research.py", "init", str(repo)])
            self.assertEqual(init.returncode, 0, init.stderr)

            # 1. Producing run: fetch a source straight into the global store.
            run1 = repo / "runs" / "r1"
            run1.mkdir(parents=True)
            write_json(run1 / "config.json", {"created_at": "2026-01-01T00:00:00Z"})
            text = "Methods. The trial enrolled 42 adults. Results were reported."
            s1 = store.Store(run1, repo_root=repo)
            snap = s1.write_snapshot(
                url="https://example.org/fulltext", text=text, title="Full text",
                access="full_text", origin="pubmed",
                paper={"pmid": "77", "doi": None, "pmcid": None},
                event_type="fetch", fresh=True, actor="test")
            source_id = snap["source_id"]
            self.assertFalse((run1 / "sources" / f"{source_id}.json").exists())

            # 2. Extraction with a span backed only by the global snapshot.
            start = text.index("trial enrolled")
            end = text.index(". Results")
            (run1 / "workspace" / "extractions").mkdir(parents=True)
            write_json(run1 / "workspace" / "extractions" / "pmid-77.json", {
                "evidence_id": "pmid:77",
                "spans": [{"claim": "The trial enrolled 42 adults",
                          "source_id": source_id, "start": start, "end": end,
                          "text": text[start:end]}],
            })
            rec = minimal_corpus_record("pmid:77")
            rec["source_ids"] = [source_id]
            rec["extraction_path"] = "workspace/extractions/pmid-77.json"
            write_jsonl(run1 / "corpus.jsonl", [rec])

            # 3. Promote to the canonical store.
            promote = run_py(["scripts/registry.py", "promote", "--repo", str(repo),
                              "--run-dir", str(run1)])
            self.assertEqual(promote.returncode, 0, promote.stderr + promote.stdout)
            self.assertTrue(
                (repo / "data" / "papers" / "extractions" / "pmid-77.json").exists())

            # 4. Reuse into a second, unrelated run: no local snapshot, no local
            #    producing-run dependency.
            run2 = repo / "runs" / "r2"
            run2.mkdir(parents=True)
            write_json(run2 / "config.json", {"created_at": "2026-01-02T00:00:00Z"})
            reuse = run_py(["scripts/pool.py", "reuse", "--run-dir", str(run2),
                            "--repo", str(repo), "--pmid", "77"])
            self.assertEqual(reuse.returncode, 0, reuse.stderr + reuse.stdout)
            self.assertTrue(
                (run2 / "workspace" / "extractions" / "pmid-77.json").exists())
            self.assertFalse((run2 / "sources").exists())
            write_jsonl(run2 / "corpus.jsonl", [rec])

            # 5. Assembler and verifier in repo mode must resolve the span through the
            #    global store, without ever touching run1. R15 freshness is per-run by
            #    design, so this reused-from-elsewhere record is correctly NO_FRESH_FETCH
            #    (not accepted) rather than UNKNOWN_SOURCE (span/snapshot unresolvable).
            assembled = run_py(["scripts/assemble.py", "run", "--run-dir", str(run2),
                                "--repo", str(repo)])
            result = json.loads((run2 / "outputs" / "result.json").read_text(encoding="utf-8"))
            reasons = result["diagnostics"]["counts_by_reason"]
            self.assertNotIn("UNKNOWN_SOURCE", reasons)
            self.assertEqual(result["counts"]["spans_checked"], 1)
            self.assertEqual(len(result["sources"]), 1)
            self.assertEqual(result["sources"][0]["source_id"], source_id)

            report = run2 / "outputs" / "report.md"
            report.write_text(
                "# Repo-mode report\n\n"
                "The trial enrolled 42 adults.[^pubmed-77]\n\n"
                "## References\n\n"
                "[^pubmed-77]: A. Journal of Validation. PMID 77. PubMed "
                "https://pubmed.ncbi.nlm.nih.gov/77/\n",
                encoding="utf-8")
            verified = run_py(["scripts/verify.py", "run", "--run-dir", str(run2),
                               "--report", str(report), "--repo", str(repo), "--json"])
            self.assertIn(verified.returncode, (0, 1), verified.stderr + verified.stdout)
            payload = json.loads(verified.stdout)
            checks = {c["check_id"]: c["status"] for c in payload["checks"]}
            self.assertEqual(checks["C-SNAPSHOT"], "pass")
            self.assertEqual(checks["C-SPAN"], "pass")

            # Deleting the producing run must not affect resolution: the global store,
            # not run1, is what verify/assemble depend on.
            import shutil
            shutil.rmtree(run1)
            reverified = run_py(["scripts/verify.py", "run", "--run-dir", str(run2),
                                 "--report", str(report), "--repo", str(repo), "--json"])
            self.assertIn(reverified.returncode, (0, 1), reverified.stderr + reverified.stdout)
            repayload = json.loads(reverified.stdout)
            rechecks = {c["check_id"]: c["status"] for c in repayload["checks"]}
            self.assertEqual(rechecks["C-SNAPSHOT"], "pass")
            self.assertEqual(rechecks["C-SPAN"], "pass")


def _ns(**kwargs):
    class NS:
        pass
    ns = NS()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


if __name__ == "__main__":
    unittest.main()
