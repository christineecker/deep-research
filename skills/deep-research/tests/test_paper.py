from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script

paper = load_script("paper.py")
paper_summary = load_script("paper_summary.py")
registry_mod = load_script("registry.py")
verify_mod = load_script("verify.py")
annotations_mod = load_script("annotations.py")


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    for rel in ("data/sources/assets", "data/sources/snapshots", "data/papers/extractions",
               "data/papers/appraisals", "projects", "runs", "templates"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "data" / "papers" / "registry.jsonl").touch()
    (repo / "data" / "papers" / "pool.jsonl").touch()
    (repo / "data" / "sources" / "events.jsonl").touch()
    return repo


def _register(repo: Path, evidence_id: str = "pmid:12345678", **overrides) -> dict:
    reg = registry_mod.Registry(repo)
    raw = {
        "pmid": evidence_id.split(":", 1)[1] if evidence_id.startswith("pmid:") else None,
        "doi": evidence_id.split(":", 1)[1] if evidence_id.startswith("doi:") else None,
        "title": "A test RCT of CBT for adolescent depression",
        "journal": "J Test", "publication_date": "2024-01-01", "authors": ["Smith J"],
    }
    raw.update(overrides)
    rec, _ = reg.register(raw)
    reg.save()
    reg.generate_pool()
    return rec


def _mark_fulltext(run_dir: Path, evidence_id: str) -> None:
    import corpus as _corpus
    c = _corpus.Corpus(run_dir).load()
    rec = c.records[evidence_id]
    rec["fulltext"] = {"status": "fulltext", "source_tier": 1, "access_route": "test",
                       "local_path": None, "sha256": None, "truncation_detected": False}
    c.save()


def _write_extraction(run_dir: Path, evidence_id: str) -> None:
    slug = paper_summary.evidence_slug(evidence_id)
    path = run_dir / "workspace" / "extractions" / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1, "pmid": evidence_id.split(":", 1)[1], "evidence_id": evidence_id,
        "design": "parallel-group randomized controlled trial", "n_total": 240,
        "n_arms": [120, 120], "population": None, "intervention": None, "comparator": None,
        "outcomes": [], "diagnostic_accuracy": [], "prediction_model": [],
        "qualitative_evidence": None, "cross_sectional_evidence": None, "funding": None,
        "coi": None, "limitations": None, "evidence_basis": "fulltext",
        "extractor_notes": None, "spans": [], "quotes": [],
    }), encoding="utf-8")


def _write_appraisal(run_dir: Path, evidence_id: str) -> None:
    slug = paper_summary.evidence_slug(evidence_id)
    path = run_dir / "workspace" / "appraisals" / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1, "pmid": evidence_id.split(":", 1)[1], "evidence_id": evidence_id,
        "tool": "RoB2", "appraisal_target": None,
        "domains": [{"name": "randomization process", "judgement": "low", "spans": []}],
        "overall_judgement": "some_concerns", "evidence_basis": "fulltext", "grade": None,
        "appraiser_notes": None,
    }), encoding="utf-8")


def _write_summary(run_dir: Path, evidence_id: str, *, forbidden_phrase: bool = False) -> Path:
    slug = paper_summary.evidence_slug(evidence_id)
    path = run_dir / "workspace" / "summaries" / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    bottom_line = "Pooled meta-analysis across studies." if forbidden_phrase else \
        "Parallel-group RCT, N=240."
    sections = [{"name": n, "claims": []} for n in paper_summary.SECTION_NAMES]
    by_name = {s["name"]: s for s in sections}
    by_name["bottom_line"]["claims"] = [
        {"text": bottom_line, "evidence_id": evidence_id, "span_refs": []}]
    by_name["what_not_to_conclude"]["claims"] = [
        {"text": "Do not generalize beyond this one trial.", "evidence_id": evidence_id,
         "span_refs": []}]
    doc = {
        "schema_version": 1, "summary_id": f"single-paper:{evidence_id}:journal-club",
        "evidence_id": evidence_id, "project": None, "purpose": "journal-club",
        "audience": "researcher", "source_basis": "fulltext",
        "extraction_path": f"workspace/extractions/{slug}.json",
        "appraisal_path": f"workspace/appraisals/{slug}.json",
        "appraisal_skipped_reason": None, "sections": sections, "limitations": [],
        "do_not_conclude": ["Do not generalize beyond this one trial."],
        "created_at": "2026-09-10T00:00:00Z",
    }
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class ArgParsingTest(unittest.TestCase):
    def test_summarize_requires_repo(self):
        with self.assertRaises(SystemExit):
            paper.build_parser().parse_args(["summarize", "--pmid", "1"])

    def test_summarize_set_dispatches(self):
        args = paper.build_parser().parse_args(
            ["summarize-set", "--repo", "/tmp/x", "--pmid", "1", "--pmid", "2"])
        self.assertEqual(args.pmid, ["1", "2"])
        self.assertIs(args.func, paper.cmd_summarize_set)

    def test_summarize_dispatches(self):
        args = paper.build_parser().parse_args(
            ["summarize", "--repo", "/tmp/x", "--pmid", "1"])
        self.assertIs(args.func, paper.cmd_summarize)
        self.assertEqual(args.purpose, "background")
        self.assertEqual(args.audience, "researcher")


