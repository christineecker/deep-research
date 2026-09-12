from __future__ import annotations

import hashlib
import json
import re
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from helpers import load_script, run_py, write_jsonl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

export = load_script("export.py")


# --------------------------------------------------------------------- fixtures

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_registry(repo: Path, records: list[dict]) -> None:
    write_jsonl(repo / "data" / "papers" / "registry.jsonl", records)


def _pdf_record(repo: Path, evidence_id: str, pdf_bytes: bytes, **extra) -> dict:
    pdf_path = repo / "data" / "sources" / "assets" / f"{export.safe_label(evidence_id)}.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(pdf_bytes)
    digest = _sha256_bytes(pdf_bytes)
    base = {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "status": "included",
        "title": f"Title for {evidence_id}",
        "authors_structured": [{"family": "Smith", "given": "Jane", "initials": "J"}],
        "journal": "Journal of Testing",
        "publication_date": "2025-01-01",
        "fulltext": {"status": "fulltext", "local_path": str(pdf_path), "sha256": digest},
    }
    base.update(extra)
    return base, pdf_path, digest


def _meta_record(evidence_id: str, **extra) -> dict:
    base = {
        "schema_version": 1,
        "evidence_id": evidence_id,
        "status": "registered",
        "title": f"Title for {evidence_id}",
        "authors_structured": [{"family": "Smith", "given": "Jane", "initials": "J"}],
        "journal": "Journal of Testing",
        "publication_date": "2025-01-01",
    }
    base.update(extra)
    return base


def _make_fixture_repo(repo: Path) -> dict:
    """Four records: full metadata + real PDF, metadata-only, missing-DOI, Unicode."""
    with_pdf, pdf_path, digest = _pdf_record(
        repo, "pmid:1001", b"%PDF-1.4 fake pdf bytes for pmid1001",
        doi="10.1000/pmid1001", pmid="1001", pmcid="PMC1001",
    )
    metadata_only = _meta_record("doi:10.1000/metaonly", doi="10.1000/metaonly")
    missing_doi = _meta_record("pmid:1003", pmid="1003")
    del missing_doi["title"]
    missing_doi["title"] = "Missing-DOI study"
    unicode_rec = _meta_record(
        "pmid:1004", pmid="1004",
        title="Über die Grundlagen — β-Analyse",
        authors_structured=[{"family": "Müller", "given": "Jörg", "initials": "J"}],
    )
    _write_registry(repo, [with_pdf, metadata_only, missing_doi, unicode_rec])
    return {"with_pdf": with_pdf, "pdf_path": pdf_path, "digest": digest,
            "metadata_only": metadata_only, "missing_doi": missing_doi,
            "unicode": unicode_rec}


def _cli(*args: str) -> "subprocess.CompletedProcess":  # noqa: F821 - typing only
    return run_py(["scripts/export.py", "readcube", *args])


# ------------------------------------------------------------------------ tests

class DryRunTest(unittest.TestCase):
    def test_all_dry_run_reports_counts_and_writes_nothing(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            _make_fixture_repo(repo)

            result = _cli("--repo", str(repo), "--all", "--destination", str(dest), "--dry-run")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["selection"]["count"], 4)
            self.assertEqual(payload["primary_pdf_count"], 1)
            self.assertEqual(payload["metadata_only_count"], 3)
            self.assertIsNone(payload["bundle_path"])
            self.assertFalse(dest.exists())


