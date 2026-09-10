"""JBI prevalence / analytical cross-sectional checklist support.

Mirrors `tests/test_casp_qualitative.py`. `JBI-prevalence` and `JBI-cross-sectional`
(`references/appraisal.md` §9, `SCIENTIFIC_FRAMEWORKS_OPTIMIZATION_PLAN.md` Phase 5 /
recommended-order step 10 — JBI chosen over AXIS per explicit user direction) share CASP's flat
shape: no risk-of-bias/applicability split (`domain_group` null), no `appraisal_target`, a
`yes`-count overall judgement. Both run through the same generic, tool-agnostic code in
`assemble.appraisal_artifact` and `html_report.h_card`.
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


def _jbi_prevalence_record(*, complete: bool) -> dict:
    span = {"claim": "National insurance claims database, simple random sample, n=12000.",
            "evidence_id": "pmid:77701234", "source_id": "src-jkl012", "start": 20, "end": 90,
            "access": "full_text"}
    items = [
        ("1. Sample frame appropriateness", "yes", "National claims database matches target population."),
        ("2. Appropriate sampling", "yes", "Simple random sample."),
        ("3. Sample size adequacy", "yes", "n=12000, power justified."),
        ("4. Subjects/setting described", "yes", "Described in Table 1."),
        ("5. Data analysis coverage", "yes", "All sampled records analysed."),
        ("6. Valid condition identification", "unclear", "Case definition not described."),
        ("7. Standard reliable condition measurement", "yes", "ICD-10 codes applied uniformly."),
        ("8. Appropriate statistical analysis", "yes", "Prevalence with 95% CI, standard methods."),
        ("9. Adequate response rate", "yes", "Administrative data, no non-response."),
    ]
    domains = []
    for name, judgement, rationale in items:
        needs_span = judgement != "unclear"
        domains.append({
            "domain": name, "domain_group": None, "judgement": judgement,
            "rationale": rationale, "spans": [span] if (needs_span and complete) else [],
        })
    return {
        "schema_version": 1, "pmid": "77701234", "evidence_id": "pmid:77701234",
        "tool": "JBI-prevalence", "tool_variant": None, "appraisal_target": None,
        "domains": domains, "overall_judgement": "8/9" if complete else "unclear",
        "grade": None, "evidence_basis": "fulltext",
    }


class JbiPrevalenceArtifactTest(unittest.TestCase):
    def test_fully_spanned_record_owes_nothing(self):
        rec = _jbi_prevalence_record(complete=True)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-77701234.json")
        self.assertEqual(len(art.spans), 8)  # item 6 is "unclear", exempt
        self.assertIsNone(getattr(art, "no_spans_detail", None))

    def test_incomplete_record_flags_unverified_items_by_index(self):
        rec = _jbi_prevalence_record(complete=False)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-77701234.json")
        self.assertIsNotNone(art.no_spans_detail)
        self.assertIn("domains[0]", art.no_spans_detail)
        self.assertNotIn("domains[5]", art.no_spans_detail)  # item 6 "unclear" is exempt (R19)


class JbiReportRenderingTest(unittest.TestCase):
    def test_jbi_card_has_no_group_column_and_no_target(self):
        rec = minimal_corpus_record()
        app = _jbi_prevalence_record(complete=True)
        st = html_report.Study(rec, {"evidence_basis": "fulltext"}, app)
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertNotIn("<th>Group</th>", card)
        self.assertNotIn("Appraisal target", card)
        self.assertIn("8/9", card)
        self.assertIn("Sample frame appropriateness", card)
        # count-based tool: overall styled as a neutral checklist count, never a risk-of-bias
        # colour (individual per-item judgements below it still use rob-low/rob-unc/etc.)
        self.assertIn("Checklist/star count", card)
        self.assertIn('<dd class="rob rob-count">8/9</dd>', card)


def _jbi_cross_sectional_record(*, complete: bool) -> dict:
    span = {"claim": "Registry cohort, logistic regression adjusted for age and sex, n=4300.",
            "evidence_id": "pmid:77709999", "source_id": "src-mno345", "start": 40, "end": 110,
            "access": "full_text"}
    items = [
        ("1. Inclusion criteria clarity", "yes", "Registry inclusion criteria stated."),
        ("2. Subjects/setting described", "yes", "Described in Methods."),
        ("3. Exposure measured validly/reliably", "yes", "Validated questionnaire."),
        ("4. Objective, standard criteria for condition", "yes", "ICD-10 diagnosis codes."),
        ("5. Confounding factors identified", "yes", "Age, sex, comorbidity pre-specified."),
        ("6. Strategies to deal with confounders stated", "yes", "Adjusted logistic regression."),
        ("7. Outcomes measured validly/reliably", "unclear", "Outcome ascertainment not described."),
        ("8. Appropriate statistical analysis", "yes", "Adjusted OR with 95% CI reported."),
    ]
    domains = []
    for name, judgement, rationale in items:
        needs_span = judgement != "unclear"
        domains.append({
            "domain": name, "domain_group": None, "judgement": judgement,
            "rationale": rationale, "spans": [span] if (needs_span and complete) else [],
        })
    return {
        "schema_version": 1, "pmid": "77709999", "evidence_id": "pmid:77709999",
        "tool": "JBI-cross-sectional", "tool_variant": None, "appraisal_target": None,
        "domains": domains, "overall_judgement": "7/8" if complete else "unclear",
        "grade": None, "evidence_basis": "fulltext",
    }


class JbiCrossSectionalArtifactTest(unittest.TestCase):
    def test_fully_spanned_record_owes_nothing(self):
        rec = _jbi_cross_sectional_record(complete=True)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-77709999.json")
        self.assertEqual(len(art.spans), 7)  # item 7 is "unclear", exempt
        self.assertIsNone(getattr(art, "no_spans_detail", None))

    def test_incomplete_record_flags_unverified_items_by_index(self):
        rec = _jbi_cross_sectional_record(complete=False)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-77709999.json")
        self.assertIsNotNone(art.no_spans_detail)
        self.assertIn("domains[0]", art.no_spans_detail)
        self.assertNotIn("domains[6]", art.no_spans_detail)  # item 7 "unclear" is exempt (R19)


class JbiCrossSectionalReportRenderingTest(unittest.TestCase):
    def test_jbi_cross_sectional_card_has_no_group_column_and_no_target(self):
        rec = minimal_corpus_record()
        app = _jbi_cross_sectional_record(complete=True)
        st = html_report.Study(rec, {"evidence_basis": "fulltext"}, app)
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertNotIn("<th>Group</th>", card)
        self.assertNotIn("Appraisal target", card)
        self.assertIn("7/8", card)
        self.assertIn("Inclusion criteria clarity", card)


class CrossSectionalEvidenceExtractionVerifierTest(unittest.TestCase):
    """`cross_sectional_evidence` (`references/schema/07-extraction.md`) gets the same R19
    span-debt treatment as `qualitative_evidence`."""

    def _run_with_cross_sectional_evidence(self, td: Path, *, spanned: bool):
        _wiki, run = make_run(td)
        text = ("Methods. National insurance claims database, simple random sample of "
                "12000 adults. Prevalence was 8.4% (95% CI 7.9-8.9).")
        snap = store.write_snapshot(
            run, url="https://example.org/fulltext", text=text, title="Full text",
            access="full_text", origin="pubmed",
            paper={"pmid": "77701234", "doi": "10.1000/validation", "pmcid": "PMC7770123"},
            event_type="fetch", fresh=True, actor="test")
        rec = minimal_corpus_record(evidence_id="pmid:77701234")
        rec["pmid"] = "77701234"
        rec["source_ids"] = [snap["source_id"]]
        write_jsonl(run / "corpus.jsonl", [rec])
        span = {"claim": "cross_sectional_evidence: prevalence 8.4% (7.9-8.9), n=12000",
                "evidence_id": "pmid:77701234", "source_id": snap["source_id"],
                "start": text.index("Prevalence"), "end": text.index("7.9-8.9)") + len("7.9-8.9)"),
                "access": "full_text"}
        write_json(run / "workspace" / "extractions" / "pmid-77701234.json", {
            "evidence_id": "pmid:77701234",
            "design": "cross-sectional prevalence survey",
            "cross_sectional_evidence": {
                "sample_frame": "national insurance claims database",
                "sampling_method": "simple random sample",
                "sample_size": 12000,
                "prevalence_estimate": "8.4% (95% CI 7.9-8.9)",
                "spans": [span] if spanned else [],
            },
        })
        return run

    def test_cross_sectional_evidence_without_spans_is_flagged_unverified(self):
        with TemporaryDirectory() as td:
            run = self._run_with_cross_sectional_evidence(Path(td), spanned=False)
            report = run / "outputs" / "report.md"
            report.write_text("# Report\n\n## References\n\n", encoding="utf-8")
            proc = run_py([
                "scripts/verify.py", "run", "--run-dir", str(run),
                "--report", str(report), "--json",
            ], cwd=ROOT)
            self.assertIn(proc.returncode, (0, 1), proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            span_check = next(c for c in payload["checks"] if c["check_id"] == "C-SPAN")
            self.assertIn("cross_sectional_evidence", span_check["detail"])
            self.assertIn("R16", span_check["detail"])

    def test_cross_sectional_evidence_with_spans_is_not_flagged(self):
        with TemporaryDirectory() as td:
            run = self._run_with_cross_sectional_evidence(Path(td), spanned=True)
            report = run / "outputs" / "report.md"
            report.write_text("# Report\n\n## References\n\n", encoding="utf-8")
            proc = run_py([
                "scripts/verify.py", "run", "--run-dir", str(run),
                "--report", str(report), "--json",
            ], cwd=ROOT)
            self.assertIn(proc.returncode, (0, 1), proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            span_check = next(c for c in payload["checks"] if c["check_id"] == "C-SPAN")
            self.assertNotIn("cross_sectional_evidence", span_check["detail"])


class CrossSectionalEvidenceReportRenderingTest(unittest.TestCase):
    def test_card_renders_cross_sectional_evidence_block_when_present(self):
        rec = minimal_corpus_record()
        ext = {
            "evidence_basis": "fulltext",
            "cross_sectional_evidence": {
                "sample_frame": "national insurance claims database",
                "sampling_method": "simple random sample",
                "prevalence_estimate": "8.4% (95% CI 7.9-8.9)",
            },
        }
        st = html_report.Study(rec, ext, {})
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertIn("Cross-sectional evidence", card)
        self.assertIn("national insurance claims database", card)
        self.assertIn("8.4% (95% CI 7.9-8.9)", card)

    def test_card_omits_cross_sectional_evidence_block_when_absent(self):
        rec = minimal_corpus_record()
        ext = {"evidence_basis": "fulltext", "design": "RCT"}
        st = html_report.Study(rec, ext, {})
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertNotIn("Cross-sectional evidence", card)


if __name__ == "__main__":
    unittest.main()
