from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import ROOT, run_py


class TutorialCliTest(unittest.TestCase):
    def test_quickstart_creates_demo_repo(self):
        with TemporaryDirectory() as td:
            repo = Path(td) / "demo"
            result = run_py([
                "scripts/tutorial.py", "quickstart", "--repo", str(repo),
                "--project", "diagnostic-demo",
            ], cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout[result.stdout.rfind("{"):])
            self.assertEqual(payload["status"], "ok")
            self.assertTrue((repo / "data" / "papers" / "registry.jsonl").exists())
            self.assertTrue((repo / "data" / "papers" / "extractions" /
                             "pmid-12345678.json").exists())
            self.assertTrue((repo / "data" / "papers" / "appraisals" /
                             "diagnostic-demo" / "pmid-12345678.json").exists())
            self.assertTrue((repo / "projects" / "diagnostic-demo" / "refs.bib").exists())

    def test_build_site_writes_html_and_fixtures(self):
        with TemporaryDirectory() as td:
            out = Path(td) / "site"
            result = run_py(["scripts/tutorial.py", "build-site", "--out", str(out)], cwd=ROOT)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue((out / "index.html").exists())
            self.assertTrue((out / "style.css").exists())
            self.assertTrue((out / "01-standalone-repo-quickstart.html").exists())
            self.assertTrue((out / "fixtures" / "sample-refs.bib").exists())
            html = (out / "04-appraise-with-frameworks.html").read_text(encoding="utf-8")
            self.assertIn("<table>", html)
            self.assertIn("QUADAS-2", html)


if __name__ == "__main__":
    unittest.main()