class PipelineStagesTest(unittest.TestCase):
    def test_pending_states_then_completed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root)
            _register(repo)
            import argparse
            args = paper.build_parser().parse_args([
                "summarize", "--repo", str(repo), "--evidence-id", "pmid:12345678",
                "--offline", "--purpose", "journal-club"])

            payload = paper.summarize_one(repo, args)
            self.assertEqual(payload["status"], "pending_retrieval")
            run_dir = Path(payload["run_dir"])

            _mark_fulltext(run_dir, "pmid:12345678")
            _write_extraction(run_dir, "pmid:12345678")
            payload = paper.summarize_one(repo, args)
            self.assertEqual(payload["status"], "pending_appraisal")

            _write_appraisal(run_dir, "pmid:12345678")
            payload = paper.summarize_one(repo, args)
            self.assertEqual(payload["status"], "pending_summary")

            _write_summary(run_dir, "pmid:12345678")
            payload = paper.summarize_one(repo, args)
            self.assertEqual(payload["status"], "completed")
            self.assertTrue(Path(payload["output_path"]).exists())

            # extraction promoted to the canonical store
            self.assertTrue((repo / "data" / "papers" / "extractions"
                            / "pmid-12345678.json").exists())

    def test_no_appraise_flag_skips_appraisal(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root)
            _register(repo)
            args = paper.build_parser().parse_args([
                "summarize", "--repo", str(repo), "--evidence-id", "pmid:12345678",
                "--offline", "--no-appraise"])
            payload = paper.summarize_one(repo, args)
            run_dir = Path(payload["run_dir"])
            _mark_fulltext(run_dir, "pmid:12345678")
            _write_extraction(run_dir, "pmid:12345678")
            payload = paper.summarize_one(repo, args)
            self.assertEqual(payload["status"], "pending_summary")
            self.assertNotIn("appraisal_path=", payload["detail"])

    def test_extract_only_stops_after_extraction_and_promotes(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root)
            _register(repo)
            args = paper.build_parser().parse_args([
                "summarize", "--repo", str(repo), "--evidence-id", "pmid:12345678",
                "--offline", "--extract-only"])

            payload = paper.summarize_one(repo, args)
            self.assertEqual(payload["status"], "pending_retrieval")
            run_dir = Path(payload["run_dir"])

            _mark_fulltext(run_dir, "pmid:12345678")
            _write_extraction(run_dir, "pmid:12345678")
            payload = paper.summarize_one(repo, args)

            # completed right after extraction -- no appraisal or summary dispatch, ever
            self.assertEqual(payload["status"], "completed")
            self.assertIsNone(payload["summary_path"])
            self.assertTrue(Path(payload["output_path"]).exists())

            # extraction promoted to the canonical store without a summary existing
            self.assertTrue((repo / "data" / "papers" / "extractions"
                            / "pmid-12345678.json").exists())
            reg = registry_mod.Registry(repo)
            rec = reg.lookup(evidence_id="pmid:12345678")
            self.assertEqual(rec["extraction_status"], "extracted")


