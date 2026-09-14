import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr import db
from refmgr.repositories.attachments import AttachmentRepository
from refmgr.repositories.chunks import ChunkRepository
from refmgr.repositories.figures import FigureRepository
from refmgr.repositories.identifiers import IdentifierRepository
from refmgr.repositories.merges import (
    DeletedSurvivorError,
    InterveningChangeError,
    MergeService,
    SelfMergeError,
)
from refmgr.repositories.organization import OrganizationRepository
from refmgr.repositories.papers import PaperRepository
from refmgr.repositories.search import SearchRepository
from refmgr.repositories.terms import TermRepository


class MergesTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.conn = db.open_and_migrate(self.tmp)
        self.papers = PaperRepository(self.conn)
        self.identifiers = IdentifierRepository(self.conn)
        self.attachments = AttachmentRepository(self.conn)
        self.organization = OrganizationRepository(self.conn)
        self.chunks = ChunkRepository(self.conn)
        self.terms = TermRepository(self.conn)
        self.figures = FigureRepository(self.conn)
        self.search = SearchRepository(self.conn)
        self.merges = MergeService(self.conn)

    def _add_asset(self, sha256: str) -> None:
        self.conn.execute(
            "INSERT INTO assets (sha256, byte_size, mime_type, storage_path, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sha256, 100, "application/pdf", f"/assets/{sha256}.pdf", "now"),
        )


class PreviewMergeTest(MergesTestBase):
    def setUp(self):
        super().setUp()
        self.survivor = self.papers.create("Survivor Paper", "article")
        self.absorbed = self.papers.create("Absorbed Paper", "article")

        self.identifiers.add(self.absorbed, "doi", "10.1234/absorbed")
        self.identifiers.add(self.survivor, "pmid", "1111")

        self._add_asset("a" * 64)
        self.attachment_id = self.attachments.link(
            self.absorbed, "a" * 64, role="pdf", original_filename="absorbed.pdf"
        )

        self.collection_id = self.organization.create_collection("Reading list")
        self.organization.add_to_collection(self.collection_id, self.absorbed)

        self.organization.add_tag(self.absorbed, "neuroscience")
        self.organization.add_tag(self.survivor, "shared-tag")
        self.organization.add_tag(self.absorbed, "shared-tag")

    def test_preview_reports_expected_moves(self):
        preview = self.merges.preview_merge(self.survivor, self.absorbed)

        self.assertEqual(preview["survivor_id"], self.survivor)
        self.assertEqual(preview["absorbed_id"], self.absorbed)

        self.assertEqual(
            [i["value"] for i in preview["identifiers_to_move"]], ["10.1234/absorbed"]
        )
        self.assertEqual(
            [a["id"] for a in preview["attachments_to_move"]], [self.attachment_id]
        )
        self.assertEqual(preview["collections_to_add"], [self.collection_id])
        self.assertEqual(preview["tags_to_add"], ["neuroscience"])
        self.assertEqual(preview["identifier_conflicts"], [])

    def test_preview_does_not_mutate(self):
        first = self.merges.preview_merge(self.survivor, self.absorbed)
        second = self.merges.preview_merge(self.survivor, self.absorbed)
        self.assertEqual(first, second)

        # Nothing should have moved.
        self.assertEqual(len(self.identifiers.list_for_paper(self.survivor)), 1)
        self.assertEqual(len(self.identifiers.list_for_paper(self.absorbed)), 1)
        self.assertEqual(len(self.attachments.list_for_paper(self.absorbed)), 1)
        self.assertIsNotNone(self.papers.get(self.absorbed))

    def test_preview_raises_for_unknown_paper(self):
        with self.assertRaises(KeyError):
            self.merges.preview_merge("nope", self.absorbed)
        with self.assertRaises(KeyError):
            self.merges.preview_merge(self.survivor, "nope")


