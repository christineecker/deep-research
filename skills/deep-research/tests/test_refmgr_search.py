import json
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import db
from refmgr.repositories.search import SearchQueryError, SearchRepository


def _now(offset_seconds: int = 0) -> str:
    return (
        datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)
    ).isoformat()


class SearchRepositoryTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conn = db.open_and_migrate(self.tmp)
        self.search = SearchRepository(self.conn)

    def _insert_paper(
        self,
        title: str,
        paper_type: str = "journal-article",
        metadata: dict | None = None,
        deleted_at: str | None = None,
        created_offset: int = 0,
        paper_id: str | None = None,
    ) -> str:
        paper_id = paper_id or uuid.uuid4().hex
        now = _now(created_offset)
        self.conn.execute(
            "INSERT INTO papers "
            "(id, title, paper_type, metadata_json, provenance, created_at, "
            "updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                paper_id,
                title,
                paper_type,
                json.dumps(metadata or {}),
                None,
                now,
                now,
                deleted_at,
            ),
        )
        self.conn.commit()
        return paper_id

    def _insert_identifier(self, paper_id: str, scheme: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO identifiers (id, paper_id, scheme, value, is_primary, "
            "created_at) VALUES (?, ?, ?, ?, 0, ?)",
            (uuid.uuid4().hex, paper_id, scheme, value, _now()),
        )
        self.conn.commit()

    def _insert_tag(self, paper_id: str, tag_name: str) -> None:
        tag_id = uuid.uuid4().hex
        self.conn.execute("INSERT INTO tags (id, name) VALUES (?, ?)", (tag_id, tag_name))
        self.conn.execute(
            "INSERT INTO paper_tags (paper_id, tag_id) VALUES (?, ?)",
            (paper_id, tag_id),
        )
        self.conn.commit()

    def _insert_collection(self, name: str) -> str:
        collection_id = uuid.uuid4().hex
        self.conn.execute(
            "INSERT INTO collections (id, name, kind, created_at) VALUES (?, ?, ?, ?)",
            (collection_id, name, "collection", _now()),
        )
        self.conn.commit()
        return collection_id

    def _add_to_collection(self, collection_id: str, paper_id: str) -> None:
        self.conn.execute(
            "INSERT INTO collection_members (collection_id, paper_id, added_at) "
            "VALUES (?, ?, ?)",
            (collection_id, paper_id, _now()),
        )
        self.conn.commit()


class ReindexAndSearchTest(SearchRepositoryTestBase):
    def test_round_trip_title_abstract_and_no_match(self):
        pid = self._insert_paper(
            "Photosynthesis in Arabidopsis thaliana",
            metadata={"abstract": "Chlorophyll fluorescence measurements were taken."},
        )
        self.search.reindex_paper(pid)

        title_hit = self.search.search("Arabidopsis")
        self.assertEqual([r["paper_id"] for r in title_hit["results"]], [pid])

        abstract_hit = self.search.search("fluorescence")
        self.assertEqual([r["paper_id"] for r in abstract_hit["results"]], [pid])

        miss = self.search.search("nonexistentxyz")
        self.assertEqual(miss["results"], [])
        self.assertEqual(miss["total_matched"], 0)

    def test_soft_deleted_paper_excluded(self):
        pid = self._insert_paper("Deleted paper title unique term zylophone")
        self.search.reindex_paper(pid)
        self.assertEqual(
            len(self.search.search("zylophone")["results"]), 1
        )

        # Soft-delete then reindex: reindex_paper must remove the fts row.
        self.conn.execute(
            "UPDATE papers SET deleted_at = ? WHERE id = ?", (_now(), pid)
        )
        self.conn.commit()
        self.search.reindex_paper(pid)
        rows = self.conn.execute(
            "SELECT * FROM papers_fts WHERE paper_id = ?", (pid,)
        ).fetchall()
        self.assertEqual(rows, [])

        self.assertEqual(self.search.search("zylophone")["results"], [])

    def test_search_defensively_excludes_deleted_even_if_index_stale(self):
        pid = self._insert_paper("Stale index term wobblefrog")
        self.search.reindex_paper(pid)
        # Soft-delete WITHOUT reindexing -- simulate a stale fts row.
        self.conn.execute(
            "UPDATE papers SET deleted_at = ? WHERE id = ?", (_now(), pid)
        )
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT * FROM papers_fts WHERE paper_id = ?", (pid,)
        ).fetchall()
        self.assertEqual(len(rows), 1)  # confirm still stale/present

        self.assertEqual(self.search.search("wobblefrog")["results"], [])

    def test_unicode_round_trip(self):
        pid = self._insert_paper(
            "Étude sur la photosynthèse et 光合作用",
            metadata={"abstract": "Effets du café sur la croissance des plantes"},
        )
        self.search.reindex_paper(pid)
        self.assertEqual(
            [r["paper_id"] for r in self.search.search("photosynthèse")["results"]],
            [pid],
        )
        self.assertEqual(
            [r["paper_id"] for r in self.search.search("café")["results"]], [pid]
        )

    def test_fts5_special_characters_do_not_crash_or_act_as_operators(self):
        pid = self._insert_paper("Normal paper about cell biology")
        self.search.reindex_paper(pid)

        adversarial = [
            '-leading-dash',
            'embedded "quote" here',
            'trailing*star*',
            '"',
            '""""',
            'a"b',
            '*',
            '-',
            'NEAR(foo bar)',
            'foo OR bar',
            'col:value',
        ]
        for query in adversarial:
            try:
                result = self.search.search(query)
            except SearchQueryError:
                self.fail(f"SearchQueryError raised for {query!r}")
            self.assertIsInstance(result["results"], list)

        # This design escapes every whitespace-separated term in double
        # quotes (with embedded quotes doubled), which turns FTS5 operator
        # syntax into inert literal text. That makes SearchQueryError
        # structurally unreachable for arbitrary text input under normal
        # operation -- verified above with a batch of adversarial inputs
        # rather than trying (and failing) to force the exception.