class SummarizeFromPdfTest(unittest.TestCase):
    """Phase 5: `paper.py summarize --pdf` must route through the same shared refmgr
    attachment pool as `registry.py add-pdf` -- not its own independent flat-store
    implementation (that was the actual gap: two disconnected PDF-intake paths)."""

    def test_pdf_ingestion_registers_a_refmgr_attachment_not_a_flat_store_copy(self):
        import refmgr.service as refmgr_service

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\nsummarize me\n")

            args = paper.build_parser().parse_args([
                "summarize", "--repo", str(repo), "--pdf", str(pdf),
                "--pmid", "77777777", "--offline"])
            payload = paper.summarize_one(repo, args)
            self.assertIn(payload["status"], ("pending_retrieval", "pending_appraisal",
                                              "pending_summary", "completed"))

            rec = registry_mod.Registry(repo).lookup(pmid="77777777")
            self.assertIsNotNone(rec)
            self.assertTrue(rec.get("refmgr_paper_id"))
            # _init_repo pre-creates the old flat store dir; it must stay empty.
            self.assertEqual(list((repo / "data" / "sources" / "assets").iterdir()), [])

            service = refmgr_service.ReferenceManagerService(
                registry_mod.repo_paths(repo)["refmgr"])
            try:
                attachments = service.attachments.list_for_paper(rec["refmgr_paper_id"])
                self.assertEqual(len(attachments), 1)
                self.assertEqual(attachments[0]["role"], "fulltext")
            finally:
                service.close()


class PersonalNotesRenderingTest(unittest.TestCase):
    """Phase 5: personal annotations render in a distinct, clearly-labeled section of the
    single-paper summary, never mixed into the extraction-derived (verified) sections."""

    def test_annotation_renders_in_its_own_section_and_none_recorded_otherwise(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root)
            _register(repo)
            ann = annotations_mod.Annotations(repo)
            rec = ann.get("pmid:12345678")
            rec["tags"] = ["to-read", "diagnostics"]
            rec["rating"] = 4
            rec["note"] = "Worth revisiting for the sample-size discussion."
            ann.records["pmid:12345678"] = rec
            ann.save()

            args = paper.build_parser().parse_args([
                "summarize", "--repo", str(repo), "--evidence-id", "pmid:12345678",
                "--offline"])
            payload = paper.summarize_one(repo, args)
            run_dir = Path(payload["run_dir"])
            _mark_fulltext(run_dir, "pmid:12345678")
            _write_extraction(run_dir, "pmid:12345678")
            _write_appraisal(run_dir, "pmid:12345678")
            _write_summary(run_dir, "pmid:12345678")
            payload = paper.summarize_one(repo, args)
            self.assertEqual(payload["status"], "completed")

            rendered = Path(payload["output_path"]).read_text(encoding="utf-8")
            self.assertIn("## 14. Personal notes (unverified — not extracted evidence)",
                         rendered)
            notes_section = rendered.split("## 14. Personal notes")[1]
            self.assertIn("to-read, diagnostics", notes_section)
            self.assertIn("4/5", notes_section)
            self.assertIn("Worth revisiting for the sample-size discussion.", notes_section)
            self.assertNotIn("{{PERSONAL_NOTES}}", rendered)

    def test_unannotated_paper_shows_none_recorded(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root)
            _register(repo)
            args = paper.build_parser().parse_args([
                "summarize", "--repo", str(repo), "--evidence-id", "pmid:12345678",
                "--offline"])
            payload = paper.summarize_one(repo, args)
            run_dir = Path(payload["run_dir"])
            _mark_fulltext(run_dir, "pmid:12345678")
            _write_extraction(run_dir, "pmid:12345678")
            _write_appraisal(run_dir, "pmid:12345678")
            _write_summary(run_dir, "pmid:12345678")
            payload = paper.summarize_one(repo, args)

            rendered = Path(payload["output_path"]).read_text(encoding="utf-8")
            notes_section = rendered.split("## 14. Personal notes")[1]
            self.assertIn("_None recorded._", notes_section)