class ExecuteMergeTest(MergesTestBase):
    def setUp(self):
        super().setUp()
        self.survivor = self.papers.create("Survivor Paper", "article")
        self.absorbed = self.papers.create("Absorbed Paper", "article")

        self.identifiers.add(self.absorbed, "doi", "10.1234/absorbed")
        self.identifiers.add(self.survivor, "pmid", "1111")

        self._add_asset("b" * 64)
        self.attachment_id = self.attachments.link(
            self.absorbed, "b" * 64, role="pdf", original_filename="absorbed.pdf"
        )

        self.collection_id = self.organization.create_collection("Reading list")
        self.organization.add_to_collection(self.collection_id, self.absorbed)

        self.organization.add_tag(self.absorbed, "neuroscience")

    def test_execute_moves_everything_and_soft_deletes_absorbed(self):
        merge_id = self.merges.execute_merge(self.survivor, self.absorbed)
        self.assertTrue(merge_id)

        survivor_identifier_values = {
            row["value"] for row in self.identifiers.list_for_paper(self.survivor)
        }
        self.assertEqual(survivor_identifier_values, {"10.1234/absorbed", "1111"})
        self.assertEqual(self.identifiers.list_for_paper(self.absorbed), [])

        survivor_attachment_ids = {
            row["id"] for row in self.attachments.list_for_paper(self.survivor)
        }
        self.assertEqual(survivor_attachment_ids, {self.attachment_id})
        self.assertEqual(self.attachments.list_for_paper(self.absorbed), [])

        survivor_collections = {
            row["id"] for row in self.organization.list_collections_for_paper(self.survivor)
        }
        self.assertEqual(survivor_collections, {self.collection_id})

        self.assertEqual(self.organization.list_tags(self.survivor), ["neuroscience"])

        self.assertIsNone(self.papers.get(self.absorbed))
        self.assertIsNotNone(self.papers.get(self.absorbed, include_deleted=True))

    def test_execute_raises_keyerror_for_unknown_paper_without_partial_apply(self):
        before = self.identifiers.list_for_paper(self.survivor)

        with self.assertRaises(KeyError):
            self.merges.execute_merge(self.survivor, "does-not-exist")

        after = self.identifiers.list_for_paper(self.survivor)
        self.assertEqual(before, after)
        # The real absorbed paper's identifiers must be untouched too.
        self.assertEqual(len(self.identifiers.list_for_paper(self.absorbed)), 1)
        self.assertIsNotNone(self.papers.get(self.absorbed))

        with self.assertRaises(KeyError):
            self.merges.execute_merge("does-not-exist", self.absorbed)

    def test_execute_rejects_self_merge(self):
        with self.assertRaises(SelfMergeError):
            self.merges.execute_merge(self.survivor, self.survivor)
        # Nothing touched -- survivor is still alive with its own identifier.
        self.assertIsNotNone(self.papers.get(self.survivor))
        self.assertEqual(len(self.identifiers.list_for_paper(self.survivor)), 1)

    def test_execute_rejects_deleted_survivor(self):
        self.papers.soft_delete(self.survivor)
        with self.assertRaises(DeletedSurvivorError):
            self.merges.execute_merge(self.survivor, self.absorbed)
        # Absorbed is untouched -- still alive, still owns its identifier.
        self.assertIsNotNone(self.papers.get(self.absorbed))
        self.assertEqual(len(self.identifiers.list_for_paper(self.absorbed)), 1)


class MergeDependentRecordsTest(MergesTestBase):
    def setUp(self):
        super().setUp()
        self.survivor = self.papers.create("Survivor Paper", "article")
        self.absorbed = self.papers.create("Absorbed Paper", "article")
        self.identifiers.add(self.survivor, "pmid", "1111")

        self._add_asset("d" * 64)
        self.pdf_attachment = self.attachments.link(
            self.absorbed, "d" * 64, role="pdf", original_filename="absorbed.pdf"
        )

        self.chunks.index_source(
            self.absorbed, "src-1", "The absorbed paper discusses zebrafish regeneration.",
            "hash-1",
        )
        self.terms.set_terms(self.absorbed, [("mesh", "Zebrafish")])
        self._add_asset("e" * 64)
        figure_attachment = self.attachments.link(
            self.absorbed, "e" * 64, role="figure", original_filename="fig1.png"
        )
        self.figure_id = self.figures.record(
            paper_id=self.absorbed,
            source_attachment_id=self.pdf_attachment,
            figure_attachment_id=figure_attachment,
            asset_sha256="e" * 64,
            kind="figure",
            extractor="test",
            caption="Zebrafish regeneration over time",
        )

    def test_merge_reassigns_chunks_terms_figures_and_reindexes_fts(self):
        merge_id = self.merges.execute_merge(self.survivor, self.absorbed)
        self.assertTrue(merge_id)

        # Chunks moved, still findable under survivor -- not silently lost.
        chunk_hit = self.chunks.search("zebrafish")
        self.assertTrue(chunk_hit)
        self.assertEqual({h["paper_id"] for h in chunk_hit}, {self.survivor})

        # Terms moved.
        self.assertEqual(self.terms.for_paper(self.absorbed), {})
        self.assertEqual(self.terms.for_paper(self.survivor), {"mesh": ["Zebrafish"]})

        # Figures moved, and their captions are searchable under survivor.
        figures = self.figures.list_for_paper(self.survivor)
        self.assertEqual([f["id"] for f in figures], [self.figure_id])
        self.assertEqual(self.figures.list_for_paper(self.absorbed), [])
        figure_hit = self.figures.search("zebrafish")
        self.assertEqual({h["paper_id"] for h in figure_hit}, {self.survivor})

        # Metadata FTS: survivor's row reflects the merged identifiers;
        # absorbed's row is gone (it's soft-deleted).
        result = self.search.search("1111")
        self.assertIn(self.survivor, [r["paper_id"] for r in result["results"]])
        coverage = self.search.coverage()
        self.assertEqual(coverage["stale_or_missing"], 0)

    def test_reindex_after_merge_does_not_resurrect_duplicate(self):
        self.merges.execute_merge(self.survivor, self.absorbed)
        self.search.rebuild()
        self.chunks.remove_paper(self.absorbed)  # no-op: absorbed owns nothing now
        chunk_hit = self.chunks.search("zebrafish")
        self.assertEqual({h["paper_id"] for h in chunk_hit}, {self.survivor})

    def test_revert_moves_chunks_terms_figures_back(self):
        merge_id = self.merges.execute_merge(self.survivor, self.absorbed)
        self.merges.revert_merge(merge_id)

        self.assertEqual(self.terms.for_paper(self.survivor), {})
        self.assertEqual(self.terms.for_paper(self.absorbed), {"mesh": ["Zebrafish"]})

        chunk_hit = self.chunks.search("zebrafish")
        self.assertEqual({h["paper_id"] for h in chunk_hit}, {self.absorbed})

        self.assertEqual(self.figures.list_for_paper(self.survivor), [])
        figures = self.figures.list_for_paper(self.absorbed)
        self.assertEqual([f["id"] for f in figures], [self.figure_id])

    def test_revert_refuses_when_a_later_merge_moved_the_same_chunk(self):
        merge_id = self.merges.execute_merge(self.survivor, self.absorbed)

        # A second, unrelated survivor absorbs the first survivor -- which
        # now owns the chunk this test's merge moved onto it.
        third = self.papers.create("Third Paper", "article")
        self.merges.execute_merge(third, self.survivor)

        with self.assertRaises(InterveningChangeError):
            self.merges.revert_merge(merge_id)

        # Refused cleanly: the original merge is still recorded as not
        # reverted, and nothing was partially moved.
        row = self.conn.execute(
            "SELECT reverted_at FROM merges WHERE id = ?", (merge_id,)
        ).fetchone()
        self.assertIsNone(row["reverted_at"])


