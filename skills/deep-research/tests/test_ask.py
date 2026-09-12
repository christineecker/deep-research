"""`ask.py retrieve` — hybrid retrieval with span verification.

What matters here is not ranking quality (that needs a corpus, not a fixture) but the
contract the answering command depends on:

  * every returned claim/passage carries a `(source_id, start, end)` span that verified
    against the snapshot store *at retrieval time*;
  * a span that no longer verifies is reported under `unverified` and never appears as
    quotable evidence -- a tampered snapshot must not be citable;
  * missing optional pieces (no embeddings, no chunk index, empty registry) degrade to
    a note, never an error.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, run_py, write_json

ask = load_script("ask.py")
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


BODY = ("Background. " + "Unrelated filler sentence. " * 60
        + "Results: the rare zebra biomarker fell by 41 percent in the exercise arm. "
        + "Discussion. " * 30)
CLAIM_START = BODY.index("Results:")
CLAIM_END = CLAIM_START + len("Results: the rare zebra biomarker fell by 41 percent in "
                              "the exercise arm.")


def _repo_with_one_paper(tmp: Path) -> tuple[Path, str]:
    repo = tmp / "repo"
    research.cmd_init(_ns(path=str(repo), from_wiki=None))
    reg = registry.Registry(repo)
    with reg.locked():
        reg.register({"pmid": "1", "title": "Exercise for adolescent depression",
                      "journal": "J Val", "publication_date": "2026"})
        reg.commit()

    result = store.global_write_snapshot_result(
        repo, url="https://example.org/one", text=BODY, title="Snap",
        access="full_text", origin="web", paper=None, event_type="fetch", fresh=True,
        actor="test")
    source_id = result["source_id"]
    extraction = repo / "data" / "papers" / "extractions" / "pmid-1.json"
    write_json(extraction, {
        "evidence_id": "pmid:1",
        "spans": [{"claim": "The zebra biomarker fell by 41 percent.",
                   "evidence_id": "pmid:1", "source_id": source_id,
                   "start": CLAIM_START, "end": CLAIM_END, "access": "full_text"}],
    })
    reg = registry.Registry(repo)
    with reg.locked():
        reg.set_extraction("pmid:1", str(extraction.relative_to(repo)))
        reg.commit()
    run_py(["scripts/registry.py", "reindex", "--repo", str(repo)])
    return repo, source_id


def _retrieve(repo: Path, question: str, **kwargs) -> dict:
    argv = ["scripts/ask.py", "retrieve", "--repo", str(repo), "--question", question,
            "--no-semantic"]
    for key, value in kwargs.items():
        argv.extend(["--" + key.replace("_", "-"), str(value)])
    result = run_py(argv)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


class ReciprocalRankFusionTest(unittest.TestCase):
    def test_agreement_between_rankers_outranks_one_confident_ranker(self):
        fused = dict(ask.reciprocal_rank_fusion(["a", "b"], ["b", "c"]))
        self.assertGreater(fused["b"], fused["a"])
        self.assertGreater(fused["a"], fused["c"])

    def test_a_single_list_keeps_its_order(self):
        fused = ask.reciprocal_rank_fusion(["a", "b", "c"])
        self.assertEqual([key for key, _ in fused], ["a", "b", "c"])

    def test_empty_input_fuses_to_nothing(self):
        self.assertEqual(ask.reciprocal_rank_fusion([], []), [])


class RetrieveTest(unittest.TestCase):
    def test_returns_verified_claims_and_passages(self):
        with TemporaryDirectory() as tmp:
            repo, source_id = _repo_with_one_paper(Path(tmp))
            payload = _retrieve(repo, "did the zebra biomarker change with exercise?")

            self.assertEqual(len(payload["papers"]), 1)
            paper = payload["papers"][0]
            self.assertEqual(paper["evidence_id"], "pmid:1")
            self.assertEqual(paper["found_by"], ["lexical"])

            claim = paper["claims"][0]
            self.assertEqual(claim["source_id"], source_id)
            self.assertIn("zebra biomarker", claim["excerpt"])

            passage = paper["passages"][0]
            self.assertEqual(passage["source_id"], source_id)
            self.assertEqual(BODY[passage["start"]:passage["end"]], passage["text"])
            self.assertEqual(payload["unverified"], [])

    def test_every_returned_span_verifies_against_the_snapshot(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_one_paper(Path(tmp))
            payload = _retrieve(repo, "zebra biomarker exercise")
            snapshots = ask._SnapshotCache(registry.repo_paths(repo)["repo_root"])
            for paper in payload["papers"]:
                for entry in paper["claims"] + paper["passages"]:
                    span = {"source_id": entry["source_id"], "start": entry["start"],
                            "end": entry["end"]}
                    self.assertTrue(ask.verify(span, snapshots)["ok"])

    def test_a_tampered_snapshot_makes_its_spans_unciteable(self):
        with TemporaryDirectory() as tmp:
            repo, source_id = _repo_with_one_paper(Path(tmp))
            snapshot_path = store.global_snapshot_path(repo, source_id)
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            data["text"] = data["text"].replace("41 percent", "94 percent")
            snapshot_path.write_text(json.dumps(data), encoding="utf-8")

            payload = _retrieve(repo, "zebra biomarker exercise")
            for paper in payload["papers"]:
                self.assertEqual(paper["claims"], [])
                self.assertEqual(paper["passages"], [])
            self.assertTrue(payload["unverified"])
            self.assertEqual(payload["unverified"][0]["reason_code"],
                             "SNAPSHOT_HASH_MISMATCH")
            self.assertTrue(any("nothing quotable" in note for note in payload["notes"]))

    def test_passages_per_paper_caps_the_bundle(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_one_paper(Path(tmp))
            payload = _retrieve(repo, "filler sentence discussion", passages_per_paper=1)
            self.assertLessEqual(len(payload["papers"][0]["passages"]), 1)

    def test_no_chunk_index_says_so_instead_of_failing(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_one_paper(Path(tmp))
            (repo / "data" / "refmgr" / "library.sqlite3").unlink()
            payload = _retrieve(repo, "zebra biomarker")
            self.assertTrue(any("reindex" in note for note in payload["notes"]))

    def test_empty_registry_answers_nothing_cleanly(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            payload = _retrieve(repo, "anything at all")
            self.assertEqual(payload["papers"], [])
            self.assertTrue(any("empty" in note for note in payload["notes"]))

    def test_empty_question_is_rejected(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_one_paper(Path(tmp))
            result = run_py(["scripts/ask.py", "retrieve", "--repo", str(repo),
                             "--question", "   "])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_semantic_leg_is_reported_as_unavailable_without_an_index(self):
        with TemporaryDirectory() as tmp:
            repo, _ = _repo_with_one_paper(Path(tmp))
            result = run_py(["scripts/ask.py", "retrieve", "--repo", str(repo),
                             "--question", "zebra biomarker"])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["retrieval"]["semantic_status"]["status"],
                             "unavailable")
            self.assertTrue(payload["papers"], "lexical retrieval must still run")

    def test_help_does_not_require_sentence_transformers(self):
        for argv in (["--help"], ["retrieve", "--help"]):
            result = run_py(["scripts/ask.py", *argv])
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
