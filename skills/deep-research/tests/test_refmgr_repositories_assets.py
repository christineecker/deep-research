import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import db
from refmgr.repositories.assets import AssetCorruptionError, AssetRepository
from refmgr.repositories.attachments import AttachmentRepository


class RefmgrAssetsAttachmentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conn = db.open_and_migrate(self.tmp)
        self.assets = AssetRepository(self.conn, self.tmp)
        self.attachments = AttachmentRepository(self.conn)
        self.paper_id = self._insert_paper("p1")

    def _insert_paper(self, paper_id: str) -> str:
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO papers "
                "(id, title, paper_type, metadata_json, created_at, updated_at) "
                "VALUES (?, 'Some Title', 'article', '{}', 'now', 'now')",
                (paper_id,),
            )
        return paper_id

    def _write_source_file(self, name: str, content: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(content)
        return path

    # -- AssetRepository ----------------------------------------------

    def test_stage_and_commit_computes_sha256_and_relative_path(self):
        content = b"%PDF-1.4 some pdf bytes"
        source = self._write_source_file("source.pdf", content)

        import hashlib
        expected_hash = hashlib.sha256(content).hexdigest()

        digest = self.assets.stage_and_commit(source, mime_type="application/pdf")
        self.assertEqual(digest, expected_hash)

        row = self.assets.get(digest)
        self.assertIsNotNone(row)
        self.assertEqual(row["byte_size"], len(content))
        self.assertEqual(row["mime_type"], "application/pdf")
        expected_relative = f"assets/sha256/{expected_hash[:2]}/{expected_hash}.pdf"
        self.assertEqual(row["storage_path"], expected_relative)
        # Must not be absolute.
        self.assertFalse(Path(row["storage_path"]).is_absolute())

        final_path = self.tmp / row["storage_path"]
        self.assertTrue(final_path.exists())
        self.assertEqual(final_path.read_bytes(), content)

    def test_stage_and_commit_dedupes_identical_bytes(self):
        content = b"duplicate content here"
        source_a = self._write_source_file("a.pdf", content)
        source_b = self._write_source_file("b.pdf", content)

        digest_a = self.assets.stage_and_commit(source_a)
        digest_b = self.assets.stage_and_commit(source_b)

        self.assertEqual(digest_a, digest_b)

        rows = self.conn.execute(
            "SELECT COUNT(*) AS n FROM assets WHERE sha256 = ?", (digest_a,)
        ).fetchone()
        self.assertEqual(rows["n"], 1)

        asset_dir = self.tmp / "assets" / "sha256" / digest_a[:2]
        files = list(asset_dir.glob(f"{digest_a}*"))
        self.assertEqual(len(files), 1)

    def test_stage_and_commit_same_filename_different_bytes_are_independent(self):
        source_dir_a = self.tmp / "dir_a"
        source_dir_b = self.tmp / "dir_b"
        source_dir_a.mkdir()
        source_dir_b.mkdir()
        path_a = source_dir_a / "paper.pdf"
        path_b = source_dir_b / "paper.pdf"
        path_a.write_bytes(b"version one bytes")
        path_b.write_bytes(b"version two bytes, different")

        digest_a = self.assets.stage_and_commit(path_a)
        digest_b = self.assets.stage_and_commit(path_b)

        self.assertNotEqual(digest_a, digest_b)
        self.assertIsNotNone(self.assets.get(digest_a))
        self.assertIsNotNone(self.assets.get(digest_b))

    def test_verify_integrity_true_then_false_after_corruption(self):
        content = b"content to verify"
        source = self._write_source_file("verify.pdf", content)
        digest = self.assets.stage_and_commit(source)

        self.assertTrue(self.assets.verify_integrity(digest))

        row = self.assets.get(digest)
        final_path = self.tmp / row["storage_path"]
        final_path.unlink()

        self.assertFalse(self.assets.verify_integrity(digest))

    def test_verify_integrity_false_for_unknown_asset(self):
        self.assertFalse(self.assets.verify_integrity("0" * 64))

    def test_stage_and_commit_dedup_detects_size_mismatch_corruption(self):
        content = b"original content"
        source = self._write_source_file("orig.pdf", content)
        digest = self.assets.stage_and_commit(source)

        row = self.assets.get(digest)
        final_path = self.tmp / row["storage_path"]
        # Corrupt the stored file by truncating it so its size no longer
        # matches the recorded byte_size, without deleting it outright.
        final_path.write_bytes(content[:-1])

        source2 = self._write_source_file("orig2.pdf", content)
        with self.assertRaises(AssetCorruptionError):
            self.assets.stage_and_commit(source2)

    # -- AttachmentRepository -------------------------------------------

    def test_linking_two_attachments_preserves_both(self):
        digest = self.assets.stage_and_commit(
            self._write_source_file("f1.pdf", b"aaa")
        )
        att1 = self.attachments.link(self.paper_id, digest, role="main")
        att2 = self.attachments.link(self.paper_id, digest, role="supplement")

        self.assertNotEqual(att1, att2)
        rows = self.attachments.list_for_paper(self.paper_id)
        ids = {row["id"] for row in rows}
        self.assertEqual(ids, {att1, att2})

    def test_preferred_true_twice_leaves_exactly_one_preferred(self):
        digest = self.assets.stage_and_commit(
            self._write_source_file("f2.pdf", b"bbb")
        )
        self.attachments.link(self.paper_id, digest, role="main", preferred=True)
        att2 = self.attachments.link(self.paper_id, digest, role="main", preferred=True)

        rows = self.attachments.list_for_paper(self.paper_id)
        preferred = [row for row in rows if row["is_preferred_reader"]]
        self.assertEqual(len(preferred), 1)
        self.assertEqual(preferred[0]["id"], att2)

    def test_set_preferred_moves_flag(self):
        digest = self.assets.stage_and_commit(
            self._write_source_file("f3.pdf", b"ccc")
        )
        att1 = self.attachments.link(self.paper_id, digest, role="main", preferred=True)
        att2 = self.attachments.link(self.paper_id, digest, role="supplement")

        self.attachments.set_preferred(att2)

        rows = {row["id"]: row for row in self.attachments.list_for_paper(self.paper_id)}
        self.assertEqual(rows[att1]["is_preferred_reader"], 0)
        self.assertEqual(rows[att2]["is_preferred_reader"], 1)

    def test_set_preferred_unknown_attachment_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.attachments.set_preferred("does-not-exist")

    def test_soft_delete_is_idempotent_and_preserves_row_and_asset(self):
        digest = self.assets.stage_and_commit(
            self._write_source_file("f4.pdf", b"ddd")
        )
        att = self.attachments.link(self.paper_id, digest, role="main")

        self.attachments.soft_delete(att)
        self.attachments.soft_delete(att)  # idempotent, no error

        visible = self.attachments.list_for_paper(self.paper_id)
        self.assertEqual(visible, [])

        all_rows = self.attachments.list_for_paper(self.paper_id, include_deleted=True)
        self.assertEqual(len(all_rows), 1)
        self.assertIsNotNone(all_rows[0]["deleted_at"])

        # Underlying asset untouched.
        self.assertIsNotNone(self.assets.get(digest))
        self.assertTrue(self.assets.verify_integrity(digest))


if __name__ == "__main__":
    unittest.main()
