"""SQLite connection and migration management for the reference manager.

Each write operation must run inside `transaction(conn)`, which issues
`BEGIN IMMEDIATE` up front. That takes SQLite's write lock before any
statement runs, closing the read-then-lock window that let concurrent
writers on the old JSONL registry silently clobber each other (see
REFERENCE_MANAGER_V2_PLAN.md, "Known issues and standing decisions").
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_FILENAME = "library.sqlite3"
BUSY_TIMEOUT_MS = 5000
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_connection(library_root: Path) -> sqlite3.Connection:
    """Open (creating if needed) the library database with required pragmas."""
    library_root = Path(library_root)
    library_root.mkdir(parents=True, exist_ok=True)
    db_path = library_root / DB_FILENAME
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Run a block as a single immediate-lock write transaction.

    `BEGIN IMMEDIATE` acquires the write lock before the block runs any
    statement, so a concurrent writer blocks (or retries via busy_timeout)
    at the start of the transaction rather than racing a read that happened
    before either side took a lock.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _applied_migrations(conn: sqlite3.Connection) -> set:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    rows = conn.execute("SELECT id FROM schema_migrations").fetchall()
    return {row["id"] for row in rows}


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list:
    """Apply pending .sql migrations in filename order. Returns ids applied."""
    applied = _applied_migrations(conn)
    pending = sorted(p for p in migrations_dir.glob("*.sql") if p.stem not in applied)
    newly_applied = []
    for path in pending:
        sql = path.read_text(encoding="utf-8")
        # executescript() implicitly commits any open transaction and runs
        # outside our transaction() helper's BEGIN/COMMIT bookkeeping, so
        # the tracking insert is appended to the same script to keep schema
        # changes and the migration record atomic with each other.
        tracked_sql = (
            sql
            + "\nINSERT OR IGNORE INTO schema_migrations (id, applied_at) "
            + f"VALUES ('{path.stem}', strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
        )
        conn.executescript(tracked_sql)
        newly_applied.append(path.stem)
    return newly_applied


def open_and_migrate(library_root: Path) -> sqlite3.Connection:
    """Convenience: open a connection and bring the schema up to date."""
    conn = get_connection(library_root)
    migrate(conn)
    return conn
