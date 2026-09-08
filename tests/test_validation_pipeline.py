from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import ROOT, load_script, make_run, minimal_corpus_record, run_py, write_json, write_jsonl


store = load_script("store.py")


class ValidationPipelineSmokeTest(unittest.TestCase):
    def test_assembler_verify_and_eval_commands_exercise_kernel(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            text = "Methods. The trial enrolled 42 adults. Results were reported."
            snap = store.write_snapshot(
                run, url="https://example.org/fulltext", text=text, title="Full text",
                access="full_text", origin="pubmed",
                paper={"pmid": "12345678", "doi": "10.1000/validation",
                       "pmcid": "PMC1234567"},
                event_type="fetch", fresh=True, actor="test")
            start = text.index("trial enrolled")
            end = text.index(". Results")
            rec = minimal_corpus_record()
            rec["source_ids"] = [snap["source_id"]]
            write_jsonl(run / "corpus.jsonl", [rec])
            write_json(run / "workspace" / "extractions" / "pmid-12345678.json", {
                "evidence_id": "pmid:12345678",
                "spans": [{"claim": "The trial enrolled 42 adults",
                           "source_id": snap["source_id"], "start": start, "end": end,
                           "text": text[start:end]}],
            })
            (run / "outputs" / "report.md").write_text(
                "# Validation report\n\n"
                "The trial enrolled 42 adults.[^pubmed-12345678]\n\n"
                "## References\n\n"
                "[^pubmed-12345678]: Smith JA. Journal of Validation. PMID "
                "12345678. DOI 10.1000/validation. PubMed "
                "https://pubmed.ncbi.nlm.nih.gov/12345678/\n",
                encoding="utf-8")

            assembled = run_py(["scripts/assemble.py", "run", "--run-dir", str(run)], cwd=ROOT)
            self.assertEqual(assembled.returncode, 0, assembled.stderr + assembled.stdout)
            result = json.loads((run / "outputs" / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["counts"]["unresolved"], 0)

            verified = run_py([
                "scripts/verify.py", "run", "--run-dir", str(run), "--report",
                str(run / "outputs" / "report.md"), "--json",
            ], cwd=ROOT)
            self.assertIn(verified.returncode, (0, 1), verified.stderr + verified.stdout)
            payload = json.loads(verified.stdout)
            checks = {c["check_id"]: c["status"] for c in payload["checks"]}
            self.assertEqual(checks["C-SNAPSHOT"], "pass")
            self.assertEqual(checks["C-SPAN"], "pass")
            self.assertEqual(checks["C-FRESH-FETCH"], "pass")
            self.assertEqual(checks["C-ASSEMBLER"], "pass")

        with TemporaryDirectory() as out:
            proc = run_py([
                "scripts/eval.py", "--cases", "eval/cases.json", "--out", out,
            ], cwd=ROOT)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            summary = json.loads(proc.stdout)
            self.assertTrue(summary["ok"], summary)
            self.assertGreaterEqual(summary["passed"], 4)

    def test_verify_gate_controls_missing_assembler_result(self):
        for gate_flag, expected in ((None, "skipped"), ("--gate", "fail")):
            with self.subTest(gate=gate_flag), TemporaryDirectory() as td:
                _wiki, run = make_run(Path(td))
                write_jsonl(run / "corpus.jsonl", [])
                report = run / "outputs" / "report.md"
                report.write_text("# Empty report\n\n## References\n\n", encoding="utf-8")
                args = ["scripts/verify.py", "run", "--run-dir", str(run),
                        "--report", str(report), "--json"]
                if gate_flag:
                    args.append(gate_flag)
                proc = run_py(args, cwd=ROOT)
                self.assertIn(proc.returncode, (0, 1), proc.stderr + proc.stdout)
                payload = json.loads(proc.stdout)
                checks = {c["check_id"]: c["status"] for c in payload["checks"]}
                self.assertEqual(checks["C-ASSEMBLER"], expected)

    def test_okf_promote_check_writes_nothing_and_blocks_tamper(self):
        with TemporaryDirectory() as td:
            wiki, run = make_run(Path(td))
            init = run_py(["scripts/okf.py", "init", "--wiki", str(wiki)], cwd=ROOT)
            self.assertEqual(init.returncode, 0, init.stderr + init.stdout)
            text = "Methods. The trial enrolled 42 adults."
            snap = store.write_snapshot(
                run, url="https://example.org/fulltext", text=text, title="Full text",
                access="full_text", origin="pubmed",
                paper={"pmid": "12345678", "doi": "10.1000/validation",
                       "pmcid": "PMC1234567"},
                event_type="fetch", fresh=True, actor="test")
            start = text.index("trial enrolled")
            end = len(text) - 1
            rec = minimal_corpus_record()
            rec["source_ids"] = [snap["source_id"]]
            write_jsonl(run / "corpus.jsonl", [rec])
            write_json(run / "workspace" / "extractions" / "case.json", {
                "evidence_id": "pmid:12345678",
                "spans": [{"claim": "The trial enrolled 42 adults",
                           "source_id": snap["source_id"], "start": start,
                           "end": end, "text": text[start:end]}],
            })
            assembled = run_py(["scripts/assemble.py", "run", "--run-dir", str(run)], cwd=ROOT)
            self.assertEqual(assembled.returncode, 0, assembled.stderr + assembled.stdout)
            before = sorted(str(p.relative_to(wiki)) for p in (wiki / "research").rglob("*"))
            check = run_py([
                "scripts/okf.py", "promote", "--run-dir", str(run), "--wiki", str(wiki),
                "--allow-unverified", "--check",
            ], cwd=ROOT)
            self.assertEqual(check.returncode, 0, check.stderr + check.stdout)
            after = sorted(str(p.relative_to(wiki)) for p in (wiki / "research").rglob("*"))
            self.assertEqual(after, before)

            source_path = run / "sources" / f"{snap['source_id']}.json"
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            payload["text"] += "\nTampered."
            source_path.write_text(json.dumps(payload), encoding="utf-8")
            blocked = run_py([
                "scripts/okf.py", "promote", "--run-dir", str(run), "--wiki", str(wiki),
                "--allow-unverified", "--check",
            ], cwd=ROOT)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("SNAPSHOT_HASH_MISMATCH", blocked.stdout + blocked.stderr)


if __name__ == "__main__":
    unittest.main()
