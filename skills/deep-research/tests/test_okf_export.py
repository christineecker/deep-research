from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import run_py


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")


def _registry_record(evidence_id: str, pmid: str, extraction_rel: str,
                     appraisals: dict | None = None, **overrides) -> dict:
    rec = {
        "schema_version": 1, "evidence_id": evidence_id, "pmid": pmid,
        "title": f"Study {pmid}", "journal": "Journal of Testing",
        "publication_date": "2025-01-01",
        "authors": [{"family": "Doe", "given": "Jane", "initials": "J"}],
        "status": "included", "metadata_status": "complete", "asset_status": "missing",
        "extraction_status": "extracted",
        "appraisal_status": "appraised" if appraisals else "not_appraised",
        "extraction_path": extraction_rel, "appraisals": appraisals or {},
        "sources": [], "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    rec.update(overrides)
    return rec


def _setup_repo_and_wiki(tmp: Path, *, with_appraisal_project: str | None = None):
    repo = tmp / "repo"
    wiki = tmp / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    run_py(["scripts/research.py", "init", str(repo)])
    init = run_py(["scripts/okf.py", "init", "--wiki", str(wiki)])
    assert init.returncode == 0, init.stderr

    extraction1 = repo / "data" / "papers" / "extractions" / "pmid-111.json"
    _write_json(extraction1, {"evidence_id": "pmid:111", "design": "RCT",
                             "population": "Adults with condition X"})
    appraisals1 = {}
    if with_appraisal_project:
        appraisal1 = (repo / "data" / "papers" / "appraisals" / with_appraisal_project
                     / "pmid-111.json")
        _write_json(appraisal1, {"evidence_id": "pmid:111", "tool": "none",
                                "domains": [], "overall_judgement": "unclear"})
        appraisals1 = {with_appraisal_project:
                       str(appraisal1.relative_to(repo))}

    extraction2 = repo / "data" / "papers" / "extractions" / "pmid-222.json"
    _write_json(extraction2, {"evidence_id": "pmid:222", "design": "Cohort",
                             "population": "Adults with condition Y"})

    reg_path = repo / "data" / "papers" / "registry.jsonl"
    records = [
        _registry_record("pmid:111", "111", str(extraction1.relative_to(repo)), appraisals1),
        _registry_record("pmid:222", "222", str(extraction2.relative_to(repo))),
    ]
    reg_path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return repo, wiki


class OkfExportHappyPathTest(unittest.TestCase):
    def test_two_papers_one_appraisal_promotes_a_validator_clean_bundle(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo, wiki = _setup_repo_and_wiki(tmp, with_appraisal_project="proj1")

            result = run_py(["scripts/research.py", "okf-export", "--repo", str(repo),
                            "--evidence-id", "pmid:111", "--evidence-id", "pmid:222",
                            "--wiki", str(wiki), "--project", "proj1"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["run_kept"])
            run_dir = Path(payload["run_dir"])
            self.assertTrue(run_dir.is_dir())
            self.assertTrue((run_dir / "outputs" / "verification.json").exists())

            self.assertTrue((wiki / "research" / "studies" / "pmid-111.md").exists())
            self.assertTrue((wiki / "research" / "studies" / "pmid-222.md").exists())

            validate = run_py(["scripts/okf.py", "validate", "--wiki", str(wiki), "--json"])
            self.assertEqual(validate.returncode, 0, validate.stdout + validate.stderr)
            vpayload = json.loads(validate.stdout)
            self.assertEqual(vpayload["okf_validation"], "pass")
            self.assertEqual(vpayload["violations"], [])

    def test_no_keep_run_deletes_the_synthetic_run_directory(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo, wiki = _setup_repo_and_wiki(tmp)

            result = run_py(["scripts/research.py", "okf-export", "--repo", str(repo),
                            "--evidence-id", "pmid:111", "--evidence-id", "pmid:222",
                            "--wiki", str(wiki), "--no-keep-run"])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["run_kept"])
            self.assertIsNone(payload["run_dir"])
            self.assertEqual(list((repo / "runs").iterdir()), [])


class OkfExportExemptionBoundaryTest(unittest.TestCase):
    """Phase 5: `okf-export` no longer passes a blanket `--force` — only
    C-SEARCH-LOG/C-PRISMA (expected-absent for a hand-picked registry
    selection) are exempted. A genuine content-consistency failure (here,
    an unmarked retracted paper — C-RETRACTION) must still block."""

    def test_retracted_paper_without_a_retraction_marker_still_blocks_promotion(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            wiki = tmp / "wiki"
            wiki.mkdir(parents=True, exist_ok=True)
            run_py(["scripts/research.py", "init", str(repo)])
            init = run_py(["scripts/okf.py", "init", "--wiki", str(wiki)])
            assert init.returncode == 0, init.stderr

            extraction = repo / "data" / "papers" / "extractions" / "pmid-444.json"
            _write_json(extraction, {"evidence_id": "pmid:444", "design": "RCT",
                                     "population": "Adults with condition Z"})
            reg_path = repo / "data" / "papers" / "registry.jsonl"
            rec = _registry_record("pmid:444", "444", str(extraction.relative_to(repo)),
                                   retraction_status="retracted")
            reg_path.write_text(json.dumps(rec) + "\n", encoding="utf-8")

            result = run_py(["scripts/research.py", "okf-export", "--repo", str(repo),
                            "--evidence-id", "pmid:444", "--wiki", str(wiki)])
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertIn("C-RETRACTION", payload["error"])
            # nothing was promoted into the bundle
            self.assertFalse((wiki / "research" / "studies" / "pmid-444.md").exists())


class OkfExportFailureTest(unittest.TestCase):
    def test_missing_extraction_fails_closed_and_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            wiki = tmp / "wiki"
            wiki.mkdir(parents=True, exist_ok=True)
            run_py(["scripts/research.py", "init", str(repo)])
            run_py(["scripts/okf.py", "init", "--wiki", str(wiki)])

            reg_path = repo / "data" / "papers" / "registry.jsonl"
            # registered but never extracted: no extraction_path at all.
            rec = _registry_record("pmid:333", "333", None)
            del rec["extraction_path"]
            reg_path.write_text(json.dumps(rec) + "\n", encoding="utf-8")

            result = run_py(["scripts/research.py", "okf-export", "--repo", str(repo),
                            "--evidence-id", "pmid:333", "--wiki", str(wiki)])
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertIn("pmid:333", payload["missing"][0])
            self.assertEqual(list((repo / "runs").iterdir()), [])
            self.assertFalse((wiki / "research" / "studies" / "pmid-333.md").exists())

    def test_unknown_evidence_id_fails_closed(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            wiki = tmp / "wiki"
            wiki.mkdir(parents=True, exist_ok=True)
            run_py(["scripts/research.py", "init", str(repo)])
            run_py(["scripts/okf.py", "init", "--wiki", str(wiki)])

            result = run_py(["scripts/research.py", "okf-export", "--repo", str(repo),
                            "--evidence-id", "pmid:999999", "--wiki", str(wiki)])
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertIn("pmid:999999", payload["missing"][0])


if __name__ == "__main__":
    unittest.main()
