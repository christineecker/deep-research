"""PROBAST support: appraisal-artifact span duties and report rendering.

Mirrors `tests/test_quadas2.py`. PROBAST (`references/appraisal.md` §6,
`SCIENTIFIC_FRAMEWORKS_OPTIMIZATION_PLAN.md` Phase 3 / recommended-order step 5) shares its shape
with QUADAS-2 (seven `domains[]` entries: four risk-of-bias, three applicability; `low`/`high`/
`unclear`; a required `appraisal_target`) but a different domain structure and target semantics
(one model, development vs. validation). Both run through the same generic, tool-agnostic code in
`assemble.appraisal_artifact` and `html_report.h_card` — no PROBAST-specific branch exists or
should exist in either.
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


def _probast_record(*, complete: bool) -> dict:
    span = {"claim": "Model derived from a prospective cohort of 1200 admissions.",
            "evidence_id": "pmid:98765432", "source_id": "src-def456", "start": 200, "end": 260,
            "access": "full_text"}
    domains = [
        {"domain": "Participants", "domain_group": "risk_of_bias", "judgement": "low",
         "rationale": "Appropriate data source, no distorting exclusions.",
         "spans": [span] if complete else []},
        {"domain": "Predictors", "domain_group": "risk_of_bias", "judgement": "unclear",
         "rationale": "Blinding of predictor assessment to outcome not described.", "spans": []},
        {"domain": "Outcome", "domain_group": "risk_of_bias", "judgement": "low",
         "rationale": "30-day mortality from linked registry, objective outcome.",
         "spans": [span] if complete else []},
        {"domain": "Analysis", "domain_group": "risk_of_bias",
         "judgement": "high" if complete else "unclear",
         "rationale": "8 events per predictor, no internal validation." if complete
                      else "Events-per-predictor not reported.",
         "spans": [span] if complete else []},
        {"domain": "Participants", "domain_group": "applicability", "judgement": "low",
         "rationale": "Population matches the review question.",
         "spans": [span] if complete else []},
        {"domain": "Predictors", "domain_group": "applicability", "judgement": "low",
         "rationale": "Predictors and timing match the review question.",
         "spans": [span] if complete else []},
        {"domain": "Outcome", "domain_group": "applicability", "judgement": "low",
         "rationale": "Outcome definition matches the review question.",
         "spans": [span] if complete else []},
    ]
    return {
        "schema_version": 1,
        "pmid": "98765432",
        "evidence_id": "pmid:98765432",
        "tool": "PROBAST",
        "tool_variant": None,
        "appraisal_target": {
            "target_type": "prediction_model",
            "target_id": "CAP-mortality-score, development",
            "population": "Adults hospitalized with community-acquired pneumonia",
            "outcome": "30-day mortality",
            "prediction_horizon": "30 days from admission",
        },
        "domains": domains,
        "overall_judgement": "high" if complete else "unclear",
        "grade": None,
        "evidence_basis": "fulltext",
    }


class ProbastArtifactTest(unittest.TestCase):
    def test_fully_spanned_record_owes_nothing(self):
        rec = _probast_record(complete=True)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-98765432.json")
        # 6 of 7 domains carry a non-"unclear" judgement with one span each; domains[1]
        # ("Predictors", risk of bias) is "unclear" and legitimately carries spans: [].
        self.assertEqual(len(art.spans), 6)
        self.assertIsNone(getattr(art, "no_spans_detail", None))

    def test_incomplete_record_flags_unverified_domains_by_index(self):
        rec = _probast_record(complete=False)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-98765432.json")
        self.assertIsNotNone(art.no_spans_detail)
        self.assertIn("domains[0]", art.no_spans_detail)
        self.assertIn("domains[2]", art.no_spans_detail)
        self.assertNotIn("domains[3]", art.no_spans_detail)  # unclear is exempt (R19)


class ProbastReportRenderingTest(unittest.TestCase):
    def test_probast_card_groups_domains_and_shows_target(self):
        rec = minimal_corpus_record()
        app = _probast_record(complete=True)
        st = html_report.Study(rec, {"evidence_basis": "fulltext"}, app)
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertIn("<th>Group</th>", card)
        self.assertIn("risk of bias", card)  # DOMAIN_GROUP_LABEL: human-readable, not raw enum
        self.assertIn("applicability", card)
        self.assertIn("Appraisal target", card)
        self.assertIn("CAP-mortality-score, development", card)


class PredictionModelExtractionVerifierTest(unittest.TestCase):
    """`prediction_model[]` (`references/schema/07-extraction.md`) gets the same R19 span-debt
    treatment as `outcomes[]`/`diagnostic_accuracy[]`, end to end through `verify.py run`."""

    def _run_with_prediction_model(self, td: Path, *, spanned: bool):
        _wiki, run = make_run(td)
        text = ("Methods. The model was developed in 1200 admissions with 30-day mortality "
                "as outcome. C-statistic was 0.81 (0.77-0.85), calibration slope 0.94.")
        snap = store.write_snapshot(
            run, url="https://example.org/fulltext", text=text, title="Full text",
            access="full_text", origin="pubmed",
            paper={"pmid": "98765432", "doi": "10.1000/validation", "pmcid": "PMC9876543"},
            event_type="fetch", fresh=True, actor="test")
        rec = minimal_corpus_record(evidence_id="pmid:98765432")
        rec["pmid"] = "98765432"
        rec["source_ids"] = [snap["source_id"]]
        write_jsonl(run / "corpus.jsonl", [rec])
        span = {"claim": "prediction_model: C-statistic 0.81, calibration slope 0.94",
                "evidence_id": "pmid:98765432", "source_id": snap["source_id"],
                "start": text.index("C-statistic"), "end": text.index("slope 0.94") + len("slope 0.94"),
                "access": "full_text"}
        write_json(run / "workspace" / "extractions" / "pmid-98765432.json", {
            "evidence_id": "pmid:98765432",
            "design": "prediction model development study",
            "prediction_model": [{
                "model_name": "CAP mortality score",
                "study_type": "development",
                "outcome_definition": "30-day mortality",
                "n_participants": 1200,
                "discrimination": "C-statistic 0.81 (0.77-0.85)",
                "calibration": "calibration slope 0.94",
                "spans": [span] if spanned else [],
            }],
        })
        return run

    def test_prediction_model_without_spans_is_flagged_unverified(self):
        with TemporaryDirectory() as td:
            run = self._run_with_prediction_model(Path(td), spanned=False)
            report = run / "outputs" / "report.md"
            report.write_text("# Report\n\n## References\n\n", encoding="utf-8")
            proc = run_py([
                "scripts/verify.py", "run", "--run-dir", str(run),
                "--report", str(report), "--json",
            ], cwd=ROOT)
            self.assertIn(proc.returncode, (0, 1), proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            span_check = next(c for c in payload["checks"] if c["check_id"] == "C-SPAN")
            self.assertIn("prediction_model[0]", span_check["detail"])
            self.assertIn("R16", span_check["detail"])

    def test_prediction_model_with_spans_is_not_flagged(self):
        with TemporaryDirectory() as td:
            run = self._run_with_prediction_model(Path(td), spanned=True)
            report = run / "outputs" / "report.md"
            report.write_text("# Report\n\n## References\n\n", encoding="utf-8")
            proc = run_py([
                "scripts/verify.py", "run", "--run-dir", str(run),
                "--report", str(report), "--json",
            ], cwd=ROOT)
            self.assertIn(proc.returncode, (0, 1), proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            span_check = next(c for c in payload["checks"] if c["check_id"] == "C-SPAN")
            self.assertNotIn("prediction_model[0]", span_check["detail"])


class PredictionModelReportRenderingTest(unittest.TestCase):
    def test_card_renders_prediction_model_table_when_present(self):
        rec = minimal_corpus_record()
        ext = {
            "evidence_basis": "fulltext",
            "prediction_model": [{
                "model_name": "CAP mortality score", "study_type": "development",
                "outcome_definition": "30-day mortality",
                "discrimination": "C-statistic 0.81", "calibration": "slope 0.94",
            }],
        }
        st = html_report.Study(rec, ext, {})
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertIn("Prediction model", card)
        self.assertIn("CAP mortality score", card)
        self.assertIn("C-statistic 0.81", card)

    def test_card_omits_prediction_model_table_when_absent(self):
        rec = minimal_corpus_record()
        ext = {"evidence_basis": "fulltext", "design": "RCT"}
        st = html_report.Study(rec, ext, {})
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertNotIn("Prediction model", card)


if __name__ == "__main__":
    unittest.main()
