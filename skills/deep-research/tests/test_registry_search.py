from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, run_py, write_json

registry = load_script("registry.py")
research = load_script("research.py")
annotations = load_script("annotations.py")
embeddings = load_script("embeddings.py")
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


def _search(repo: Path, **kwargs) -> dict:
    """Run `registry.py search` via the CLI and parse its JSON payload."""
    argv = ["scripts/registry.py", "search", "--repo", str(repo)]
    for key, value in kwargs.items():
        if value is None:
            continue
        flag = "--" + key.replace("_", "-")
        if value is True:
            argv.append(flag)
        else:
            argv.extend([flag, str(value)])
    result = run_py(argv)
    assert result.returncode in (0, 1), result.stderr
    return json.loads(result.stdout), result.returncode


class FacetFilterTest(unittest.TestCase):
    def test_no_filters_returns_everything_like_a_light_list(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "Paper one", "journal": "J A", "publication_date": "2020"})
            reg.register({"pmid": "2", "title": "Paper two", "journal": "J B", "publication_date": "2021"})
            reg.save()
            payload, code = _search(repo)
            self.assertEqual(code, 0)
            self.assertEqual(payload["count"], 2)
            eids = {r["evidence_id"] for r in payload["results"]}
            self.assertEqual(eids, {"pmid:1", "pmid:2"})

    def test_limit_caps_results(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            for i in range(3):
                reg.register({"pmid": str(i), "title": f"Paper {i}", "journal": "J",
                             "publication_date": "2020"})
            reg.save()
            payload, _ = _search(repo, limit=1)
            self.assertEqual(len(payload["results"]), 1)

    def test_journal_substring_filter_is_case_insensitive(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "Journal of Testing",
                         "publication_date": "2020"})
            reg.register({"pmid": "2", "title": "T2", "journal": "Another Venue",
                         "publication_date": "2020"})
            reg.save()
            payload, _ = _search(repo, journal="testing")
            eids = [r["evidence_id"] for r in payload["results"]]
            self.assertEqual(eids, ["pmid:1"])

    def test_year_single_and_range_filter(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "J", "publication_date": "2018-05-01"})
            reg.register({"pmid": "2", "title": "T2", "journal": "J", "publication_date": "2020-01-01"})
            reg.register({"pmid": "3", "title": "T3", "journal": "J", "publication_date": "2022-01-01"})
            reg.save()
            single, _ = _search(repo, year="2020")
            self.assertEqual([r["evidence_id"] for r in single["results"]], ["pmid:2"])
            ranged, _ = _search(repo, year="2018-2020")
            self.assertEqual(sorted(r["evidence_id"] for r in ranged["results"]),
                             ["pmid:1", "pmid:2"])

    def test_status_extraction_status_filters(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "J", "publication_date": "2020"})
            reg.register({"pmid": "2", "title": "T2", "journal": "J", "publication_date": "2020"})
            reg.records["pmid:2"]["status"] = "included"
            reg.set_extraction("pmid:2", "data/papers/extractions/pmid-2.json")
            reg.save()
            by_status, _ = _search(repo, status="included")
            self.assertEqual([r["evidence_id"] for r in by_status["results"]], ["pmid:2"])
            by_extraction, _ = _search(repo, **{"extraction-status": "extracted"})
            self.assertEqual([r["evidence_id"] for r in by_extraction["results"]], ["pmid:2"])

    def test_appraisal_status_scoped_to_project(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "J", "publication_date": "2020"})
            reg.set_appraisal("pmid:1", "proj-a", "data/papers/appraisals/proj-a/pmid-1.json")
            reg.save()
            # appraised overall, but not for proj-b
            hit, _ = _search(repo, **{"appraisal-status": "appraised", "project": "proj-a"})
            self.assertEqual([r["evidence_id"] for r in hit["results"]], ["pmid:1"])
            miss, _ = _search(repo, **{"appraisal-status": "appraised", "project": "proj-b"})
            self.assertEqual(miss["results"], [])


class AnnotationFilterTest(unittest.TestCase):
    def test_tag_and_min_rating_filter(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "J", "publication_date": "2020"})
            reg.register({"pmid": "2", "title": "T2", "journal": "J", "publication_date": "2020"})
            reg.save()
            ann = annotations.Annotations(repo)
            ann.upsert("pmid:1", add_tag="to-read", rating=5)
            ann.upsert("pmid:2", add_tag="other", rating=2)
            ann.save()

            by_tag, _ = _search(repo, tag="to-read")
            self.assertEqual([r["evidence_id"] for r in by_tag["results"]], ["pmid:1"])

            by_rating, _ = _search(repo, **{"min-rating": 3})
            self.assertEqual([r["evidence_id"] for r in by_rating["results"]], ["pmid:1"])


