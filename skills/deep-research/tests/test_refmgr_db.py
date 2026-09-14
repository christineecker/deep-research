import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import db


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_migrate_creates_expected_tables(self):
        conn = db.open_and_migrate(self.tmp)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = {row["name"] for row in rows}
        expected = {
            "papers", "identifiers", "assets", "attachments",
            "collections", "collection_members", "tags", "paper_tags",
            "audit_log", "merges", "schema_migrations",
        }
        self.assertTrue(expected.issubset(names))

    def test_migrate_is_idempotent(self):
        conn = db.open_and_migrate(self.tmp)
        applied_again = db.migrate(conn)
        self.assertEqual(applied_again, [])

    def test_migrate_records_applied_migration(self):
        conn = db.open_and_migrate(self.tmp)
        rows = conn.execute("SELECT id FROM schema_migrations").fetchall()
        self.assertIn("0001_init", {row["id"] for row in rows})

    def test_pragmas_set(self):
        conn = db.open_and_migrate(self.tmp)
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        self.assertEqual(foreign_keys, 1)

    def test_reopening_existing_library_does_not_error(self):
        db.open_and_migrate(self.tmp)
        conn2 = db.open_and_migrate(self.tmp)
        rows = conn2.execute("SELECT COUNT(*) AS n FROM papers").fetchone()
        self.assertEqual(rows["n"], 0)


class TransactionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conn = db.open_and_migrate(self.tmp)

    def _insert_paper(self, conn, paper_id):
        conn.execute(
            "INSERT INTO papers (id, title, paper_type, metadata_json, created_at, updated_at) "
            "VALUES (?, 'T', 'article', '{}', 'now', 'now')",
            (paper_id,),
        )

    def test_commit_persists(self):
        with db.transaction(self.conn):
            self._insert_paper(self.conn, "p1")
        row = self.conn.execute("SELECT id FROM papers WHERE id='p1'").fetchone()
        self.assertIsNotNone(row)

    def test_exception_rolls_back(self):
        with self.assertRaises(RuntimeError):
            with db.transaction(self.conn):
                self._insert_paper(self.conn, "p2")
                raise RuntimeError("boom")
        row = self.conn.execute("SELECT id FROM papers WHERE id='p2'").fetchone()
        self.assertIsNone(row)

    def test_concurrent_writers_do_not_lose_updates(self):
        # Two separate connections to the same library each try to insert a
        # distinct row inside BEGIN IMMEDIATE. Neither should silently lose
        # the other's write: after both finish, both rows must be present.
        conn_a = db.get_connection(self.tmp)
        conn_b = db.get_connection(self.tmp)
        with db.transaction(conn_a):
            self._insert_paper(conn_a, "pa")
        with db.transaction(conn_b):
            self._insert_paper(conn_b, "pb")
        rows = {r["id"] for r in self.conn.execute("SELECT id FROM papers").fetchall()}
        self.assertEqual(rows, {"pa", "pb"})


class MigrationAtomicityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_failed_migration_leaves_no_partial_schema(self):
        migrations_dir = self.tmp / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "0001_bad.sql").write_text(
            "CREATE TABLE foo (id TEXT);\n"
            "CREATE TABLE THIS IS NOT VALID SQL;\n"
        )
        conn = db.get_connection(self.tmp / "lib")
        with self.assertRaises(sqlite3.OperationalError):
            db.migrate(conn, migrations_dir=migrations_dir)

        # The whole unit rolls back together: the first statement's table
        # and the schema_migrations bookkeeping table it shared a
        # transaction with are both gone -- nothing is left half-applied.
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertNotIn("foo", tables)
        self.assertNotIn("schema_migrations", tables)
        # No transaction is left open/holding the write lock.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")

    def test_open_and_migrate_closes_connection_on_failure(self):
        migrations_dir = self.tmp / "migrations"
        migrations_dir.mkdir()
        (migrations_dir / "0001_bad.sql").write_text("NOT VALID SQL;\n")

        with self.assertRaises(sqlite3.OperationalError):
            db.open_and_migrate(self.tmp / "lib", migrations_dir=migrations_dir)

        # A closed connection raises ProgrammingError on any further use.
        # We cannot get the connection object back from open_and_migrate
        # (it raised), so instead verify a fresh connection can immediately
        # take the write lock -- which a leaked, still-open transaction on
        # an unclosed connection would block via busy_timeout.
        conn = db.get_connection(self.tmp / "lib")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")

    def test_concurrent_initialization_does_not_double_apply(self):
        conn_a = db.get_connection(self.tmp)
        conn_b = db.get_connection(self.tmp)
        applied_a = db.migrate(conn_a)
        applied_b = db.migrate(conn_b)
        # Between two connections, each migration is applied exactly once.
        self.assertEqual(set(applied_a) & set(applied_b), set())
        rows = conn_a.execute("SELECT id, COUNT(*) AS c FROM schema_migrations GROUP BY id").fetchall()
        for row in rows:
            self.assertEqual(row["c"], 1)


if __name__ == "__main__":
    unittest.main()
