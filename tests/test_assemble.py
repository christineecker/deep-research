from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, make_run, minimal_corpus_record, write_json, write_jsonl


store = load_script("store.py")
assemble = load_script("assemble.py")


class AssemblerArchitectureTest(unittest.TestCase):
    def _accepted_fixture(self, run: Path):
        text = "Methods. The trial enrolled 42 adults and followed them for 12 weeks."
        snap = store.write_snapshot(
            run, url="https://example.org/fulltext", text=text, title="Full text",
            access="full_text", origin="pubmed",
            paper={"pmid": "12345678", "doi": "10.1000/validation",
                   "pmcid": "PMC1234567"},
            event_type="fetch", fresh=True, actor="test")
        start = text.index("trial enrolled")
        end = text.index(" and followed")
        rec = minimal_corpus_record()
        rec["source_ids"] = [snap["source_id"]]
        write_jsonl(run / "corpus.jsonl", [rec])
        return snap, text, start, end

    def test_assembler_accepts_only_snapshot_resliced_artifacts(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            snap, text, start, end = self._accepted_fixture(run)
            write_json(run / "workspace" / "extractions" / "pmid-12345678.json", {
                "evidence_id": "pmid:12345678",
                "design": "trial",
                "spans": [{"claim": "The trial enrolled 42 adults",
                           "source_id": snap["source_id"], "start": start, "end": end,
                           "text": text[start:end], "access": "full_text"}],
                "quotes": [{"text": text[start:end], "source_id": snap["source_id"],
                            "start": start, "end": end}],
            })

            result = assemble.Assembler(run).run()
            self.assertEqual(result["counts"]["accepted"], 1)
            self.assertEqual(result["counts"]["unresolved"], 0)
            accepted = result["accepted"][0]
            self.assertEqual(accepted["quotes"][0]["text"], text[start:end])
            self.assertEqual(accepted["url"], "https://example.org/fulltext")
            self.assertTrue(accepted["fresh"])

    def test_assembler_rejects_legacy_quotes_and_mismatched_excerpts(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            snap, text, start, end = self._accepted_fixture(run)
            write_json(run / "workspace" / "extractions" / "legacy.json", {
                "evidence_id": "pmid:12345678",
                "design": "trial",
                "quotes": [{"text": "The trial enrolled 42 adults."}],
            })
            result = assemble.Assembler(run).run()
            self.assertEqual(result["diagnostics"]["counts_by_reason"], {"NO_SPANS": 1})

            (run / "workspace" / "extractions" / "legacy.json").unlink()
            write_json(run / "workspace" / "extractions" / "badquote.json", {
                "evidence_id": "pmid:12345678",
                "spans": [{"claim": "The trial enrolled 42 adults",
                           "source_id": snap["source_id"], "start": start, "end": end,
                           "text": text[start:end]}],
                "quotes": [{"text": "The trial enrolled 41 adults",
                            "source_id": snap["source_id"], "start": start, "end": end}],
            })
            result = assemble.Assembler(run).run()
            self.assertEqual(result["diagnostics"]["counts_by_reason"],
                             {"EXCERPT_MISMATCH": 1})

    def test_assembler_enforces_fresh_fetch_rule(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            text = "Methods. The trial enrolled 42 adults."
            snap = store.register_text(
                run, url="https://example.org/cached", text=text, title="Cached",
                access="full_text", origin="pubmed",
                paper={"pmid": "12345678", "doi": "10.1000/validation",
                       "pmcid": "PMC1234567"},
                actor="test")
            start = text.index("trial enrolled")
            end = len(text) - 1
            rec = minimal_corpus_record()
            rec["source_ids"] = [snap["source_id"]]
            write_jsonl(run / "corpus.jsonl", [rec])
            write_json(run / "workspace" / "extractions" / "cached.json", {
                "evidence_id": "pmid:12345678",
                "spans": [{"claim": "The trial enrolled 42 adults",
                           "source_id": snap["source_id"], "start": start, "end": end,
                           "text": text[start:end]}],
            })

            result = assemble.Assembler(run).run()
            self.assertEqual(result["diagnostics"]["counts_by_reason"],
                             {"NO_FRESH_FETCH": 1})

    def test_assembler_rejects_unknown_range_overlong_and_strict_gate_fails(self):
        cases = [
            ("unknown", "src-" + ("0" * 64), 0, 5, "UNKNOWN_SOURCE"),
            ("range", None, -1, 5, "SPAN_OUT_OF_RANGE"),
            ("overlong", None, 0, store.MAX_SPAN_CHARS + 1, "SPAN_TOO_LONG"),
        ]
        for name, source_id, start, end, reason in cases:
            with self.subTest(name=name), TemporaryDirectory() as td:
                _wiki, run = make_run(Path(td))
                if source_id is None:
                    text = "x" * max(end + 1, store.MAX_SPAN_CHARS + 2)
                    snap = store.write_snapshot(
                        run, url=f"https://example.org/{name}", text=text, title=name,
                        access="full_text", origin="pubmed",
                        paper={"pmid": "12345678", "doi": "10.1000/validation",
                               "pmcid": "PMC1234567"},
                        event_type="fetch", fresh=True, actor="test")
                    source_id = snap["source_id"]
                rec = minimal_corpus_record()
                rec["source_ids"] = [source_id]
                write_jsonl(run / "corpus.jsonl", [rec])
                write_json(run / "workspace" / "extractions" / f"{name}.json", {
                    "evidence_id": "pmid:12345678",
                    "spans": [{"claim": "Claim", "source_id": source_id,
                               "start": start, "end": end}],
                })
                result = assemble.Assembler(run, strict=True).run()
                self.assertEqual(result["gate"]["verdict"], "fail")
                self.assertEqual(result["diagnostics"]["counts_by_reason"], {reason: 1})

    def test_report_claim_outside_accepted_is_unresolved_and_result_is_written(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            write_jsonl(run / "corpus.jsonl", [minimal_corpus_record()])
            (run / "outputs" / "report.md").write_text(
                "# Report\n\nA cited claim.[^pubmed-12345678]\n\n"
                "[^pubmed-12345678]: PMID 12345678. DOI 10.1000/validation.\n",
                encoding="utf-8")
            asm = assemble.Assembler(run)
            result = asm.run()
            out = assemble.write_result(result, run / "outputs" / "result.json")
            self.assertTrue(out.is_file())
            self.assertEqual(result["diagnostics"]["counts_by_reason"],
                             {"SOURCE_OUTSIDE_ACCEPTED": 1})


if __name__ == "__main__":
    unittest.main()
