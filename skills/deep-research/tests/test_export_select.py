from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, write_jsonl

export_select = load_script("export_select.py")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_registry(repo: Path, records: list[dict]) -> None:
    path = repo / "data" / "papers" / "registry.jsonl"
    write_jsonl(path, records)


def _rec(evidence_id: str, **extra) -> dict:
    base = {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "status": "registered",
        "title": f"Title for {evidence_id}",
    }
    base.update(extra)
    return base


class SelectAllTest(unittest.TestCase):
    def test_includes_metadata_only_records_sorted_by_evidence_id(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_registry(repo, [
                _rec("pmid:2"),
                _rec("doi:10.1000/aaa"),  # metadata-only, no fulltext key at all
                _rec("pmid:1"),
            ])
            records = export_select.select_all(repo)
            eids = [r["evidence_id"] for r in records]
            self.assertEqual(eids, sorted(eids))
            self.assertEqual(set(eids), {"pmid:2", "doi:10.1000/aaa", "pmid:1"})
            metadata_only = next(r for r in records if r["evidence_id"] == "doi:10.1000/aaa")
            self.assertNotIn("fulltext", metadata_only)


class SelectByEvidenceIdsTest(unittest.TestCase):
    def test_resolves_multiple_valid_ids(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_registry(repo, [_rec("pmid:1"), _rec("pmid:2"), _rec("doi:10.1/x")])
            records = export_select.select_by_evidence_ids(repo, ["pmid:1", "doi:10.1/x"])
            self.assertEqual({r["evidence_id"] for r in records}, {"pmid:1", "doi:10.1/x"})

    def test_raises_naming_all_unresolved_ids(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _write_registry(repo, [_rec("pmid:1")])
            with self.assertRaises(export_select.ExportError) as ctx:
                export_select.select_by_evidence_ids(
                    repo, ["pmid:1", "pmid:999", "doi:10.1/missing"]
                )
            message = str(ctx.exception)
            self.assertIn("pmid:999", message)
            self.assertIn("doi:10.1/missing", message)
            self.assertNotIn("pmid:1'", message)  # the resolved id must not be reported


class SelectByRunDirTest(unittest.TestCase):
    def _write_run_corpus(self, run_dir: Path, records: list[dict]) -> None:
        write_jsonl(run_dir / "corpus.jsonl", records)

    def test_only_included_records_are_selected_and_resolved(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(parents=True)
            _write_registry(repo, [_rec("pmid:1"), _rec("pmid:2"), _rec("pmid:3")])
            self._write_run_corpus(run_dir, [
                {"evidence_id": "pmid:1", "screening": {"decision": "include"}},
                {"evidence_id": "pmid:2", "screening": {"decision": "exclude"}},
                {"evidence_id": "pmid:3", "screening": {"decision": "include"}},
            ])
            records = export_select.select_by_run_dir(repo, run_dir)
            self.assertEqual({r["evidence_id"] for r in records}, {"pmid:1", "pmid:3"})

    def test_run_with_no_screening_state_raises(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(parents=True)
            _write_registry(repo, [_rec("pmid:1")])
            self._write_run_corpus(run_dir, [
                {"evidence_id": "pmid:1"},  # no `screening` key at all
            ])
            with self.assertRaises(export_select.ExportError):
                export_select.select_by_run_dir(repo, run_dir)

    def test_missing_corpus_file_raises(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(parents=True)
            _write_registry(repo, [_rec("pmid:1")])
            with self.assertRaises(export_select.ExportError):
                export_select.select_by_run_dir(repo, run_dir)

    def test_included_evidence_id_absent_from_registry_raises(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(parents=True)
            _write_registry(repo, [_rec("pmid:1")])
            self._write_run_corpus(run_dir, [
                {"evidence_id": "pmid:1", "screening": {"decision": "include"}},
                {"evidence_id": "pmid:404", "screening": {"decision": "include"}},
            ])
            with self.assertRaises(export_select.ExportError) as ctx:
                export_select.select_by_run_dir(repo, run_dir)
            self.assertIn("pmid:404", str(ctx.exception))


class LegacyRegistryAssetSourceTest(unittest.TestCase):
    def test_available_when_file_exists_and_checksum_matches(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            pdf_path = repo / "data" / "sources" / "assets" / "sha256-abc.pdf"
            pdf_path.parent.mkdir(parents=True)
            data = b"%PDF-1.4 fake pdf bytes"
            pdf_path.write_bytes(data)
            digest = _sha256_bytes(data)
            record = _rec("pmid:1", fulltext={
                "status": "fulltext", "local_path": str(pdf_path), "sha256": digest,
            })
            source = export_select.LegacyRegistryAssetSource(repo)
            resolution = source.resolve(record)
            self.assertTrue(resolution.available)
            self.assertEqual(resolution.local_path, pdf_path)
            self.assertEqual(resolution.sha256_expected, digest)
            self.assertEqual(resolution.role, "primary")
            self.assertIsNone(resolution.problem)

    def test_relative_local_path_resolves_against_repo_root(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            rel = "data/sources/assets/sha256-def.pdf"
            pdf_path = repo / rel
            pdf_path.parent.mkdir(parents=True)
            data = b"another fake pdf"
            pdf_path.write_bytes(data)
            digest = _sha256_bytes(data)
            record = _rec("pmid:2", fulltext={
                "status": "fulltext", "local_path": rel, "sha256": digest,
            })
            source = export_select.LegacyRegistryAssetSource(repo)
            resolution = source.resolve(record)
            self.assertTrue(resolution.available)
            self.assertEqual(resolution.local_path, pdf_path.resolve())

    def test_no_fulltext_key_reports_no_fulltext_recorded(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            record = _rec("pmid:3")
            source = export_select.LegacyRegistryAssetSource(repo)
            resolution = source.resolve(record)
            self.assertFalse(resolution.available)
            self.assertEqual(resolution.problem, "no fulltext recorded")

    def test_missing_file_reports_missing_file(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            record = _rec("pmid:4", fulltext={
                "status": "fulltext",
                "local_path": str(repo / "does" / "not" / "exist.pdf"),
                "sha256": "deadbeef",
            })
            source = export_select.LegacyRegistryAssetSource(repo)
            resolution = source.resolve(record)
            self.assertFalse(resolution.available)
            self.assertEqual(resolution.problem, "missing file")

    def test_checksum_mismatch_is_reported(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            pdf_path = repo / "bad.pdf"
            pdf_path.write_bytes(b"actual bytes")
            record = _rec("pmid:5", fulltext={
                "status": "fulltext", "local_path": str(pdf_path),
                "sha256": "0" * 64,
            })
            source = export_select.LegacyRegistryAssetSource(repo)
            resolution = source.resolve(record)
            self.assertFalse(resolution.available)
            self.assertEqual(resolution.problem, "checksum mismatch")

    def test_unreadable_file_is_reported(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            pdf_path = repo / "unreadable.pdf"
            pdf_path.write_bytes(b"secret bytes")
            os.chmod(pdf_path, 0o000)
            try:
                if os.access(pdf_path, os.R_OK):
                    self.skipTest("running as a user that bypasses file permissions (e.g. root)")
                record = _rec("pmid:6", fulltext={
                    "status": "fulltext", "local_path": str(pdf_path), "sha256": None,
                })
                source = export_select.LegacyRegistryAssetSource(repo)
                resolution = source.resolve(record)
                self.assertFalse(resolution.available)
                self.assertEqual(resolution.problem, "unreadable")
            finally:
                os.chmod(pdf_path, 0o644)


if __name__ == "__main__":
    unittest.main()
