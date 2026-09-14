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

    def test_enriching_a_pmid_only_record_with_a_doi_reconciles_onto_the_same_paper(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Exercise for depression"})
                reg.commit()
            first_paper_id = registry.Registry(repo).records["pmid:1"]["refmgr_paper_id"]

            # Re-register the same record, now with a DOI -- an enrichment pass,
            # exactly what a later metadata lookup would produce.
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Exercise for depression",
                              "doi": "10.1000/enriched"})
                report = reg.commit()

            self.assertEqual(report["errors"], [])
            # Still one paper -- the existing refmgr_paper_id was reused, not
            # bypassed past reconciliation.
            papers = _papers(repo)
            self.assertEqual(len(papers), 1)
            self.assertEqual(papers[0]["id"], first_paper_id)

            conn = sqlite3.connect(repo / "data" / "refmgr" / "library.sqlite3")
            try:
                rows = conn.execute(
                    "SELECT scheme, value FROM identifiers WHERE paper_id = ?",
                    (first_paper_id,),
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(set(rows), {("pmid", "1"), ("doi", "10.1000/enriched")})

            # Repeating the same enrichment is idempotent -- no duplicate rows.
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Exercise for depression",
                              "doi": "10.1000/enriched"})
                reg.commit()
            conn = sqlite3.connect(repo / "data" / "refmgr" / "library.sqlite3")
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM identifiers WHERE paper_id = ?",
                    (first_paper_id,),
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 2)

    def test_conflicting_enrichment_leaves_sqlite_state_intact_and_reports_both_ids(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Paper one",
                              "doi": "10.1000/already-taken"})
                reg.register({"pmid": "2", "title": "Paper two"})
                reg.commit()

            paper_one_id = registry.Registry(repo).records["pmid:1"]["refmgr_paper_id"]
            paper_two_id = registry.Registry(repo).records["pmid:2"]["refmgr_paper_id"]

            # pmid:2 is (incorrectly) enriched with pmid:1's DOI -- a genuine conflict.
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "2", "title": "Paper two",
                              "doi": "10.1000/already-taken"})
                report = reg.commit()

            self.assertEqual(len(report["errors"]), 1)
            error = report["errors"][0]
            self.assertEqual(error["kind"], "identifier_conflict")
            self.assertEqual(error["evidence_id"], "pmid:2")
            self.assertEqual(error["scheme"], "doi")
            self.assertEqual(error["existing_paper_id"], paper_one_id)
            self.assertEqual(error["conflicting_paper_id"], paper_two_id)

            # pmid:2's refmgr identifiers are untouched -- still just its own pmid.
            conn = sqlite3.connect(repo / "data" / "refmgr" / "library.sqlite3")
            try:
                rows = conn.execute(
                    "SELECT scheme, value FROM identifiers WHERE paper_id = ?",
                    (paper_two_id,),
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(set(rows), {("pmid", "2")})

    def test_export_uses_primary_identifier_per_scheme(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "T1", "doi": "10.1000/preprint"})
                reg.commit()
            paper_id = registry.Registry(repo).records["pmid:1"]["refmgr_paper_id"]

            export_select_refmgr = load_script("export_select_refmgr.py")
            from refmgr.service import ReferenceManagerService
            service = ReferenceManagerService(repo / "data" / "refmgr")
            try:
                record = export_select_refmgr.paper_to_export_record(service, paper_id)
                self.assertEqual(record["doi"], "10.1000/preprint")

                # Promote a second DOI (a published version) to primary; export
                # must follow, using the same policy refmgr itself tracks.
                published_id = service.identifiers.add(
                    paper_id, "doi", "10.1000/published"
                )
                service.identifiers.set_primary(paper_id, published_id)
                record = export_select_refmgr.paper_to_export_record(service, paper_id)
                self.assertEqual(record["doi"], "10.1000/published")
            finally:
                service.close()

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


