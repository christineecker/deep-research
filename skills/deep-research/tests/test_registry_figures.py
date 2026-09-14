"""registry.py's figure-extraction orchestration (`extract_figures_for_attachment`):
the seam that decides whether to run `library.extract_figures` at all, using
`service.figure_extraction` (hardening plan package 7) instead of "does a figures row
exist" to make that call.

`library.extract_figures` needs poppler binaries this test environment does not
promise, so `library.extract_figures` itself is monkeypatched throughout -- these
tests are about the orchestration/state-tracking seam, not the PDF-rendering
heuristic on the other side of it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import library  # noqa: E402  (patched directly; see module docstring)

registry = load_script("registry.py")


def _figure(label="Figure 1", content=b"figure-bytes", caption="A caption"):
    return {"png_bytes": content, "kind": "figure", "label": label, "number": "1",
            "caption": caption, "page": 1, "bbox": None}


class ExtractFiguresOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp_ctx = TemporaryDirectory()
        self.tmp = Path(self._tmp_ctx.name)
        self.addCleanup(self._tmp_ctx.cleanup)

        self.service = registry._refmgr_service(self.tmp)
        self.addCleanup(self.service.close)
        self.paper_id = self.service.add_paper(title="A Paper", paper_type="article")
        pdf_path = self.tmp / "source.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake pdf")
        self.attachment_id = self.service.import_attachment(
            self.paper_id, pdf_path, role="fulltext", mime_type="application/pdf"
        )

        self._original_extract_figures = library.extract_figures
        self.addCleanup(setattr, library, "extract_figures", self._original_extract_figures)

    def _stub(self, calls, figures_by_call):
        def fake_extract_figures(pdf, *, dpi=300, pages=None, max_figures=100, out_dir=None):
            calls.append({"dpi": dpi, "max_figures": max_figures})
            return figures_by_call[len(calls) - 1]
        library.extract_figures = fake_extract_figures

    def test_first_run_extracts_and_records_complete(self):
        calls = []
        self._stub(calls, [[_figure()]])

        result = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id
        )
        self.assertFalse(result["skipped"])
        self.assertEqual(result["figures"], 1)
        self.assertEqual(len(calls), 1)

        state = self.service.figure_extraction.get(self.attachment_id)
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["figure_count"], 1)

    def test_second_run_same_configuration_is_skipped_without_reextracting(self):
        calls = []
        self._stub(calls, [[_figure()], [_figure()]])

        registry.extract_figures_for_attachment(self.service, self.paper_id, self.attachment_id)
        result = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id
        )
        self.assertTrue(result["skipped"])
        self.assertEqual(result["figures"], 1)
        self.assertEqual(len(calls), 1, "a matching-configuration rerun must not re-extract")

    def test_zero_result_success_does_not_repeat_forever(self):
        calls = []
        self._stub(calls, [[], []])

        first = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id
        )
        self.assertFalse(first["skipped"])
        self.assertEqual(first["figures"], 0)

        second = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id
        )
        self.assertTrue(second["skipped"])
        self.assertEqual(second["figures"], 0)
        self.assertEqual(len(calls), 1, "a genuine zero-figure result must not re-extract")

    def test_changed_dpi_reruns_extraction(self):
        calls = []
        self._stub(calls, [
            [_figure()],
            [_figure(), _figure(label="Figure 2", content=b"figure-bytes-2")],
        ])

        registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id, dpi=300
        )
        result = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id, dpi=600
        )
        self.assertFalse(result["skipped"])
        self.assertEqual(result["figures"], 2)
        self.assertEqual(len(calls), 2)
        # No duplicate active figures after the rerun -- exactly the new set.
        rows = self.service.figures.list_for_paper(self.paper_id)
        self.assertEqual(len(rows), 2)

    def test_replace_true_forces_a_rerun_of_an_unchanged_configuration(self):
        calls = []
        self._stub(calls, [[_figure()], [_figure()]])

        registry.extract_figures_for_attachment(self.service, self.paper_id, self.attachment_id)
        result = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id, replace=True
        )
        self.assertFalse(result["skipped"])
        self.assertEqual(len(calls), 2)
        rows = self.service.figures.list_for_paper(self.paper_id)
        self.assertEqual(len(rows), 1, "no duplicate active figures after a forced rerun")

    def test_extraction_failure_is_recorded_and_retried_next_time(self):
        calls = []

        def flaky(pdf, *, dpi=300, pages=None, max_figures=100, out_dir=None):
            calls.append(1)
            raise RuntimeError("poppler crashed")

        library.extract_figures = flaky

        result = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id
        )
        self.assertFalse(result["skipped"])
        self.assertIn("error", result)
        state = self.service.figure_extraction.get(self.attachment_id)
        self.assertEqual(state["status"], "failed")
        self.assertIn("poppler crashed", state["error"])

        # A subsequent call retries rather than treating the failure as done.
        library.extract_figures = lambda pdf, **kw: [_figure()]
        second = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id
        )
        self.assertFalse(second["skipped"])
        self.assertEqual(second["figures"], 1)

    def test_interrupted_attempt_resumes_safely_on_next_call(self):
        # Simulate a process killed mid-extraction: begin_attempt ran (via a first
        # extract_figures_for_attachment call whose extraction never returns because
        # we monkeypatch it to just record the attempt and stop -- close enough to
        # "died before completing" for this seam, since the real failure mode is a
        # killed process, not a Python exception).
        state_before = self.service.figure_extraction.begin_attempt(
            paper_id=self.paper_id, source_attachment_id=self.attachment_id,
            source_asset_sha256=self.service.attachments.list_for_paper(self.paper_id)[0][
                "asset_sha256"],
            extractor=registry.FIGURE_EXTRACTOR, options={"dpi": 300, "max_figures": 100},
        )
        self.assertTrue(state_before["should_run"])
        row = self.service.figure_extraction.get(self.attachment_id)
        self.assertEqual(row["status"], "running")  # never completed or failed

        calls = []
        self._stub(calls, [[_figure()]])
        result = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id
        )
        self.assertFalse(result["skipped"], "a 'running' leftover must resume, not skip")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            self.service.figure_extraction.get(self.attachment_id)["status"], "complete"
        )

    def test_missing_asset_bytes_fails_the_attempt_visibly(self):
        row = self.service.assets.get(
            self.service.attachments.list_for_paper(self.paper_id)[0]["asset_sha256"]
        )
        (self.service.library_root / row["storage_path"]).unlink()

        result = registry.extract_figures_for_attachment(
            self.service, self.paper_id, self.attachment_id
        )
        self.assertIn("error", result)
        state = self.service.figure_extraction.get(self.attachment_id)
        self.assertEqual(state["status"], "failed")

    def test_unknown_attachment_errors_without_touching_state(self):
        result = registry.extract_figures_for_attachment(
            self.service, self.paper_id, "does-not-exist"
        )
        self.assertIn("error", result)
        self.assertIsNone(self.service.figure_extraction.get("does-not-exist"))


if __name__ == "__main__":
    unittest.main()
