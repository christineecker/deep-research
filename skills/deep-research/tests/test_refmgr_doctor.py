"""Integrity reporting (`refmgr/doctor.py`, `registry.py doctor`).

Content-addressed storage fails quietly, so these tests break things on purpose — delete
an asset file, flip a byte inside one, drop a paper out from under its index rows — and
assert the report names them. The exit code matters too: `doctor` is meant to be runnable
from cron, so "index is stale" must not look the same as "a PDF is gone".
"""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, run_py

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import doctor  # noqa: E402
from refmgr.service import ReferenceManagerService  # noqa: E402

registry = load_script("registry.py")
research = load_script("research.py")

PDF_BYTES = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n"
             b"trailer<</Root 1 0 R>>\n%%EOF\n")


def _ns(**kwargs):
    class NS:
        pass
    ns = NS()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _repo_with_attachment(tmp: Path) -> tuple[Path, Path]:
    """A repo with one registered paper and one attached PDF. Returns (repo, asset path)."""
    repo = tmp / "repo"
    research.cmd_init(_ns(path=str(repo), from_wiki=None))
    pdf = tmp / "paper.pdf"
    pdf.write_bytes(PDF_BYTES)
    result = run_py(["scripts/registry.py", "add-pdf", "--repo", str(repo),
                     "--file", str(pdf), "--pmid", "1", "--title", "Attached paper"])
    assert result.returncode == 0, result.stderr
    assets = sorted((repo / "data" / "refmgr" / "assets").rglob("*.pdf"))
    assert len(assets) == 1, assets
    return repo, assets[0]


def _doctor(repo: Path, *flags: str) -> tuple[dict, int]:
    result = run_py(["scripts/registry.py", "doctor", "--repo", str(repo), *flags])
    return json.loads(result.stdout), result.returncode