def _seed_fulltext_custom(repo: Path, reg, evidence_id: str, *, body: str,
                          claim: str) -> str:
    """Like `_seed_fulltext`, but with caller-chosen body/claim text so a test can put
    a term ONLY in the snapshot body (never in the always-scanned claim text) to
    isolate metadata/claim matching from chunk-index-only matching."""
    result = store.global_write_snapshot_result(
        repo, url=f"https://example.org/{evidence_id}", text=body, title="Snap",
        access="full_text", origin="web", paper=None, event_type="fetch", fresh=True,
        actor="test")
    source_id = result["source_id"]
    path = repo / "data" / "papers" / "extractions" / f"{registry.extraction_slug(evidence_id)}.json"
    write_json(path, {
        "evidence_id": evidence_id,
        "spans": [{"claim": claim, "evidence_id": evidence_id,
                   "source_id": source_id, "start": 0, "end": 10, "access": "full_text"}],
    })
    with reg.locked():
        reg.set_extraction(evidence_id, str(path.relative_to(repo)))
        reg.commit()
    return source_id


class SearchCoverageCorrectnessTest(unittest.TestCase):
    """Regression coverage for hardening-plan package 2: a nonzero global chunk
    count must never stand in for one specific paper's own coverage, and AND terms
    must match across the union of metadata and body text, never only one or the
    other."""

    def _search(self, repo: Path, query: str) -> dict:
        result = run_py(["scripts/registry.py", "search", "--repo", str(repo),
                         "--q", query])
        assert result.returncode in (0, 1), result.stderr
        return json.loads(result.stdout)

    def test_indexing_one_paper_does_not_suppress_results_for_an_unindexed_one(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Paper one"})
                reg.commit()
            _seed_fulltext_custom(
                repo, registry.Registry(repo), "pmid:1",
                body="Background. " + "Filler. " * 40 + "Discussion of alpha findings.",
                claim="unrelated claim text",
            )
            # Reindex now, while pmid:2 does not exist yet -- only pmid:1 gets chunked.
            run_py(["scripts/registry.py", "reindex", "--repo", str(repo)])

            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "2", "title": "Paper two"})
                reg.commit()
            _seed_fulltext_custom(
                repo, registry.Registry(repo), "pmid:2",
                body="Background. " + "Filler. " * 40 + "Discussion of gammawombat findings.",
                claim="a second unrelated claim",
            )
            # pmid:2 is registered and has full text, but was never (re)indexed --
            # the chunk table now has rows for pmid:1 only, so the global count is
            # nonzero. A term unique to pmid:2's body must still be found.
            payload = self._search(repo, "gammawombat")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:2"])

    def test_and_terms_match_across_title_and_indexed_body(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Kestrelfinch therapy trial"})
                reg.commit()
            _seed_fulltext_custom(
                repo, registry.Registry(repo), "pmid:1",
                body="Background. " + "Filler. " * 40
                    + "Results show a marked wombatolin response.",
                claim="unrelated claim text",
            )
            run_py(["scripts/registry.py", "reindex", "--repo", str(repo)])

            # "kestrelfinch" is only in the title (metadata); "wombatolin" is only in
            # the chunk-indexed body. Neither term alone is in both places, so this
            # only matches if AND is evaluated over their union.
            payload = self._search(repo, "kestrelfinch wombatolin")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])

    def test_stale_chunker_version_falls_back_to_scanning_that_source(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "1", "title": "Exercise for depression"})
                reg.commit()
            _seed_fulltext(repo, registry.Registry(repo), "pmid:1")
            run_py(["scripts/registry.py", "reindex", "--repo", str(repo)])

            # Simulate a chunker upgrade: the indexed rows were produced by an
            # older splitting version than what search now expects.
            db_path = repo / "data" / "refmgr" / "library.sqlite3"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("UPDATE chunks SET chunker_version = 0")
                conn.commit()
            finally:
                conn.close()

            # Coverage no longer holds at the current chunker version, so the
            # record's own body must be scanned directly instead of silently
            # relying on stale rows -- the result must not disappear.
            payload = self._search(repo, "zebra biomarker")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])


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
