from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr.service import ReferenceManagerService  # noqa: E402

from helpers import load_script  # noqa: E402

export_select_refmgr = load_script("export_select_refmgr.py")


class RefmgrExportTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.service = ReferenceManagerService(self.tmp)
        self.addCleanup(self.service.close)

    def _write_source_file(self, name: str, content: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(content)
        return path


class PaperToExportRecordTest(RefmgrExportTestBase):
    def test_maps_title_identifiers_and_metadata_fields(self):
        paper_id = self.service.add_paper(
            title="A Study of Things",
            paper_type="article",
            metadata={
                "journal": "Journal of Things",
                "volume": "12",
                "issue": "3",
                "pages": "100-110",
                "abstract": "An abstract.",
                "not_a_legacy_field": "should not appear",
            },
            identifiers=[("doi", "10.1000/xyz"), ("pmid", "123456")],
        )

        record = export_select_refmgr.paper_to_export_record(self.service, paper_id)

        self.assertEqual(record["evidence_id"], paper_id)
        self.assertEqual(record["title"], "A Study of Things")
        self.assertEqual(record["journal"], "Journal of Things")
        self.assertEqual(record["volume"], "12")
        self.assertEqual(record["issue"], "3")
        self.assertEqual(record["pages"], "100-110")
        self.assertEqual(record["abstract"], "An abstract.")
        self.assertEqual(record["doi"], "10.1000/xyz")
        self.assertEqual(record["pmid"], "123456")
        self.assertEqual(record["_refmgr_paper_id"], paper_id)

        # No refmgr equivalent yet -> omitted, not None.
        self.assertNotIn("mesh_terms", record)
        self.assertNotIn("keywords", record)
        self.assertNotIn("retraction_status", record)
        self.assertNotIn("pmcid", record)
        self.assertNotIn("fulltext", record)
        # Non-legacy metadata keys are not passed through.
        self.assertNotIn("not_a_legacy_field", record)

    def test_unknown_paper_id_raises_key_error(self):
        with self.assertRaises(KeyError):
            export_select_refmgr.paper_to_export_record(self.service, "no-such-paper")


class ResolveAllTest(RefmgrExportTestBase):
    def setUp(self):
        super().setUp()
        self.source = export_select_refmgr.RefmgrExportSource(self.tmp)
        self.addCleanup(self.source.close)

    def test_two_attachments_preferred_one_ordered_first(self):
        paper_id = self.service.add_paper(title="Multi-attachment paper", paper_type="article")

        self.service.import_attachment(
            paper_id,
            self._write_source_file("primary.pdf", b"primary bytes"),
            role="primary",
        )
        preferred_id = self.service.import_attachment(
            paper_id,
            self._write_source_file("supplement.pdf", b"supplement bytes"),
            role="supplement",
            preferred=True,
        )

        resolutions = self.source.resolve_all(paper_id)

        self.assertEqual(len(resolutions), 2)
        self.assertEqual(resolutions[0].role, "supplement")
        self.assertTrue(resolutions[0].available)
        self.assertEqual(resolutions[1].role, "primary")
        self.assertTrue(resolutions[1].available)

        # sanity: the preferred attachment really is the one we marked preferred.
        preferred_attachment = [
            a for a in self.service.attachments.list_for_paper(paper_id)
            if a["id"] == preferred_id
        ][0]
        self.assertEqual(preferred_attachment["role"], "supplement")

    def test_two_attachments_no_preferred_ordered_by_created_at(self):
        paper_id = self.service.add_paper(title="Two versions", paper_type="article")

        self.service.import_attachment(
            paper_id,
            self._write_source_file("v1.pdf", b"version one"),
            role="version",
            version_label="v1",
        )
        self.service.import_attachment(
            paper_id,
            self._write_source_file("v2.pdf", b"version two"),
            role="version",
            version_label="v2",
        )

        resolutions = self.source.resolve_all(paper_id)
        attachments = self.service.attachments.list_for_paper(paper_id)

        self.assertEqual(len(resolutions), 2)
        expected_order = [a["asset_sha256"] for a in attachments]
        actual_order = [r.sha256_expected for r in resolutions]
        self.assertEqual(actual_order, expected_order)

    def test_zero_attachments_resolve_all_empty_and_resolve_reports_no_attachments(self):
        paper_id = self.service.add_paper(title="No files", paper_type="article")

        self.assertEqual(self.source.resolve_all(paper_id), [])

        record = export_select_refmgr.paper_to_export_record(self.service, paper_id)
        resolution = self.source.resolve(record)
        self.assertFalse(resolution.available)
        self.assertEqual(resolution.role, "unknown")
        self.assertEqual(resolution.problem, "no attachments")

    def test_corrupted_asset_file_reports_unavailable_without_crashing(self):
        paper_id = self.service.add_paper(title="Corrupted asset", paper_type="article")
        self.service.import_attachment(
            paper_id,
            self._write_source_file("doomed.pdf", b"doomed bytes"),
            role="primary",
        )
        attachment = self.service.attachments.list_for_paper(paper_id)[0]
        asset = self.service.assets.get(attachment["asset_sha256"])
        final_path = self.tmp / asset["storage_path"]
        final_path.unlink()

        resolutions = self.source.resolve_all(paper_id)

        self.assertEqual(len(resolutions), 1)
        self.assertFalse(resolutions[0].available)
        self.assertEqual(resolutions[0].problem, "checksum mismatch or missing file")

    def test_missing_asset_record_reports_unavailable(self):
        # The FK on attachments.asset_sha256 -> assets.sha256 means this case "shouldn't
        # normally happen" (per the plan's own wording); simulate it directly by dropping
        # the asset row underneath a real attachment, with FK enforcement briefly relaxed,
        # to exercise the diagnostic path rather than assume it away.
        paper_id = self.service.add_paper(title="Dangling attachment", paper_type="article")
        self.service.import_attachment(
            paper_id,
            self._write_source_file("orphan.pdf", b"orphan bytes"),
            role="primary",
        )
        attachment = self.service.attachments.list_for_paper(paper_id)[0]
        conn = self.service.conn
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("DELETE FROM assets WHERE sha256 = ?", (attachment["asset_sha256"],))
        conn.execute("PRAGMA foreign_keys=ON")

        resolutions = self.source.resolve_all(paper_id)

        self.assertEqual(len(resolutions), 1)
        self.assertFalse(resolutions[0].available)
        self.assertEqual(resolutions[0].problem, "asset record missing")


class ResolveSingleTest(RefmgrExportTestBase):
    def test_resolve_delegates_to_first_resolve_all_item(self):
        paper_id = self.service.add_paper(title="Single asset", paper_type="article")
        self.service.import_attachment(
            paper_id,
            self._write_source_file("only.pdf", b"only bytes"),
            role="primary",
        )

        with export_select_refmgr.RefmgrExportSource(self.tmp) as source:
            record = export_select_refmgr.paper_to_export_record(self.service, paper_id)
            resolution = source.resolve(record)

        self.assertTrue(resolution.available)
        self.assertEqual(resolution.role, "primary")

    def test_resolve_requires_refmgr_paper_id_key(self):
        with export_select_refmgr.RefmgrExportSource(self.tmp) as source:
            with self.assertRaises(ValueError):
                source.resolve({"evidence_id": "not-a-refmgr-record"})


class ListPapersForExportTest(RefmgrExportTestBase):
    def test_excludes_soft_deleted_papers(self):
        kept_id = self.service.add_paper(title="Kept", paper_type="article")
        deleted_id = self.service.add_paper(title="Deleted", paper_type="article")
        self.service.papers.soft_delete(deleted_id)

        with export_select_refmgr.RefmgrExportSource(self.tmp) as source:
            paper_ids = source.list_papers_for_export()

        self.assertIn(kept_id, paper_ids)
        self.assertNotIn(deleted_id, paper_ids)


class ContextManagerTest(RefmgrExportTestBase):
    def test_context_manager_closes_cleanly(self):
        with export_select_refmgr.RefmgrExportSource(self.tmp) as source:
            self.assertIsNotNone(source.service.conn)
        # Connection is closed after exit; further use should raise.
        with self.assertRaises(Exception):
            source.service.conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
