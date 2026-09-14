import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import db
from refmgr.repositories.saved_searches import SavedSearchRepository


class SavedSearchRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conn = db.open_and_migrate(self.tmp)
        self.repo = SavedSearchRepository(self.conn)

    def test_create_and_get_round_trip(self):
        query = {"query": "diabetes", "year_from": 2020, "tag": "to-read"}
        saved_search_id = self.repo.create("diabetes-recent", query)
        result = self.repo.get(saved_search_id)
        self.assertIsNotNone(result)
        self.assertEqual(result["id"], saved_search_id)
        self.assertEqual(result["name"], "diabetes-recent")
        self.assertEqual(result["query"], query)
        self.assertIn("created_at", result)

    def test_create_duplicate_name_raises_value_error(self):
        self.repo.create("dup", {"query": "a"})
        with self.assertRaises(ValueError):
            self.repo.create("dup", {"query": "b"})

    def test_get_nonexistent_returns_none(self):
        self.assertIsNone(self.repo.get("nonexistent"))

    def test_get_by_name_round_trip_and_nonexistent(self):
        query = {"query": "cancer"}
        self.repo.create("cancer-search", query)
        result = self.repo.get_by_name("cancer-search")
        self.assertIsNotNone(result)
        self.assertEqual(result["query"], query)
        self.assertIsNone(self.repo.get_by_name("does-not-exist"))

    def test_list_ordering_and_pagination(self):
        ids = []
        for i in range(5):
            ids.append(self.repo.create(f"search-{i}", {"query": str(i)}))
            time.sleep(0.001)
        all_results = self.repo.list(limit=50, offset=0)
        self.assertEqual([r["id"] for r in all_results], ids)

        page1 = self.repo.list(limit=2, offset=0)
        page2 = self.repo.list(limit=2, offset=2)
        self.assertEqual([r["id"] for r in page1], ids[0:2])
        self.assertEqual([r["id"] for r in page2], ids[2:4])

    def test_rename_updates_name(self):
        saved_search_id = self.repo.create("old-name", {"query": "x"})
        self.repo.rename(saved_search_id, "new-name")
        result = self.repo.get(saved_search_id)
        self.assertEqual(result["name"], "new-name")

    def test_rename_to_taken_name_raises_value_error(self):
        self.repo.create("taken", {"query": "x"})
        saved_search_id = self.repo.create("mine", {"query": "y"})
        with self.assertRaises(ValueError):
            self.repo.rename(saved_search_id, "taken")

    def test_rename_to_own_current_name_is_noop_success(self):
        saved_search_id = self.repo.create("same-name", {"query": "x"})
        self.repo.rename(saved_search_id, "same-name")
        result = self.repo.get(saved_search_id)
        self.assertEqual(result["name"], "same-name")

    def test_rename_nonexistent_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.repo.rename("nonexistent", "whatever")

    def test_update_query_replaces_query(self):
        saved_search_id = self.repo.create("q", {"query": "old"})
        new_query = {"query": "new", "year_to": 2025}
        self.repo.update_query(saved_search_id, new_query)
        result = self.repo.get(saved_search_id)
        self.assertEqual(result["query"], new_query)

    def test_update_query_nonexistent_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.repo.update_query("nonexistent", {"query": "x"})

    def test_delete_removes_row_and_is_idempotent(self):
        saved_search_id = self.repo.create("to-delete", {"query": "x"})
        self.repo.delete(saved_search_id)
        self.assertIsNone(self.repo.get(saved_search_id))
        # deleting again (already deleted) must not raise
        self.repo.delete(saved_search_id)
        # deleting a never-existing id must not raise
        self.repo.delete("never-existed")

    def test_concurrent_writers_do_not_lose_updates(self):
        conn_a = db.get_connection(self.tmp)
        conn_b = db.get_connection(self.tmp)
        repo_a = SavedSearchRepository(conn_a)
        repo_b = SavedSearchRepository(conn_b)
        id_a = repo_a.create("from-a", {"query": "a"})
        id_b = repo_b.create("from-b", {"query": "b"})
        rows = {r["id"] for r in self.conn.execute("SELECT id FROM saved_searches").fetchall()}
        self.assertEqual(rows, {id_a, id_b})


if __name__ == "__main__":
    unittest.main()
