import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import db, identity
from refmgr.repositories.organization import OrganizationRepository


class OrganizationRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conn = db.open_and_migrate(self.tmp)
        self.addCleanup(self.conn.close)
        self.repo = OrganizationRepository(self.conn)
        self.paper_a = self._make_paper("Paper A")
        self.paper_b = self._make_paper("Paper B")

    def _make_paper(self, title: str) -> str:
        paper_id = identity.new_id()
        now = "2026-01-01T00:00:00+00:00"
        with db.transaction(self.conn):
            self.conn.execute(
                "INSERT INTO papers "
                "(id, title, paper_type, metadata_json, created_at, updated_at) "
                "VALUES (?, ?, 'article', '{}', ?, ?)",
                (paper_id, title, now, now),
            )
        return paper_id

    # -- collections ---------------------------------------------------

    def test_create_and_list_collections(self):
        cid1 = self.repo.create_collection("Reading List")
        cid2 = self.repo.create_collection("Thesis", kind="project")

        all_collections = self.repo.list_collections()
        self.assertEqual({c["id"] for c in all_collections}, {cid1, cid2})

        projects = self.repo.list_collections(kind="project")
        self.assertEqual([c["id"] for c in projects], [cid2])

        collections_only = self.repo.list_collections(kind="collection")
        self.assertEqual([c["id"] for c in collections_only], [cid1])

    def test_get_collection(self):
        cid = self.repo.create_collection("Reading List")
        got = self.repo.get_collection(cid)
        self.assertEqual(got["name"], "Reading List")
        self.assertEqual(got["kind"], "collection")
        self.assertIsNone(self.repo.get_collection("nonexistent"))

    # -- membership ------------------------------------------------------

    def test_add_to_collection_idempotent(self):
        cid = self.repo.create_collection("Reading List")
        self.repo.add_to_collection(cid, self.paper_a)
        self.repo.add_to_collection(cid, self.paper_a)

        rows = self.conn.execute(
            "SELECT COUNT(*) AS n FROM collection_members "
            "WHERE collection_id = ? AND paper_id = ?",
            (cid, self.paper_a),
        ).fetchone()
        self.assertEqual(rows["n"], 1)

    def test_add_to_collection_raises_for_missing_collection(self):
        with self.assertRaises(KeyError):
            self.repo.add_to_collection("nonexistent", self.paper_a)

    def test_remove_from_collection_idempotent_noop(self):
        cid = self.repo.create_collection("Reading List")
        # Never added -- removing should be a silent no-op.
        self.repo.remove_from_collection(cid, self.paper_a)

        self.repo.add_to_collection(cid, self.paper_a)
        self.repo.remove_from_collection(cid, self.paper_a)
        self.repo.remove_from_collection(cid, self.paper_a)
        self.assertEqual(self.repo.list_members(cid), [])

    def test_list_members_ordering(self):
        cid = self.repo.create_collection("Reading List")
        self.repo.add_to_collection(cid, self.paper_a)
        self.repo.add_to_collection(cid, self.paper_b)
        self.assertEqual(
            self.repo.list_members(cid), [self.paper_a, self.paper_b]
        )

    def test_list_collections_for_paper(self):
        cid1 = self.repo.create_collection("Reading List")
        cid2 = self.repo.create_collection("Thesis", kind="project")
        self.repo.add_to_collection(cid1, self.paper_a)
        self.repo.add_to_collection(cid2, self.paper_a)

        collections = self.repo.list_collections_for_paper(self.paper_a)
        self.assertEqual({c["id"] for c in collections}, {cid1, cid2})
        self.assertEqual(self.repo.list_collections_for_paper(self.paper_b), [])

    # -- tags --------------------------------------------------------------

    def test_add_tag_shared_row_across_papers(self):
        self.repo.add_tag(self.paper_a, "important")
        self.repo.add_tag(self.paper_b, "important")

        tag_rows = self.conn.execute(
            "SELECT id FROM tags WHERE name = 'important'"
        ).fetchall()
        self.assertEqual(len(tag_rows), 1)

        link_rows = self.conn.execute(
            "SELECT paper_id FROM paper_tags WHERE tag_id = ?",
            (tag_rows[0]["id"],),
        ).fetchall()
        self.assertEqual(
            {row["paper_id"] for row in link_rows}, {self.paper_a, self.paper_b}
        )

    def test_add_tag_idempotent_per_paper(self):
        self.repo.add_tag(self.paper_a, "important")
        self.repo.add_tag(self.paper_a, "important")
        link_rows = self.conn.execute(
            "SELECT COUNT(*) AS n FROM paper_tags"
        ).fetchone()
        self.assertEqual(link_rows["n"], 1)

    def test_remove_tag_keeps_shared_tag_row(self):
        self.repo.add_tag(self.paper_a, "important")
        self.repo.add_tag(self.paper_b, "important")

        self.repo.remove_tag(self.paper_a, "important")

        self.assertEqual(self.repo.list_tags(self.paper_a), [])
        self.assertEqual(self.repo.list_tags(self.paper_b), ["important"])
        tag_rows = self.conn.execute(
            "SELECT id FROM tags WHERE name = 'important'"
        ).fetchall()
        self.assertEqual(len(tag_rows), 1)

    def test_remove_tag_noop_when_not_tagged_or_unknown(self):
        # Tag doesn't exist at all.
        self.repo.remove_tag(self.paper_a, "nonexistent")
        # Tag exists but paper isn't linked to it.
        self.repo.add_tag(self.paper_b, "important")
        self.repo.remove_tag(self.paper_a, "important")
        self.assertEqual(self.repo.list_tags(self.paper_a), [])

    def test_list_tags_sorted_alphabetically(self):
        self.repo.add_tag(self.paper_a, "zeta")
        self.repo.add_tag(self.paper_a, "alpha")
        self.repo.add_tag(self.paper_a, "mid")
        self.assertEqual(
            self.repo.list_tags(self.paper_a), ["alpha", "mid", "zeta"]
        )

    def test_list_papers_with_tag(self):
        self.repo.add_tag(self.paper_a, "important")
        self.repo.add_tag(self.paper_b, "important")
        self.assertEqual(
            set(self.repo.list_papers_with_tag("important")),
            {self.paper_a, self.paper_b},
        )
        self.assertEqual(self.repo.list_papers_with_tag("unknown"), [])


if __name__ == "__main__":
    unittest.main()
