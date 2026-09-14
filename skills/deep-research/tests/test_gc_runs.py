from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script

paper = load_script("paper.py")
paper_summary = load_script("paper_summary.py")
registry_mod = load_script("registry.py")
research = load_script("research.py")


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    for rel in ("data/sources/assets", "data/sources/snapshots", "data/papers/extractions",
               "data/papers/appraisals", "projects", "runs", "templates"):
        (repo / rel).mkdir(parents=True, exist_ok=True)
    (repo / "data" / "papers" / "registry.jsonl").touch()
    (repo / "data" / "papers" / "pool.jsonl").touch()
    (repo / "data" / "sources" / "events.jsonl").touch()
    return repo


def _register(repo: Path, evidence_id: str, **overrides) -> dict:
    reg = registry_mod.Registry(repo)
    raw = {
        "pmid": evidence_id.split(":", 1)[1] if evidence_id.startswith("pmid:") else None,
        "doi": evidence_id.split(":", 1)[1] if evidence_id.startswith("doi:") else None,
        "title": f"Test paper {evidence_id}", "journal": "J Test",
        "publication_date": "2024-01-01", "authors": ["Smith J"],
    }
    raw.update(overrides)
    rec, _ = reg.register(raw)
    reg.save()
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


def _write_summary(run_dir: Path, evidence_id: str) -> None:
    slug = paper_summary.evidence_slug(evidence_id)
    path = run_dir / "workspace" / "summaries" / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = [{"name": n, "claims": []} for n in paper_summary.SECTION_NAMES]
    by_name = {s["name"]: s for s in sections}
    by_name["bottom_line"]["claims"] = [
        {"text": "N=240 RCT.", "evidence_id": evidence_id, "span_refs": []}]
    by_name["what_not_to_conclude"]["claims"] = [
        {"text": "Do not generalize beyond this one trial.", "evidence_id": evidence_id,
         "span_refs": []}]
    doc = {
        "schema_version": 1, "summary_id": f"single-paper:{evidence_id}:background",
        "evidence_id": evidence_id, "project": None, "purpose": "background",
        "audience": "researcher", "source_basis": "fulltext",
        "extraction_path": f"workspace/extractions/{slug}.json",
        "appraisal_path": f"workspace/appraisals/{slug}.json",
        "appraisal_skipped_reason": None, "sections": sections, "limitations": [],
        "do_not_conclude": ["Do not generalize beyond this one trial."],
        "created_at": "2026-09-10T00:00:00Z",
    }
    path.write_text(json.dumps(doc), encoding="utf-8")


def _complete_pending_tasks(run_dir: Path) -> None:
    """Stand in for the orchestrator marking a dispatched subagent's task `completed`
    (`corpus.py task complete`) once its receipt comes back -- a real run never leaves a
    `pending`/`active` task behind once the work it names is actually done."""
    import taskboard as _taskboard
    board = _taskboard.TaskBoard(run_dir)
    for tid, rec in list(board.state().items()):
        if rec.get("status") in ("pending", "active"):
            board.write({**rec, "status": "completed"})


def _run_to_completion(repo: Path, evidence_id: str, *, project: str | None = None,
                       extract_only: bool = False) -> dict:
    """Drive `paper.py summarize[--extract-only]` through its resumable loop to
    `completed`, writing each subagent output by hand -- same choreography as
    test_paper.py's PipelineStagesTest, plus closing out each taskboard entry the way
    the real orchestrator does once a subagent's output lands."""
    argv = ["summarize", "--repo", str(repo), "--evidence-id", evidence_id, "--offline"]
    if project:
        argv += ["--project", project]
    if extract_only:
        argv += ["--extract-only"]
    args = paper.build_parser().parse_args(argv)

    payload = paper.summarize_one(repo, args)
    assert payload["status"] == "pending_retrieval", payload
    run_dir = Path(payload["run_dir"])
    _mark_fulltext(run_dir, evidence_id)
    _write_extraction(run_dir, evidence_id)
    _complete_pending_tasks(run_dir)
    payload = paper.summarize_one(repo, args)

    if extract_only:
        assert payload["status"] == "completed", payload
        _complete_pending_tasks(run_dir)
        return payload

    assert payload["status"] == "pending_appraisal", payload
    _write_appraisal(run_dir, evidence_id)
    _complete_pending_tasks(run_dir)
    payload = paper.summarize_one(repo, args)
    assert payload["status"] == "pending_summary", payload
    _write_summary(run_dir, evidence_id)
    _complete_pending_tasks(run_dir)
    payload = paper.summarize_one(repo, args)
    assert payload["status"] == "completed", payload
    return payload


def _gc(repo: Path, **flags) -> dict:
    argv = ["gc-runs", "--repo", str(repo)]
    for key, value in flags.items():
        flag = "--" + key.replace("_", "-")
        if value is True:
            argv.append(flag)
        elif value is not None:
            argv += [flag, str(value)]
    args = research.build_parser().parse_args(argv)
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        research.cmd_gc_runs(args)
    return json.loads(buf.getvalue())


