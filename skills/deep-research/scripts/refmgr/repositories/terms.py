"""Controlled-vocabulary facets: MeSH headings, keywords, article types, authors.

`papers.metadata_json` stores these; this indexes them, so "every paper tagged
`Adolescent`" is a lookup rather than a scan over every row's JSON blob
(migrations/0004_terms.sql). Derived and rebuildable from the registry, like the two
FTS indexes.

Matching is on `value_norm` — casefolded, whitespace-collapsed — so a query does not
have to guess how a source capitalized a heading. `value` keeps the original spelling
for display, and when several spellings normalize together the first one indexed wins,
which is good enough for a facet label and avoids a second authority table.

Every write happens inside `db.transaction(conn)`.
"""

from __future__ import annotations

import sqlite3

from .. import db

#: Registry/metadata field -> facet scheme. Authors are included because "everything by
#: this group" is a real question a library gets asked; they are stored as the same
#: display strings the registry holds, not parsed into name parts.
SCHEME_FIELDS = {
    "mesh": "mesh_terms",
    "keyword": "keywords",
    "article_type": "article_types",
    "author": "authors",
}
SCHEMES = tuple(SCHEME_FIELDS)


def normalize(value: str) -> str:
    return " ".join(str(value).split()).casefold()


def terms_from_metadata(metadata: dict) -> list[tuple[str, str]]:
    """`(scheme, value)` pairs from a registry record or refmgr `metadata` dict.

    Non-list fields and non-string entries are skipped rather than coerced: a malformed
    `mesh_terms` is a data problem to see in `reindex`'s output, not something to paper
    over with `str()`.
    """
    pairs: list[tuple[str, str]] = []
    for scheme, field in SCHEME_FIELDS.items():
        values = metadata.get(field)
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and value.strip():
                pairs.append((scheme, value.strip()))
    return pairs


class TermRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def set_terms(self, paper_id: str, terms: list[tuple[str, str]]) -> int:
        """Replace every term for `paper_id`. Returns the number of rows written."""
        with db.transaction(self.conn):
            self.conn.execute("DELETE FROM paper_terms WHERE paper_id = ?", (paper_id,))
            written = 0
            for scheme, value in terms:
                cursor = self.conn.execute(
                    "INSERT OR IGNORE INTO paper_terms "
                    "(paper_id, scheme, value, value_norm) VALUES (?, ?, ?, ?)",
                    (paper_id, scheme, value, normalize(value)),
                )
                written += cursor.rowcount or 0
            return written

    def reassign_paper(self, old_paper_id: str, new_paper_id: str) -> dict:
        """Move every term row from one paper to another.

        Used by merge/revert. `paper_terms` is `PRIMARY KEY (paper_id, scheme,
        value_norm)`, so a term the destination paper already carries cannot be
        moved on top of it -- that row is dropped from the source instead,
        since the destination already holds an equivalent value. Returns
        `{"moved": [...], "dropped_duplicates": [...]}`, both lists of
        `{"scheme", "value", "value_norm"}`; only `moved` rows need undoing on
        revert, since a dropped duplicate's information already survives on
        the destination.
        """
        with db.transaction(self.conn):
            return self._reassign_paper_locked(old_paper_id, new_paper_id)

    def _reassign_paper_locked(self, old_paper_id: str, new_paper_id: str) -> dict:
        rows = self.conn.execute(
            "SELECT scheme, value, value_norm FROM paper_terms WHERE paper_id = ?",
            (old_paper_id,),
        ).fetchall()
        moved, dropped = [], []
        for row in rows:
            scheme, value, value_norm = row["scheme"], row["value"], row["value_norm"]
            exists = self.conn.execute(
                "SELECT 1 FROM paper_terms WHERE paper_id = ? AND scheme = ? "
                "AND value_norm = ?",
                (new_paper_id, scheme, value_norm),
            ).fetchone()
            self.conn.execute(
                "DELETE FROM paper_terms WHERE paper_id = ? AND scheme = ? "
                "AND value_norm = ?",
                (old_paper_id, scheme, value_norm),
            )
            entry = {"scheme": scheme, "value": value, "value_norm": value_norm}
            if exists is not None:
                dropped.append(entry)
                continue
            self.conn.execute(
                "INSERT INTO paper_terms (paper_id, scheme, value, value_norm) "
                "VALUES (?, ?, ?, ?)",
                (new_paper_id, scheme, value, value_norm),
            )
            moved.append(entry)
        return {"moved": moved, "dropped_duplicates": dropped}

    def remove_paper(self, paper_id: str) -> None:
        with db.transaction(self.conn):
            self.conn.execute("DELETE FROM paper_terms WHERE paper_id = ?", (paper_id,))

    def for_paper(self, paper_id: str) -> dict[str, list[str]]:
        rows = self.conn.execute(
            "SELECT scheme, value FROM paper_terms WHERE paper_id = ? "
            "ORDER BY scheme, value",
            (paper_id,),
        ).fetchall()
        out: dict[str, list[str]] = {}
        for row in rows:
            out.setdefault(row["scheme"], []).append(row["value"])
        return out

    def papers_with_term(self, scheme: str, value: str) -> set[str]:
        """Papers carrying this term. Substring match, so `--mesh depress` finds
        "Depressive Disorder, Major" — MeSH headings are long and users type fragments."""
        pattern = "%" + normalize(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self.conn.execute(
            "SELECT DISTINCT t.paper_id AS paper_id FROM paper_terms t "
            "JOIN papers p ON p.id = t.paper_id "
            "WHERE t.scheme = ? AND t.value_norm LIKE ? ESCAPE '\\' "
            "AND p.deleted_at IS NULL",
            (scheme, pattern),
        ).fetchall()
        return {row["paper_id"] for row in rows}

    def facets(self, scheme: str, *, limit: int = 50) -> list[dict]:
        """The most common values for one scheme, with paper counts.

        This is what makes the index worth having beyond filtering: it answers "what is
        actually in this library" without reading every record.
        """
        rows = self.conn.execute(
            "SELECT t.value_norm AS value_norm, MIN(t.value) AS value, "
            "COUNT(DISTINCT t.paper_id) AS papers FROM paper_terms t "
            "JOIN papers p ON p.id = t.paper_id "
            "WHERE t.scheme = ? AND p.deleted_at IS NULL "
            "GROUP BY t.value_norm ORDER BY papers DESC, value_norm ASC LIMIT ?",
            (scheme, max(1, int(limit))),
        ).fetchall()
        return [{"value": row["value"], "papers": row["papers"]} for row in rows]

    def coverage(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) AS c FROM paper_terms").fetchone()["c"]
        papers = self.conn.execute(
            "SELECT COUNT(DISTINCT paper_id) AS c FROM paper_terms"
        ).fetchone()["c"]
        by_scheme = {
            row["scheme"]: row["c"]
            for row in self.conn.execute(
                "SELECT scheme, COUNT(*) AS c FROM paper_terms GROUP BY scheme"
            ).fetchall()
        }
        return {"terms": total, "papers_with_terms": papers, "by_scheme": by_scheme}
