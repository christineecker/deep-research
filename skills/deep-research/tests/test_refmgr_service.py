import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr.repositories.identifiers import IdentifierConflictError
from refmgr.service import ReferenceManagerService

N_WORKERS = 8
WORKER_TIMEOUT_S = 30

_WORKER_SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, sys.argv[3])
    from refmgr.service import ReferenceManagerService

    library_root = Path(sys.argv[1])
    worker_index = sys.argv[2]

    service = ReferenceManagerService(library_root)
    try:
        service.add_paper(
            title=f"Concurrent Paper {worker_index}",
            paper_type="article",
            identifiers=[("doi", f"10.1234/worker-{worker_index}")],
        )
    finally:
        service.close()
    """
)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.service = ReferenceManagerService(self.tmp)
        self.addCleanup(self.service.close)

    def test_add_paper_basic_with_identifiers(self):
        paper_id = self.service.add_paper(
            title="A Study",
            paper_type="article",
            identifiers=[("doi", "10.1000/xyz123")],
        )
        paper = self.service.papers.get(paper_id)
        self.assertEqual(paper["title"], "A Study")
        ids = self.service.identifiers.list_for_paper(paper_id)
        self.assertEqual(len(ids), 1)
        self.assertEqual(ids[0]["value"], "10.1000/xyz123")

    def test_add_paper_auto_indexes_for_search(self):
        paper_id = self.service.add_paper(
            title="Diabetes Screening in Primary Care",
            paper_type="article",
            metadata={"abstract": "A study of diabetes screening."},
            identifiers=[("doi", "10.1000/diabetes-1")],
        )
        result = self.service.search.search("diabetes")
        found_ids = [r["paper_id"] for r in result["results"]]
        self.assertIn(paper_id, found_ids)
        coverage = self.service.search.coverage()
        self.assertEqual(coverage["stale_or_missing"], 0)

    def test_add_paper_second_call_reindexes_existing_paper(self):
        paper_id = self.service.add_paper(
            title="Original Title", paper_type="article",
            identifiers=[("doi", "10.1000/reindex-1")],
        )
        # Second call resolves to the same paper (idempotent) and adds a new
        # identifier -- reindex_paper must run again so the identifier text
        # is searchable without a manual rebuild.
        same_id = self.service.add_paper(
            title="Original Title", paper_type="article",
            identifiers=[("doi", "10.1000/reindex-1"), ("pmid", "12345678")],
        )
        self.assertEqual(same_id, paper_id)
        result = self.service.search.search("12345678")
        self.assertIn(paper_id, [r["paper_id"] for r in result["results"]])

    def test_reindex_paper_convenience_method(self):
        paper_id = self.service.add_paper(title="Findable Later", paper_type="article")
        self.service.papers.update_metadata(paper_id, {"abstract": "mentions serendipity"})
        # update_metadata doesn't auto-reindex (documented: only add_paper/
        # find_or_create_paper_by_identifier do) -- explicit reindex_paper
        # is how a caller brings it back up to date.
        before = self.service.search.search("serendipity")
        self.assertEqual(before["results"], [])
        self.service.reindex_paper(paper_id)
        after = self.service.search.search("serendipity")
        self.assertIn(paper_id, [r["paper_id"] for r in after["results"]])

    def test_add_paper_repeated_import_is_idempotent(self):
        paper_id_1 = self.service.add_paper(
            title="A Study",
            paper_type="article",
            identifiers=[("doi", "10.1000/xyz123")],
        )
        paper_id_2 = self.service.add_paper(
            title="A Study",
            paper_type="article",
            identifiers=[("doi", "10.1000/xyz123"), ("pmid", "555555")],
        )
        self.assertEqual(paper_id_1, paper_id_2)

        papers = self.service.papers.list(limit=100)
        self.assertEqual(len(papers), 1)

        ids = self.service.identifiers.list_for_paper(paper_id_1)
        values = {(row["scheme"], row["value"]) for row in ids}
        self.assertEqual(values, {("doi", "10.1000/xyz123"), ("pmid", "555555")})

    def test_add_paper_raises_on_genuinely_ambiguous_identifiers(self):
        paper_a = self.service.add_paper(
            title="Paper A", paper_type="article", identifiers=[("doi", "10.1000/aaa")]
        )
        paper_b = self.service.add_paper(
            title="Paper B", paper_type="article", identifiers=[("pmid", "111111")]
        )
        self.assertNotEqual(paper_a, paper_b)

        with self.assertRaises(IdentifierConflictError):
            self.service.add_paper(
                title="Merged Paper",
                paper_type="article",
                identifiers=[("doi", "10.1000/aaa"), ("pmid", "111111")],
            )

    def test_import_attachment_twice_creates_two_rows_sharing_one_asset(self):
        paper_id = self.service.add_paper(title="Has PDF", paper_type="article")
        src = self.tmp / "source.pdf"
        src.write_bytes(b"%PDF-1.4 fake pdf bytes")

        attachment_id_1 = self.service.import_attachment(
            paper_id, src, role="fulltext", mime_type="application/pdf"
        )
        attachment_id_2 = self.service.import_attachment(
            paper_id, src, role="fulltext", mime_type="application/pdf"
        )

        self.assertNotEqual(attachment_id_1, attachment_id_2)

        rows = self.service.attachments.list_for_paper(paper_id)
        self.assertEqual(len(rows), 2)
        sha_values = {row["asset_sha256"] for row in rows}
        self.assertEqual(len(sha_values), 1)

    def test_find_or_create_paper_by_identifier_finds_existing(self):
        paper_id_1 = self.service.find_or_create_paper_by_identifier(
            "doi", "10.1000/find-me", title="Findable", paper_type="article"
        )
        paper_id_2 = self.service.find_or_create_paper_by_identifier(
            "doi", "10.1000/find-me", title="Different Title Ignored", paper_type="article"
        )
        self.assertEqual(paper_id_1, paper_id_2)

        papers = self.service.papers.list(limit=100)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["title"], "Findable")

    def test_interrupted_import_corruption_is_detectable(self):
        paper_id = self.service.add_paper(title="Interrupted", paper_type="article")
        src = self.tmp / "supplement.pdf"
        src.write_bytes(b"%PDF-1.4 supplement bytes")

        self.service.import_attachment(paper_id, src, role="supplement")

        rows = self.service.attachments.list_for_paper(paper_id)
        asset_sha256 = rows[0]["asset_sha256"]

        # Sanity check: freshly staged asset verifies fine.
        self.assertTrue(self.service.assets.verify_integrity(asset_sha256))

        # Simulate an interrupted/corrupted import: delete the staged file
        # on disk without touching the DB row.
        asset_row = self.service.assets.get(asset_sha256)
        stored_path = self.service.library_root / asset_row["storage_path"]
        stored_path.unlink()

        self.assertFalse(self.service.assets.verify_integrity(asset_sha256))


class ServiceConcurrencyTest(unittest.TestCase):
    def test_concurrent_subprocess_writes_do_not_lose_records(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)

        script_path = tmp / "_worker.py"
        script_path.write_text(_WORKER_SCRIPT, encoding="utf-8")

        scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
        library_root = tmp / "library"

        # Migrate once up front. Migration itself (CREATE TABLE, run once
        # per fresh library) is not designed to race concurrent first-time
        # callers; real usage always initializes a library before multiple
        # processes attach to it. The concurrency this test targets is
        # concurrent *writes* to an already-migrated database.
        ReferenceManagerService(library_root).close()

        procs = []
        for i in range(N_WORKERS):
            proc = subprocess.Popen(
                [sys.executable, str(script_path), str(library_root), str(i), scripts_dir],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)

        failures = []
        for i, proc in enumerate(procs):
            try:
                stdout, stderr = proc.communicate(timeout=WORKER_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                failures.append(f"worker {i} timed out. stderr:\n{stderr}")
                continue
            if proc.returncode != 0:
                failures.append(
                    f"worker {i} exited {proc.returncode}. stderr:\n{stderr}"
                )

        if failures:
            self.fail("\n---\n".join(failures))

        service = ReferenceManagerService(library_root)
        try:
            papers = service.papers.list(limit=100)
            self.assertEqual(len(papers), N_WORKERS)

            seen_identifier_values = set()
            for paper in papers:
                ids = service.identifiers.list_for_paper(paper["id"])
                self.assertEqual(len(ids), 1)
                seen_identifier_values.add(ids[0]["value"])

            expected = {f"10.1234/worker-{i}" for i in range(N_WORKERS)}
            self.assertEqual(seen_identifier_values, expected)
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main()
