from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script

export_ledger = load_script("export_ledger.py")

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

N_WORKERS = 6
WORKER_TIMEOUT_S = 30

_WORKER_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[3])
from export_ledger import ExportLedger

repo_root, idx = sys.argv[1], sys.argv[2]
ledger = ExportLedger(repo_root)
eid = f"pmid:worker-{idx}"
ledger.record_published(
    "dest-a", eid, metadata_hash=f"hash-{idx}", attachment_fingerprint="fp",
    batch_id=f"batch-{idx}", published_at="2026-01-01T00:00:00Z",
)
"""


class AttachmentFingerprintTest(unittest.TestCase):
    def test_empty_and_nonempty_are_stable_and_distinct(self):
        empty = export_ledger.attachment_fingerprint([])
        one = export_ledger.attachment_fingerprint([("primary", "abc123")])
        self.assertNotEqual(empty, one)
        self.assertEqual(empty, export_ledger.attachment_fingerprint([]))
        self.assertEqual(one, export_ledger.attachment_fingerprint([("primary", "abc123")]))

    def test_order_matters(self):
        a = export_ledger.attachment_fingerprint([("primary", "aaa"), ("supplement", "bbb")])
        b = export_ledger.attachment_fingerprint([("supplement", "bbb"), ("primary", "aaa")])
        self.assertNotEqual(a, b)


class LoadTest(unittest.TestCase):
    def test_load_empty_returns_empty_dict(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            self.assertEqual(ledger.load("dest-a"), {})

    def test_load_only_returns_matching_destination(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            ledger.record_published("dest-a", "pmid:1", "h1", "fp1", "batch-1", "2026-01-01T00:00:00Z")
            ledger.record_published("dest-b", "pmid:1", "h2", "fp2", "batch-2", "2026-01-01T00:00:00Z")
            a = ledger.load("dest-a")
            b = ledger.load("dest-b")
            self.assertEqual(set(a), {"pmid:1"})
            self.assertEqual(a["pmid:1"]["metadata_hash"], "h1")
            self.assertEqual(set(b), {"pmid:1"})
            self.assertEqual(b["pmid:1"]["metadata_hash"], "h2")


class RecordPublishedTest(unittest.TestCase):
    def test_upserts_with_published_status(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            ledger.record_published("dest-a", "pmid:1", "h1", "fp1", "batch-1", "2026-01-01T00:00:00Z")
            entry = ledger.load("dest-a")["pmid:1"]
            self.assertEqual(entry["status"], "published")
            self.assertEqual(entry["metadata_hash"], "h1")
            self.assertEqual(entry["attachment_fingerprint"], "fp1")
            self.assertEqual(entry["batch_id"], "batch-1")

            # Re-publish with new hash overwrites (upsert, not append).
            ledger.record_published("dest-a", "pmid:1", "h2", "fp2", "batch-2", "2026-01-02T00:00:00Z")
            entry = ledger.load("dest-a")["pmid:1"]
            self.assertEqual(entry["metadata_hash"], "h2")
            self.assertEqual(entry["batch_id"], "batch-2")

    def test_persists_to_disk_as_jsonl(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            ledger.record_published("dest-a", "pmid:1", "h1", "fp1", "batch-1", "2026-01-01T00:00:00Z")
            path = export_ledger.ledger_path(repo)
            self.assertTrue(path.exists())
            lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["evidence_id"], "pmid:1")
            self.assertEqual(lines[0]["destination_identity"], "dest-a")


class RecordFailedTest(unittest.TestCase):
    def test_upserts_with_failed_status_and_error(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            ledger.record_failed("dest-a", "pmid:1", batch_id=None,
                                 published_at="2026-01-01T00:00:00Z", error="disk full")
            entry = ledger.load("dest-a")["pmid:1"]
            self.assertEqual(entry["status"], "failed")
            self.assertEqual(entry["error"], "disk full")

    def test_failed_entry_is_not_a_valid_baseline(self):
        """Plan: "Only published bundles establish incremental baselines" — a record
        whose only ledger history is `failed` must diff as changed_or_new, not
        unchanged."""
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            ledger.record_failed("dest-a", "pmid:1", batch_id=None,
                                 published_at="2026-01-01T00:00:00Z", error="disk full")
            candidates = [{"evidence_id": "pmid:1", "metadata_hash": "h1",
                          "attachment_fingerprint": "fp1"}]
            changed, unchanged = ledger.diff("dest-a", candidates)
            self.assertEqual(changed, candidates)
            self.assertEqual(unchanged, [])

    def test_failed_after_published_invalidates_baseline(self):
        """A subsequent failed attempt against a previously-published record must not
        leave that record looking unchanged on the next diff — its latest ledger state is
        now `failed`, so it must be re-attempted."""
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            ledger.record_published("dest-a", "pmid:1", "h1", "fp1", "batch-1", "2026-01-01T00:00:00Z")
            ledger.record_failed("dest-a", "pmid:1", batch_id=None,
                                 published_at="2026-01-02T00:00:00Z", error="disk full")
            candidates = [{"evidence_id": "pmid:1", "metadata_hash": "h1",
                          "attachment_fingerprint": "fp1"}]
            changed, unchanged = ledger.diff("dest-a", candidates)
            self.assertEqual(changed, candidates)
            self.assertEqual(unchanged, [])


class DiffTest(unittest.TestCase):
    def test_no_prior_entries_all_changed(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            candidates = [
                {"evidence_id": "pmid:1", "metadata_hash": "h1", "attachment_fingerprint": "fp1"},
                {"evidence_id": "pmid:2", "metadata_hash": "h2", "attachment_fingerprint": "fp2"},
            ]
            changed, unchanged = ledger.diff("dest-a", candidates)
            self.assertEqual(changed, candidates)
            self.assertEqual(unchanged, [])

    def test_exact_match_is_unchanged_mismatch_is_changed(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            ledger.record_published("dest-a", "pmid:1", "h1", "fp1", "batch-1", "2026-01-01T00:00:00Z")
            ledger.record_published("dest-a", "pmid:2", "h2", "fp2", "batch-1", "2026-01-01T00:00:00Z")

            candidates = [
                {"evidence_id": "pmid:1", "metadata_hash": "h1", "attachment_fingerprint": "fp1"},  # same
                {"evidence_id": "pmid:2", "metadata_hash": "CHANGED", "attachment_fingerprint": "fp2"},  # metadata changed
                {"evidence_id": "pmid:3", "metadata_hash": "h3", "attachment_fingerprint": "fp3"},  # new
            ]
            changed, unchanged = ledger.diff("dest-a", candidates)
            self.assertEqual([c["evidence_id"] for c in unchanged], ["pmid:1"])
            self.assertEqual({c["evidence_id"] for c in changed}, {"pmid:2", "pmid:3"})

    def test_attachment_fingerprint_change_alone_is_changed(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            ledger.record_published("dest-a", "pmid:1", "h1", "fp1", "batch-1", "2026-01-01T00:00:00Z")
            candidates = [{"evidence_id": "pmid:1", "metadata_hash": "h1", "attachment_fingerprint": "fp-DIFFERENT"}]
            changed, unchanged = ledger.diff("dest-a", candidates)
            self.assertEqual(changed, candidates)
            self.assertEqual(unchanged, [])

    def test_different_destination_does_not_leak_baseline(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            ledger.record_published("dest-a", "pmid:1", "h1", "fp1", "batch-1", "2026-01-01T00:00:00Z")
            candidates = [{"evidence_id": "pmid:1", "metadata_hash": "h1", "attachment_fingerprint": "fp1"}]
            changed, unchanged = ledger.diff("dest-b", candidates)
            self.assertEqual(changed, candidates)
            self.assertEqual(unchanged, [])


class LockedHandleTest(unittest.TestCase):
    def test_locked_diff_publish_record_round_trip(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            candidates = [{"evidence_id": "pmid:1", "metadata_hash": "h1", "attachment_fingerprint": "fp1"}]

            with ledger.locked("dest-a") as handle:
                changed, unchanged = handle.diff(candidates)
                self.assertEqual(changed, candidates)
                for cand in changed:
                    handle.record_published(cand["evidence_id"], cand["metadata_hash"],
                                            cand["attachment_fingerprint"], "batch-1",
                                            "2026-01-01T00:00:00Z")

            # Persisted after the `with` block exits.
            entry = ledger.load("dest-a")["pmid:1"]
            self.assertEqual(entry["status"], "published")

            # A second locked pass against the same candidates now sees it as unchanged.
            with ledger.locked("dest-a") as handle:
                changed, unchanged = handle.diff(candidates)
                self.assertEqual(changed, [])
                self.assertEqual(unchanged, candidates)

    def test_locked_record_failed_does_not_persist_as_published(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ledger = export_ledger.ExportLedger(repo)
            with ledger.locked("dest-a") as handle:
                handle.record_failed("pmid:1", batch_id=None,
                                     published_at="2026-01-01T00:00:00Z", error="boom")
            entry = ledger.load("dest-a")["pmid:1"]
            self.assertEqual(entry["status"], "failed")


class ConcurrencyTest(unittest.TestCase):
    def test_concurrent_subprocess_writes_do_not_lose_records(self):
        """Two (or more) processes each opening their own `ExportLedger` against the same
        repo_root and calling `record_published` for distinct evidence_ids must not lose
        an update — mirrors `test_refmgr_service.py`'s subprocess concurrency pattern,
        adapted for file-based `advisory_lock`."""
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo_root = tmp / "repo"
            script_path = tmp / "_worker.py"
            script_path.write_text(_WORKER_SCRIPT, encoding="utf-8")

            procs = []
            for i in range(N_WORKERS):
                proc = subprocess.Popen(
                    [sys.executable, str(script_path), str(repo_root), str(i), str(SCRIPTS)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
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
                    failures.append(f"worker {i} exited {proc.returncode}. stderr:\n{stderr}")

            if failures:
                self.fail("\n---\n".join(failures))

            ledger = export_ledger.ExportLedger(repo_root)
            entries = ledger.load("dest-a")
            self.assertEqual(len(entries), N_WORKERS)
            expected = {f"pmid:worker-{i}" for i in range(N_WORKERS)}
            self.assertEqual(set(entries), expected)
            for i in range(N_WORKERS):
                self.assertEqual(entries[f"pmid:worker-{i}"]["status"], "published")


if __name__ == "__main__":
    unittest.main()