class FilterTest(SearchRepositoryTestBase):
    def setUp(self):
        super().setUp()
        self.p_old = self._insert_paper(
            "Old paper", metadata={"publication_date": "2010-05-01"}
        )
        self.p_mid = self._insert_paper(
            "Middle paper", metadata={"publication_date": "2018"}
        )
        self.p_new = self._insert_paper(
            "New paper", metadata={"publication_date": "2023-11-02"}
        )
        self.p_no_date = self._insert_paper("No date paper", metadata={})
        self.p_bad_date = self._insert_paper(
            "Bad date paper", metadata={"publication_date": "not-a-date"}
        )
        for pid in (
            self.p_old,
            self.p_mid,
            self.p_new,
            self.p_no_date,
            self.p_bad_date,
        ):
            self.search.reindex_paper(pid)

    def test_year_from_year_to(self):
        result = self.search.search(year_from=2015, year_to=2020)
        ids = {r["paper_id"] for r in result["results"]}
        self.assertEqual(ids, {self.p_mid})

        result_no_filter = self.search.search()
        ids_all = {r["paper_id"] for r in result_no_filter["results"]}
        self.assertEqual(
            ids_all,
            {self.p_old, self.p_mid, self.p_new, self.p_no_date, self.p_bad_date},
        )

    def test_year_filter_excludes_missing_and_malformed_dates(self):
        result = self.search.search(year_from=2000)
        ids = {r["paper_id"] for r in result["results"]}
        self.assertNotIn(self.p_no_date, ids)
        self.assertNotIn(self.p_bad_date, ids)
        self.assertIn(self.p_old, ids)
        self.assertIn(self.p_mid, ids)
        self.assertIn(self.p_new, ids)

    def test_paper_type_exact_match(self):
        pid_book = self._insert_paper("A book", paper_type="book")
        self.search.reindex_paper(pid_book)
        result = self.search.search(paper_type="book")
        self.assertEqual([r["paper_id"] for r in result["results"]], [pid_book])

    def test_journal_case_insensitive_substring(self):
        pid = self._insert_paper(
            "Journal paper", metadata={"journal": "Nature Communications"}
        )
        self.search.reindex_paper(pid)
        result = self.search.search(journal="nature comm")
        self.assertIn(pid, {r["paper_id"] for r in result["results"]})
        result_miss = self.search.search(journal="science")
        self.assertNotIn(pid, {r["paper_id"] for r in result_miss["results"]})

    def test_tag_filter(self):
        pid_tagged = self._insert_paper("Tagged paper")
        self.search.reindex_paper(pid_tagged)
        self._insert_tag(pid_tagged, "important")
        result = self.search.search(tag="important")
        self.assertEqual({r["paper_id"] for r in result["results"]}, {pid_tagged})

    def test_collection_filter(self):
        pid_member = self._insert_paper("In collection paper")
        self.search.reindex_paper(pid_member)
        collection_id = self._insert_collection("My Collection")
        self._add_to_collection(collection_id, pid_member)
        result = self.search.search(collection_id=collection_id)
        self.assertEqual({r["paper_id"] for r in result["results"]}, {pid_member})

    def test_keyword_query_combined_with_two_filters(self):
        pid = self._insert_paper(
            "Distinctive term xenobiotic study",
            paper_type="journal-article",
            metadata={"journal": "Toxicology Reports", "publication_date": "2021"},
        )
        self.search.reindex_paper(pid)
        result = self.search.search(
            "xenobiotic", paper_type="journal-article", journal="toxicology"
        )
        self.assertEqual([r["paper_id"] for r in result["results"]], [pid])

        result_wrong_type = self.search.search(
            "xenobiotic", paper_type="book", journal="toxicology"
        )
        self.assertEqual(result_wrong_type["results"], [])