class SchemaHelpersTest(unittest.TestCase):
    def test_validate_summary_shape_flags_missing_sections(self):
        errors = paper_summary.validate_summary_shape({
            "schema_version": 1, "summary_id": "x", "evidence_id": "pmid:1",
            "purpose": "background", "audience": "researcher", "source_basis": "fulltext",
            "extraction_path": "x", "sections": [], "limitations": [], "do_not_conclude": [],
            "created_at": "2026-01-01T00:00:00Z",
        })
        self.assertTrue(any("sections missing" in e for e in errors))

    def test_resolve_span_ref_out_of_range(self):
        reason = paper_summary.resolve_span_ref(
            "extraction:spans:5", {"spans": []}, None)
        self.assertIsNotNone(reason)

    def test_resolve_span_ref_ok(self):
        reason = paper_summary.resolve_span_ref(
            "extraction:spans:0", {"spans": [{"claim": "x"}]}, None)
        self.assertIsNone(reason)

    def test_forbidden_phrase_detected(self):
        self.assertEqual(paper_summary.find_forbidden_phrase("a pooled estimate"), "pooled")
        self.assertIsNone(paper_summary.find_forbidden_phrase("a single trial result"))


class VerifySinglePaperSummaryTest(unittest.TestCase):
    def test_pass_and_fail(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root)
            _register(repo)
            args = paper.build_parser().parse_args([
                "summarize", "--repo", str(repo), "--evidence-id", "pmid:12345678",
                "--offline"])
            payload = paper.summarize_one(repo, args)
            run_dir = Path(payload["run_dir"])
            _mark_fulltext(run_dir, "pmid:12345678")
            _write_extraction(run_dir, "pmid:12345678")
            _write_appraisal(run_dir, "pmid:12345678")
            good_path = _write_summary(run_dir, "pmid:12345678")

            good = json.loads(good_path.read_text(encoding="utf-8"))
            checks = verify_mod.check_single_paper_summary(good, run_dir, repo)
            self.assertTrue(all(c["status"] != "fail" for c in checks))

            bad_path = _write_summary(run_dir, "pmid:12345678", forbidden_phrase=True)
            bad = json.loads(bad_path.read_text(encoding="utf-8"))
            checks = verify_mod.check_single_paper_summary(bad, run_dir, repo)
            self.assertTrue(any(c["check_id"] == "C-SPS-NO-CROSS-STUDY" and c["status"] == "fail"
                               for c in checks))


class SummarizeSetTest(unittest.TestCase):
    def test_explicit_set_completes_with_preseeded_records(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = _init_repo(root)
            _register(repo, "pmid:11111111", pmid="11111111",
                     title="Paper one", doi=None)
            _register(repo, "pmid:22222222", pmid="22222222",
                     title="Paper two", doi=None)

            args = paper.build_parser().parse_args([
                "summarize-set", "--repo", str(repo), "--pmid", "11111111",
                "--pmid", "22222222", "--offline"])
            # First pass: both papers land on pending_retrieval — nothing to preseed via CLI
            # alone, so seed each paper's run directly the way `summarize` would name it.
            for pmid in ("11111111", "22222222"):
                eid = f"pmid:{pmid}"
                slug = paper.run_slug_for(eid, "background")
                run_dir = paper.ensure_run_dir(repo, slug, config_extra={
                    "evidence_id": eid, "purpose": "background", "audience": "researcher",
                    "project": None, "appraise_requested": True})
                paper.write_corpus_record(run_dir, registry_mod.Registry(repo).lookup(evidence_id=eid))
                _mark_fulltext(run_dir, eid)
                _write_extraction(run_dir, eid)
                _write_appraisal(run_dir, eid)
                _write_summary(run_dir, eid)

            import io
            import contextlib
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = paper.cmd_summarize_set(args)
            payload = json.loads(buf.getvalue())
            self.assertEqual(rc, 0)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["included"], 2)

            record = json.loads(Path(payload["record_path"]).read_text(encoding="utf-8"))
            errors = []
            required = ("schema_version", "set_id", "selection_mode", "selection_basis",
                       "included_evidence_ids", "failed_identifiers", "summary_paths",
                       "overview", "created_at")
            for f in required:
                if f not in record:
                    errors.append(f)
            self.assertEqual(errors, [])
            self.assertEqual(set(record["included_evidence_ids"]),
                            {"pmid:11111111", "pmid:22222222"})


if __name__ == "__main__":
    unittest.main()