def _conn(repo: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(repo / "data" / "refmgr" / "library.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn


class HealthyLibraryTest(unittest.TestCase):
    def test_a_sound_library_is_healthy_and_exits_zero(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            report, code = _doctor(repo)
            self.assertEqual(code, 0)
            self.assertTrue(report["healthy"])
            self.assertEqual(report["problems"], {})
            self.assertEqual(report["assets_checked"], 1)

    def test_a_library_with_no_attachments_is_not_unhealthy(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Metadata only"})
                reg.commit()
            report, code = _doctor(repo)
            self.assertEqual(code, 0)
            self.assertTrue(report["healthy"])
            self.assertEqual(report["papers_without_attachment"], 1)

    def test_no_library_at_all_says_so(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            report, code = _doctor(repo)
            self.assertEqual(code, 1)
            self.assertIn("reindex", report["error"])


class BrokenLibraryTest(unittest.TestCase):
    def test_a_deleted_asset_file_is_reported_and_fails(self):
        with TemporaryDirectory() as tmp:
            repo, asset = _repo_with_attachment(Path(tmp))
            asset.unlink()
            report, code = _doctor(repo)
            self.assertEqual(code, 1)
            self.assertFalse(report["healthy"])
            self.assertEqual(len(report["missing_files"]), 1)
            self.assertTrue(any("backup" in line for line in report["advice"]))

    def test_a_truncated_asset_is_caught_without_deep(self):
        with TemporaryDirectory() as tmp:
            repo, asset = _repo_with_attachment(Path(tmp))
            asset.write_bytes(PDF_BYTES[:10])
            report, code = _doctor(repo)
            self.assertEqual(code, 1)
            self.assertEqual(len(report["corrupt_assets"]), 1)

    def test_same_size_corruption_needs_deep(self):
        with TemporaryDirectory() as tmp:
            repo, asset = _repo_with_attachment(Path(tmp))
            flipped = bytearray(PDF_BYTES)
            flipped[10] = (flipped[10] + 1) % 256
            asset.write_bytes(bytes(flipped))

            shallow, shallow_code = _doctor(repo)
            self.assertEqual(shallow_code, 0)
            self.assertEqual(shallow["assets_hashed"], 0,
                             "the default pass must not hash a file whose size is right")

            deep, deep_code = _doctor(repo, "--deep")
            self.assertEqual(deep_code, 1)
            self.assertEqual(deep["assets_hashed"], 1)
            self.assertEqual(len(deep["corrupt_assets"]), 1)
            self.assertIn("hash to", deep["corrupt_assets"][0]["detail"])

    def test_an_attachment_whose_asset_row_vanished_is_an_orphan(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            conn = _conn(repo)
            with conn:
                conn.execute("DELETE FROM assets")
            conn.close()
            report, code = _doctor(repo)
            self.assertEqual(code, 1)
            self.assertEqual(len(report["orphan_attachments"]), 1)

    def test_index_rows_for_a_deleted_paper_are_reported_but_not_fatal(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            service = ReferenceManagerService(repo / "data" / "refmgr")
            try:
                paper_id = service.papers.list()[0]["id"]
                service.terms.set_terms(paper_id, [("mesh", "Adolescent")])
                service.chunks.index_source(paper_id, "src-1", "Body text.", "sha256:x")
            finally:
                service.close()

            conn = _conn(repo)
            with conn:
                # A hard delete, which nothing in the codebase does — soft delete is the
                # supported path. This is the "someone edited the database" case.
                conn.execute("DELETE FROM papers")
            conn.close()

            report, code = _doctor(repo)
            self.assertGreater(report["indexes"]["orphan_terms"], 0)
            self.assertGreater(report["indexes"]["orphan_chunks"], 0)
            self.assertGreater(report["indexes"]["orphan_fts_rows"], 0)
            self.assertTrue(any("reindex" in line for line in report["advice"]))
            # Dangling attachments make this unhealthy; the stale index rows on their own
            # would not.
            self.assertNotIn("orphan_terms", report["problems"])

    def test_orphan_assets_are_reported_without_failing(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            conn = _conn(repo)
            with conn:
                conn.execute("UPDATE attachments SET deleted_at = '2026-01-01T00:00:00Z'")
            conn.close()
            report, code = _doctor(repo)
            self.assertEqual(code, 0)
            self.assertTrue(report["healthy"])
            self.assertEqual(len(report["orphan_assets"]), 1)
            self.assertTrue(any("safe to keep" in line for line in report["advice"]))


class HardeningPackage6ChecksTest(unittest.TestCase):
    def test_healthy_library_reports_sqlite_integrity_ok(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            report, code = _doctor(repo)
            self.assertEqual(code, 0)
            self.assertTrue(report["integrity"]["integrity_ok"])
            self.assertEqual(report["integrity"]["foreign_key_violations"], [])
            self.assertEqual(report["asset_check_mode"], "shallow")
            deep_report, _ = _doctor(repo, "--deep")
            self.assertEqual(deep_report["asset_check_mode"], "deep")

    def test_hard_deleted_paper_is_caught_by_foreign_key_check(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            conn = _conn(repo)
            with conn:
                conn.execute("DELETE FROM papers")
            conn.close()

            report, code = _doctor(repo)
            self.assertEqual(code, 1)
            self.assertFalse(report["healthy"])
            self.assertGreater(len(report["integrity"]["foreign_key_violations"]), 0)
            self.assertIn("integrity.foreign_key_violations", report["problems"])

    def test_figure_pointing_at_wrong_owner_attachment_is_caught(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            service = ReferenceManagerService(repo / "data" / "refmgr")
            try:
                paper_id = service.papers.list()[0]["id"]
                other_paper_id = service.add_paper(title="Other paper", paper_type="article")
                attachment_id = service.attachments.list_for_paper(paper_id)[0]["id"]
                asset_sha256 = service.attachments.list_for_paper(paper_id)[0]["asset_sha256"]
                figure_attachment_id = service.attachments.link(
                    other_paper_id, asset_sha256, role="figure")
                figure_id = service.figures.record(
                    paper_id=other_paper_id, source_attachment_id=attachment_id,
                    figure_attachment_id=figure_attachment_id, asset_sha256=asset_sha256,
                    kind="figure", extractor="test")
            finally:
                service.close()

            report, code = _doctor(repo)
            self.assertEqual(code, 1)
            wrong = report["figure_ownership"]["figures_with_wrong_source_owner"]
            self.assertEqual([f["figure_id"] for f in wrong], [figure_id])
            self.assertIn("figure_ownership.figures_with_wrong_source_owner", report["problems"])

    def test_duplicate_primary_identifiers_for_same_scheme_are_caught(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            service = ReferenceManagerService(repo / "data" / "refmgr")
            try:
                paper_id = service.papers.list()[0]["id"]
                service.identifiers.add(paper_id, "doi", "10.1000/first")
                service.identifiers.add(paper_id, "doi", "10.1000/second")
                # Force both to be flagged primary -- a state normal code never
                # produces (add()/normalize_primaries prevent it), so this
                # simulates a direct/out-of-band edit doctor should still catch.
                service.conn.execute(
                    "UPDATE identifiers SET is_primary = 1 WHERE paper_id = ? "
                    "AND scheme = 'doi'", (paper_id,))
            finally:
                service.close()

            report, code = _doctor(repo)
            self.assertEqual(code, 1)
            dupes = report["identifiers"]["duplicate_primary_schemes"]
            self.assertEqual(len(dupes), 1)
            self.assertEqual(dupes[0]["paper_id"], paper_id)
            self.assertEqual(dupes[0]["scheme"], "doi")
            self.assertIn("identifiers.duplicate_primary_schemes", report["problems"])

    def test_stale_chunker_version_is_reported_but_not_fatal(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            service = ReferenceManagerService(repo / "data" / "refmgr")
            try:
                paper_id = service.papers.list()[0]["id"]
                service.chunks.index_source(paper_id, "src-1", "Body text.", "sha256:x")
                service.conn.execute("UPDATE chunks SET chunker_version = 0")
            finally:
                service.close()

            report, code = _doctor(repo)
            self.assertEqual(code, 0)
            self.assertTrue(report["healthy"])
            self.assertEqual(report["chunk_staleness"]["chunks_at_stale_version"], 1)
            self.assertEqual(report["chunk_staleness"]["papers_with_stale_chunks"], 1)
            self.assertTrue(any("chunker version" in line for line in report["advice"]))

    def test_untracked_asset_file_is_reported_but_not_fatal(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            stray_dir = repo / "data" / "refmgr" / "assets" / "sha256" / "ab"
            stray_dir.mkdir(parents=True, exist_ok=True)
            stray_file = stray_dir / ("0" * 64 + ".pdf")
            stray_file.write_bytes(b"leftover from an aborted stage_and_commit")

            report, code = _doctor(repo)
            self.assertEqual(code, 0)
            self.assertTrue(report["healthy"])
            self.assertEqual(len(report["untracked_files"]), 1)
            self.assertTrue(any("no matching database row" in line for line in report["advice"]))

    def test_identifiers_on_soft_deleted_paper_are_informational_only(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_attachment(Path(tmp))
            service = ReferenceManagerService(repo / "data" / "refmgr")
            try:
                paper_id = service.papers.list()[0]["id"]
                service.papers.soft_delete(paper_id)
            finally:
                service.close()

            report, code = _doctor(repo)
            self.assertEqual(code, 0)
            self.assertTrue(report["healthy"])
            self.assertEqual(len(report["identifiers"]["identifiers_on_deleted_papers"]), 1)
            self.assertNotIn("identifiers.identifiers_on_deleted_papers", report["problems"])


class DoctorIsReadOnlyTest(unittest.TestCase):
    def test_running_doctor_changes_nothing_on_disk(self):
        with TemporaryDirectory() as tmp:
            repo, asset = _repo_with_attachment(Path(tmp))
            before = {p: p.stat().st_mtime_ns for p in sorted(repo.rglob("*"))
                      if p.is_file() and "library.sqlite3" not in p.name}
            _doctor(repo, "--deep")
            after = {p: p.stat().st_mtime_ns for p in sorted(repo.rglob("*"))
                     if p.is_file() and "library.sqlite3" not in p.name}
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
