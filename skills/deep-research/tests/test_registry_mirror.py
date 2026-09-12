"""The registry -> refmgr mirror, and the chunk index it feeds.

`registry.jsonl` stays the source of truth; refmgr's `papers`/`identifiers`/`papers_fts`
and `chunks_fts` are the index built from it (OPTIMIZATION_PLAN.md items 4 and 5). The
properties pinned here are the ones that make that split safe:

  * a paper registered by identifier -- not just one imported as a PDF -- reaches
    refmgr, which is what the mirror exists to fix;
  * mirroring is idempotent, and `reindex` backfills a repo written before it existed;
  * a broken or absent index degrades `search --q` to the old full scan instead of
    failing or silently returning less.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, run_py, write_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

registry = load_script("registry.py")
research = load_script("research.py")
store = load_script("store.py")


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


def _papers(repo: Path) -> list[dict]:
    conn = sqlite3.connect(repo / "data" / "refmgr" / "library.sqlite3")
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM papers")]
    finally:
        conn.close()


def _fts_paper_ids(repo: Path) -> set[str]:
    conn = sqlite3.connect(repo / "data" / "refmgr" / "library.sqlite3")
    try:
        return {r[0] for r in conn.execute("SELECT paper_id FROM papers_fts")}
    finally:
        conn.close()


BODY = ("Background. " + "Unrelated filler sentence. " * 60
        + "Results: the rare zebra biomarker fell by 41 percent in the exercise arm. "
        + "Discussion. " * 30)


def _seed_fulltext(repo: Path, reg, evidence_id: str) -> str:
    """Register a snapshot and point `evidence_id`'s extraction at it. Returns source_id."""
    result = store.global_write_snapshot_result(
        repo, url=f"https://example.org/{evidence_id}", text=BODY, title="Snap",
        access="full_text", origin="web", paper=None, event_type="fetch", fresh=True,
        actor="test")
    source_id = result["source_id"]
    path = repo / "data" / "papers" / "extractions" / f"{registry.extraction_slug(evidence_id)}.json"
    write_json(path, {
        "evidence_id": evidence_id,
        "spans": [{"claim": "zebra biomarker fell 41 percent", "evidence_id": evidence_id,
                   "source_id": source_id, "start": 0, "end": 10, "access": "full_text"}],
    })
    with reg.locked():
        reg.set_extraction(evidence_id, str(path.relative_to(repo)))
        reg.commit()
    return source_id


