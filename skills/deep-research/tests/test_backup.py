"""backup.py: versioned backup and restore for the reference-manager library.

The comprehensive fixture test (`FullFixtureRestoreTest`) is the plan's own acceptance
scenario: restore a fixture containing attachments, figures, merges, annotations,
collections, and snapshots; verify counts, identities, bytes, search results, and claim
spans. The rest of this file targets the specific failure modes the hardening plan calls
out: truncated/corrupt backups must be rejected outright, and a consistent backup must
either be produced or explicitly refused, never silently wrong.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, write_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr.repositories.merges import MergeService  # noqa: E402
from refmgr.service import ReferenceManagerService  # noqa: E402

registry = load_script("registry.py")
research = load_script("research.py")
annotations = load_script("annotations.py")
backup = load_script("backup.py")
store = load_script("store.py")
ask = load_script("ask.py")


def _ns(**kwargs):
    class NS:
        pass
    ns = NS()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _init_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    research.cmd_init(_ns(path=str(repo), from_wiki=None))
    return repo


class CreateBackupTest(unittest.TestCase):
    def test_manifest_names_every_root_and_checksum(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _init_repo(tmp)
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "T1"})
                reg.commit()

            manifest = backup.create_backup(repo, tmp / "bk")
            self.assertEqual(manifest["backup_manifest_version"], 1)
            self.assertEqual(
                set(manifest["roots"]), {"papers", "refmgr", "sources"}
            )
            self.assertIn("runs", manifest["excluded_roots"])
            self.assertIn("data/papers/registry.jsonl", manifest["files"])
            self.assertIn("data/refmgr/library.sqlite3", manifest["files"])
            for info in manifest["files"].values():
                self.assertIn("sha256", info)
                self.assertIn("bytes", info)

    def test_create_refuses_nonempty_destination(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _init_repo(tmp)
            dest = tmp / "bk"
            dest.mkdir()
            (dest / "stray.txt").write_text("already here")
            with self.assertRaises(backup.BackupError):
                backup.create_backup(repo, dest)

    def test_create_is_idempotent_shape_for_empty_repo(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _init_repo(tmp)
            manifest = backup.create_backup(repo, tmp / "bk")
            # No refmgr library yet (nothing registered) -- refmgr root is absent,
            # not an error.
            self.assertNotIn("refmgr", manifest["roots"])
            self.assertIn("papers", manifest["roots"])


class VerifyAndCorruptionRejectionTest(unittest.TestCase):
    def _backed_up_repo(self, tmp: Path) -> Path:
        repo = _init_repo(tmp)
        reg = registry.Registry(repo)
        with reg.locked():
            reg.register({"pmid": "1", "title": "T1"})
            reg.commit()
        backup.create_backup(repo, tmp / "bk")
        return tmp / "bk"

    def test_verify_clean_backup_is_ok(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bk = self._backed_up_repo(tmp)
            result = backup.verify_backup(bk)
            self.assertTrue(result["ok"])
            self.assertEqual(result["checksum_failures"], [])

    def test_truncated_file_is_rejected_by_verify_and_restore(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bk = self._backed_up_repo(tmp)
            target = bk / "data" / "papers" / "registry.jsonl"
            target.write_bytes(target.read_bytes()[:5])

            result = backup.verify_backup(bk)
            self.assertFalse(result["ok"])
            self.assertEqual(len(result["checksum_failures"]), 1)
            self.assertEqual(result["checksum_failures"][0]["reason"], "size mismatch")

            with self.assertRaises(backup.BackupError):
                backup.restore_backup(bk, tmp / "restored")
            self.assertFalse((tmp / "restored").exists())

    def test_bit_flipped_file_same_size_is_rejected(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bk = self._backed_up_repo(tmp)
            target = bk / "data" / "papers" / "registry.jsonl"
            content = bytearray(target.read_bytes())
            content[0] ^= 0xFF
            target.write_bytes(bytes(content))

            result = backup.verify_backup(bk)
            self.assertFalse(result["ok"])
            self.assertEqual(result["checksum_failures"][0]["reason"], "checksum mismatch")

            with self.assertRaises(backup.BackupError):
                backup.restore_backup(bk, tmp / "restored")

    def test_missing_manifest_file_is_rejected(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bk = self._backed_up_repo(tmp)
            (bk / "data" / "papers" / "registry.jsonl").unlink()

            result = backup.verify_backup(bk)
            self.assertFalse(result["ok"])
            self.assertEqual(result["checksum_failures"][0]["reason"], "missing from backup")

    def test_not_a_backup_directory_raises_cleanly(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            not_a_backup = tmp / "empty"
            not_a_backup.mkdir()
            with self.assertRaises(backup.BackupError):
                backup.verify_backup(not_a_backup)

    def test_corrupt_manifest_json_raises_cleanly(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bk = self._backed_up_repo(tmp)
            (bk / backup.MANIFEST_FILENAME).write_text("{not valid json")
            with self.assertRaises(backup.BackupError):
                backup.verify_backup(bk)

    def test_unsupported_manifest_version_raises_cleanly(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bk = self._backed_up_repo(tmp)
            manifest_path = bk / backup.MANIFEST_FILENAME
            import json as _json
            manifest = _json.loads(manifest_path.read_text())
            manifest["backup_manifest_version"] = 999
            manifest_path.write_text(_json.dumps(manifest))
            with self.assertRaises(backup.BackupError):
                backup.verify_backup(bk)


class RestoreDestinationSafetyTest(unittest.TestCase):
    def test_restore_refuses_nonempty_destination(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _init_repo(tmp)
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "T1"})
                reg.commit()
            backup.create_backup(repo, tmp / "bk")

            dest = tmp / "dest"
            dest.mkdir()
            (dest / "already-here.txt").write_text("do not touch me")
            with self.assertRaises(backup.BackupError):
                backup.restore_backup(tmp / "bk", dest)
            # Untouched: restore must never write into a non-empty directory.
            self.assertEqual((dest / "already-here.txt").read_text(), "do not touch me")
            self.assertFalse((dest / "data").exists())

    def test_restore_never_touches_the_backup_source(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _init_repo(tmp)
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "T1"})
                reg.commit()
            backup.create_backup(repo, tmp / "bk")
            before = {
                p: p.stat().st_mtime_ns
                for p in sorted((tmp / "bk").rglob("*")) if p.is_file()
            }
            backup.restore_backup(tmp / "bk", tmp / "restored")
            after = {
                p: p.stat().st_mtime_ns
                for p in sorted((tmp / "bk").rglob("*")) if p.is_file()
            }
            self.assertEqual(before, after)


class LockCoordinationTest(unittest.TestCase):
    def test_create_refuses_explicitly_when_a_lock_cannot_be_acquired(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _init_repo(tmp)
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "T1"})
                reg.commit()

            release = threading.Event()
            entered = threading.Event()

            def hold_lock():
                with registry.advisory_lock(repo, "registry"):
                    entered.set()
                    release.wait(timeout=5)

            holder = threading.Thread(target=hold_lock)
            holder.start()
            try:
                self.assertTrue(entered.wait(timeout=5), "holder thread never took the lock")
                # backup.py imports registry.py itself (a separate module object from
                # this test's `load_script("registry.py")` -- see helpers.load_script,
                # which does not register into sys.modules), so the raised exception's
                # class is a different object than `registry.AdvisoryLockTimeout` here
                # even though it is the same code. Both subclass the builtin
                # TimeoutError, which IS identical across both module instances.
                with self.assertRaises(TimeoutError):
                    backup.create_backup(repo, tmp / "bk", lock_timeout=0.3)
                # No partial backup directory left behind by the failed attempt.
                self.assertFalse((tmp / "bk").exists())
            finally:
                release.set()
                holder.join(timeout=5)

    def test_create_succeeds_once_the_competing_lock_is_released(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _init_repo(tmp)
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "T1"})
                reg.commit()

            entered = threading.Event()

            def hold_briefly():
                with registry.advisory_lock(repo, "registry"):
                    entered.set()
                    import time
                    time.sleep(0.2)

            holder = threading.Thread(target=hold_briefly)
            holder.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                manifest = backup.create_backup(repo, tmp / "bk", lock_timeout=5.0)
                self.assertIn("data/papers/registry.jsonl", manifest["files"])
            finally:
                holder.join(timeout=5)


PDF_BYTES = (b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n"
             b"trailer<</Root 1 0 R>>\n%%EOF\n")
FIGURE_PNG_BYTES = b"\x89PNG\r\n\x1a\nnot a real png but content-addressed all the same"

SNAPSHOT_TEXT = ("Background. " + "Filler sentence padding the body out. " * 30
                  + "Results: the rare longtail biomarker rose sharply in treated mice. "
                  + "Discussion. " * 20)


class FullFixtureRestoreTest(unittest.TestCase):
    """Plan acceptance: restore a fixture with attachments, figures, merges,
    annotations, collections, and snapshots; verify counts, identities, bytes,
    search results, and claim spans."""

    def _build_fixture(self, tmp: Path) -> dict:
        repo = _init_repo(tmp)

        reg = registry.Registry(repo)
        with reg.locked():
            reg.register({"pmid": "1", "title": "Longtail biomarker study"})
            reg.register({"pmid": "2", "title": "A paper that gets merged away"})
            reg.commit()

        # Snapshot + extraction span, for chunk indexing and claim-span verification.
        write_result = store.global_write_snapshot_result(
            repo, url="https://example.org/longtail", text=SNAPSHOT_TEXT,
            title="Snap", access="full_text", origin="web", paper=None,
            event_type="fetch", fresh=True, actor="test",
        )
        source_id = write_result["source_id"]
        extraction_path = repo / "data" / "papers" / "extractions" / "pmid-1.json"
        write_json(extraction_path, {
            "evidence_id": "pmid:1",
            "spans": [{"claim": "longtail biomarker rose sharply", "evidence_id": "pmid:1",
                       "source_id": source_id, "start": 0, "end": 10, "access": "full_text"}],
        })
        reg = registry.Registry(repo)
        with reg.locked():
            reg.set_extraction("pmid:1", str(extraction_path.relative_to(repo)))
            reg.commit()

        # Rebuild indexes (papers_fts + chunks) so restore can be checked for search.
        from helpers import run_py
        reindex = run_py(["scripts/registry.py", "reindex", "--repo", str(repo)])
        assert reindex.returncode == 0, reindex.stderr

        paper_one_id = registry.Registry(repo).records["pmid:1"]["refmgr_paper_id"]
        paper_two_id = registry.Registry(repo).records["pmid:2"]["refmgr_paper_id"]

        # Attachment + figure.
        pdf_path = tmp / "paper.pdf"
        pdf_path.write_bytes(PDF_BYTES)
        service = ReferenceManagerService(repo / "data" / "refmgr")
        try:
            attachment_id = service.import_attachment(
                paper_one_id, pdf_path, role="fulltext", mime_type="application/pdf",
                original_filename="paper.pdf",
            )
            figures = service.import_figures(
                paper_one_id, attachment_id,
                [{"png_bytes": FIGURE_PNG_BYTES, "kind": "figure", "label": "Figure 1",
                  "number": "1", "caption": "Longtail biomarker over time", "page": 1}],
                extractor="test/1",
            )
            figure_id = figures[0]["figure_id"]

            collection_id = service.organization.create_collection("Reading list")
            service.organization.add_to_collection(collection_id, paper_one_id)
        finally:
            service.close()

        # Annotation.
        with registry.advisory_lock(repo, "annotations"):
            ann = annotations.Annotations(repo)
            ann.upsert("pmid:1", add_tag="to-read", rating=5)
            ann.save()

        # Merge pmid:2 into pmid:1.
        service = ReferenceManagerService(repo / "data" / "refmgr")
        try:
            merge_service = MergeService(service.conn)
            merge_id = merge_service.execute_merge(paper_one_id, paper_two_id)
        finally:
            service.close()

        return {
            "repo": repo, "paper_id": paper_one_id, "absorbed_id": paper_two_id,
            "merge_id": merge_id, "attachment_id": attachment_id, "figure_id": figure_id,
            "collection_id": collection_id, "source_id": source_id,
            "pdf_bytes": PDF_BYTES,
        }

    def test_restore_preserves_everything_the_fixture_built(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fixture = self._build_fixture(tmp)
            repo = fixture["repo"]

            backup.create_backup(repo, tmp / "bk")
            report = backup.restore_backup(tmp / "bk", tmp / "restored")

            self.assertTrue(report["doctor"]["healthy"], report["doctor"])
            self.assertEqual(report["papers_restored"], 2)  # survivor + soft-deleted absorbed

            restored_repo = tmp / "restored"
            service = ReferenceManagerService(restored_repo / "data" / "refmgr")
            try:
                # Identity: same paper id, still alive; absorbed still soft-deleted.
                paper = service.papers.get(fixture["paper_id"])
                self.assertIsNotNone(paper)
                self.assertIsNone(service.papers.get(fixture["absorbed_id"]))
                self.assertIsNotNone(
                    service.papers.get(fixture["absorbed_id"], include_deleted=True)
                )

                # Merge record survived.
                merge_row = service.conn.execute(
                    "SELECT * FROM merges WHERE id = ?", (fixture["merge_id"],)
                ).fetchone()
                self.assertIsNotNone(merge_row)
                self.assertIsNone(merge_row["reverted_at"])

                # Attachment bytes: restored asset is byte-identical to the original PDF.
                attachment = next(
                    a for a in service.attachments.list_for_paper(fixture["paper_id"])
                    if a["id"] == fixture["attachment_id"]
                )
                asset_path = service.asset_path(attachment["asset_sha256"])
                self.assertEqual(asset_path.read_bytes(), fixture["pdf_bytes"])
                self.assertTrue(service.assets.verify_integrity(attachment["asset_sha256"]))

                # Figure survived, still attributed to the (now-merged-into) paper.
                figure = service.figures.get(fixture["figure_id"])
                self.assertIsNotNone(figure)
                self.assertEqual(figure["paper_id"], fixture["paper_id"])
                self.assertEqual(figure["caption"], "Longtail biomarker over time")

                # Collection membership survived.
                members = service.organization.list_members(fixture["collection_id"])
                self.assertIn(fixture["paper_id"], members)
            finally:
                service.close()

            # Annotation survived.
            ann = annotations.Annotations(restored_repo)
            record = ann.get("pmid:1")
            self.assertEqual(record["tags"], ["to-read"])
            self.assertEqual(record["rating"], 5)

            # Search results survived: a term only in the chunk-indexed body still
            # finds the (now-merged, identifier-enriched) paper.
            from helpers import run_py
            search = run_py(["scripts/registry.py", "search", "--repo", str(restored_repo),
                             "--q", "longtail biomarker"])
            self.assertEqual(search.returncode, 0, search.stderr)
            import json as _json
            payload = _json.loads(search.stdout)
            self.assertIn("pmid:1", [r["evidence_id"] for r in payload["results"]])

            # Claim span re-verifies against the restored canonical snapshot.
            snapshots = ask._SnapshotCache(restored_repo)
            span = {"source_id": fixture["source_id"], "start": 0, "end": 10}
            result = ask.verify(span, snapshots)
            self.assertTrue(result["ok"], result)

    def test_restored_library_reindexes_and_merges_cleanly(self):
        """The restored repo is a fully usable repo, not a read-only snapshot --
        further mutations (a fresh reindex) must work exactly as on any other repo."""
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            fixture = self._build_fixture(tmp)
            backup.create_backup(fixture["repo"], tmp / "bk")
            backup.restore_backup(tmp / "bk", tmp / "restored")

            from helpers import run_py
            result = run_py(["scripts/registry.py", "reindex", "--repo", str(tmp / "restored")])
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
