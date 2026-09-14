"""Restartable figure processing state (hardening plan package 7).

`refmgr.repositories.figure_extraction.FigureExtractionStateRepository` tracks
progress per source PDF attachment so a caller can tell "never attempted" from
"attempted and failed" from "attempted, genuinely zero figures" from "attempted under
a now-stale configuration" -- distinctions "does a figures row exist" cannot make.
`FigureRepository.replace_for_attachment` is the atomic stage-then-swap half: a failed
replacement must leave the previous figure rows intact, never a mix of old and new.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import db
from refmgr.repositories.attachments import AttachmentRepository
from refmgr.repositories.figure_extraction import FigureExtractionStateRepository
from refmgr.repositories.figures import FigureRepository
from refmgr.repositories.papers import PaperRepository
from refmgr.service import ReferenceManagerService


class StateRepoTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conn = db.open_and_migrate(self.tmp)
        self.papers = PaperRepository(self.conn)
        self.attachments = AttachmentRepository(self.conn)
        self.figures = FigureRepository(self.conn)
        self.state = FigureExtractionStateRepository(self.conn)
        self.paper_id = self.papers.create("A Paper", "article")
        with db.transaction(self.conn):
            for letter in "abcde":
                self.conn.execute(
                    "INSERT INTO assets (sha256, byte_size, mime_type, storage_path, "
                    "created_at) VALUES (?, ?, ?, ?, ?)",
                    (letter * 64, 100, "application/pdf", f"assets/{letter}.pdf", "now"),
                )
        self.attachment_id = self.attachments.link(self.paper_id, "a" * 64, role="fulltext")

    def _options(self, dpi=300, max_figures=100):
        return {"dpi": dpi, "max_figures": max_figures}


class BeginAttemptTest(StateRepoTestBase):
    def test_never_attempted_should_run(self):
        result = self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.assertTrue(result["should_run"])
        self.assertEqual(result["attempt_count"], 1)
        row = self.state.get(self.attachment_id)
        self.assertEqual(row["status"], "running")

    def test_completed_matching_configuration_is_skipped(self):
        self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.state.complete_attempt(self.attachment_id, 3)

        result = self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.assertFalse(result["should_run"])
        self.assertEqual(result["figure_count"], 3)

    def test_zero_figure_success_is_a_final_answer_not_a_retry_forever(self):
        self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.state.complete_attempt(self.attachment_id, 0)

        result = self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.assertFalse(result["should_run"])
        self.assertEqual(result["figure_count"], 0)

    def test_changed_options_forces_a_rerun(self):
        self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(dpi=300),
        )
        self.state.complete_attempt(self.attachment_id, 3)

        result = self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(dpi=600),
        )
        self.assertTrue(result["should_run"])
        self.assertEqual(result["attempt_count"], 1)

    def test_changed_extractor_forces_a_rerun(self):
        self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.state.complete_attempt(self.attachment_id, 3)

        result = self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/2", options=self._options(),
        )
        self.assertTrue(result["should_run"])

    def test_changed_source_asset_hash_forces_a_rerun(self):
        # A PDF re-imported under the same attachment slot with different bytes.
        self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.state.complete_attempt(self.attachment_id, 3)

        result = self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="b" * 64, extractor="ext/1", options=self._options(),
        )
        self.assertTrue(result["should_run"])

    def test_failed_attempt_is_retried_not_skipped(self):
        self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.state.fail_attempt(self.attachment_id, "poppler crashed")
        row = self.state.get(self.attachment_id)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["error"], "poppler crashed")

        result = self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.assertTrue(result["should_run"])
        # Same configuration as the failed attempt -- this is attempt #2.
        self.assertEqual(result["attempt_count"], 2)

    def test_interrupted_attempt_left_running_is_retried_not_treated_as_done(self):
        # Simulates a process killed mid-extraction: begin_attempt ran, nothing ever
        # called complete_attempt or fail_attempt, so status is still 'running'.
        self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        row = self.state.get(self.attachment_id)
        self.assertEqual(row["status"], "running")

        result = self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.assertTrue(result["should_run"], "an interrupted attempt must resume, not skip")

    def test_force_reruns_an_already_complete_matching_configuration(self):
        self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
        )
        self.state.complete_attempt(self.attachment_id, 3)

        result = self.state.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256="a" * 64, extractor="ext/1", options=self._options(),
            force=True,
        )
        self.assertTrue(result["should_run"])


class ReplaceForAttachmentAtomicityTest(StateRepoTestBase):
    def _spec(self, label, asset_sha256, caption=None):
        return {
            "paper_id": self.paper_id, "figure_attachment_id": self.attachment_id,
            "asset_sha256": asset_sha256, "kind": "figure", "extractor": "ext/1",
            "label": label, "number": "1", "caption": caption, "page": 1, "bbox": None,
        }

    def test_replace_swaps_rows_atomically(self):
        first_ids = self.figures.replace_for_attachment(
            self.attachment_id, [self._spec("Figure 1", "b" * 64)]
        )
        self.assertEqual(len(first_ids), 1)

        second_ids = self.figures.replace_for_attachment(
            self.attachment_id,
            [self._spec("Figure 1", "c" * 64), self._spec("Figure 2", "d" * 64)],
        )
        self.assertEqual(len(second_ids), 2)

        rows = self.figures.list_for_attachment(self.attachment_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["id"] for r in rows}, set(second_ids))
        # No duplicates, no leftover row from the first pass.
        self.assertNotIn(first_ids[0], {r["id"] for r in rows})

    def test_failed_replacement_preserves_previous_rows(self):
        original_ids = self.figures.replace_for_attachment(
            self.attachment_id, [self._spec("Figure 1", "b" * 64, caption="Original caption")]
        )

        bad_specs = [self._spec("Figure 1", "e" * 64)]
        bad_specs[0].pop("paper_id")  # forces a KeyError partway through the swap

        with self.assertRaises(KeyError):
            self.figures.replace_for_attachment(self.attachment_id, bad_specs)

        rows = self.figures.list_for_attachment(self.attachment_id)
        self.assertEqual([r["id"] for r in rows], original_ids)
        self.assertEqual(rows[0]["caption"], "Original caption")

    def test_replace_with_empty_specs_clears_all_figures(self):
        self.figures.replace_for_attachment(
            self.attachment_id, [self._spec("Figure 1", "b" * 64)]
        )
        ids = self.figures.replace_for_attachment(self.attachment_id, [])
        self.assertEqual(ids, [])
        self.assertEqual(self.figures.list_for_attachment(self.attachment_id), [])


class ServiceImportFiguresAtomicityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.service = ReferenceManagerService(self.tmp)
        self.addCleanup(self.service.close)
        self.paper_id = self.service.add_paper(title="A Paper", paper_type="article")
        pdf_path = self.tmp / "source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake pdf")
        self.attachment_id = self.service.import_attachment(
            self.paper_id, pdf_path, role="fulltext", mime_type="application/pdf"
        )

    def _figure(self, label, content):
        return {"png_bytes": content, "kind": "figure", "label": label,
                "number": "1", "caption": f"Caption for {label}", "page": 1, "bbox": None}

    def test_replace_true_swaps_atomically(self):
        stored_1 = self.service.import_figures(
            self.paper_id, self.attachment_id,
            [self._figure("Figure 1", b"figure-one-bytes")],
            extractor="ext/1",
        )
        self.assertEqual(len(stored_1), 1)

        stored_2 = self.service.import_figures(
            self.paper_id, self.attachment_id,
            [self._figure("Figure 1", b"figure-one-bytes-v2"),
             self._figure("Figure 2", b"figure-two-bytes")],
            extractor="ext/1", replace=True,
        )
        self.assertEqual(len(stored_2), 2)
        rows = self.service.figures.list_for_paper(self.paper_id)
        self.assertEqual(len(rows), 2)

    def test_failed_replace_preserves_old_figures(self):
        stored_1 = self.service.import_figures(
            self.paper_id, self.attachment_id,
            [self._figure("Figure 1", b"figure-one-bytes")],
            extractor="ext/1",
        )
        original_ids = {f["figure_id"] for f in stored_1}

        original_replace = self.service.figures.replace_for_attachment

        def flaky_replace(source_attachment_id, specs):
            raise RuntimeError("simulated crash mid-replace")

        self.service.figures.replace_for_attachment = flaky_replace
        try:
            with self.assertRaises(RuntimeError):
                self.service.import_figures(
                    self.paper_id, self.attachment_id,
                    [self._figure("Figure 1", b"new-bytes-1"),
                     self._figure("Figure 2", b"new-bytes-2")],
                    extractor="ext/1", replace=True,
                )
        finally:
            self.service.figures.replace_for_attachment = original_replace

        rows = self.service.figures.list_for_paper(self.paper_id)
        self.assertEqual({r["id"] for r in rows}, original_ids)
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