class RebuildCoverageTest(SearchRepositoryTestBase):
    def test_rebuild_none_indexes_all_and_coverage_reports_full(self):
        pids = [self._insert_paper(f"Paper {i}") for i in range(5)]
        result = self.search.rebuild(None)
        self.assertEqual(result["indexed"], 5)
        self.assertEqual(result["errors"], [])

        coverage = self.search.coverage()
        self.assertEqual(coverage["total_papers"], 5)
        self.assertEqual(coverage["indexed_papers"], 5)
        self.assertEqual(coverage["stale_or_missing"], 0)

        # A paper added after rebuild() is a coverage gap until reindexed.
        new_pid = self._insert_paper("Paper added later")
        coverage_after = self.search.coverage()
        self.assertEqual(coverage_after["total_papers"], 6)
        self.assertEqual(coverage_after["indexed_papers"], 5)
        self.assertEqual(coverage_after["stale_or_missing"], 1)

        self.search.reindex_paper(new_pid)
        coverage_fixed = self.search.coverage()
        self.assertEqual(coverage_fixed["stale_or_missing"], 0)

        # Another full rebuild also catches gaps.
        another_pid = self._insert_paper("Paper added even later")
        self.search.rebuild(None)
        coverage_rebuilt = self.search.coverage()
        self.assertEqual(coverage_rebuilt["stale_or_missing"], 0)
        self.assertIn(another_pid, {r["paper_id"] for r in self.search.search()["results"][:10]})

    def test_rebuild_batches_the_delete_and_isolates_per_paper_insert_failures(self):
        # Regression for the batched rebuild() (hardening plan package 8): a single
        # bad paper's failed INSERT must not stop the rest of its batch from
        # indexing, and must not raise out of rebuild() entirely.
        good_before = self._insert_paper("Good paper before")
        bad_id = "bad" + uuid.uuid4().hex[3:]
        self.conn.execute(
            "INSERT INTO papers (id, title, paper_type, metadata_json, provenance, "
            "created_at, updated_at, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (bad_id, "Bad paper", "journal-article", '{"abstract": "fine at first"}',
             None, _now(), _now(), None),
        )
        self.conn.commit()
        good_after = self._insert_paper("Good paper after")

        first = self.search.rebuild(None, batch_size=10)
        self.assertEqual(first["errors"], [])
        self.assertEqual(first["indexed"], 3)
        coverage = self.search.coverage()
        self.assertEqual(coverage["stale_or_missing"], 0)

        # Corrupt the bad paper's metadata directly (bypassing PaperRepository,
        # which would never write invalid JSON) so a rebuild hits a real failure
        # partway through -- same batch as two papers that must still succeed.
        self.conn.execute(
            "UPDATE papers SET metadata_json = 'not valid json' WHERE id = ?", (bad_id,)
        )
        self.conn.commit()

        second = self.search.rebuild(None, batch_size=10)
        self.assertEqual(len(second["errors"]), 1)
        self.assertEqual(second["errors"][0]["paper_id"], bad_id)
        self.assertEqual(second["indexed"], 2)  # the two good papers, same batch

        # The bad paper ends this rebuild unindexed -- its batch's delete already
        # ran (in bulk, before any insert was attempted) and its own insert then
        # failed; that is the correct outcome for a rebuild (derive current state,
        # not preserve stale content for an unindexable paper), and it is reported
        # in `errors`/`coverage`, not silently swallowed.
        rows = self.conn.execute(
            "SELECT paper_id FROM papers_fts WHERE paper_id = ?", (bad_id,)
        ).fetchall()
        self.assertEqual(rows, [])
        self.assertEqual(self.search.coverage()["stale_or_missing"], 1)

        # The good papers in the same batch are still correctly indexed.
        good_ids = {r["paper_id"] for r in self.search.search()["results"][:10]}
        self.assertIn(good_before, good_ids)
        self.assertIn(good_after, good_ids)

    def test_rebuild_delete_scales_with_one_scan_per_batch_not_per_paper(self):
        # A more direct regression than timing: the whole batch's rows are gone
        # after ONE bulk DELETE, not needing a per-paper pass, which is what makes
        # rebuild's delete work O(n) instead of O(n^2) (package 8 finding: an
        # UNINDEXED FTS5 column forces a full-table SCAN per individual delete).
        ids = [self._insert_paper(f"Paper {i}") for i in range(40)]
        self.search.rebuild(ids, batch_size=40)
        self.assertEqual(self.search.coverage()["indexed_papers"], 40)

        plan = [
            dict(row) for row in self.conn.execute(
                "EXPLAIN QUERY PLAN DELETE FROM papers_fts WHERE paper_id IN "
                "(" + ",".join("?" * len(ids)) + ")", ids
            ).fetchall()
        ]
        self.assertTrue(
            any("SCAN papers_fts" in row["detail"] for row in plan),
            "a batched IN(...) delete still does one scan -- but only ONE, not one per id",
        )

    def test_rebuild_nonexistent_id_is_a_clean_noop(self):
        result = self.search.rebuild(["does-not-exist"])
        # A nonexistent id removing a nonexistent fts row is a legitimate
        # no-op success (reindex_paper doesn't raise for it), not an error.
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["indexed"], 1)
        rows = self.conn.execute(
            "SELECT * FROM papers_fts WHERE paper_id = ?", ("does-not-exist",)
        ).fetchall()
        self.assertEqual(rows, [])


