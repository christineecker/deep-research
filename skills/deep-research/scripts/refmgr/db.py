"""SQLite connection and migration management for the reference manager.

Each write operation must run inside `transaction(conn)`, which issues
`BEGIN IMMEDIATE` up front. That takes SQLite's write lock before any
statement runs, closing the read-then-lock window that let concurrent
writers on the old JSONL registry silently clobber each other (see
REFERENCE_MANAGER_V2_PLAN.md, "Known issues and standing decisions").
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

DB_FILENAME = "library.sqlite3"
BUSY_TIMEOUT_MS = 5000
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _split_statements(sql: str) -> list:
    """Split a migration file into individually executable statements.

    `executescript()` cannot be used here: it implicitly commits any open
    transaction before it runs, so it cannot participate in a
    caller-managed `BEGIN IMMEDIATE` and cannot be rolled back as a unit.
    Migration files are plain sequential DDL with no semicolons inside
    string literals, so a comment-stripped split on `;` is safe.
    """
    cleaned = _strip_sql_comments(sql)
    return [s.strip() for s in cleaned.split(";") if s.strip()]


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "id TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )


def _apply_migration(conn: sqlite3.Connection, path: Path) -> bool:
    """Apply one migration file plus its tracking row as a single atomic unit.

    Takes the write lock with `BEGIN IMMEDIATE` *before* checking whether
    this migration is still pending, then re-checks under that lock. That
    closes the race where two processes both see a migration as pending and
    both try to apply it: the second to acquire the lock finds the tracking
    row already present and skips. On any failure the whole unit -- schema
    changes and tracking row alike -- rolls back together, so a migration
    can never be left partially applied.

    Returns True if this call applied the migration, False if it was
    already applied (by an earlier call or a racing writer).
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        _ensure_migrations_table(conn)
        already = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE id = ?", (path.stem,)
        ).fetchone()
        if already is not None:
            conn.execute("COMMIT")
            return False
        for statement in _split_statements(path.read_text(encoding="utf-8")):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
            (path.stem, _now()),
        )
        conn.execute("COMMIT")
        return True
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list:
    """Apply pending .sql migrations in filename order. Returns ids applied."""
    newly_applied = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if _apply_migration(conn, path):
            newly_applied.append(path.stem)
    return newly_applied


def open_and_migrate(
    library_root: Path, migrations_dir: Path = MIGRATIONS_DIR
) -> sqlite3.Connection:
    """Convenience: open a connection and bring the schema up to date.

    Closes the connection before propagating a migration failure -- a
    caller that gets an exception from this function must not also be
    left holding an open, half-migrated connection to clean up.
    """
    conn = get_connection(library_root)
    try:
        migrate(conn, migrations_dir=migrations_dir)
    except BaseException:
        conn.close()
        raise
    return conn
