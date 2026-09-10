"""CASP qualitative checklist support: appraisal-artifact span duties and report rendering.

Mirrors `tests/test_quadas2.py` and `tests/test_probast.py`. CASP-qualitative
(`references/appraisal.md` §8, `SCIENTIFIC_FRAMEWORKS_OPTIMIZATION_PLAN.md` Phase 4 /
recommended-order step 7) differs in shape from QUADAS-2/PROBAST: ten flat items, no
risk-of-bias/applicability split (`domain_group` stays `null`), no `appraisal_target` (it
appraises the whole study, not a specific result), and an overall judgement that is a `yes`-count
string rather than a `low`/`high`/`unclear` worst-domain rating. All of that still runs through
the same generic, tool-agnostic code in `assemble.appraisal_artifact` and `html_report.h_card`.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import (
    ROOT,
    load_script,
    make_run,
    minimal_corpus_record,
    run_py,
    write_json,
    write_jsonl,
)


assemble = load_script("assemble.py")
html_report = load_script("html_report.py")
store = load_script("store.py")


def _casp_record(*, complete: bool) -> dict:
    span = {"claim": "Semi-structured interviews with 18 caregivers, purposive sampling.",
            "evidence_id": "pmid:55501234", "source_id": "src-ghi789", "start": 50, "end": 110,
            "access": "full_text"}
    items = [
        ("1. Clear statement of aims", "yes", "Aims stated in the introduction."),
        ("2. Qualitative methodology appropriate", "yes",
         "Thematic analysis fits the exploratory aim."),
        ("3. Research design appropriate", "yes", "Design matches the stated aims."),
        ("4. Recruitment strategy appropriate", "yes", "Purposive sampling for maximum variation."),
        ("5. Data collection addressed research issue", "yes",
         "Semi-structured interview guide aligned to aims."),
        ("6. Researcher-participant relationship considered", "unclear",
         "No reflexivity statement in the paper."),
        ("7. Ethical issues considered", "yes", "IRB approval and consent stated."),
        ("8. Data analysis sufficiently rigorous", "partial_yes",
         "Coding process described; no second coder or member checking mentioned."),
        ("9. Clear statement of findings", "yes", "Findings presented with supporting quotes."),
        ("10. Value of the research", "yes", "Discusses contribution and further questions."),
    ]
    domains = []
    for name, judgement, rationale in items:
        needs_span = judgement != "unclear"
        domains.append({
            "domain": name, "domain_group": None, "judgement": judgement,
            "rationale": rationale,
            "spans": [span] if (needs_span and complete) else [],
        })
    return {
        "schema_version": 1,
        "pmid": "55501234",
        "evidence_id": "pmid:55501234",
        "tool": "CASP-qualitative",
        "tool_variant": None,
        "appraisal_target": None,
        "domains": domains,
        "overall_judgement": "8/10" if complete else "unclear",
        "grade": None,
        "evidence_basis": "fulltext",
    }


class CaspArtifactTest(unittest.TestCase):
    def test_fully_spanned_record_owes_nothing(self):
        rec = _casp_record(complete=True)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-55501234.json")
        # 9 of 10 items are non-"unclear" and each carries one span; item 6 is "unclear"
        # and legitimately carries spans: [].
        self.assertEqual(len(art.spans), 9)
        self.assertIsNone(getattr(art, "no_spans_detail", None))

    def test_incomplete_record_flags_unverified_items_by_index(self):
        rec = _casp_record(complete=False)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-55501234.json")
        self.assertIsNotNone(art.no_spans_detail)
        self.assertIn("domains[0]", art.no_spans_detail)  # "yes" with spans:[] is a gap
        self.assertNotIn("domains[5]", art.no_spans_detail)  # item 6 "unclear" is exempt (R19)


class CaspReportRenderingTest(unittest.TestCase):
    def test_casp_card_has_no_group_column_and_no_target(self):
        rec = minimal_corpus_record()
        app = _casp_record(complete=True)
        st = html_report.Study(rec, {"evidence_basis": "fulltext"}, app)
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        # domain_group is null on every entry, so the grouped-rendering path (built for
        # QUADAS-2/PROBAST) must not kick in here -- same flat table as RoB2.
        self.assertNotIn("<th>Group</th>", card)
        self.assertNotIn("Appraisal target", card)
        self.assertIn("8/10", card)
        self.assertIn("Clear statement of aims", card)


class QualitativeEvidenceExtractionVerifierTest(unittest.TestCase):
    """`qualitative_evidence` (`references/schema/07-extraction.md`) is a single object, not an
    array, but gets the same R19 span-debt treatment as the array-shaped blocks."""

    def _run_with_qualitative_evidence(self, td: Path, *, spanned: bool):
        _wiki, run = make_run(td)
        text = ("Methods. We conducted reflexive thematic analysis of 18 semi-structured "
                "interviews with family caregivers, purposively sampled for variation in tenure.")
        snap = store.write_snapshot(
            run, url="https://example.org/fulltext", text=text, title="Full text",
            access="full_text", origin="pubmed",
            paper={"pmid": "55501234", "doi": "10.1000/validation", "pmcid": "PMC5550123"},
            event_type="fetch", fresh=True, actor="test")
        rec = minimal_corpus_record(evidence_id="pmid:55501234")
        rec["pmid"] = "55501234"
        rec["source_ids"] = [snap["source_id"]]
        write_jsonl(run / "corpus.jsonl", [rec])
        span = {"claim": "qualitative_evidence: reflexive thematic analysis, 18 interviews",
                "evidence_id": "pmid:55501234", "source_id": snap["source_id"],
                "start": text.index("reflexive"), "end": text.index("interviews") + len("interviews"),
                "access": "full_text"}
        write_json(run / "workspace" / "extractions" / "pmid-55501234.json", {
            "evidence_id": "pmid:55501234",
            "design": "qualitative study",
            "qualitative_evidence": {
                "research_question": "How do family caregivers experience respite care?",
                "methodology": "reflexive thematic analysis",
                "sampling_strategy": "purposive, maximum variation",
                "sample_size": 18,
                "data_collection_method": "semi-structured interviews",
                "analysis_approach": "reflexive thematic analysis per Braun and Clarke",
                "key_themes": "burden, relief, guilt",
                "spans": [span] if spanned else [],
            },
        })
        return run

    def test_qualitative_evidence_without_spans_is_flagged_unverified(self):
        with TemporaryDirectory() as td:
            run = self._run_with_qualitative_evidence(Path(td), spanned=False)
            report = run / "outputs" / "report.md"
            report.write_text("# Report\n\n## References\n\n", encoding="utf-8")
            proc = run_py([
                "scripts/verify.py", "run", "--run-dir", str(run),
                "--report", str(report), "--json",
            ], cwd=ROOT)
            self.assertIn(proc.returncode, (0, 1), proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            span_check = next(c for c in payload["checks"] if c["check_id"] == "C-SPAN")
            self.assertIn("qualitative_evidence", span_check["detail"])
            self.assertIn("R16", span_check["detail"])

    def test_qualitative_evidence_with_spans_is_not_flagged(self):
        with TemporaryDirectory() as td:
            run = self._run_with_qualitative_evidence(Path(td), spanned=True)
            report = run / "outputs" / "report.md"
            report.write_text("# Report\n\n## References\n\n", encoding="utf-8")
            proc = run_py([
                "scripts/verify.py", "run", "--run-dir", str(run),
                "--report", str(report), "--json",
            ], cwd=ROOT)
            self.assertIn(proc.returncode, (0, 1), proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            span_check = next(c for c in payload["checks"] if c["check_id"] == "C-SPAN")
            self.assertNotIn("qualitative_evidence", span_check["detail"])


class QualitativeEvidenceReportRenderingTest(unittest.TestCase):
    def test_card_renders_qualitative_evidence_block_when_present(self):
        rec = minimal_corpus_record()
        ext = {
            "evidence_basis": "fulltext",
            "qualitative_evidence": {
                "methodology": "reflexive thematic analysis",
                "sampling_strategy": "purposive, maximum variation",
                "data_collection_method": "semi-structured interviews",
                "analysis_approach": "Braun and Clarke",
                "researcher_reflexivity": "Not addressed in the paper.",
                "key_themes": "burden, relief, guilt",
            },
        }
        st = html_report.Study(rec, ext, {})
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertIn("Qualitative evidence", card)
        self.assertIn("reflexive thematic analysis", card)
        self.assertIn("burden, relief, guilt", card)

    def test_card_omits_qualitative_evidence_block_when_absent(self):
        rec = minimal_corpus_record()
        ext = {"evidence_basis": "fulltext", "design": "RCT"}
        st = html_report.Study(rec, ext, {})
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertNotIn("Qualitative evidence", card)


if __name__ == "__main__":
    unittest.main()
