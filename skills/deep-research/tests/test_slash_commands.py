"""C-SLASH-COMMANDS: every `/deep-research:*` command body's script invocation must be
argv-valid for the script it calls, so a rewritten flag or reordered positional in a
command .md file fails CI instead of shipping silently (this is exactly the class of
error rev 1 of SLASH_COMMANDS_PLAN.md shipped: positional-vs-flag, string-vs-list).

Each case below is the minimal required argv for one documented invocation, fed straight
into the target script's own `build_parser().parse_args()` (or, for status.py which
does not expose one, an equivalent parser reconstructed from its documented flags).
Parsing only — no execution, no network, no filesystem writes.
"""

from __future__ import annotations

import argparse
import unittest

from helpers import load_script


registry = load_script("registry.py")
research = load_script("research.py")
eutils = load_script("eutils.py")
paper = load_script("paper.py")
verify = load_script("verify.py")
watch = load_script("watch.py")


def status_parser() -> argparse.ArgumentParser:
    # status.py builds its parser inline in main() rather than exposing build_parser();
    # mirrored here so this test never needs to execute status.py's real main().
    p = argparse.ArgumentParser(prog="status.py")
    p.add_argument("run_dir")
    p.add_argument("--table", action="store_true")
    p.add_argument("--missing", action="store_true")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--no-summary", action="store_true")
    return p


class SlashCommandArgvTest(unittest.TestCase):
    """One case per command body in commands/*.md. Keep argv here in lockstep with the
    "Steps" call template documented in each command file."""

    def test_init(self):
        research.build_parser().parse_args(["init", "/tmp/demo-repo"])
        research.build_parser().parse_args(["init", "/tmp/demo-repo", "--from-wiki", "/tmp/wiki"])

    def test_pool_add(self):
        eutils.build_parser().parse_args([
            "esearch", "--query", "MRS autism",
            "--filters-json", '{"species":["human"],"years":[2020,2026]}',
            "--sort", "pub_date", "--retmax", "5",
        ])
        registry.build_parser().parse_args(["add", "--repo", "/tmp/demo-repo", "--pmid", "12345678"])
        registry.build_parser().parse_args(["pool", "--repo", "/tmp/demo-repo"])
        registry.build_parser().parse_args(["list", "--repo", "/tmp/demo-repo", "--limit", "5"])

    def test_summarize(self):
        paper.build_parser().parse_args([
            "summarize", "--repo", "/tmp/demo-repo", "--pmid", "12345678",
        ])
        paper.build_parser().parse_args([
            "summarize", "--repo", "/tmp/demo-repo", "--doi", "10.1000/x",
            "--project", "autism-mrs", "--purpose", "background",
            "--audience", "researcher", "--no-reuse", "--format", "both",
        ])

    def test_summarize_set(self):
        paper.build_parser().parse_args([
            "summarize-set", "--repo", "/tmp/demo-repo",
            "--question", "MRS studies in autism, human only", "--limit", "5",
        ])
        paper.build_parser().parse_args([
            "summarize-set", "--repo", "/tmp/demo-repo",
            "--pmid", "12345678", "--select-only", "--no-overview", "--strict",
        ])

    def test_bib_export(self):
        registry.build_parser().parse_args([
            "bib", "--repo", "/tmp/demo-repo", "--out", "/tmp/refs.bib", "--select", "all",
        ])
        registry.build_parser().parse_args([
            "bib", "--repo", "/tmp/demo-repo", "--out", "/tmp/refs.bib",
            "--select", "appraised", "--project", "autism-mrs",
        ])

    def test_pdf_lookup(self):
        registry.build_parser().parse_args([
            "lookup", "--repo", "/tmp/demo-repo", "--pmid", "12345678",
        ])

    def test_status(self):
        status_parser().parse_args(["/tmp/demo-run", "--table", "--limit", "50"])
        status_parser().parse_args(["/tmp/demo-run", "--missing"])

    def test_verify(self):
        verify.build_parser().parse_args([
            "run", "--run-dir", "/tmp/demo-run", "--json", "--markdown", "/tmp/verify.md",
        ])
        verify.build_parser().parse_args([
            "single-paper-summary", "--summary", "/tmp/summary.json", "--run-dir", "/tmp/demo-run",
        ])
        verify.build_parser().parse_args([
            "paper-summary-set", "--summary-set", "/tmp/summary-set.json", "--run-dir", "/tmp/demo-run",
        ])

    def test_project(self):
        research.build_parser().parse_args([
            "project", "create", "autism-mrs", "--repo", "/tmp/demo-repo", "--title", "Autism MRS review",
        ])
        research.build_parser().parse_args(["project", "list", "--repo", "/tmp/demo-repo"])

    def test_watch(self):
        watch.build_parser().parse_args(["--run-dir", "/tmp/demo-run", "--once"])
        watch.build_parser().parse_args(["--follow-latest", "/tmp/wiki", "--json"])


if __name__ == "__main__":
    unittest.main()