class RevertMergeTest(MergesTestBase):
    def setUp(self):
        super().setUp()
        self.survivor = self.papers.create("Survivor Paper", "article")
        self.absorbed = self.papers.create("Absorbed Paper", "article")

        self.identifiers.add(self.absorbed, "doi", "10.1234/absorbed")
        self.identifiers.add(self.survivor, "pmid", "1111")

        self._add_asset("c" * 64)
        self.attachment_id = self.attachments.link(
            self.absorbed, "c" * 64, role="pdf", original_filename="absorbed.pdf"
        )

        self.collection_id = self.organization.create_collection("Reading list")
        self.organization.add_to_collection(self.collection_id, self.absorbed)

        # survivor already independently has "shared-tag" before the merge;
        # absorbed also has it. Revert must NOT strip it from survivor.
        self.organization.add_tag(self.survivor, "shared-tag")
        self.organization.add_tag(self.absorbed, "shared-tag")
        self.organization.add_tag(self.absorbed, "neuroscience")

    def test_revert_restores_absorbed_and_moves_things_back(self):
        merge_id = self.merges.execute_merge(self.survivor, self.absorbed)

        self.merges.revert_merge(merge_id)

        self.assertIsNotNone(self.papers.get(self.absorbed))

        absorbed_identifier_values = {
            row["value"] for row in self.identifiers.list_for_paper(self.absorbed)
        }
        self.assertEqual(absorbed_identifier_values, {"10.1234/absorbed"})
        survivor_identifier_values = {
            row["value"] for row in self.identifiers.list_for_paper(self.survivor)
        }
        self.assertEqual(survivor_identifier_values, {"1111"})

        absorbed_attachment_ids = {
            row["id"] for row in self.attachments.list_for_paper(self.absorbed)
        }
        self.assertEqual(absorbed_attachment_ids, {self.attachment_id})
        self.assertEqual(self.attachments.list_for_paper(self.survivor), [])

        # Merge-added collection membership is removed from survivor...
        survivor_collections = {
            row["id"] for row in self.organization.list_collections_for_paper(self.survivor)
        }
        self.assertNotIn(self.collection_id, survivor_collections)
        # ...but absorbed keeps its own membership.
        absorbed_collections = {
            row["id"] for row in self.organization.list_collections_for_paper(self.absorbed)
        }
        self.assertIn(self.collection_id, absorbed_collections)

        # "neuroscience" was merge-added to survivor -> removed on revert.
        # "shared-tag" pre-existed on survivor -> must remain.
        self.assertEqual(self.organization.list_tags(self.survivor), ["shared-tag"])
        self.assertEqual(
            sorted(self.organization.list_tags(self.absorbed)), ["neuroscience", "shared-tag"]
        )

    def test_revert_twice_raises_valueerror(self):
        merge_id = self.merges.execute_merge(self.survivor, self.absorbed)
        self.merges.revert_merge(merge_id)
        with self.assertRaises(ValueError):
            self.merges.revert_merge(merge_id)

    def test_revert_unknown_merge_raises_keyerror(self):
        with self.assertRaises(KeyError):
            self.merges.revert_merge("does-not-exist")


if __name__ == "__main__":
    unittest.main()
