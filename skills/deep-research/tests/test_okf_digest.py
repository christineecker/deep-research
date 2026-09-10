from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, make_run, minimal_corpus_record, write_jsonl


okf = load_script("okf.py")


class OkfDigestPromotionTest(unittest.TestCase):
    def test_digest_gate_requires_completed_receipt(self):
        with TemporaryDirectory() as td:
            _wiki, run_dir = make_run(Path(td))
            run = okf.load_run(run_dir)
            ready, reason = okf.validate_digest_ready(run)
            self.assertFalse(ready)
            self.assertIn("missing or empty", reason)

            (run_dir / "outputs" / "digest.md").write_text("# Digest\n\nShort.\n",
                                                            encoding="utf-8")
            write_jsonl(run_dir / "taskboard.jsonl", [
                {"task_id": "digest:slug:report", "status": "failed",
                 "output_path": "outputs/digest.md"}
            ])
            run = okf.load_run(run_dir)
            ready, reason = okf.validate_digest_ready(run)
            self.assertFalse(ready)
            self.assertIn("not 'completed'", reason)

            write_jsonl(run_dir / "taskboard.jsonl", [
                {"task_id": "digest:slug:report", "status": "completed",
                 "output_path": "outputs/digest.md"}
            ])
            run = okf.load_run(run_dir)
            ready, reason = okf.validate_digest_ready(run)
            self.assertTrue(ready, reason)

    def test_review_concept_uses_digest_not_full_report(self):
        with TemporaryDirectory() as td:
            wiki, run_dir = make_run(Path(td))
            research = wiki / "research"
            rec = minimal_corpus_record()
            rec["screening"] = {"decision": "include", "reason": "eligible"}
            write_jsonl(run_dir / "corpus.jsonl", [rec])
            (run_dir / "outputs" / "report.md").write_text(
                "# Full Report\n\nFULL REPORT ONLY\n", encoding="utf-8")
            (run_dir / "outputs" / "digest.md").write_text(
                "# Digest\n\nDIGEST BODY ONLY\n", encoding="utf-8")
            write_jsonl(run_dir / "taskboard.jsonl", [
                {"task_id": "digest:slug:report", "status": "completed",
                 "output_path": "outputs/digest.md"}
            ])

            run = okf.load_run(run_dir)
            concepts = okf.build_run_concepts(
                run, wiki, research, "2026-09-09T00:00:00Z",
                "2026-09-09T00:00:00Z", "stable")
            review = next(c for c in concepts if c.type == "Review")

            self.assertIn("DIGEST BODY ONLY", review.body)
            self.assertNotIn("FULL REPORT ONLY", review.body)


if __name__ == "__main__":
    unittest.main()