class MirrorTest(unittest.TestCase):
    def test_register_then_commit_mirrors_into_refmgr(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Exercise for depression",
                              "journal": "J Val", "publication_date": "2026"})
                report = reg.commit()

            self.assertEqual(report["mirrored"], 1)
            self.assertEqual(report["errors"], [])
            papers = _papers(repo)
            self.assertEqual(len(papers), 1)
            self.assertEqual(papers[0]["title"], "Exercise for depression")
            metadata = json.loads(papers[0]["metadata_json"])
            self.assertEqual(metadata["evidence_id"], "pmid:1")
            self.assertEqual(metadata["journal"], "J Val")
            self.assertEqual(_fts_paper_ids(repo), {papers[0]["id"]})

            # The link is persisted on the registry record, so it survives a reload.
            self.assertEqual(registry.Registry(repo).records["pmid:1"]["refmgr_paper_id"],
                             papers[0]["id"])

    def test_mirroring_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            for _ in range(3):
                reg = registry.Registry(repo)
                with reg.locked():
                    reg.register({"pmid": "1", "title": "Exercise for depression",
                                  "journal": "J Val", "publication_date": "2026"})
                    reg.commit()
            self.assertEqual(len(_papers(repo)), 1)

    def test_a_corrected_title_reaches_the_index(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Wrong title"})
                reg.commit()
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Corrected title"})
                reg.commit()
            self.assertEqual([p["title"] for p in _papers(repo)], ["Corrected title"])

    def test_records_without_a_resolvable_identifier_do_not_duplicate(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            for _ in range(2):
                reg = registry.Registry(repo)
                with reg.locked():
                    # No pmid/doi/pmcid: evidence_id is a url:<digest>, which refmgr
                    # cannot match on. The persisted refmgr_paper_id is what keeps this
                    # from minting a fresh paper every pass.
                    reg.register({"title": "A title-only record"})
                    reg.commit()
            self.assertEqual(len(_papers(repo)), 1)

    def test_commit_with_mirror_disabled_writes_the_registry_only(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "T"})
                report = reg.commit(mirror=False)
            self.assertTrue(report["skipped"])
            self.assertFalse((repo / "data" / "refmgr" / "library.sqlite3").exists())
            self.assertIn("pmid:1", registry.Registry(repo).records)


class ReindexTest(unittest.TestCase):
    def test_reindex_backfills_records_written_before_the_mirror_existed(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "Legacy one"})
            reg.register({"pmid": "2", "title": "Legacy two"})
            reg.save()  # save(), not commit(): exactly what the old code path did
            self.assertFalse((repo / "data" / "refmgr" / "library.sqlite3").exists())

            result = run_py(["scripts/registry.py", "reindex", "--repo", str(repo)])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["papers"]["mirrored"], 2)
            self.assertEqual(payload["coverage"]["papers_fts"]["stale_or_missing"], 0)
            self.assertEqual({p["title"] for p in _papers(repo)}, {"Legacy one", "Legacy two"})

    def test_reindex_builds_the_chunk_index_and_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Exercise for depression"})
                reg.commit()
            _seed_fulltext(repo, registry.Registry(repo), "pmid:1")

            first = json.loads(run_py(["scripts/registry.py", "reindex", "--repo",
                                       str(repo)]).stdout)
            self.assertEqual(first["chunks"]["sources_indexed"], 1)
            self.assertGreater(first["chunks"]["chunks"], 0)

            second = json.loads(run_py(["scripts/registry.py", "reindex", "--repo",
                                        str(repo), "--chunks-only"]).stdout)
            self.assertEqual(second["chunks"]["sources_indexed"], 0)
            self.assertEqual(second["chunks"]["sources_current"], 1)
            self.assertEqual(second["coverage"]["chunks"]["chunks"],
                             first["coverage"]["chunks"]["chunks"])

    def test_reindex_reports_a_record_with_no_refmgr_paper(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "T"})
                reg.commit()
            payload = json.loads(run_py(["scripts/registry.py", "reindex", "--repo",
                                         str(repo), "--chunks-only"]).stdout)
            self.assertEqual(payload["chunks"]["no_paper_id"], 0)


class SearchThroughTheChunkIndexTest(unittest.TestCase):
    def _search(self, repo: Path, query: str) -> dict:
        result = run_py(["scripts/registry.py", "search", "--repo", str(repo),
                         "--q", query])
        assert result.returncode in (0, 1), result.stderr
        return json.loads(result.stdout)

    def _repo_with_fulltext(self, tmp: Path) -> Path:
        repo = _init_repo(tmp)
        reg = registry.Registry(repo)
        with reg.locked():
            reg.register({"pmid": "1", "title": "Exercise for depression"})
            reg.commit()
        _seed_fulltext(repo, registry.Registry(repo), "pmid:1")
        run_py(["scripts/registry.py", "reindex", "--repo", str(repo)])
        return repo

    def test_a_term_only_in_the_full_text_still_matches(self):
        with TemporaryDirectory() as tmp:
            repo = self._repo_with_fulltext(Path(tmp))
            payload = self._search(repo, "zebra biomarker")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])

    def test_and_semantics_survive_the_move_to_the_index(self):
        with TemporaryDirectory() as tmp:
            repo = self._repo_with_fulltext(Path(tmp))
            # "zebra" is in the body, "unicorn" is nowhere: AND must still mean AND.
            self.assertEqual(self._search(repo, "zebra unicorn")["results"], [])

    def test_falls_back_to_scanning_snapshots_when_no_index_exists(self):
        with TemporaryDirectory() as tmp:
            repo = self._repo_with_fulltext(Path(tmp))
            (repo / "data" / "refmgr" / "library.sqlite3").unlink()
            payload = self._search(repo, "zebra biomarker")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])

    def test_a_corrupt_index_degrades_to_the_scan_rather_than_failing(self):
        with TemporaryDirectory() as tmp:
            repo = self._repo_with_fulltext(Path(tmp))
            (repo / "data" / "refmgr" / "library.sqlite3").write_bytes(b"not a database")
            result = run_py(["scripts/registry.py", "search", "--repo", str(repo),
                             "--q", "zebra biomarker"])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])
            self.assertIn("reindex", result.stderr)


if __name__ == "__main__":
    unittest.main()