class SafeCandidateTest(unittest.TestCase):
    def test_completed_run_with_project_writeup_is_safe_and_gets_deleted_on_apply(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _register(repo, "pmid:1")
            (repo / "projects" / "proj1").mkdir(parents=True, exist_ok=True)
            payload = _run_to_completion(repo, "pmid:1", project="proj1")
            run_dir = Path(payload["run_dir"])
            self.assertTrue(run_dir.exists())

            report = _gc(repo)
            self.assertEqual(report["applied"], False)
            self.assertEqual(len(report["candidates"]), 1)
            cand = report["candidates"][0]
            self.assertEqual(cand["tier"], "safe")
            self.assertEqual(cand["blocking_reasons"], [])
            self.assertEqual(cand["unsaved_reasons"], [])
            self.assertTrue(run_dir.exists())  # dry-run never deletes

            applied = _gc(repo, apply=True)
            self.assertEqual(applied["deleted"], [cand["run_dir"]])
            self.assertGreater(applied["freed_bytes"], 0)
            self.assertFalse(run_dir.exists())

            # the paper data itself is untouched
            reg = registry_mod.Registry(repo)
            rec = reg.lookup(evidence_id="pmid:1")
            self.assertEqual(rec["extraction_status"], "extracted")

    def test_extract_only_run_is_safe_with_no_summary_ever_expected(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _register(repo, "pmid:1")
            payload = _run_to_completion(repo, "pmid:1", extract_only=True)
            report = _gc(repo)
            cand = report["candidates"][0]
            self.assertEqual(cand["tier"], "safe")


class BlockingCandidateTest(unittest.TestCase):
    def test_run_with_pending_task_is_never_deleted_even_with_apply(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _register(repo, "pmid:1")
            args = paper.build_parser().parse_args([
                "summarize", "--repo", str(repo), "--evidence-id", "pmid:1", "--offline"])
            payload = paper.summarize_one(repo, args)
            self.assertEqual(payload["status"], "pending_retrieval")
            run_dir = Path(payload["run_dir"])

            report = _gc(repo, apply=True, include_unsaved=True)
            cand = report["candidates"][0]
            self.assertEqual(cand["tier"], "blocking")
            self.assertIn("unfinished task", cand["blocking_reasons"][0])
            self.assertTrue(run_dir.exists())
            self.assertEqual(report["deleted"], [])

    def test_unpromoted_extraction_blocks_even_without_a_pending_task(self):
        """Simulates a --force re-run that regenerated an extraction but was never
        promoted (registry.py promote intentionally skipped) -- the registry copy is
        stale/absent while workspace still holds the only good extraction."""
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _register(repo, "pmid:1")
            payload = _run_to_completion(repo, "pmid:1")
            run_dir = Path(payload["run_dir"])

            # simulate the registry losing its promoted extraction record
            reg = registry_mod.Registry(repo)
            rec = reg.lookup(evidence_id="pmid:1")
            rec["extraction_status"] = "not_started"
            rec["extraction_path"] = None
            reg.save()

            report = _gc(repo, apply=True, include_unsaved=True)
            cand = report["candidates"][0]
            self.assertEqual(cand["tier"], "blocking")
            self.assertTrue(any("not (yet) promoted" in r for r in cand["blocking_reasons"]))
            self.assertTrue(run_dir.exists())


class UnsavedCandidateTest(unittest.TestCase):
    def test_completed_run_without_project_is_unsaved_not_safe(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _register(repo, "pmid:1")
            payload = _run_to_completion(repo, "pmid:1")  # no --project
            run_dir = Path(payload["run_dir"])

            report = _gc(repo)
            cand = report["candidates"][0]
            self.assertEqual(cand["tier"], "unsaved")
            self.assertTrue(any("no --project" in r for r in cand["unsaved_reasons"]))

            # default apply leaves it alone
            default_apply = _gc(repo, apply=True)
            self.assertEqual(default_apply["deleted"], [])
            self.assertTrue(run_dir.exists())

            # opt-in widens deletion to include it
            widened = _gc(repo, apply=True, include_unsaved=True)
            self.assertEqual(widened["deleted"], [cand["run_dir"]])
            self.assertFalse(run_dir.exists())


class OutOfScopeTest(unittest.TestCase):
    def test_full_pipeline_run_is_never_touched(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            run_dir = repo / "runs" / "full-review-slug"
            (run_dir / "outputs").mkdir(parents=True)
            (run_dir / "config.json").write_text(json.dumps({
                "schema_version": 1, "profile": "standard", "scope": "broad",
            }), encoding="utf-8")
            (run_dir / "outputs" / "report.md").write_text("# Report\n", encoding="utf-8")

            report = _gc(repo, apply=True, include_unsaved=True)
            self.assertEqual(report["candidates"], [])
            self.assertEqual(report["skipped_out_of_scope"], 1)
            self.assertTrue(run_dir.exists())
            self.assertTrue((run_dir / "outputs" / "report.md").exists())


class PaperSetTest(unittest.TestCase):
    def test_paper_set_run_classified_from_included_evidence_ids(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _register(repo, "pmid:1")
            _register(repo, "pmid:2")
            args = paper.build_parser().parse_args([
                "summarize-set", "--repo", str(repo), "--pmid", "1", "--pmid", "2",
                "--offline"])
            for eid in ("pmid:1", "pmid:2"):
                _run_to_completion(repo, eid)

            code = paper.cmd_summarize_set(args)
            self.assertEqual(code, 0)

            report = _gc(repo)
            kinds = {c["kind"] for c in report["candidates"]}
            self.assertIn("paper-summary-set", kinds)
            set_cand = next(c for c in report["candidates"] if c["kind"] == "paper-summary-set")
            self.assertEqual(set_cand["tier"], "unsaved")  # no --project on the set run
            self.assertEqual(sorted(set_cand["evidence_ids"]), ["pmid:1", "pmid:2"])


if __name__ == "__main__":
    unittest.main()
