#!/usr/bin/env python3
"""Tests for scripts/ris.py — pure RIS serializer, no file/network I/O.

Run: python3 -m unittest discover -s skills/deep-research/tests -p "test_ris.py" -v
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ris import (  # noqa: E402
    build_ris_record,
    escape_ris_value,
    render_ris_bundle,
    render_ris_record,
)


def block_fields(text: str) -> dict[str, list[str]]:
    """Parse a rendered RIS block into {tag: [values...]} for easy assertions."""
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z0-9]{2})\s{2}-\s?(.*)$", line)
        if not m:
            continue
        out.setdefault(m.group(1), []).append(m.group(2))
    return out


class RisRecordCases(unittest.TestCase):
    def test_journal_article_full_metadata(self):
        record = {
            "evidence_id": "pmid:12345678",
            "pmid": "12345678",
            "doi": "10.1000/example",
            "pmcid": "PMC1234567",
            "title": "A randomized trial of X",
            "journal": "Journal of Examples",
            "publication_date": "2020-05-14",
            "authors": ["Smith JA", "Doe RB"],
            "volume": "12",
            "issue": "3",
            "pages": "101-115",
            "abstract": "We studied X in Y.",
        }
        rec = build_ris_record(record)
        self.assertEqual(rec.ris_type, "JOUR")
        text = render_ris_record(rec)
        fields = block_fields(text)
        self.assertEqual(fields["TY"], ["JOUR"])
        self.assertEqual(fields["AU"], ["Smith, J. A.", "Doe, R. B."])
        self.assertEqual(fields["TI"], ["A randomized trial of X"])
        self.assertEqual(fields["T2"], ["Journal of Examples"])
        self.assertEqual(fields["PY"], ["2020"])
        self.assertEqual(fields["DA"], ["2020/05/14"])
        self.assertEqual(fields["VL"], ["12"])
        self.assertEqual(fields["IS"], ["3"])
        self.assertEqual(fields["SP"], ["101"])
        self.assertEqual(fields["EP"], ["115"])
        self.assertEqual(fields["DO"], ["10.1000/example"])
        self.assertEqual(fields["AN"], ["12345678"])
        self.assertEqual(fields["AB"], ["We studied X in Y."])
        self.assertTrue(any("PMC1234567" in n for n in fields["N1"]))
        self.assertTrue(text.rstrip("\n").endswith("ER  - "))

    def test_book(self):
        record = {
            "evidence_id": "doi:10.1/book1",
            "title": "Principles of Testing",
            "source": "book",
            "authors": ["Author AB"],
            "publication_date": "2015",
        }
        rec = build_ris_record(record)
        self.assertEqual(rec.ris_type, "BOOK")
        fields = block_fields(render_ris_record(rec))
        self.assertEqual(fields["TY"], ["BOOK"])
        self.assertEqual(fields["PY"], ["2015"])
        self.assertEqual(fields["DA"], ["2015"])  # year only, no partial guess

    def test_book_chapter(self):
        record = {
            "evidence_id": "doi:10.1/chap1",
            "title": "Chapter Nine",
            "article_types": ["Book Chapter"],
            "journal": "Handbook of Things",
            "authors": ["Writer CD"],
        }
        rec = build_ris_record(record)
        self.assertEqual(rec.ris_type, "CHAP")

    def test_preprint_biorxiv(self):
        record = {
            "evidence_id": "doi:10.1101/2021.01.01.000001",
            "title": "A preprint about Z",
            "is_preprint": True,
            "doi": "10.1101/2021.01.01.000001",
            "authors": ["Prep AB"],
        }
        rec = build_ris_record(record)
        self.assertEqual(rec.ris_type, "UNPB")
        fields = block_fields(render_ris_record(rec))
        self.assertNotIn("T2", fields)
        self.assertTrue(any("Preprint" in n for n in fields["N1"]))

    def test_guideline_no_journal(self):
        record = {
            "evidence_id": "url:example-guideline",
            "title": "Clinical Guideline for Q",
            "source": "guideline",
            "authors": [{"collective": "World Health Organization"}],
        }
        rec = build_ris_record(record)
        self.assertEqual(rec.ris_type, "GEN")
        fields = block_fields(render_ris_record(rec))
        self.assertEqual(fields["AU"], ["World Health Organization"])

    def test_single_page_no_range(self):
        record = {"evidence_id": "pmid:1", "title": "T", "journal": "J", "pages": "45"}
        rec = build_ris_record(record)
        self.assertEqual(rec.start_page, "45")
        self.assertIsNone(rec.end_page)
        fields = block_fields(render_ris_record(rec))
        self.assertEqual(fields["SP"], ["45"])
        self.assertNotIn("EP", fields)

    def test_page_range_hyphen(self):
        record = {"evidence_id": "pmid:2", "title": "T", "journal": "J", "pages": "101-115"}
        rec = build_ris_record(record)
        self.assertEqual((rec.start_page, rec.end_page), ("101", "115"))

    def test_page_range_endash(self):
        record = {"evidence_id": "pmid:3", "title": "T", "journal": "J", "pages": "101–115"}
        rec = build_ris_record(record)
        self.assertEqual((rec.start_page, rec.end_page), ("101", "115"))

    def test_structured_authors_with_collective(self):
        record = {
            "evidence_id": "pmid:4",
            "title": "T",
            "journal": "J",
            "authors_structured": [
                {"family": "Garcia", "given": "Maria"},
                {"collective": "Consortium for Research"},
            ],
        }
        rec = build_ris_record(record)
        self.assertEqual(rec.authors, ["Garcia, Maria", "Consortium for Research"])

    def test_string_form_authors_only(self):
        record = {
            "evidence_id": "pmid:5",
            "title": "T",
            "journal": "J",
            "authors": ["Smith JA", "Lee K"],
        }
        rec = build_ris_record(record)
        self.assertEqual(rec.authors, ["Smith, J. A.", "Lee, K."])

    def test_unicode_passes_through_unescaped(self):
        record = {
            "evidence_id": "pmid:6",
            "title": "Étude sur la résistance",
            "journal": "J",
            "authors": [{"family": "Müller", "given": "Anke"},
                        {"family": "李", "given": "雷"}],
        }
        rec = build_ris_record(record)
        text = render_ris_record(rec)
        self.assertIn("Étude sur la résistance", text)
        self.assertIn("Müller, Anke", text)
        self.assertIn("李, 雷", text)

    def test_multiline_abstract_collapses_to_one_line(self):
        record = {
            "evidence_id": "pmid:7",
            "title": "T",
            "journal": "J",
            "abstract": "Paragraph one.\n\nParagraph two.\r\nParagraph three.",
        }
        rec = build_ris_record(record)
        text = render_ris_record(rec)
        fields = block_fields(text)
        self.assertEqual(len(fields["AB"]), 1)
        self.assertEqual(
            fields["AB"][0],
            "Paragraph one.  Paragraph two. Paragraph three.",
        )
        self.assertNotIn("\n\n", text.split("AB  -")[1].split("\n", 1)[0])

    def test_missing_identifiers_no_do_no_an(self):
        record = {"evidence_id": "url:x", "title": "T", "journal": "J"}
        rec = build_ris_record(record)
        fields = block_fields(render_ris_record(rec))
        self.assertNotIn("DO", fields)
        self.assertNotIn("AN", fields)
        self.assertNotIn("UR", fields)  # nothing to resolve a URL from

    def test_url_fallback_from_pmid(self):
        record = {"evidence_id": "pmid:9", "title": "T", "journal": "J", "pmid": "9"}
        rec = build_ris_record(record)
        self.assertEqual(rec.url, "https://pubmed.ncbi.nlm.nih.gov/9/")

    def test_url_fallback_from_doi(self):
        record = {"evidence_id": "doi:10.1/x", "title": "T", "journal": "J", "doi": "10.1/x"}
        rec = build_ris_record(record)
        self.assertEqual(rec.url, "https://doi.org/10.1/x")

    def test_url_fallback_from_pmcid(self):
        record = {"evidence_id": "pmcid:PMC1", "title": "T", "journal": "J", "pmcid": "PMC1"}
        rec = build_ris_record(record)
        self.assertEqual(rec.url, "https://pmc.ncbi.nlm.nih.gov/articles/PMC1/")

    def test_retraction_note(self):
        record = {
            "evidence_id": "pmid:10",
            "title": "T",
            "journal": "J",
            "retraction_status": "retracted",
        }
        rec = build_ris_record(record)
        fields = block_fields(render_ris_record(rec))
        self.assertIn("RETRACTED.", fields["N1"])

    def test_expression_of_concern_note(self):
        record = {
            "evidence_id": "pmid:11",
            "title": "T",
            "journal": "J",
            "retraction_status": "expression_of_concern",
        }
        rec = build_ris_record(record)
        fields = block_fields(render_ris_record(rec))
        self.assertIn("Expression of Concern.", fields["N1"])

    def test_preprint_note(self):
        record = {
            "evidence_id": "doi:10.1101/x",
            "title": "T",
            "is_preprint": True,
        }
        rec = build_ris_record(record)
        fields = block_fields(render_ris_record(rec))
        self.assertIn("Preprint. Not peer reviewed.", fields["N1"])

    def test_no_fabricated_date(self):
        record = {"evidence_id": "pmid:12", "title": "T", "journal": "J"}
        rec = build_ris_record(record)
        self.assertIsNone(rec.year)
        fields = block_fields(render_ris_record(rec))
        self.assertNotIn("PY", fields)
        self.assertNotIn("DA", fields)

    def test_render_ris_bundle_three_records(self):
        records = [
            build_ris_record({"evidence_id": f"pmid:{i}", "title": f"T{i}", "journal": "J"})
            for i in range(1, 4)
        ]
        bundle = render_ris_bundle(records)
        self.assertEqual(bundle.count("TY  -"), 3)
        self.assertEqual(bundle.count("ER  - "), 3)
        self.assertNotIn("\n\n\n", bundle)
        self.assertFalse(bundle.endswith("\n\n"))

    def test_embedded_newline_in_title_collapses(self):
        record = {
            "evidence_id": "pmid:13",
            "title": "Line one\nLine two",
            "journal": "J",
        }
        rec = build_ris_record(record)
        text = render_ris_record(rec)
        fields = block_fields(text)
        self.assertEqual(fields["TI"], ["Line one Line two"])
        self.assertEqual(text.count("TI  -"), 1)
        self.assertEqual(text.count("ER  - "), 1)


class EscapeRisValueCases(unittest.TestCase):
    def test_collapses_crlf(self):
        self.assertEqual(escape_ris_value("a\r\nb"), "a b")

    def test_collapses_cr(self):
        self.assertEqual(escape_ris_value("a\rb"), "a b")

    def test_collapses_lf(self):
        self.assertEqual(escape_ris_value("a\nb"), "a b")

    def test_strips_whitespace(self):
        self.assertEqual(escape_ris_value("  a  "), "a")

    def test_none_becomes_empty_string(self):
        self.assertEqual(escape_ris_value(None), "")


if __name__ == "__main__":
    unittest.main()
