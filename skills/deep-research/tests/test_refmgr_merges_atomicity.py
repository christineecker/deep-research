"""Proves execute_merge is a single atomic transaction.

Forces a failure partway through the mutation sequence (after identifiers,
attachments, and collection membership have already been moved) and asserts
that nothing committed: the absorbed paper is not soft-deleted, no
identifiers/attachments were reassigned, no tag was added, and no
merges/audit_log row exists. Before the fix in merges.py (calling the
`_locked` repository cores inside one `db.transaction`), each repository
write committed independently as soon as it ran, so a failure here would
have left a partially-applied merge -- this test would fail against that
old behavior.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import db
from refmgr.repositories.attachments import AttachmentRepository
from refmgr.repositories.identifiers import IdentifierRepository
from refmgr.repositories.merges import MergeService
from refmgr.repositories.organization import OrganizationRepository
from refmgr.repositories.papers import PaperRepository


class ExecuteMergeAtomicityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conn = db.open_and_migrate(self.tmp)
        self.papers = PaperRepository(self.conn)
        self.identifiers = IdentifierRepository(self.conn)
        self.attachments = AttachmentRepository(self.conn)
        self.organization = OrganizationRepository(self.conn)
        self.merges = MergeService(self.conn)

        self.survivor = self.papers.create("Survivor Paper", "article")
        self.absorbed = self.papers.create("Absorbed Paper", "article")

        self.identifiers.add(self.absorbed, "doi", "10.1234/absorbed")
        self.identifiers.add(self.survivor, "pmid", "1111")

        self.conn.execute(
            "INSERT INTO assets (sha256, byte_size, mime_type, storage_path, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("d" * 64, 100, "application/pdf", f"/assets/{'d' * 64}.pdf", "now"),
        )
        self.attachment_id = self.attachments.link(
            self.absorbed, "d" * 64, role="pdf", original_filename="absorbed.pdf"
        )

        self.collection_id = self.organization.create_collection("Reading list")
        self.organization.add_to_collection(self.collection_id, self.absorbed)

        self.organization.add_tag(self.absorbed, "neuroscience")

    def test_failure_partway_through_rolls_back_everything(self):
        # Tags are added in step 5, after identifiers (step 2), attachments
        # (step 3), and collections (step 4) have already been mutated
        # in-process. Failing here proves the whole sequence -- not just the
        # failing call -- is undone.
        with patch.object(
            OrganizationRepository,
            "_add_tag_locked",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                self.merges.execute_merge(self.survivor, self.absorbed)

        # Nothing committed: absorbed paper still alive and untouched.
        self.assertIsNotNone(self.papers.get(self.absorbed))

        absorbed_identifier_values = {
            row["value"] for row in self.identifiers.list_for_paper(self.absorbed)
        }
        self.assertEqual(absorbed_identifier_values, {"10.1234/absorbed"})
        survivor_identifier_values = {
            row["value"] for row in self.identifiers.list_for_paper(self.survivor)
        }
        self.assertEqual(survivor_identifier_values, {"1111"})

        self.assertEqual(
            {row["id"] for row in self.attachments.list_for_paper(self.absorbed)},
            {self.attachment_id},
        )
        self.assertEqual(self.attachments.list_for_paper(self.survivor), [])

        survivor_collections = {
            row["id"] for row in self.organization.list_collections_for_paper(self.survivor)
        }
        self.assertNotIn(self.collection_id, survivor_collections)
        absorbed_collections = {
            row["id"] for row in self.organization.list_collections_for_paper(self.absorbed)
        }
        self.assertIn(self.collection_id, absorbed_collections)

        self.assertEqual(self.organization.list_tags(self.survivor), [])
        self.assertEqual(self.organization.list_tags(self.absorbed), ["neuroscience"])

        merges_rows = self.conn.execute("SELECT * FROM merges").fetchall()
        self.assertEqual(merges_rows, [])

        audit_rows = self.conn.execute(
            "SELECT * FROM audit_log WHERE action = 'merge'"
        ).fetchall()
        self.assertEqual(audit_rows, [])


if __name__ == "__main__":
    unittest.main()