class KeywordSearchTest(unittest.TestCase):
    def test_matches_title(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "Exercise therapy for adolescent depression",
                         "journal": "J", "publication_date": "2020"})
            reg.register({"pmid": "2", "title": "Unrelated paper about geology",
                         "journal": "J", "publication_date": "2020"})
            reg.save()
            payload, _ = _search(repo, q="adolescent depression")
            eids = [r["evidence_id"] for r in payload["results"]]
            self.assertEqual(eids, ["pmid:1"])
            self.assertIn("snippet", payload["results"][0])
            self.assertIsNotNone(payload["results"][0]["snippet"])

    def test_matches_abstract(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "J", "publication_date": "2020",
                         "abstract": "This trial enrolled forty adults with a rare biomarker."})
            reg.save()
            payload, _ = _search(repo, q="rare biomarker")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])
            self.assertIn("biomarker", payload["results"][0]["snippet"])

    def test_and_match_requires_every_term(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "Alpha beta", "journal": "J", "publication_date": "2020"})
            reg.save()
            hit, _ = _search(repo, q="alpha beta")
            self.assertEqual(len(hit["results"]), 1)
            miss, _ = _search(repo, q="alpha gamma")
            self.assertEqual(miss["results"], [])

    def test_matches_extraction_narrative_field(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "J", "publication_date": "2020"})
            extraction_path = repo / "data" / "papers" / "extractions" / "pmid-1.json"
            write_json(extraction_path, {
                "population": "Adults with treatment-resistant hypertension",
                "intervention": "Renal denervation", "comparator": None,
                "limitations": None, "extractor_notes": None,
            })
            reg.set_extraction("pmid:1", str(extraction_path.relative_to(repo)))
            reg.save()
            payload, _ = _search(repo, q="renal denervation")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])

    def test_matches_appraisal_rationale(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "J", "publication_date": "2020"})
            appraisal_path = repo / "data" / "papers" / "appraisals" / "proj-a" / "pmid-1.json"
            write_json(appraisal_path, {
                "tool": "RoB2",
                "domains": [
                    {"domain": "Randomization process", "judgement": "low",
                     "rationale": "Computer-generated sequence, central allocation described.",
                     "spans": []},
                ],
            })
            reg.set_appraisal("pmid:1", "proj-a", str(appraisal_path.relative_to(repo)))
            reg.save()
            payload, _ = _search(repo, q="central allocation")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])

    def test_matches_snapshot_body_via_extraction_spans(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            write_result = store.global_write_snapshot_result(
                repo, url="https://example.org/paper1", text="Background: a rare zebra "
                "disorder was studied in this cohort of 40 patients.",
                title="Snapshot title", access="full_text", origin="web",
                paper=None, event_type="fetch", fresh=True, actor="test")
            source_id = write_result["source_id"]

            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "J", "publication_date": "2020"})
            extraction_path = repo / "data" / "papers" / "extractions" / "pmid-1.json"
            write_json(extraction_path, {
                "population": None, "intervention": None, "comparator": None,
                "limitations": None, "extractor_notes": None,
                "spans": [{"claim": "cohort of 40 patients", "evidence_id": "pmid:1",
                          "source_id": source_id, "start": 0, "end": 10, "access": "full_text"}],
            })
            reg.set_extraction("pmid:1", str(extraction_path.relative_to(repo)))
            reg.save()

            payload, _ = _search(repo, q="zebra disorder")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])


class SimilarToTest(unittest.TestCase):
    def test_errors_cleanly_when_no_embeddings_file(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "T1", "journal": "J", "publication_date": "2020"})
            reg.save()
            payload, code = _search(repo, **{"similar-to": "pmid:1"})
            self.assertEqual(code, 1)
            self.assertEqual(payload["status"], "error")
            self.assertIn("embeddings.py index", payload["error"])

    def test_ranks_by_similarity_and_merges_with_other_filters(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "Query paper", "journal": "J", "publication_date": "2020"})
            reg.register({"pmid": "2", "title": "Close paper", "journal": "J", "publication_date": "2020"})
            reg.register({"pmid": "3", "title": "Far paper", "journal": "J", "publication_date": "2020"})
            reg.save()
            embeddings.write_embeddings(repo, {
                "pmid:1": {"schema_version": 1, "evidence_id": "pmid:1", "model": "m",
                          "dim": 2, "vector": [1.0, 0.0], "updated_at": "2026-01-01T00:00:00Z"},
                "pmid:2": {"schema_version": 1, "evidence_id": "pmid:2", "model": "m",
                          "dim": 2, "vector": [0.9, 0.1], "updated_at": "2026-01-01T00:00:00Z"},
                "pmid:3": {"schema_version": 1, "evidence_id": "pmid:3", "model": "m",
                          "dim": 2, "vector": [0.0, 1.0], "updated_at": "2026-01-01T00:00:00Z"},
            })
            payload, code = _search(repo, **{"similar-to": "pmid:1"})
            self.assertEqual(code, 0)
            eids = [r["evidence_id"] for r in payload["results"]]
            self.assertEqual(eids, ["pmid:2", "pmid:3"])
            self.assertNotIn("pmid:1", eids)
            self.assertIn("score", payload["results"][0])
            self.assertGreater(payload["results"][0]["score"], payload["results"][1]["score"])

    def test_similar_to_intersects_with_other_filters(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "Query paper", "journal": "J A", "publication_date": "2020"})
            reg.register({"pmid": "2", "title": "Close paper", "journal": "J B", "publication_date": "2020"})
            reg.register({"pmid": "3", "title": "Far paper", "journal": "J A", "publication_date": "2020"})
            reg.save()
            embeddings.write_embeddings(repo, {
                "pmid:1": {"schema_version": 1, "evidence_id": "pmid:1", "model": "m",
                          "dim": 2, "vector": [1.0, 0.0], "updated_at": "2026-01-01T00:00:00Z"},
                "pmid:2": {"schema_version": 1, "evidence_id": "pmid:2", "model": "m",
                          "dim": 2, "vector": [0.9, 0.1], "updated_at": "2026-01-01T00:00:00Z"},
                "pmid:3": {"schema_version": 1, "evidence_id": "pmid:3", "model": "m",
                          "dim": 2, "vector": [0.0, 1.0], "updated_at": "2026-01-01T00:00:00Z"},
            })
            # Restrict to journal "J A" -- pmid:2 (the closest neighbour) is filtered out
            # first, so only pmid:3 should remain in the similarity-ranked output.
            payload, code = _search(repo, **{"similar-to": "pmid:1", "journal": "J A"})
            self.assertEqual(code, 0)
            eids = [r["evidence_id"] for r in payload["results"]]
            self.assertEqual(eids, ["pmid:3"])


if __name__ == "__main__":
    unittest.main()