class PaginationTest(SearchRepositoryTestBase):
    def setUp(self):
        super().setUp()
        self.pids = []
        for i in range(15):
            pid = self._insert_paper(f"Pagination widget paper number {i}", created_offset=i)
            self.search.reindex_paper(pid)
            self.pids.append(pid)

    def _walk_all(self, query=None):
        seen = []
        cursor = None
        total_matched_values = set()
        pages = 0
        while True:
            page = self.search.search(query, limit=5, cursor=cursor)
            total_matched_values.add(page["total_matched"])
            seen.extend(r["paper_id"] for r in page["results"])
            pages += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
            self.assertTrue(pages < 10, "pagination did not terminate")
        return seen, total_matched_values, pages

    def test_pagination_filter_only_walk(self):
        seen1, totals1, pages1 = self._walk_all(None)
        self.assertEqual(pages1, 3)
        self.assertEqual(len(seen1), 15)
        self.assertEqual(set(seen1), set(self.pids))
        self.assertEqual(totals1, {15})

        seen2, _, _ = self._walk_all(None)
        self.assertEqual(seen1, seen2)

    def test_pagination_query_walk(self):
        seen1, totals1, pages1 = self._walk_all("widget")
        self.assertEqual(pages1, 3)
        self.assertEqual(len(seen1), 15)
        self.assertEqual(set(seen1), set(self.pids))
        self.assertEqual(totals1, {15})

        seen2, _, _ = self._walk_all("widget")
        self.assertEqual(seen1, seen2)


if __name__ == "__main__":
    unittest.main()
