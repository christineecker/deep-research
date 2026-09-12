import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import db
from refmgr.repositories.audit import AuditRepository
from refmgr.repositories.identifiers import IdentifierConflictError, IdentifierRepository
from refmgr.repositories.papers import PaperRepository


class RepoTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conn = db.open_and_migrate(self.tmp)
        self.papers = PaperRepository(self.conn)
        self.identifiers = IdentifierRepository(self.conn)
        self.audit = AuditRepository(self.conn)


class PaperRepositoryTest(RepoTestBase):
    def test_create_and_get(self):
        paper_id = self.papers.create("Title A", "article", metadata={"year": 2020})
        paper = self.papers.get(paper_id)
        self.assertIsNotNone(paper)
        self.assertEqual(paper["title"], "Title A")
        self.assertEqual(paper["paper_type"], "article")
        self.assertEqual(paper["metadata"], {"year": 2020})
        self.assertNotIn("metadata_json", paper)
        self.assertIsNone(paper["deleted_at"])

    def test_create_default_metadata(self):
        paper_id = self.papers.create("No meta", "article")
        paper = self.papers.get(paper_id)
        self.assertEqual(paper["metadata"], {})

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.papers.get("nonexistent"))

    def test_list_pagination_and_order(self):
        ids = [self.papers.create(f"Paper {i}", "article") for i in range(5)]
        page1 = self.papers.list(limit=2, offset=0)
        page2 = self.papers.list(limit=2, offset=2)
        self.assertEqual([p["id"] for p in page1], ids[0:2])
        self.assertEqual([p["id"] for p in page2], ids[2:4])

    def test_update_metadata_shallow_merge(self):
        paper_id = self.papers.create("Title", "article", metadata={"a": 1, "b": 2})
        self.papers.update_metadata(paper_id, {"b": 3, "c": 4})
        paper = self.papers.get(paper_id)
        self.assertEqual(paper["metadata"], {"a": 1, "b": 3, "c": 4})

    def test_update_metadata_never_touches_id_or_created_at(self):
        paper_id = self.papers.create("Title", "article")
        before = self.papers.get(paper_id)
        self.papers.update_metadata(paper_id, {"x": 1})
        after = self.papers.get(paper_id)
        self.assertEqual(after["id"], before["id"])
        self.assertEqual(after["created_at"], before["created_at"])

    def test_update_metadata_missing_paper_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.papers.update_metadata("nonexistent", {"x": 1})

    def test_set_citation_key(self):
        paper_id = self.papers.create("Title", "article")
        self.papers.set_citation_key(paper_id, "smith2020")
        paper = self.papers.get(paper_id)
        self.assertEqual(paper["citation_key"], "smith2020")

    def test_set_citation_key_collision_raises(self):
        p1 = self.papers.create("Title 1", "article")
        p2 = self.papers.create("Title 2", "article")
        self.papers.set_citation_key(p1, "smith2020")
        with self.assertRaises(ValueError):
            self.papers.set_citation_key(p2, "smith2020")

    def test_set_citation_key_collision_raises_even_with_force(self):
        p1 = self.papers.create("Title 1", "article")
        p2 = self.papers.create("Title 2", "article")
        self.papers.set_citation_key(p1, "smith2020")
        with self.assertRaises(ValueError):
            self.papers.set_citation_key(p2, "smith2020", force=True)

    def test_set_citation_key_missing_paper_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.papers.set_citation_key("nonexistent", "key")

    def test_soft_delete_and_restore(self):
        paper_id = self.papers.create("Title", "article")
        self.papers.soft_delete(paper_id)
        self.assertIsNone(self.papers.get(paper_id))
        self.assertIsNotNone(self.papers.get(paper_id, include_deleted=True))
        self.papers.restore(paper_id)
        self.assertIsNotNone(self.papers.get(paper_id))

    def test_soft_delete_is_idempotent(self):
        paper_id = self.papers.create("Title", "article")
        self.papers.soft_delete(paper_id)
        self.papers.soft_delete(paper_id)  # should not raise or double-log
        entries = self.audit.list_for_entity("paper", paper_id)
        soft_deletes = [e for e in entries if e["action"] == "soft_delete"]
        self.assertEqual(len(soft_deletes), 1)

    def test_restore_is_idempotent(self):
        paper_id = self.papers.create("Title", "article")
        self.papers.restore(paper_id)  # already active, no-op
        entries = self.audit.list_for_entity("paper", paper_id)
        restores = [e for e in entries if e["action"] == "restore"]
        self.assertEqual(len(restores), 0)

    def test_list_excludes_deleted_by_default(self):
        p1 = self.papers.create("Kept", "article")
        p2 = self.papers.create("Deleted", "article")
        self.papers.soft_delete(p2)
        visible_ids = {p["id"] for p in self.papers.list()}
        self.assertIn(p1, visible_ids)
        self.assertNotIn(p2, visible_ids)
        all_ids = {p["id"] for p in self.papers.list(include_deleted=True)}
        self.assertIn(p2, all_ids)


