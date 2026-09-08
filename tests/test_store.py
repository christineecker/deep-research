from __future__ import annotations

import json
import hashlib
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from helpers import load_script, make_run


store = load_script("store.py")


class StoreArchitectureTest(unittest.TestCase):
    def test_hash_helpers_match_schema_formulas(self):
        url = "https://example.org/a"
        text = "Alpha βeta"
        expected_source = "src-" + hashlib.sha256(
            url.encode("utf-8") + b"\x00" + text.encode("utf-8")).hexdigest()
        expected_content = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual(store.compute_source_id(url, text), expected_source)
        self.assertEqual(store.compute_content_hash(text), expected_content)

    def test_snapshot_identity_is_write_once_and_every_read_rehashes(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            text = "Alpha βeta.\nThe trial enrolled 42 adults."
            result = store.write_snapshot_result(
                run, url="https://example.org/a", text=text, title="A",
                access="full_text", origin="pubmed",
                paper={"pmid": "12345678", "doi": "10.1000/validation", "pmcid": None},
                event_type="fetch", fresh=True, actor="test")

            again = store.write_snapshot_result(
                run, url="https://example.org/a", text=text, title="A",
                access="full_text", origin="pubmed",
                paper={"pmid": "12345678", "doi": "10.1000/validation", "pmcid": None},
                event_type="fetch", fresh=True, actor="test")

            self.assertEqual(result["source_id"], again["source_id"])
            self.assertFalse(again["created"])
            self.assertEqual(store.read_snapshot(run, result["source_id"])["text"], text)

            path = run / "sources" / f"{result['source_id']}.json"
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["text"] += "\nTampered."
            path.write_text(json.dumps(tampered), encoding="utf-8")

            verdict = store.verify_snapshot(run, result["source_id"])
            self.assertFalse(verdict["ok"])
            self.assertEqual(verdict["reason_code"], "SNAPSHOT_HASH_MISMATCH")

    def test_span_and_fresh_fetch_rules_are_single_store_contract(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            text = "Methods. The trial enrolled 42 adults and followed them for 12 weeks."
            snap = store.write_snapshot(
                run, url="https://example.org/fulltext", text=text, title="Full text",
                access="full_text", origin="web", paper=None,
                event_type="fetch", fresh=True, actor="test")
            start = text.index("trial enrolled")
            end = text.index(" and followed")

            ok = store.verify_span(run, {
                "source_id": snap["source_id"], "start": start, "end": end,
                "text": text[start:end],
            })
            self.assertTrue(ok["ok"])
            self.assertEqual(ok["excerpt"], "trial enrolled 42 adults")
            self.assertTrue(store.has_fresh_retrieval(run, snap["source_id"]))

            bad = store.verify_span(run, {
                "source_id": snap["source_id"], "start": start, "end": end,
                "text": "trial enrolled 41 adults",
            })
            self.assertFalse(bad["ok"])
            self.assertEqual(bad["reason_code"], "EXCERPT_MISMATCH")

            unknown = store.verify_span(run, {
                "source_id": "src-" + "0" * 64, "start": 0, "end": 5,
            })
            self.assertFalse(unknown["ok"])
            self.assertEqual(unknown["reason_code"], "UNKNOWN_SOURCE")

            out_of_range = store.verify_span(run, {
                "source_id": snap["source_id"], "start": -1, "end": 5,
            })
            self.assertFalse(out_of_range["ok"])
            self.assertEqual(out_of_range["reason_code"], "SPAN_OUT_OF_RANGE")

            long_text = "x" * (store.MAX_SPAN_CHARS + 10)
            long_snap = store.write_snapshot(
                run, url="https://example.org/long", text=long_text, title="Long",
                access="web", origin="web", paper=None,
                event_type="fetch", fresh=True, actor="test")
            overlong = store.verify_span(run, {
                "source_id": long_snap["source_id"], "start": 0, "end": len(long_text),
            })
            self.assertFalse(overlong["ok"])
            self.assertEqual(overlong["reason_code"], "SPAN_TOO_LONG")

    def test_freshness_fails_closed_without_config_created_at(self):
        with TemporaryDirectory() as td:
            run = Path(td) / "run"
            run.mkdir()
            snap = store.write_snapshot(
                run, url="https://example.org/a", text="text", title="A",
                access="web", origin="web", paper=None,
                event_type="fetch", fresh=True, actor="test")
            fresh = store.freshness(run, snap["source_id"])
            self.assertFalse(fresh["fresh"])
            self.assertEqual(fresh["reason_code"], "NO_FRESH_FETCH")

    def test_user_supplied_pdf_freshness_depends_on_asset_hash(self):
        with TemporaryDirectory() as td:
            wiki, run = make_run(Path(td))
            asset = wiki / "assets" / "papers" / "paper.pdf"
            asset.write_bytes(b"%PDF-1.4\nsynthetic bytes\n")
            digest = store.sha256_file(asset)
            snap = store.write_snapshot(
                run, url="file:///assets/papers/paper.pdf", text="Extracted text",
                title="Local PDF", access="full_text", origin="user-supplied-pdf",
                paper=None, asset={"path": "assets/papers/paper.pdf",
                                   "sha256": digest, "bytes": asset.stat().st_size},
                event_type="local_pdf", fresh=True, actor="test")

            self.assertTrue(store.freshness(run, snap["source_id"], wiki_root=wiki)["fresh"])
            asset.write_bytes(b"%PDF-1.4\nchanged bytes\n")
            stale = store.freshness(run, snap["source_id"], wiki_root=wiki)
            self.assertFalse(stale["fresh"])
            self.assertEqual(stale["reason_code"], "ASSET_HASH_MISMATCH")


if __name__ == "__main__":
    unittest.main()
