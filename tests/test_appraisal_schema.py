"""C-APPRAISAL-SCHEMA: tool-specific appraisal-record contract enforcement (schema §8),
beyond the generic span-debt / rendering checks that other tool test files exercise.

Each test writes one appraisal record to `workspace/appraisals/` and asserts the verifier
rejects it, with a reason that names the actual defect.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import ROOT, make_run, minimal_corpus_record, run_py, write_json, write_jsonl


def _run_verify(run: Path) -> dict:
    report = run / "outputs" / "report.md"
    report.write_text("# Report\n\n## References\n\n", encoding="utf-8")
    proc = run_py([
        "scripts/verify.py", "run", "--run-dir", str(run),
        "--report", str(report), "--json",
    ], cwd=ROOT)
    return json.loads(proc.stdout)


def _appraisal_check(payload: dict) -> dict:
    return next(c for c in payload["checks"] if c["check_id"] == "C-APPRAISAL-SCHEMA")


def _write(run: Path, pmid: str, record: dict) -> None:
    write_json(run / "workspace" / "appraisals" / f"pmid-{pmid}.json", record)


def _base(**overrides) -> dict:
    rec = {
        "schema_version": 1,
        "pmid": "12345678",
        "evidence_id": "pmid:12345678",
        "tool": "RoB2",
        "tool_variant": None,
        "appraisal_target": None,
        "domains": [
            {"domain": "Randomization process", "judgement": "low",
             "rationale": "Computer-generated sequence.", "spans": []},
            {"domain": "Deviations from intended interventions", "judgement": "low",
             "rationale": "Blinded, ITT.", "spans": []},
            {"domain": "Missing outcome data", "judgement": "low",
             "rationale": "No attrition.", "spans": []},
            {"domain": "Measurement of the outcome", "judgement": "low",
             "rationale": "Objective outcome.", "spans": []},
            {"domain": "Selection of the reported result", "judgement": "unclear",
             "rationale": "No protocol cited.", "spans": []},
        ],
        "overall_judgement": "low",
        "grade": None,
        "evidence_basis": "fulltext",
    }
    rec.update(overrides)
    return rec


class AppraisalSchemaNegativeTest(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        _, self.run = make_run(Path(self._td.name))
        write_jsonl(self.run / "corpus.jsonl", [minimal_corpus_record()])

    def test_quadas2_missing_appraisal_target(self):
        rec = _base(tool="QUADAS-2", appraisal_target=None, domains=[
            {"domain": "Patient selection", "domain_group": "risk_of_bias", "judgement": "low",
             "rationale": "Consecutive enrollment.", "spans": []},
            {"domain": "Index test", "domain_group": "risk_of_bias", "judgement": "unclear",
             "rationale": "Not described.", "spans": []},
            {"domain": "Reference standard", "domain_group": "risk_of_bias", "judgement": "low",
             "rationale": "Adjudicated.", "spans": []},
            {"domain": "Flow and timing", "domain_group": "risk_of_bias", "judgement": "unclear",
             "rationale": "Not reported.", "spans": []},
            {"domain": "Patient selection", "domain_group": "applicability", "judgement": "low",
             "rationale": "Matches question.", "spans": []},
            {"domain": "Index test", "domain_group": "applicability", "judgement": "low",
             "rationale": "Matches question.", "spans": []},
            {"domain": "Reference standard", "domain_group": "applicability", "judgement": "low",
             "rationale": "Matches question.", "spans": []},
        ], overall_judgement="low")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("requires appraisal_target", check["detail"])

    def test_quadas2_missing_domain_group(self):
        target = {"target_type": "index_test", "index_test": "Troponin I",
                  "reference_standard": "Adjudicated diagnosis", "population": "ED adults"}
        rec = _base(tool="QUADAS-2", appraisal_target=target, domains=[
            {"domain": "Patient selection", "domain_group": None, "judgement": "low",
             "rationale": "Consecutive enrollment.", "spans": []},
            {"domain": "Index test", "domain_group": "risk_of_bias", "judgement": "unclear",
             "rationale": "Not described.", "spans": []},
            {"domain": "Reference standard", "domain_group": "risk_of_bias", "judgement": "low",
             "rationale": "Adjudicated.", "spans": []},
            {"domain": "Flow and timing", "domain_group": "risk_of_bias", "judgement": "unclear",
             "rationale": "Not reported.", "spans": []},
            {"domain": "Patient selection", "domain_group": "applicability", "judgement": "low",
             "rationale": "Matches question.", "spans": []},
            {"domain": "Index test", "domain_group": "applicability", "judgement": "low",
             "rationale": "Matches question.", "spans": []},
            {"domain": "Reference standard", "domain_group": "applicability", "judgement": "low",
             "rationale": "Matches question.", "spans": []},
        ], overall_judgement="low")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("domain_group", check["detail"])

    def test_quadas2_wrong_domain_count(self):
        target = {"target_type": "index_test", "index_test": "Troponin I",
                  "reference_standard": "Adjudicated diagnosis", "population": "ED adults"}
        rec = _base(tool="QUADAS-2", appraisal_target=target, domains=[
            {"domain": "Patient selection", "domain_group": "risk_of_bias", "judgement": "low",
             "rationale": "Consecutive enrollment.", "spans": []},
        ] * 6, overall_judgement="low")  # 6, not 7
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("exactly 7 domains", check["detail"])

    def test_probast_wrong_target_type(self):
        target = {"target_type": "index_test", "target_id": "CHA2DS2-VASc",
                  "population": "AF patients", "outcome": "stroke",
                  "prediction_horizon": "1 year"}
        rec = _base(tool="PROBAST", appraisal_target=target, domains=[
            {"domain": "Participants", "domain_group": "risk_of_bias", "judgement": "low",
             "rationale": "Representative cohort.", "spans": []},
            {"domain": "Predictors", "domain_group": "risk_of_bias", "judgement": "low",
             "rationale": "Assessed blind to outcome.", "spans": []},
            {"domain": "Outcome", "domain_group": "risk_of_bias", "judgement": "low",
             "rationale": "Predefined.", "spans": []},
            {"domain": "Analysis", "domain_group": "risk_of_bias", "judgement": "low",
             "rationale": "Adequate EPV.", "spans": []},
            {"domain": "Participants", "domain_group": "applicability", "judgement": "low",
             "rationale": "Matches question.", "spans": []},
            {"domain": "Predictors", "domain_group": "applicability", "judgement": "low",
             "rationale": "Matches question.", "spans": []},
            {"domain": "Outcome", "domain_group": "applicability", "judgement": "low",
             "rationale": "Matches question.", "spans": []},
        ], overall_judgement="low")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("target_type must be", check["detail"])

    def test_casp_qualitative_wrong_item_count(self):
        rec = _base(tool="CASP-qualitative", appraisal_target=None, domains=[
            {"domain": f"Item {i}", "domain_group": None, "judgement": "yes",
             "rationale": "Adequate.", "spans": []}
            for i in range(1, 9)  # 8, not 10
        ], overall_judgement="8/10")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("exactly 10 domains", check["detail"])

    def test_jbi_prevalence_out_of_9_mismatch(self):
        rec = _base(tool="JBI-prevalence", appraisal_target=None, domains=[
            {"domain": f"Item {i}", "domain_group": None, "judgement": "yes",
             "rationale": "Adequate.", "spans": []}
            for i in range(1, 10)  # 9 items, correct count
        ], overall_judgement="9/8")  # wrong denominator
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("overall_judgement", check["detail"])

    def test_jbi_cross_sectional_appraisal_record(self):
        """Not only cross-sectional extraction (see test_jbi.py) -- an appraisal record too."""
        rec = _base(tool="JBI-cross-sectional", appraisal_target=None, domains=[
            {"domain": f"Item {i}", "domain_group": None, "judgement": "no",
             "rationale": "Not addressed.", "spans": []}
            for i in range(1, 7)  # 6, not 8
        ], overall_judgement="0/8")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("JBI-cross-sectional requires exactly 8 domains", check["detail"])

    def test_tool_none_with_nonempty_domains(self):
        rec = _base(tool="none", appraisal_target=None, domains=[
            {"domain": "Randomization process", "judgement": "low",
             "rationale": "Computer-generated sequence.", "spans": []},
        ], overall_judgement="unclear")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn('tool="none" requires domains=[]', check["detail"])

    def test_abstract_only_with_rob2(self):
        rec = _base(tool="RoB2", evidence_basis="abstract_only", overall_judgement="low")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("abstract_only record must have", check["detail"])

    def test_abstract_only_with_quadas2(self):
        target = {"target_type": "index_test", "index_test": "Troponin I",
                  "reference_standard": "Adjudicated diagnosis", "population": "ED adults"}
        rec = _base(tool="QUADAS-2", appraisal_target=target, evidence_basis="abstract_only",
                     domains=[], overall_judgement="unclear")
        # tool is not "none", so this must still fail even with domains=[] / overall="unclear"
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("abstract_only record must have", check["detail"])

    def test_abstract_only_with_casp(self):
        rec = _base(tool="CASP-qualitative", appraisal_target=None,
                     evidence_basis="abstract_only", domains=[], overall_judgement="unclear")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("abstract_only record must have", check["detail"])

    def test_abstract_only_with_jbi(self):
        rec = _base(tool="JBI-prevalence", appraisal_target=None,
                     evidence_basis="abstract_only", domains=[], overall_judgement="unclear")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("abstract_only record must have", check["detail"])

    def test_invalid_judgement_token(self):
        rec = _base(overall_judgement="low")
        rec["domains"][0]["judgement"] = "probably_fine"
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("not valid for RoB2", check["detail"])

    def test_unknown_tool_enum_rejected(self):
        rec = _base(tool="Cochrane-EPOC", appraisal_target=None, domains=[],
                     overall_judgement="unclear")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "fail")
        self.assertIn("not a recognized enum value", check["detail"])


class AppraisalSchemaPositiveTest(unittest.TestCase):
    """A well-formed record of each shape must still pass -- the point is not to over-fire."""

    def setUp(self):
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        _, self.run = make_run(Path(self._td.name))
        write_jsonl(self.run / "corpus.jsonl", [minimal_corpus_record()])

    def test_valid_rob2_record_passes(self):
        _write(self.run, "12345678", _base())
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "pass", check["detail"])

    def test_valid_rob2_with_appraisal_target_passes(self):
        rec = _base(appraisal_target={
            "target_type": "outcome", "outcome": "24-week response",
            "population": "Adults with MDD",
        })
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "pass", check["detail"])

    def test_valid_abstract_only_none_record_passes(self):
        rec = _base(tool="none", appraisal_target=None, domains=[],
                     evidence_basis="abstract_only", overall_judgement="unclear")
        _write(self.run, "12345678", rec)
        check = _appraisal_check(_run_verify(self.run))
        self.assertEqual(check["status"], "pass", check["detail"])


if __name__ == "__main__":
    unittest.main()
