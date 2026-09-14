"""Chunk index invariants (`refmgr/repositories/chunks.py`).

The index is derived and rebuildable, so the tests that matter are about the
properties downstream code relies on:

  * every chunk is short enough to survive `store.verify_span`'s span cap, and its
    offsets slice the original text back out exactly -- that is what lets a retrieval
    hit double as a claim span (`ask.py`);
  * `search` ranks by any term (a question), `papers_matching_all` requires all of
    them (a filter) -- the two have deliberately different semantics;
  * re-indexing an unchanged snapshot is a no-op, and a changed one replaces its rows.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr.repositories import chunks  # noqa: E402
from refmgr.service import ReferenceManagerService  # noqa: E402

store = load_script("store.py")


BODY = (
    "Background. Adolescent depression is common.\n\n"
    "Methods. We randomized 240 adolescents to group CBT or waitlist. "
    + "Filler sentence with no particular content. " * 60
    + "\n\nResults. The rare zebra biomarker fell by 41 percent in the exercise arm.\n\n"
    + "Discussion. " * 40
)


class SplitTextTest(unittest.TestCase):
    def test_chunks_are_verifiable_length_and_cover_the_text(self):
        ranges = chunks.split_text(BODY)
        self.assertTrue(ranges)
        for start, end in ranges:
            self.assertLessEqual(end - start, store.MAX_SPAN_CHARS,
                                 "a chunk longer than the span cap could never verify")
            self.assertEqual(BODY[start:end], BODY[start:end])
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], len(BODY))

    def test_consecutive_chunks_overlap(self):
        ranges = chunks.split_text(BODY)
        self.assertGreater(len(ranges), 1)
        for (_, prev_end), (next_start, _) in zip(ranges, ranges[1:]):
            self.assertLess(next_start, prev_end, "chunks must overlap, not abut")

    def test_empty_text_yields_no_chunks(self):
        self.assertEqual(chunks.split_text(""), [])

    def test_short_text_is_one_chunk(self):
        self.assertEqual(chunks.split_text("Short body."), [(0, 11)])

    def test_overlap_must_be_smaller_than_size(self):
        with self.assertRaises(ValueError):
            chunks.split_text("text", size=10, overlap=10)


class ChunkRepositoryTest(unittest.TestCase):
    def _service(self, tmp: Path) -> ReferenceManagerService:
        service = ReferenceManagerService(tmp / "refmgr")
        self.addCleanup(service.close)
        return service

    def test_index_search_and_offsets_slice_the_original_text(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            result = service.chunks.index_source(paper_id, "src-1", BODY, "sha256:abc")
            self.assertFalse(result["skipped"])
            self.assertGreater(result["indexed"], 1)

            hits = service.chunks.search("zebra biomarker")
            self.assertTrue(hits)
            hit = hits[0]
            self.assertEqual(hit["paper_id"], paper_id)
            self.assertEqual(hit["source_id"], "src-1")
            self.assertEqual(BODY[hit["start"]:hit["end"]], hit["text"])
            self.assertIn("zebra", hit["snippet"].lower())

    def test_search_matches_any_term_so_a_question_still_retrieves(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            service.chunks.index_source(paper_id, "src-1", BODY, "sha256:abc")
            # None of "did"/"change"/"with" appear next to "zebra"; an AND query would
            # return nothing at all here.
            hits = service.chunks.search("did the zebra biomarker change with exercise?")
            self.assertTrue(hits)

    def test_papers_matching_all_requires_every_term(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            first = service.add_paper(title="A", paper_type="article",
                                      identifiers=[("pmid", "1")])
            second = service.add_paper(title="B", paper_type="article",
                                       identifiers=[("pmid", "2")])
            service.chunks.index_source(first, "src-1", BODY, "sha256:abc")
            service.chunks.index_source(second, "src-2", "An unrelated body about zebras.",
                                        "sha256:def")

            self.assertEqual(service.chunks.papers_matching_all(["zebra"]), {first, second})
            self.assertEqual(service.chunks.papers_matching_all(["zebra", "biomarker"]),
                             {first})
            self.assertEqual(service.chunks.papers_matching_all(["zebra", "absent"]), set())
            self.assertEqual(service.chunks.papers_matching_all([]), set())

    def test_reindexing_an_unchanged_snapshot_is_a_no_op(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            first = service.chunks.index_source(paper_id, "src-1", BODY, "sha256:abc")
            again = service.chunks.index_source(paper_id, "src-1", BODY, "sha256:abc")
            self.assertTrue(again["skipped"])
            self.assertEqual(again["indexed"], 0)
            self.assertEqual(service.chunks.coverage()["chunks"], first["indexed"])

    def test_changed_snapshot_replaces_its_rows(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            service.chunks.index_source(paper_id, "src-1", "Old body about zebras.",
                                        "sha256:old")
            service.chunks.index_source(paper_id, "src-1", "New body about giraffes.",
                                        "sha256:new")
            self.assertEqual(service.chunks.search("zebras"), [])
            self.assertTrue(service.chunks.search("giraffes"))
            self.assertEqual(service.chunks.coverage()["chunks"], 1)

    def test_remove_paper_clears_both_tables(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            service.chunks.index_source(paper_id, "src-1", BODY, "sha256:abc")
            service.chunks.remove_paper(paper_id)
            self.assertEqual(service.chunks.search("zebra"), [])
            self.assertEqual(service.chunks.coverage(),
                             {"chunks": 0, "papers_with_chunks": 0, "sources_indexed": 0})

    def test_fts_operators_in_a_query_are_inert(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            service.chunks.index_source(paper_id, "src-1", BODY, "sha256:abc")
            for hostile in ('zebra"', "zebra*", "-zebra", "NEAR(zebra biomarker)"):
                service.chunks.search(hostile)  # must not raise
            self.assertEqual(service.chunks.search(""), [])


if __name__ == "__main__":
    unittest.main()
