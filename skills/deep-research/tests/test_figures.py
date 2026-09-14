"""Caption-anchored figure extraction (library.py "figures" section).

Everything here exercises the pure geometry and parsing, which is where the
judgement calls live. The two poppler shell-outs (`pdftotext -bbox`,
`pdftoppm`) are not invoked: the binaries may be absent in CI, and their
behaviour is not what these rules get wrong.
"""

from __future__ import annotations

import unittest

from helpers import load_script

library = load_script("library.py")


BBOX_XHTML = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "x.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title></title></head>
<body>
<doc>
  <page width="612.000000" height="792.000000">
    <word xMin="72.000000" yMin="65.170000" xMax="123.940000" yMax="74.170000">Introduction</word>
    <word xMin="126.440000" yMin="65.170000" xMax="144.220000" yMax="74.170000">here</word>
    <word xMin="72.000000" yMin="90.000000" xMax="110.000000" yMax="99.000000">Second</word>
  </page>
</doc>
</body>
</html>
"""


def _word(x0, y0, x1, y1, text="x"):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text}


def _line(x0, y0, x1, y1, text=""):
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text,
            "words": [_word(x0, y0, x1, y1, text or "x")]}


class BBoxParsingTests(unittest.TestCase):
    def test_parses_pages_and_word_rectangles(self):
        parser = library._BBoxParser()
        parser.feed(BBOX_XHTML)
        parser.close()
        self.assertEqual(len(parser.pages), 1)
        page = parser.pages[0]
        self.assertEqual((page["width"], page["height"]), (612.0, 792.0))
        self.assertEqual(len(page["words"]), 3)
        self.assertEqual(page["words"][0]["text"], "Introduction")
        self.assertAlmostEqual(page["words"][0]["x1"], 123.94)

    def test_attribute_names_are_matched_case_insensitively(self):
        # html.parser lower-cases attribute names, so a parser written against
        # the source's xMin/yMin spelling silently yields zero words.
        parser = library._BBoxParser()
        parser.feed(BBOX_XHTML)
        parser.close()
        self.assertTrue(all(w["y1"] > 0 for w in parser.pages[0]["words"]))

    def test_malformed_input_yields_no_pages(self):
        parser = library._BBoxParser()
        parser.feed("<html><body>no doc element</body></html>")
        parser.close()
        self.assertEqual(parser.pages, [])


class LineGroupingTests(unittest.TestCase):
    def test_words_on_one_baseline_become_one_line(self):
        words = [_word(72, 100, 100, 110, "alpha"), _word(105, 100, 140, 110, "beta")]
        lines = library._group_lines(words)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["text"], "alpha beta")

    def test_separate_baselines_stay_separate(self):
        words = [_word(72, 100, 100, 110, "alpha"), _word(72, 130, 100, 140, "beta")]
        self.assertEqual(len(library._group_lines(words)), 2)

    def test_words_are_ordered_left_to_right_within_a_line(self):
        words = [_word(200, 100, 240, 110, "second"), _word(72, 100, 100, 110, "first")]
        self.assertEqual(library._group_lines(words)[0]["text"], "first second")


class ColumnDetectionTests(unittest.TestCase):
    page = {"width": 612.0, "height": 792.0}

    def _two_column_lines(self):
        lines = []
        for i in range(8):
            y = 100 + i * 14
            lines.append(_line(60, y, 290, y + 9))
            lines.append(_line(322, y, 552, y + 9))
        return lines

    def test_two_column_layout_is_split_at_the_gutter(self):
        columns = library._detect_columns(self.page, self._two_column_lines())
        self.assertEqual(len(columns), 2)
        self.assertTrue(290 <= columns[0][1] <= 322)

    def test_a_full_width_title_does_not_hide_the_gutter(self):
        lines = self._two_column_lines() + [_line(60, 60, 400, 76, "A Spanning Title")]
        self.assertEqual(len(library._detect_columns(self.page, lines)), 2)

    def test_ragged_single_column_is_not_split(self):
        # Every line stops short of the right margin, so a clear vertical band
        # exists — but the right side holds no lines at all.
        lines = [_line(72, 100 + i * 14, 380, 109 + i * 14) for i in range(8)]
        self.assertEqual(library._detect_columns(self.page, lines), [(0.0, 612.0)])

    def test_too_few_lines_to_judge_stays_single_column(self):
        lines = [_line(60, 100, 290, 109), _line(322, 100, 552, 109)]
        self.assertEqual(library._detect_columns(self.page, lines), [(0.0, 612.0)])


class CaptionMatchingTests(unittest.TestCase):
    def test_caption_openers_match(self):
        for text in ("Figure 1. Survival by arm.", "Fig. 2 Kaplan-Meier curves",
                     "FIGURE 10: Forest plot", "Scheme 3. Synthesis route",
                     "Figure 4a. Subgroup analysis"):
            with self.subTest(text=text):
                self.assertIsNotNone(library.CAPTION_RE.match(text))

    def test_in_text_mentions_do_not_match(self):
        for text in ("as shown in Figure 3 the curves separate",
                     "see Figure 2 for details", "The figure below shows"):
            with self.subTest(text=text):
                self.assertIsNone(library.CAPTION_RE.match(text))

    def test_tables_are_not_treated_as_figures(self):
        # Table bodies are text; the text layer represents them better than a crop.
        self.assertIsNone(library.CAPTION_RE.match("Table 1. Baseline characteristics."))

    def test_kind_is_normalised(self):
        self.assertEqual(library._normalise_kind("Fig."), "figure")
        self.assertEqual(library._normalise_kind("FIGURE"), "figure")
        self.assertEqual(library._normalise_kind("Scheme"), "scheme")


class CaptionBlockTests(unittest.TestCase):
    def test_continuation_line_is_absorbed(self):
        lines = [_line(72, 500, 300, 509, "Figure 1. Survival by arm."),
                 _line(72, 512, 300, 521, "Ticks mark censoring.")]
        caption, bottom = library._caption_block(0, lines)
        self.assertEqual(caption, "Figure 1. Survival by arm. Ticks mark censoring.")
        self.assertEqual(bottom, 521)

    def test_paragraph_after_a_gap_is_not_absorbed(self):
        lines = [_line(72, 500, 300, 509, "Figure 1. Survival by arm."),
                 _line(72, 560, 300, 569, "Discussion. The separation persisted.")]
        caption, _ = library._caption_block(0, lines)
        self.assertEqual(caption, "Figure 1. Survival by arm.")

    def test_next_caption_stops_the_block(self):
        lines = [_line(72, 500, 300, 509, "Figure 1. First."),
                 _line(72, 512, 300, 521, "Figure 2. Second.")]
        caption, _ = library._caption_block(0, lines)
        self.assertEqual(caption, "Figure 1. First.")


class RegionTests(unittest.TestCase):
    column = (0.0, 300.0)

    def test_band_above_the_caption_is_bounded_by_nearest_content(self):
        anchor = _line(72, 400, 290, 409, "Figure 1. Caption.")
        lines = [_line(72, 100, 290, 109), _line(72, 250, 290, 259), anchor]
        box = library._region_above(anchor, self.column, lines)
        self.assertIsNotNone(box)
        x0, y0, x1, y1 = box
        self.assertGreater(y0, 259)   # starts below the last text above
        self.assertLess(y1, 400)      # stops before the caption
        self.assertEqual((x0, x1), self.column)

    def test_caption_flush_against_text_above_yields_no_region(self):
        anchor = _line(72, 270, 290, 279, "Figure 1. Caption.")
        lines = [_line(72, 250, 290, 259), anchor]
        self.assertIsNone(library._region_above(anchor, self.column, lines))

    def test_nothing_above_means_the_band_runs_to_the_page_top(self):
        anchor = _line(72, 400, 290, 409, "Figure 1. Caption.")
        box = library._region_above(anchor, self.column, [anchor])
        self.assertIsNotNone(box)
        self.assertLess(box[1], library.FIGURE_MIN_POINTS)

    def test_content_in_the_other_column_does_not_bound_the_band(self):
        anchor = _line(72, 400, 290, 409, "Figure 1. Caption.")
        other_column = _line(322, 380, 552, 389, "unrelated text")
        box = library._region_above(anchor, self.column, [anchor, other_column])
        self.assertIsNotNone(box)
        self.assertLess(box[1], 380)


class CropGeometryTests(unittest.TestCase):
    def test_points_are_scaled_to_pixels_at_the_render_resolution(self):
        args = library._crop_args((72.0, 144.0, 216.0, 288.0), 300)
        self.assertEqual(args, ["-x", "300", "-y", "600", "-W", "600", "-H", "600"])

    def test_degenerate_boxes_still_render_at_least_one_pixel(self):
        args = library._crop_args((10.0, 10.0, 10.0, 10.0), 72)
        self.assertEqual(args[5], "1")
        self.assertEqual(args[7], "1")


class InkFractionTests(unittest.TestCase):
    @staticmethod
    def _pgm(pixels: bytes, width: int = 4, height: int = 1) -> bytes:
        return b"P5\n%d %d\n255\n" % (width, height) + pixels

    def test_all_white_reads_as_no_ink(self):
        self.assertEqual(library._ink_fraction(self._pgm(b"\xff\xff\xff\xff")), 0.0)

    def test_half_dark_reads_as_half_ink(self):
        self.assertAlmostEqual(library._ink_fraction(self._pgm(b"\x00\x00\xff\xff")), 0.5)

    def test_comments_in_the_header_are_skipped(self):
        pgm = b"P5\n# generated by pdftoppm\n2 1\n255\n\x00\xff"
        self.assertAlmostEqual(library._ink_fraction(pgm), 0.5)

    def test_non_pgm_input_is_reported_as_no_ink(self):
        self.assertEqual(library._ink_fraction(b"\x89PNG\r\n\x1a\n"), 0.0)

    def test_blank_threshold_rejects_an_empty_crop(self):
        blank = self._pgm(b"\xff" * 1000, width=1000)
        self.assertLess(library._ink_fraction(blank), library.BLANK_INK_FRACTION)


if __name__ == "__main__":
    unittest.main()
