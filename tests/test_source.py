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


if __name__ == "__main__":
    unittest.main()
