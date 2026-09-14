"""Figure storage invariants (`refmgr/repositories/figures.py`, service wiring).

Figures are derived data sitting on top of the ordinary asset/attachment path,
which makes three properties load-bearing:

  * re-running extraction is a no-op, not a duplicate -- deterministic crops
    dedupe to one asset, one role='figure' attachment, and one figure row;
  * `--replace` rebuilds the derived rows without touching the immutable bytes
    they point at (invariant #2 covers attachments, not derived meaning);
  * captions are searchable, and live in `figures_fts` rather than `chunks_fts`
    so that unverifiable text never enters the span-verifiable chunk index.

Extraction itself (poppler, layout heuristics) is tested in test_figures.py;
nothing here shells out, so these run wherever SQLite does.
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr.service import ReferenceManagerService  # noqa: E402

EXTRACTOR = "test-extractor/1"

# Distinct bytes per figure; the content is irrelevant, the hashing is not.
PNG_A = b"\x89PNG\r\n\x1a\n" + b"figure-one-pixels"
PNG_B = b"\x89PNG\r\n\x1a\n" + b"figure-two-pixels"


def _figure(png: bytes, number: str, caption: str, page: int = 1) -> dict:
    return {"page": page, "kind": "figure", "label": f"Figure {number}",
            "number": number, "caption": caption, "bbox": [0.0, 10.0, 300.0, 200.0],
            "dpi": 300, "png_bytes": png}


class FigureStorageTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.service = ReferenceManagerService(Path(self._tmp.name) / "library")
        self.addCleanup(self.service.close)
        self.paper_id = self.service.add_paper(
            title="A trial with figures", paper_type="article",
            identifiers=[("doi", "10.1234/figs")])
        pdf = Path(self._tmp.name) / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4 pretend")
        self.attachment_id = self.service.import_attachment(
            self.paper_id, pdf, role="fulltext", mime_type="application/pdf")

    def _import(self, figures, **kwargs):
        return self.service.import_figures(
            self.paper_id, self.attachment_id, figures, extractor=EXTRACTOR, **kwargs)


class ImportTests(FigureStorageTestCase):
    def test_figures_are_stored_with_their_caption_metadata(self):
        self._import([_figure(PNG_A, "1", "Figure 1. Survival by arm.", page=3)])
        rows = self.service.figures.list_for_paper(self.paper_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["label"], "Figure 1")
        self.assertEqual(rows[0]["number"], "1")
        self.assertEqual(rows[0]["page"], 3)
        self.assertEqual(rows[0]["bbox"], [0.0, 10.0, 300.0, 200.0])
        self.assertEqual(rows[0]["extractor"], EXTRACTOR)
        self.assertEqual(rows[0]["source_attachment_id"], self.attachment_id)

    def test_image_bytes_land_in_the_asset_store(self):
        stored = self._import([_figure(PNG_A, "1", "Figure 1. A caption.")])
        path = self.service.asset_path(stored[0]["asset_sha256"])
        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes(), PNG_A)

    def test_each_figure_gets_a_role_figure_attachment(self):
        self._import([_figure(PNG_A, "1", "One."), _figure(PNG_B, "2", "Two.")])
        roles = [a["role"] for a in self.service.attachments.list_for_paper(self.paper_id)]
        self.assertEqual(roles.count("figure"), 2)
        self.assertEqual(roles.count("fulltext"), 1)

    def test_figures_without_bytes_are_skipped(self):
        figure = _figure(PNG_A, "1", "One.")
        figure["png_bytes"] = b""
        self.assertEqual(self._import([figure]), [])
        self.assertEqual(self.service.figures.list_for_paper(self.paper_id), [])

    def test_a_caption_less_figure_is_still_stored(self):
        figure = _figure(PNG_A, "1", "")
        figure["caption"] = None
        self._import([figure])
        rows = self.service.figures.list_for_paper(self.paper_id)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["caption"])


class IdempotenceTests(FigureStorageTestCase):
    def test_re_extraction_creates_no_duplicate_rows(self):
        figures = [_figure(PNG_A, "1", "One."), _figure(PNG_B, "2", "Two.")]
        first = self._import(figures)
        second = self._import(figures)
        self.assertEqual([f["figure_id"] for f in first],
                         [f["figure_id"] for f in second])
        self.assertEqual(len(self.service.figures.list_for_paper(self.paper_id)), 2)

    def test_re_extraction_creates_no_duplicate_attachments(self):
        figures = [_figure(PNG_A, "1", "One.")]
        self._import(figures)
        self._import(figures)
        roles = [a["role"] for a in self.service.attachments.list_for_paper(self.paper_id)]
        self.assertEqual(roles.count("figure"), 1)

    def test_replace_rebuilds_rows_but_keeps_the_bytes(self):
        stored = self._import([_figure(PNG_A, "1", "Old caption.")])
        sha = stored[0]["asset_sha256"]
        rebuilt = self._import([_figure(PNG_A, "1", "New caption.")], replace=True)

        rows = self.service.figures.list_for_paper(self.paper_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["caption"], "New caption.")
        self.assertNotEqual(rows[0]["id"], stored[0]["figure_id"])
        self.assertEqual(rebuilt[0]["asset_sha256"], sha)
        self.assertTrue(self.service.asset_path(sha).exists())

    def test_replace_drops_the_stale_caption_from_the_index(self):
        self._import([_figure(PNG_A, "1", "Zebra biomarker plot.")])
        self._import([_figure(PNG_A, "1", "Ocelot biomarker plot.")], replace=True)
        self.assertEqual(self.service.figures.search("zebra"), [])
        self.assertEqual(len(self.service.figures.search("ocelot")), 1)

    def test_has_figures_for_attachment_gates_a_second_pass(self):
        self.assertFalse(
            self.service.figures.has_figures_for_attachment(self.attachment_id))
        self._import([_figure(PNG_A, "1", "One.")])
        self.assertTrue(
            self.service.figures.has_figures_for_attachment(self.attachment_id))


class CaptionSearchTests(FigureStorageTestCase):
    def setUp(self):
        super().setUp()
        self._import([
            _figure(PNG_A, "1", "Figure 1. Kaplan-Meier survival by treatment arm."),
            _figure(PNG_B, "2", "Figure 2. Forest plot of subgroup hazard ratios."),
        ])

    def test_captions_are_searchable(self):
        hits = self.service.figures.search("forest plot")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["number"], "2")

    def test_hits_carry_a_marked_snippet(self):
        hits = self.service.figures.search("survival")
        self.assertIn("**survival**", hits[0]["snippet"].lower())

    def test_search_ranks_any_term_rather_than_requiring_all(self):
        # Same contract as ChunkRepository.search: a question shares only some of
        # its words with the caption that answers it.
        self.assertTrue(self.service.figures.search("hazard ratios in older adults"))

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.service.figures.search("   "), [])

    def test_fts_operator_characters_are_inert(self):
        # Not "returns nothing" -- every term is quoted, so FTS5 syntax becomes
        # ordinary text. AND is matched as the literal word (which no caption
        # contains) rather than intersecting, so the survival hit still stands.
        self.assertEqual(
            [h["number"] for h in self.service.figures.search("survival AND zebra")],
            ["1"])

    def test_unbalanced_quotes_do_not_raise(self):
        self.assertEqual(self.service.figures.search('") OR ('), [])

    def test_paper_filter_restricts_results(self):
        other = self.service.add_paper(title="Other", paper_type="article")
        self.assertEqual(self.service.figures.search("survival", paper_ids=[other]), [])
        self.assertTrue(
            self.service.figures.search("survival", paper_ids=[self.paper_id]))

    def test_empty_paper_filter_means_no_papers(self):
        self.assertEqual(self.service.figures.search("survival", paper_ids=[]), [])

    def test_deleted_papers_drop_out_of_caption_search(self):
        self.service.papers.soft_delete(self.paper_id)
        self.assertEqual(self.service.figures.search("survival"), [])

    def test_captions_do_not_enter_the_chunk_index(self):
        # chunks_fts rows must stay verifiable spans into a snapshot; a caption
        # lifted out of a PDF's layout is not one.
        self.assertEqual(self.service.chunks.search("survival"), [])


class RemovalTests(FigureStorageTestCase):
    def test_removing_a_paper_clears_its_figures_and_captions(self):
        self._import([_figure(PNG_A, "1", "Zebra plot.")])
        self.assertEqual(self.service.figures.remove_paper(self.paper_id), 1)
        self.assertEqual(self.service.figures.list_for_paper(self.paper_id), [])
        self.assertEqual(self.service.figures.search("zebra"), [])

    def test_removing_an_attachments_figures_leaves_the_assets_alone(self):
        stored = self._import([_figure(PNG_A, "1", "One.")])
        self.service.figures.remove_for_attachment(self.attachment_id)
        self.assertEqual(self.service.figures.list_for_attachment(self.attachment_id), [])
        self.assertTrue(self.service.asset_path(stored[0]["asset_sha256"]).exists())


class CoverageTests(FigureStorageTestCase):
    def test_coverage_counts_figures_papers_and_source_pdfs(self):
        self._import([_figure(PNG_A, "1", "One."), _figure(PNG_B, "2", None)])
        self.assertEqual(self.service.figures.coverage(),
                         {"figures": 2, "papers_with_figures": 1,
                          "pdfs_extracted": 1, "captioned": 1})

    def test_an_empty_library_reports_zeroes(self):
        self.assertEqual(self.service.figures.coverage()["figures"], 0)


class DoctorTests(FigureStorageTestCase):
    def test_a_library_with_figures_is_healthy(self):
        import refmgr.doctor as doctor

        self._import([_figure(PNG_A, "1", "One.")])
        report = doctor.run(self.service)
        self.assertTrue(report["healthy"])
        self.assertEqual(report["indexes"]["papers_with_figures"], 1)

    def test_the_schema_refuses_to_orphan_a_figures_source(self):
        self._import([_figure(PNG_A, "1", "One.")])
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.conn.execute(
                "DELETE FROM attachments WHERE id = ?", (self.attachment_id,))

    def test_a_figure_whose_source_attachment_vanished_is_a_problem(self):
        import refmgr.doctor as doctor

        self._import([_figure(PNG_A, "1", "One.")])
        # Foreign keys are a per-connection pragma, so a row written by some other
        # client, or an older database, can still reach this state. The doctor check
        # is the backstop for that, which means the test has to reach it the same way.
        self.service.conn.execute("PRAGMA foreign_keys=OFF")
        self.service.conn.execute(
            "DELETE FROM attachments WHERE id = ?", (self.attachment_id,))
        self.service.conn.execute("PRAGMA foreign_keys=ON")
        report = doctor.run(self.service)
        self.assertEqual(len(report["figures_without_source"]), 1)
        self.assertFalse(report["healthy"])
        self.assertTrue(any("--replace" in line for line in report["advice"]))


if __name__ == "__main__":
    unittest.main()
