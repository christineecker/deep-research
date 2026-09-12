from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import ROOT, make_run, run_py


class SourceCliTest(unittest.TestCase):
    def test_file_fetch_snapshots_and_span_tools_use_current_flags(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            page = Path(td) / "article.html"
            page.write_text(
                "<html><title>Validation article</title><body><p>The trial enrolled "
                "42 adults.</p></body></html>",
                encoding="utf-8")
            url = page.resolve().as_uri()

            fetch = run_py([
                "scripts/source.py", "fetch", "--run-dir", str(run), "--url", url,
                "--fresh", "--access", "full_text", "--origin", "web",
                "--pmid", "12345678", "--doi", "10.1000/validation",
                "--keep-requested-url",
            ], cwd=ROOT)
            self.assertEqual(fetch.returncode, 0, fetch.stderr + fetch.stdout)
            got = json.loads(fetch.stdout)
            self.assertTrue(got["fresh"])

            spans = run_py([
                "scripts/source.py", "spans", "--run-dir", str(run),
                "--source-id", got["source_id"], "--query", "trial enrolled 42 adults",
                "--max", "1",
            ], cwd=ROOT)
            self.assertEqual(spans.returncode, 0, spans.stderr + spans.stdout)
            cand = json.loads(spans.stdout)["candidates"][0]
            self.assertIn("trial enrolled 42 adults", cand["excerpt"])

            read = run_py([
                "scripts/source.py", "read", "--run-dir", str(run),
                "--source-id", got["source_id"], "--start", str(cand["match_start"]),
                "--end", str(cand["match_end"]),
            ], cwd=ROOT)
            self.assertEqual(read.returncode, 0, read.stderr + read.stdout)
            self.assertEqual(json.loads(read.stdout)["text"], "trial enrolled 42 adults")

    def test_fetch_refuses_pdf_bodies(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            pdf = Path(td) / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            proc = run_py([
                "scripts/source.py", "fetch", "--run-dir", str(run),
                "--url", pdf.resolve().as_uri(), "--fresh",
            ], cwd=ROOT)
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(json.loads(proc.stdout)["error_type"], "PolicyError")

    def test_fetch_refuses_credentials_and_unsupported_schemes(self):
        with TemporaryDirectory() as td:
            _wiki, run = make_run(Path(td))
            for url in ("https://user:pass@example.org/article", "ftp://example.org/article"):
                proc = run_py([
                    "scripts/source.py", "fetch", "--run-dir", str(run),
                    "--url", url,
                ], cwd=ROOT)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(json.loads(proc.stdout)["error_type"], "PolicyError")

    def test_local_pdf_from_shared_library_writes_fresh_asset_event(self):
        with TemporaryDirectory() as td:
            wiki, run = make_run(Path(td))
            src = ROOT / "tests" / "fixtures" / "pdf" / "paywalled-cohort.pdf"
            dest = wiki / "assets" / "papers" / "paywalled-cohort.pdf"
            shutil.copyfile(src, dest)
            proc = run_py([
                "scripts/source.py", "local", "--run-dir", str(run), "--wiki", str(wiki),
                "--pdf", str(dest), "--pmid", "12345678", "--doi", "10.1000/validation",
            ], cwd=ROOT)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            got = json.loads(proc.stdout)
            self.assertTrue(got["fresh"])
            self.assertEqual(got["origin"], "user-supplied-pdf")
            self.assertEqual(got["asset"]["path"], "assets/papers/paywalled-cohort.pdf")

    def test_local_via_refmgr_attachment_records_refmgr_ids_not_a_wiki_path(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            repo = tmp / "repo"
            run = repo / "runs" / "r1"
            run.mkdir(parents=True)
            (run / "config.json").write_text(
                json.dumps({"created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
            src = ROOT / "tests" / "fixtures" / "pdf" / "paywalled-cohort.pdf"

            add_pdf = run_py([
                "scripts/registry.py", "add-pdf", "--repo", str(repo),
                "--file", str(src), "--pmid", "12345678",
            ], cwd=ROOT)
            self.assertEqual(add_pdf.returncode, 0, add_pdf.stderr + add_pdf.stdout)
            attachment_id = json.loads(add_pdf.stdout)["asset"]["attachment_id"]

            proc = run_py([
                "scripts/source.py", "local", "--run-dir", str(run), "--repo", str(repo),
                "--attachment-id", attachment_id, "--pdf", str(src),
                "--pmid", "12345678", "--doi", "10.1000/validation",
            ], cwd=ROOT)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            got = json.loads(proc.stdout)
            self.assertTrue(got["fresh"])
            self.assertEqual(got["origin"], "user-supplied-pdf")
            self.assertIsNone(got["asset"]["path"])
            self.assertEqual(got["asset"]["refmgr_attachment_id"], attachment_id)
            self.assertTrue(got["asset"]["refmgr_paper_id"])

    def test_local_via_refmgr_rejects_a_pdf_that_does_not_match_the_attachment(self):
        with TemporaryDirectory() as td:
            tmp = Path(td)
            repo = tmp / "repo"
            run = repo / "runs" / "r1"
            run.mkdir(parents=True)
            (run / "config.json").write_text(
                json.dumps({"created_at": "2026-01-01T00:00:00Z"}), encoding="utf-8")
            src = ROOT / "tests" / "fixtures" / "pdf" / "paywalled-cohort.pdf"
            other = tmp / "other.pdf"
            other.write_bytes(b"%PDF-1.4\nnot the same bytes\n")

            add_pdf = run_py([
                "scripts/registry.py", "add-pdf", "--repo", str(repo),
                "--file", str(src), "--pmid", "12345678",
            ], cwd=ROOT)
            attachment_id = json.loads(add_pdf.stdout)["asset"]["attachment_id"]

            proc = run_py([
                "scripts/source.py", "local", "--run-dir", str(run), "--repo", str(repo),
                "--attachment-id", attachment_id, "--pdf", str(other),
            ], cwd=ROOT)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("does not match", json.loads(proc.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
