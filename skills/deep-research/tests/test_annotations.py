from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, run_py

annotations = load_script("annotations.py")


def _ns(**kwargs):
    class NS:
        pass
    ns = NS()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


class AnnotationsTagTest(unittest.TestCase):
    def test_tag_add_and_remove(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ann = annotations.Annotations(repo)
            rec = ann.upsert("pmid:1", add_tag="to-read")
            self.assertEqual(rec["tags"], ["to-read"])
            rec = ann.upsert("pmid:1", add_tag="diagnostics")
            self.assertEqual(rec["tags"], ["diagnostics", "to-read"])
            rec = ann.upsert("pmid:1", remove_tag="to-read")
            self.assertEqual(rec["tags"], ["diagnostics"])

    def test_tag_add_is_deduped_and_remove_of_absent_tag_is_noop(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ann = annotations.Annotations(repo)
            ann.upsert("pmid:1", add_tag="x")
            rec = ann.upsert("pmid:1", add_tag="x")
            self.assertEqual(rec["tags"], ["x"])
            rec = ann.upsert("pmid:1", remove_tag="not-there")
            self.assertEqual(rec["tags"], ["x"])

    def test_cli_tag_requires_exactly_one_of_add_remove(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            result = run_py(["scripts/annotations.py", "tag", "--repo", str(repo),
                             "--evidence-id", "pmid:1"])
            self.assertEqual(result.returncode, 2)
            result2 = run_py(["scripts/annotations.py", "tag", "--repo", str(repo),
                              "--evidence-id", "pmid:1", "--add", "a", "--remove", "b"])
            self.assertEqual(result2.returncode, 2)


class AnnotationsRateTest(unittest.TestCase):
    def test_rate_set_and_clear(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ann = annotations.Annotations(repo)
            rec = ann.upsert("pmid:1", rating=4)
            self.assertEqual(rec["rating"], 4)
            rec = ann.upsert("pmid:1", rating=None)
            self.assertNotIn("rating", rec)

    def test_cli_rejects_out_of_range_rating(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            for stars in ("0", "6", "-1"):
                result = run_py(["scripts/annotations.py", "rate", "--repo", str(repo),
                                 "--evidence-id", "pmid:1", "--stars", stars])
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            ok = run_py(["scripts/annotations.py", "rate", "--repo", str(repo),
                        "--evidence-id", "pmid:1", "--stars", "3"])
            self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_cli_rate_requires_exactly_one_of_stars_clear(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            result = run_py(["scripts/annotations.py", "rate", "--repo", str(repo),
                             "--evidence-id", "pmid:1"])
            self.assertEqual(result.returncode, 2)


class AnnotationsNoteTest(unittest.TestCase):
    def test_note_set_and_clear(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ann = annotations.Annotations(repo)
            rec = ann.upsert("pmid:1", note="hello")
            self.assertEqual(rec["note"], "hello")
            rec = ann.upsert("pmid:1", note=None)
            self.assertNotIn("note", rec)

    def test_cli_note_requires_exactly_one_of_set_clear(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            result = run_py(["scripts/annotations.py", "note", "--repo", str(repo),
                             "--evidence-id", "pmid:1"])
            self.assertEqual(result.returncode, 2)


class AnnotationsShowTest(unittest.TestCase):
    def test_show_never_errors_on_unannotated_paper(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            result = run_py(["scripts/annotations.py", "show", "--repo", str(repo),
                             "--evidence-id", "pmid:999"])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["record"]["evidence_id"], "pmid:999")
            self.assertEqual(payload["record"]["tags"], [])
            self.assertNotIn("rating", payload["record"])
            self.assertNotIn("note", payload["record"])


class AnnotationsListTest(unittest.TestCase):
    def _seeded_repo(self, tmp: Path) -> Path:
        repo = tmp / "repo"
        ann = annotations.Annotations(repo)
        ann.upsert("pmid:1", add_tag="diagnostics", rating=5)
        ann.upsert("pmid:2", add_tag="diagnostics", rating=2)
        ann.upsert("pmid:3", add_tag="to-read", rating=4)
        ann.save()
        return repo

    def test_list_filters_by_tag(self):
        with TemporaryDirectory() as tmp:
            repo = self._seeded_repo(Path(tmp))
            result = run_py(["scripts/annotations.py", "list", "--repo", str(repo),
                             "--tag", "diagnostics"])
            self.assertEqual(result.returncode, 0, result.stderr)
            entries = json.loads(result.stdout)
            self.assertEqual({e["evidence_id"] for e in entries}, {"pmid:1", "pmid:2"})

    def test_list_filters_by_min_rating(self):
        with TemporaryDirectory() as tmp:
            repo = self._seeded_repo(Path(tmp))
            result = run_py(["scripts/annotations.py", "list", "--repo", str(repo),
                             "--min-rating", "4"])
            self.assertEqual(result.returncode, 0, result.stderr)
            entries = json.loads(result.stdout)
            self.assertEqual({e["evidence_id"] for e in entries}, {"pmid:1", "pmid:3"})

    def test_list_is_sorted_by_evidence_id_and_respects_limit(self):
        with TemporaryDirectory() as tmp:
            repo = self._seeded_repo(Path(tmp))
            result = run_py(["scripts/annotations.py", "list", "--repo", str(repo),
                             "--limit", "2"])
            self.assertEqual(result.returncode, 0, result.stderr)
            entries = json.loads(result.stdout)
            self.assertEqual(len(entries), 2)
            self.assertEqual([e["evidence_id"] for e in entries], ["pmid:1", "pmid:2"])


class AnnotationsPersistenceTest(unittest.TestCase):
    def test_atomic_write_round_trips(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            ann = annotations.Annotations(repo)
            ann.upsert("pmid:1", add_tag="x", rating=3, note="n")
            ann.save()
            path = repo / "data" / "papers" / "annotations.jsonl"
            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(".jsonl.tmp").exists())
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            on_disk = json.loads(lines[0])
            self.assertEqual(on_disk["evidence_id"], "pmid:1")
            self.assertEqual(on_disk["tags"], ["x"])
            self.assertEqual(on_disk["rating"], 3)
            self.assertEqual(on_disk["note"], "n")

            reloaded = annotations.Annotations(repo)
            self.assertEqual(reloaded.get("pmid:1")["note"], "n")

    def test_corrupt_line_is_skipped_not_raised(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            path = repo / "data" / "papers" / "annotations.jsonl"
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"schema_version": 1, "evidence_id": "pmid:1", "tags": []}\n'
                "not json at all\n"
                '{"schema_version": 1, "evidence_id": "pmid:2", "tags": ["y"]}\n',
                encoding="utf-8",
            )
            ann = annotations.Annotations(repo)
            self.assertEqual(set(ann.records), {"pmid:1", "pmid:2"})

    def test_uses_annotations_lock_not_registry_lock(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            result = run_py(["scripts/annotations.py", "tag", "--repo", str(repo),
                             "--evidence-id", "pmid:1", "--add", "x"])
            self.assertEqual(result.returncode, 0, result.stderr)
            lock_dir = repo / ".locks"
            self.assertTrue((lock_dir / "annotations.lock").exists())
            self.assertFalse((lock_dir / "registry.lock").exists())


if __name__ == "__main__":
    unittest.main()
