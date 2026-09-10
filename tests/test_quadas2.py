"""QUADAS-2 support: appraisal-artifact span duties and report rendering.

Exercises the schema §8 additions from `SCIENTIFIC_FRAMEWORKS_OPTIMIZATION_PLAN.md` phases 0-3
(`appraisal_target`, `tool_variant`, `domain_group`) against generic, tool-agnostic code paths —
`assemble.appraisal_artifact` and `html_report.h_card` never special-case a tool name, so a
QUADAS-2 record must round-trip through them exactly like a RoB2 record does.
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


def _quadas2_record(*, complete: bool) -> dict:
    span = {"claim": "Consecutive patients presenting to the ED were enrolled.",
            "evidence_id": "pmid:12345678", "source_id": "src-abc123", "start": 100, "end": 160,
            "access": "full_text"}
    domains = [
        {"domain": "Patient selection", "domain_group": "risk_of_bias", "judgement": "low",
         "rationale": "Consecutive ED enrollment, no case-control enrichment.",
         "spans": [span] if complete else []},
        {"domain": "Index test", "domain_group": "risk_of_bias", "judgement": "unclear",
         "rationale": "Blinding to reference standard not described.", "spans": []},
        {"domain": "Reference standard", "domain_group": "risk_of_bias", "judgement": "low",
         "rationale": "Adjudicated diagnosis per Fourth Universal Definition.",
         "spans": [span] if complete else []},
        {"domain": "Flow and timing", "domain_group": "risk_of_bias",
         "judgement": "high" if complete else "unclear",
         "rationale": "6-week gap between index test and reference standard." if complete
                      else "Interval not reported.",
         "spans": [span] if complete else []},
        {"domain": "Patient selection", "domain_group": "applicability", "judgement": "low",
         "rationale": "Population matches the review question.",
         "spans": [span] if complete else []},
        {"domain": "Index test", "domain_group": "applicability", "judgement": "low",
         "rationale": "Index test and threshold match the review question.",
         "spans": [span] if complete else []},
        {"domain": "Reference standard", "domain_group": "applicability", "judgement": "low",
         "rationale": "Target condition matches the review question.",
         "spans": [span] if complete else []},
    ]
    return {
        "schema_version": 1,
        "pmid": "12345678",
        "evidence_id": "pmid:12345678",
        "tool": "QUADAS-2",
        "tool_variant": None,
        "appraisal_target": {
            "target_type": "index_test",
            "target_id": "troponin-I-99th-percentile",
            "population": "Adults presenting to ED with suspected ACS",
            "index_test": "High-sensitivity troponin I, single draw at presentation",
            "reference_standard": "Adjudicated final diagnosis of MI",
        },
        "domains": domains,
        "overall_judgement": "high" if complete else "unclear",
        "grade": None,
        "evidence_basis": "fulltext",
    }


class Quadas2ArtifactTest(unittest.TestCase):
    """`assemble.appraisal_artifact` treats QUADAS-2 like any other tool: no special-casing,
    but the R19 span-debt check still fires per-domain regardless of `domain_group`."""

    def test_fully_spanned_record_owes_nothing(self):
        rec = _quadas2_record(complete=True)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-12345678.json")
        # 6 of the 7 domains carry a non-"unclear" judgement with one span each; domains[1]
        # ("Index test", risk of bias) is "unclear" and legitimately carries spans: [].
        self.assertEqual(len(art.spans), 6)
        self.assertIsNone(getattr(art, "no_spans_detail", None))

    def test_incomplete_record_flags_unverified_domains_by_index(self):
        rec = _quadas2_record(complete=False)
        art = assemble.appraisal_artifact(rec, "workspace/appraisals/pmid-12345678.json")
        # domains[3] ("Flow and timing", risk_of_bias) is judged "unclear" with rationale
        # stating absent reporting, so it is legitimately unverified -- but domains[0] and
        # domains[2] are "low"/"low" with spans:[] in the incomplete fixture, which IS a gap.
        self.assertIsNotNone(art.no_spans_detail)
        self.assertIn("domains[0]", art.no_spans_detail)
        self.assertIn("domains[2]", art.no_spans_detail)
        self.assertNotIn("domains[3]", art.no_spans_detail)  # unclear is exempt (R19)


class Quadas2ReportRenderingTest(unittest.TestCase):
    """`html_report.h_card` groups domains by `domain_group` only when the record carries one,
    and stays byte-for-byte the same shape for tools (RoB2, ...) that never set it."""

    def test_quadas2_card_groups_domains_and_shows_target(self):
        rec = minimal_corpus_record()
        app = _quadas2_record(complete=True)
        st = html_report.Study(rec, {"evidence_basis": "fulltext"}, app)
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertIn("<th>Group</th>", card)
        self.assertIn("risk of bias", card)  # DOMAIN_GROUP_LABEL: human-readable, not raw enum
        self.assertIn("applicability", card)
        self.assertIn("Appraisal target", card)
        self.assertIn("troponin-I-99th-percentile", card)

    def test_rob2_card_has_no_group_column(self):
        rec = minimal_corpus_record()
        app = {
            "tool": "RoB2",
            "overall_judgement": "low",
            "evidence_basis": "fulltext",
            "domains": [
                {"domain": "Randomization process", "judgement": "low",
                 "rationale": "Computer-generated sequence.", "spans": []},
            ],
        }
        st = html_report.Study(rec, {"evidence_basis": "fulltext"}, app)
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertNotIn("<th>Group</th>", card)
        self.assertNotIn("Appraisal target", card)


class DiagnosticAccuracyExtractionVerifierTest(unittest.TestCase):
    """`diagnostic_accuracy[]` (`references/schema/07-extraction.md`) gets the same R19
    span-debt treatment as `outcomes[]`, end to end through `verify.py run`."""

    def _run_with_diagnostic_accuracy(self, td: Path, *, spanned: bool):
        _wiki, run = make_run(td)
        text = ("Methods. Consecutive ED patients received troponin I testing, "
                "then adjudicated diagnosis. Of 500 patients, 120 had confirmed MI.")
        snap = store.write_snapshot(
            run, url="https://example.org/fulltext", text=text, title="Full text",
            access="full_text", origin="pubmed",
            paper={"pmid": "12345678", "doi": "10.1000/validation", "pmcid": "PMC1234567"},
            event_type="fetch", fresh=True, actor="test")
        rec = minimal_corpus_record()
        rec["source_ids"] = [snap["source_id"]]
        write_jsonl(run / "corpus.jsonl", [rec])
        span = {"claim": "diagnostic_accuracy: troponin I vs adjudicated diagnosis, n=500",
                "evidence_id": "pmid:12345678", "source_id": snap["source_id"],
                "start": text.index("Of 500"), "end": text.index("had confirmed") + len("had confirmed"),
                "access": "full_text"}
        write_json(run / "workspace" / "extractions" / "pmid-12345678.json", {
            "evidence_id": "pmid:12345678",
            "design": "diagnostic accuracy study",
            "diagnostic_accuracy": [{
                "index_test": "Troponin I",
                "reference_standard": "Adjudicated diagnosis",
                "target_condition": "MI",
                "n_total": 500,
                "tp": 110, "fp": 20, "fn": 10, "tn": 360,
                "spans": [span] if spanned else [],
            }],
        })
        return run

    def test_diagnostic_accuracy_without_spans_is_flagged_unverified(self):
        with TemporaryDirectory() as td:
            run = self._run_with_diagnostic_accuracy(Path(td), spanned=False)
            report = run / "outputs" / "report.md"
            report.write_text("# Report\n\n## References\n\n", encoding="utf-8")
            proc = run_py([
                "scripts/verify.py", "run", "--run-dir", str(run),
                "--report", str(report), "--json",
            ], cwd=ROOT)
            self.assertIn(proc.returncode, (0, 1), proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            span_check = next(c for c in payload["checks"] if c["check_id"] == "C-SPAN")
            self.assertIn("diagnostic_accuracy[0]", span_check["detail"])
            self.assertIn("R16", span_check["detail"])

    def test_diagnostic_accuracy_with_spans_is_not_flagged(self):
        with TemporaryDirectory() as td:
            run = self._run_with_diagnostic_accuracy(Path(td), spanned=True)
            report = run / "outputs" / "report.md"
            report.write_text("# Report\n\n## References\n\n", encoding="utf-8")
            proc = run_py([
                "scripts/verify.py", "run", "--run-dir", str(run),
                "--report", str(report), "--json",
            ], cwd=ROOT)
            self.assertIn(proc.returncode, (0, 1), proc.stderr + proc.stdout)
            payload = json.loads(proc.stdout)
            span_check = next(c for c in payload["checks"] if c["check_id"] == "C-SPAN")
            self.assertNotIn("diagnostic_accuracy[0]", span_check["detail"])


class DiagnosticAccuracyReportRenderingTest(unittest.TestCase):
    def test_card_renders_diagnostic_accuracy_table_when_present(self):
        rec = minimal_corpus_record()
        ext = {
            "evidence_basis": "fulltext",
            "diagnostic_accuracy": [{
                "index_test": "Troponin I", "reference_standard": "Adjudicated diagnosis",
                "threshold": ">=99th percentile", "sensitivity": 0.92, "specificity": 0.88,
                "verification": "all_patients",
            }],
        }
        st = html_report.Study(rec, ext, {})
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertIn("Diagnostic accuracy", card)
        self.assertIn("Troponin I", card)
        self.assertIn("0.92", card)

    def test_card_omits_diagnostic_accuracy_table_when_absent(self):
        rec = minimal_corpus_record()
        ext = {"evidence_basis": "fulltext", "design": "RCT"}
        st = html_report.Study(rec, ext, {})
        card = html_report.h_card(st, links=False, kernel=html_report.Kernel())
        self.assertNotIn("Diagnostic accuracy", card)


if __name__ == "__main__":
    unittest.main()