class PublishTest(unittest.TestCase):
    def test_all_publish_produces_expected_bundle(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            _make_fixture_repo(repo)

            result = _cli("--repo", str(repo), "--all", "--destination", str(dest))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["local_publication_state"], "published")

            batch_dir = Path(payload["bundle_path"])
            self.assertTrue(batch_dir.is_dir())
            for name in ("references.ris", "manifest.json", "import-report.md",
                         "README.md", "COMPLETE.json"):
                self.assertTrue((batch_dir / name).exists(), name)
            self.assertTrue((batch_dir / "PDFs").is_dir())

            manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["records"], 4)
            self.assertEqual({r["evidence_id"] for r in manifest["records"]},
                             {"pmid:1001", "doi:10.1000/metaonly", "pmid:1003", "pmid:1004"})
            self.assertEqual(manifest["counts"]["primary_pdf"], 1)

            # Phase 5: per-record fulltext status, both in the manifest and listed
            # (not just aggregated) in import-report.md.
            by_eid = {r["evidence_id"]: r for r in manifest["records"]}
            self.assertEqual(by_eid["pmid:1001"]["fulltext_status"], "fulltext")
            self.assertEqual(by_eid["doi:10.1000/metaonly"]["fulltext_status"], "missing")
            report_text = (batch_dir / "import-report.md").read_text(encoding="utf-8")
            self.assertIn("## Records", report_text)
            self.assertIn("| pmid:1001 |", report_text)
            self.assertIn("fulltext", report_text)
            self.assertIn("missing", report_text)

            ris_text = (batch_dir / "references.ris").read_text(encoding="utf-8")
            ty_blocks = re.findall(r"^TY  - ", ris_text, flags=re.MULTILINE)
            self.assertEqual(len(ty_blocks), 4)

            # write-order: COMPLETE.json must be the newest file by mtime.
            files = [p for p in batch_dir.iterdir() if p.is_file()]
            complete = batch_dir / "COMPLETE.json"
            complete_mtime = complete.stat().st_mtime_ns
            for f in files:
                self.assertGreaterEqual(complete_mtime, f.stat().st_mtime_ns)

    def test_rerun_creates_a_second_independent_batch(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            _make_fixture_repo(repo)

            r1 = _cli("--repo", str(repo), "--all", "--destination", str(dest))
            r2 = _cli("--repo", str(repo), "--all", "--destination", str(dest))
            self.assertEqual(r1.returncode, 0)
            self.assertEqual(r2.returncode, 0)
            p1 = json.loads(r1.stdout)["bundle_path"]
            p2 = json.loads(r2.stdout)["bundle_path"]
            self.assertNotEqual(p1, p2)
            self.assertTrue(Path(p1).is_dir())
            self.assertTrue(Path(p2).is_dir())

    def test_bib_flag_writes_references_bib(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            _make_fixture_repo(repo)

            result = _cli("--repo", str(repo), "--all", "--destination", str(dest), "--bib")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            batch_dir = Path(json.loads(result.stdout)["bundle_path"])
            bib_path = batch_dir / "references.bib"
            self.assertTrue(bib_path.exists())
            content = bib_path.read_text(encoding="utf-8")
            self.assertEqual(content.count("@"), 4)


class UnknownEvidenceIdTest(unittest.TestCase):
    def test_unknown_id_fails_closed(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            _make_fixture_repo(repo)

            result = _cli("--repo", str(repo), "--evidence-id", "pmid:1001",
                          "--evidence-id", "pmid:999999", "--destination", str(dest))
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertIn("pmid:999999", json.dumps(payload))
            self.assertFalse(dest.exists() and any(dest.iterdir()))


class RequirePdfsTest(unittest.TestCase):
    def test_require_pdfs_blocks_publication_when_asset_unresolved(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            fixtures = _make_fixture_repo(repo)

            result = _cli("--repo", str(repo), "--evidence-id", fixtures["metadata_only"]["evidence_id"],
                          "--destination", str(dest), "--require-pdfs")
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertTrue(payload["blocking_errors"])
            self.assertFalse(dest.exists() and any(dest.iterdir()))

    def test_without_require_pdfs_publishes_with_warning(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            fixtures = _make_fixture_repo(repo)

            result = _cli("--repo", str(repo), "--evidence-id", fixtures["metadata_only"]["evidence_id"],
                          "--destination", str(dest))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertTrue(any("unavailable" in w for w in payload["warnings"]))
            self.assertEqual(payload["local_publication_state"], "published")


class FilenameCollisionTest(unittest.TestCase):
    def test_collision_extends_hash_prefix_until_unique(self):
        # Two distinct sha256 values hand-crafted to share an 8-hex-char prefix.
        sha_a = "aaaaaaaa" + "1" * 56
        sha_b = "aaaaaaaa" + "2" * 56
        used: dict[str, str] = {}
        name_a = export.unique_bundle_filename("same-title", sha_a, "pdf", used)
        name_b = export.unique_bundle_filename("same-title", sha_b, "pdf", used)
        self.assertNotEqual(name_a, name_b)
        self.assertTrue(name_a.startswith("same-title--aaaaaaaa"))
        self.assertTrue(name_b.startswith("same-title--aaaaaaaa"))
        # Re-requesting the same sha256 returns the same filename (idempotent).
        name_a_again = export.unique_bundle_filename("same-title", sha_a, "pdf", used)
        self.assertEqual(name_a, name_a_again)


class IncrementalTest(unittest.TestCase):
    def test_first_incremental_run_behaves_like_full_export_and_records_ledger(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            _make_fixture_repo(repo)

            result = _cli("--repo", str(repo), "--all", "--destination", str(dest), "--incremental")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["exported_count"], 4)
            self.assertEqual(payload["unchanged_count"], 0)
            self.assertEqual(payload["ledger_state"], "tracked")
            self.assertIsNotNone(payload["bundle_path"])

            batch_dir = Path(payload["bundle_path"])
            manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["records"], 4)

            ledger_path = repo / "data" / "papers" / "export_ledger.jsonl"
            self.assertTrue(ledger_path.exists())
            lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 4)
            self.assertTrue(all(entry["status"] == "published" for entry in lines))

    def test_second_identical_incremental_run_is_a_no_op(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            _make_fixture_repo(repo)

            first = _cli("--repo", str(repo), "--all", "--destination", str(dest), "--incremental")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            batches_before = list(dest.iterdir())

            second = _cli("--repo", str(repo), "--all", "--destination", str(dest), "--incremental")
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            payload = json.loads(second.stdout)
            self.assertEqual(payload["status"], "no_op")
            self.assertEqual(payload["exported_count"], 0)
            self.assertEqual(payload["unchanged_count"], 4)
            self.assertIsNone(payload["batch_id"])
            self.assertIsNone(payload["bundle_path"])

            # No new batch directory was created.
            self.assertEqual(list(dest.iterdir()), batches_before)

    def test_changing_one_record_reexports_only_that_record(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            fixtures = _make_fixture_repo(repo)

            first = _cli("--repo", str(repo), "--all", "--destination", str(dest), "--incremental")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

            # Mutate one record's metadata (title) in the registry, then re-export.
            registry_path = repo / "data" / "papers" / "registry.jsonl"
            records = [json.loads(line) for line in registry_path.read_text(encoding="utf-8").splitlines()]
            target_eid = fixtures["metadata_only"]["evidence_id"]
            for rec in records:
                if rec["evidence_id"] == target_eid:
                    rec["title"] = "A brand-new title"
            _write_registry(repo, records)

            second = _cli("--repo", str(repo), "--all", "--destination", str(dest), "--incremental")
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            payload = json.loads(second.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["exported_count"], 1)
            self.assertEqual(payload["unchanged_count"], 3)

            batch_dir = Path(payload["bundle_path"])
            manifest = json.loads((batch_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["records"], 1)
            self.assertEqual(manifest["records"][0]["evidence_id"], target_eid)

    def test_failed_publish_leaves_ledger_sane_for_next_attempt(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            _make_fixture_repo(repo)

            parser = export.build_arg_parser()
            args = parser.parse_args([
                "readcube", "--repo", str(repo), "--all", "--destination", str(dest), "--incremental",
            ])
            with mock.patch.object(export.shutil, "copyfile", side_effect=OSError("disk full")):
                exit_code = export.cmd_readcube(args)
            self.assertEqual(exit_code, 3)

            ledger_path = repo / "data" / "papers" / "export_ledger.jsonl"
            self.assertTrue(ledger_path.exists())
            entries = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
            # None of the attempted records were falsely marked as published.
            self.assertTrue(all(entry["status"] == "failed" for entry in entries))
            self.assertEqual(len(entries), 4)

            # A subsequent (unmocked) incremental run must treat all of them as
            # changed/new — a failed entry is not a valid baseline — and succeed.
            retry = _cli("--repo", str(repo), "--all", "--destination", str(dest), "--incremental")
            self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
            payload = json.loads(retry.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["exported_count"], 4)
            self.assertEqual(payload["unchanged_count"], 0)


class AtomicityTest(unittest.TestCase):
    def test_mid_publish_failure_leaves_no_partial_batch_at_final_path(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            _make_fixture_repo(repo)

            parser = export.build_arg_parser()
            args = parser.parse_args([
                "readcube", "--repo", str(repo), "--all", "--destination", str(dest),
            ])

            with mock.patch.object(export.shutil, "copyfile", side_effect=OSError("disk full")):
                exit_code = export.cmd_readcube(args)

            self.assertEqual(exit_code, 3)
            # No batch directory at the final path, and no leftover staging dir either.
            if dest.exists():
                remaining = list(dest.iterdir())
                self.assertEqual(remaining, [], f"unexpected leftovers: {remaining}")


class RefmgrSourceTest(unittest.TestCase):
    """Phase 2.6: --source refmgr, wired into the real CLI (not just the standalone
    export_select_refmgr.py module tests)."""

    def test_all_via_refmgr_source_publishes_real_paper_and_pdf(self):
        from refmgr.service import ReferenceManagerService
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            library = tmp / "lib"
            dest = tmp / "dest"
            src_pdf = tmp / "src" / "paper.pdf"
            src_pdf.parent.mkdir(parents=True)
            src_pdf.write_bytes(b"%PDF-1.4 fake pdf bytes for refmgr source test")
            (repo / "data" / "papers").mkdir(parents=True)

            with ReferenceManagerService(library) as svc:
                paper_id = svc.add_paper(
                    "A Refmgr-Sourced Export Test", "article",
                    metadata={"journal": "Journal of Wiring", "publication_date": "2025-01",
                              "authors": ["Doe J"]},
                    identifiers=[("doi", "10.2000/refmgr-1")],
                )
                svc.import_attachment(paper_id, src_pdf, role="primary", preferred=True)

            result = _cli(
                "--repo", str(repo), "--source", "refmgr", "--library", str(library),
                "--all", "--destination", str(dest), "--name", "refmgrtest",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["selection"]["count"], 1)
            self.assertEqual(payload["primary_pdf_count"], 1)

            bundle = Path(payload["bundle_path"])
            ris_text = (bundle / "references.ris").read_text()
            self.assertIn("A Refmgr-Sourced Export Test", ris_text)
            self.assertIn("10.2000/refmgr-1", ris_text)
            pdfs = list((bundle / "PDFs").glob("*.pdf"))
            self.assertEqual(len(pdfs), 1)
            self.assertEqual(pdfs[0].read_bytes(), src_pdf.read_bytes())

    def test_evidence_id_selection_resolves_bare_paper_id_and_identifier(self):
        from refmgr.service import ReferenceManagerService
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            library = tmp / "lib"
            dest = tmp / "dest"
            (repo / "data" / "papers").mkdir(parents=True)

            with ReferenceManagerService(library) as svc:
                paper_id = svc.add_paper(
                    "Findable By Both", "article",
                    identifiers=[("doi", "10.3000/findme-1")],
                )

            # Resolve by bare paper id.
            result = _cli(
                "--repo", str(repo), "--source", "refmgr", "--library", str(library),
                "--evidence-id", paper_id, "--destination", str(dest), "--name", "byid",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["selection"]["count"], 1)

            # Resolve by "scheme:value" identifier.
            result = _cli(
                "--repo", str(repo), "--source", "refmgr", "--library", str(library),
                "--evidence-id", "doi:10.3000/findme-1", "--destination", str(dest),
                "--name", "byidentifier",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["selection"]["count"], 1)

    def test_evidence_id_selection_unresolved_fails_closed(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            library = tmp / "lib"
            dest = tmp / "dest"
            (repo / "data" / "papers").mkdir(parents=True)

            result = _cli(
                "--repo", str(repo), "--source", "refmgr", "--library", str(library),
                "--evidence-id", "whatever-unresolvable", "--destination", str(dest),
            )
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")

    def test_run_dir_selection_rejected_for_refmgr_source(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            library = tmp / "lib"
            dest = tmp / "dest"
            run_dir = tmp / "run"
            run_dir.mkdir(parents=True)
            (repo / "data" / "papers").mkdir(parents=True)

            result = _cli(
                "--repo", str(repo), "--source", "refmgr", "--library", str(library),
                "--run-dir", str(run_dir), "--destination", str(dest),
            )
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")

    def test_multi_attachment_paper_exports_primary_and_supplement(self):
        from refmgr.service import ReferenceManagerService
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            library = tmp / "lib"
            dest = tmp / "dest"
            (repo / "data" / "papers").mkdir(parents=True)
            primary_pdf = tmp / "primary.pdf"
            primary_pdf.write_bytes(b"%PDF-1.4 primary attachment bytes")
            supp_pdf = tmp / "supplement.pdf"
            supp_pdf.write_bytes(b"%PDF-1.4 supplement attachment bytes, longer")

            with ReferenceManagerService(library) as svc:
                paper_id = svc.add_paper("Multi-Attachment Paper", "article")
                svc.import_attachment(paper_id, primary_pdf, role="primary", preferred=True)
                svc.import_attachment(paper_id, supp_pdf, role="supplement")

            result = _cli(
                "--repo", str(repo), "--source", "refmgr", "--library", str(library),
                "--all", "--destination", str(dest), "--name", "multi",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["primary_pdf_count"], 1)
            self.assertEqual(payload["supplement_count"], 1)

            bundle = Path(payload["bundle_path"])
            pdfs = list((bundle / "PDFs").glob("*.pdf"))
            supplements = list((bundle / "Supplements").glob("*.pdf"))
            self.assertEqual(len(pdfs), 1)
            self.assertEqual(len(supplements), 1)
            self.assertEqual(pdfs[0].read_bytes(), primary_pdf.read_bytes())
            self.assertEqual(supplements[0].read_bytes(), supp_pdf.read_bytes())

            manifest = json.loads((bundle / "manifest.json").read_text())
            record = manifest["records"][0]
            roles = sorted(a["role"] for a in record["attachments"])
            self.assertEqual(roles, ["primary", "supplement"])

    def test_refmgr_source_requires_library_flag(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = tmp / "repo"
            dest = tmp / "dest"
            (repo / "data" / "papers").mkdir(parents=True)

            result = _cli(
                "--repo", str(repo), "--source", "refmgr",
                "--all", "--destination", str(dest),
            )
            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")


if __name__ == "__main__":
    unittest.main()