class AuditLogTest(RepoTestBase):
    def test_create_writes_audit_entry(self):
        paper_id = self.papers.create("Title", "article")
        entries = self.audit.list_for_entity("paper", paper_id)
        creates = [e for e in entries if e["action"] == "create"]
        self.assertEqual(len(creates), 1)
        self.assertIsNone(creates[0]["before"])
        self.assertEqual(creates[0]["after"]["id"], paper_id)
        self.assertEqual(creates[0]["reversible"], 0)

    def test_soft_delete_writes_audit_entry(self):
        paper_id = self.papers.create("Title", "article")
        self.papers.soft_delete(paper_id)
        entries = self.audit.list_for_entity("paper", paper_id)
        soft_deletes = [e for e in entries if e["action"] == "soft_delete"]
        self.assertEqual(len(soft_deletes), 1)
        self.assertEqual(soft_deletes[0]["reversible"], 1)

    def test_update_metadata_writes_audit_entry_with_metadata_snapshots(self):
        paper_id = self.papers.create("Title", "article", metadata={"a": 1})
        self.papers.update_metadata(paper_id, {"b": 2})
        entries = self.audit.list_for_entity("paper", paper_id)
        updates = [e for e in entries if e["action"] == "update_metadata"]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["before"], {"a": 1})
        self.assertEqual(updates[0]["after"], {"a": 1, "b": 2})

    def test_identifier_add_writes_audit_entry(self):
        paper_id = self.papers.create("Title", "article")
        identifier_id = self.identifiers.add(paper_id, "doi", "10.1000/xyz123")
        entries = self.audit.list_for_entity("identifier", identifier_id)
        creates = [e for e in entries if e["action"] == "create"]
        self.assertEqual(len(creates), 1)

    def test_reassign_writes_audit_entry(self):
        p1 = self.papers.create("Paper 1", "article")
        p2 = self.papers.create("Paper 2", "article")
        identifier_id = self.identifiers.add(p1, "doi", "10.1000/xyz123")
        self.identifiers.reassign(identifier_id, p2)
        entries = self.audit.list_for_entity("identifier", identifier_id)
        reassigns = [e for e in entries if e["action"] == "reassign"]
        self.assertEqual(len(reassigns), 1)
        self.assertEqual(reassigns[0]["before"], {"paper_id": p1})
        self.assertEqual(reassigns[0]["after"], {"paper_id": p2})


class IdentifierRepositoryTest(RepoTestBase):
    def test_add_and_list_for_paper(self):
        paper_id = self.papers.create("Title", "article")
        self.identifiers.add(paper_id, "doi", "10.1000/xyz123")
        rows = self.identifiers.list_for_paper(paper_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scheme"], "doi")
        self.assertEqual(rows[0]["value"], "10.1000/xyz123")

    def test_add_normalizes_value(self):
        paper_id = self.papers.create("Title", "article")
        self.identifiers.add(paper_id, "doi", "https://doi.org/10.1000/XYZ123")
        rows = self.identifiers.list_for_paper(paper_id)
        self.assertEqual(rows[0]["value"], "10.1000/xyz123")

    def test_add_is_idempotent_for_same_paper(self):
        paper_id = self.papers.create("Title", "article")
        id1 = self.identifiers.add(paper_id, "doi", "10.1000/xyz123")
        id2 = self.identifiers.add(paper_id, "doi", "10.1000/xyz123")
        self.assertEqual(id1, id2)
        rows = self.identifiers.list_for_paper(paper_id)
        self.assertEqual(len(rows), 1)

    def test_add_conflict_raises_for_different_paper(self):
        p1 = self.papers.create("Paper 1", "article")
        p2 = self.papers.create("Paper 2", "article")
        self.identifiers.add(p1, "doi", "10.1000/xyz123")
        with self.assertRaises(IdentifierConflictError) as ctx:
            self.identifiers.add(p2, "doi", "10.1000/xyz123")
        err = ctx.exception
        self.assertEqual(err.scheme, "doi")
        self.assertEqual(err.value, "10.1000/xyz123")
        self.assertEqual(err.existing_paper_id, p1)
        self.assertEqual(err.new_paper_id, p2)

    def test_find_paper_by_identifier(self):
        paper_id = self.papers.create("Title", "article")
        self.identifiers.add(paper_id, "pmid", "12345")
        found = self.identifiers.find_paper_by_identifier("pmid", "PMID: 12345")
        self.assertEqual(found, paper_id)

    def test_find_paper_by_identifier_missing_returns_none(self):
        self.assertIsNone(self.identifiers.find_paper_by_identifier("doi", "10.1000/nope"))

    def test_reassign(self):
        p1 = self.papers.create("Paper 1", "article")
        p2 = self.papers.create("Paper 2", "article")
        identifier_id = self.identifiers.add(p1, "doi", "10.1000/xyz123")
        self.identifiers.reassign(identifier_id, p2)
        rows = self.identifiers.list_for_paper(p2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.identifiers.list_for_paper(p1), [])

    def test_reassign_missing_identifier_raises_keyerror(self):
        p2 = self.papers.create("Paper 2", "article")
        with self.assertRaises(KeyError):
            self.identifiers.reassign("nonexistent", p2)


if __name__ == "__main__":
    unittest.main()
