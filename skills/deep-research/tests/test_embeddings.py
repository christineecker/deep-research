from __future__ import annotations

import json
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, run_py, write_json

embeddings = load_script("embeddings.py")
registry = load_script("registry.py")
research = load_script("research.py")

try:
    import sentence_transformers  # noqa: F401
    _HAS_ST = True
except ImportError:
    _HAS_ST = False


def _ns(**kwargs):
    class NS:
        pass
    ns = NS()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


class CosineSimilarityTest(unittest.TestCase):
    def test_identical_vectors_score_one(self):
        v = [1.0, 2.0, 3.0]
        self.assertAlmostEqual(embeddings.cosine_similarity(v, v), 1.0)

    def test_orthogonal_vectors_score_zero(self):
        self.assertAlmostEqual(embeddings.cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_opposite_vectors_score_negative_one(self):
        self.assertAlmostEqual(embeddings.cosine_similarity([1.0, 0.0], [-1.0, 0.0]), -1.0)

    def test_zero_vector_scores_zero_not_nan(self):
        self.assertEqual(embeddings.cosine_similarity([0.0, 0.0], [1.0, 2.0]), 0.0)

    def test_dimension_mismatch_raises(self):
        with self.assertRaises(ValueError):
            embeddings.cosine_similarity([1.0, 2.0], [1.0])

    def test_scaled_vectors_still_score_one(self):
        self.assertAlmostEqual(
            embeddings.cosine_similarity([1.0, 1.0], [2.0, 2.0]), 1.0)


class EmbeddingTextTest(unittest.TestCase):
    def test_combines_title_abstract_and_extraction_narrative(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            extraction_path = repo / "data" / "papers" / "extractions" / "pmid-1.json"
            write_json(extraction_path, {
                "population": "Adults with X",
                "intervention": "Drug Y",
                "comparator": "Placebo",
                "limitations": "Small sample",
                "extractor_notes": "Note Z",
            })
            rec = {
                "title": "A trial title",
                "abstract": "An abstract body.",
                "extraction_path": str(extraction_path.relative_to(repo)),
            }
            text = embeddings.embedding_text(rec, repo)
            self.assertIn("A trial title", text)
            self.assertIn("An abstract body.", text)
            self.assertIn("Adults with X", text)
            self.assertIn("Drug Y", text)
            self.assertIn("Note Z", text)

    def test_missing_extraction_file_is_silently_skipped(self):
        rec = {"title": "T", "abstract": "A", "extraction_path": "does/not/exist.json"}
        text = embeddings.embedding_text(rec, Path("/tmp"))
        self.assertEqual(text, "T A")

    def test_no_extraction_path_uses_title_and_abstract_only(self):
        rec = {"title": "T", "abstract": "A"}
        text = embeddings.embedding_text(rec, Path("/tmp"))
        self.assertEqual(text, "T A")

    def test_missing_fields_do_not_raise(self):
        text = embeddings.embedding_text({}, Path("/tmp"))
        self.assertEqual(text, "")


class ReadWriteEmbeddingsTest(unittest.TestCase):
    def test_read_embeddings_on_missing_file_returns_empty_dict(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(embeddings.read_embeddings(Path(tmp)), {})

    def test_write_then_read_roundtrip_sorted_by_evidence_id(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            records = {
                "pmid:2": {"schema_version": 1, "evidence_id": "pmid:2", "model": "m",
                          "dim": 2, "vector": [0.1, 0.2], "updated_at": "2026-01-01T00:00:00Z"},
                "pmid:1": {"schema_version": 1, "evidence_id": "pmid:1", "model": "m",
                          "dim": 2, "vector": [0.3, 0.4], "updated_at": "2026-01-01T00:00:00Z"},
            }
            embeddings.write_embeddings(repo, records)
            path = embeddings.embeddings_path(repo)
            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["evidence_id"], "pmid:1")
            reloaded = embeddings.read_embeddings(repo)
            self.assertEqual(reloaded, records)

    def test_read_embeddings_skips_corrupt_lines(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = embeddings.embeddings_path(repo)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "not json\n"
                '{"schema_version": 1, "evidence_id": "pmid:1", "model": "m", "dim": 1, '
                '"vector": [0.5], "updated_at": "2026-01-01T00:00:00Z"}\n',
                encoding="utf-8")
            reloaded = embeddings.read_embeddings(repo)
            self.assertEqual(list(reloaded.keys()), ["pmid:1"])


class CliPlumbingTest(unittest.TestCase):
    def test_help_does_not_require_sentence_transformers(self):
        for argv in (["--help"], ["index", "--help"], ["similar", "--help"]):
            result = run_py(["scripts/embeddings.py", *argv])
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_similar_errors_cleanly_when_embeddings_file_missing(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            result = run_py(["scripts/embeddings.py", "similar", "--repo", str(repo),
                             "--evidence-id", "pmid:1"])
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertIn("index", payload["error"])

    def test_similar_errors_cleanly_when_target_has_no_embedding(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            embeddings.write_embeddings(repo, {
                "pmid:2": {"schema_version": 1, "evidence_id": "pmid:2", "model": "m",
                          "dim": 1, "vector": [0.5], "updated_at": "2026-01-01T00:00:00Z"},
            })
            result = run_py(["scripts/embeddings.py", "similar", "--repo", str(repo),
                             "--evidence-id", "pmid:1"])
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertIn("pmid:1", payload["error"])

    def test_similar_ranks_by_cosine_similarity_excluding_query(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "Query paper", "journal": "J",
                         "publication_date": "2026"})
            reg.register({"pmid": "2", "title": "Close paper", "journal": "J",
                         "publication_date": "2026"})
            reg.register({"pmid": "3", "title": "Far paper", "journal": "J",
                         "publication_date": "2026"})
            reg.save()
            embeddings.write_embeddings(repo, {
                "pmid:1": {"schema_version": 1, "evidence_id": "pmid:1", "model": "m",
                          "dim": 2, "vector": [1.0, 0.0], "updated_at": "2026-01-01T00:00:00Z"},
                "pmid:2": {"schema_version": 1, "evidence_id": "pmid:2", "model": "m",
                          "dim": 2, "vector": [0.9, 0.1], "updated_at": "2026-01-01T00:00:00Z"},
                "pmid:3": {"schema_version": 1, "evidence_id": "pmid:3", "model": "m",
                          "dim": 2, "vector": [0.0, 1.0], "updated_at": "2026-01-01T00:00:00Z"},
            })
            result = run_py(["scripts/embeddings.py", "similar", "--repo", str(repo),
                             "--evidence-id", "pmid:1", "--k", "2"])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            eids = [r["evidence_id"] for r in payload["results"]]
            self.assertEqual(eids, ["pmid:2", "pmid:3"])
            self.assertNotIn("pmid:1", eids)
            self.assertEqual(payload["results"][0]["title"], "Close paper")

    def test_index_with_no_candidates_is_a_no_op(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            result = run_py(["scripts/embeddings.py", "index", "--repo", str(repo)])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["embedded"], 0)

    def test_index_skips_pending_metadata_records_without_needing_the_model(self):
        # A registry record with metadata_status == "pending" (no title/abstract yet)
        # must never reach the model-loading code path -- exercise this without the
        # optional dependency installed by asserting the run stays a no-op.
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            reg = registry.Registry(repo)
            # register() always requires a title; a "pending" record (no metadata
            # resolved yet) is written directly, as `pool.py seed` does for a stub hit.
            reg.records["pmid:9"] = {
                "schema_version": 1, "evidence_id": "pmid:9", "status": "registered",
                "metadata_status": "pending", "asset_status": "missing",
                "extraction_status": "not_started", "appraisal_status": "not_appraised",
                "sources": [], "created_at": "2026-01-01T00:00:00Z",
            }
            reg.save()
            result = run_py(["scripts/embeddings.py", "index", "--repo", str(repo)])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["embedded"], 0)
            self.assertEqual(payload["total_candidates"], 0)


class CachedModelSelectionTest(unittest.TestCase):
    """`query` must never rank across models — the vector spaces are unrelated."""

    @staticmethod
    def _cache(*models: str) -> dict:
        return {f"pmid:{i}": {"evidence_id": f"pmid:{i}", "model": m, "vector": [1.0]}
                for i, m in enumerate(models, start=1)}

    def test_single_cached_model_is_chosen_implicitly(self):
        model, error = embeddings._cached_model(self._cache("m", "m"), None)
        self.assertEqual((model, error), ("m", None))

    def test_mixed_models_require_an_explicit_choice(self):
        model, error = embeddings._cached_model(self._cache("m1", "m2"), None)
        self.assertIsNone(model)
        self.assertIn("--model", error)

    def test_explicit_model_must_actually_be_cached(self):
        model, error = embeddings._cached_model(self._cache("m1"), "m2")
        self.assertIsNone(model)
        self.assertIn("m2", error)

    def test_empty_cache_points_at_index(self):
        model, error = embeddings._cached_model({}, None)
        self.assertIsNone(model)
        self.assertIn("index", error)


class QueryCommandTest(unittest.TestCase):
    """`query` ranking and error paths, exercised without the optional model."""

    class _StubModel:
        """Stands in for a SentenceTransformer: encodes the one text it is given."""

        def __init__(self, vector):
            self.vector = vector

        def encode(self, texts, show_progress_bar=False):
            return [self.vector for _ in texts]

    def _query(self, repo: Path, *, text: str, k: int = 10, model: str | None = None,
               vector=(1.0, 0.0)) -> dict:
        import contextlib
        import io

        buf = io.StringIO()
        with unittest.mock.patch.object(
                embeddings, "_load_sentence_transformer",
                return_value=self._StubModel(list(vector))):
            with contextlib.redirect_stdout(buf):
                code = embeddings.cmd_query(
                    _ns(repo=str(repo), text=text, k=k, model=model))
        return json.loads(buf.getvalue()), code

    def _seed(self, repo: Path) -> None:
        research.cmd_init(_ns(path=str(repo), from_wiki=None))
        reg = registry.Registry(repo)
        reg.register({"pmid": "1", "title": "Close paper", "journal": "J",
                     "publication_date": "2026"})
        reg.register({"pmid": "2", "title": "Far paper", "journal": "J",
                     "publication_date": "2026"})
        reg.save()
        embeddings.write_embeddings(repo, {
            "pmid:1": {"schema_version": 1, "evidence_id": "pmid:1", "model": "m",
                      "dim": 2, "vector": [0.9, 0.1], "updated_at": "2026-01-01T00:00:00Z"},
            "pmid:2": {"schema_version": 1, "evidence_id": "pmid:2", "model": "m",
                      "dim": 2, "vector": [0.0, 1.0], "updated_at": "2026-01-01T00:00:00Z"},
        })

    def test_ranks_papers_against_free_text(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            self._seed(repo)
            payload, code = self._query(repo, text="does X reduce Y?")
            self.assertEqual(code, 0)
            self.assertEqual([r["evidence_id"] for r in payload["results"]],
                             ["pmid:1", "pmid:2"])
            self.assertEqual(payload["results"][0]["title"], "Close paper")
            self.assertEqual(payload["model"], "m")
            self.assertEqual(payload["compared"], 2)

    def test_k_caps_results(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            self._seed(repo)
            payload, _ = self._query(repo, text="a question", k=1)
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])

    def test_vectors_from_other_models_are_not_compared(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            self._seed(repo)
            cached = embeddings.read_embeddings(repo)
            cached["pmid:2"]["model"] = "other"
            embeddings.write_embeddings(repo, cached)
            payload, code = self._query(repo, text="a question", model="m")
            self.assertEqual(code, 0)
            self.assertEqual(payload["compared"], 1)
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])

    def test_empty_text_is_rejected(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            self._seed(repo)
            payload, code = self._query(repo, text="   ")
            self.assertEqual(code, 2)
            self.assertEqual(payload["status"], "error")

    def test_errors_cleanly_when_embeddings_file_missing(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            result = run_py(["scripts/embeddings.py", "query", "--repo", str(repo),
                             "--text", "a question"])
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "error")
            self.assertIn("index", payload["error"])

    def test_help_does_not_require_sentence_transformers(self):
        result = run_py(["scripts/embeddings.py", "query", "--help"])
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(_HAS_ST, "sentence-transformers not installed")
class RealModelIndexTest(unittest.TestCase):
    def test_index_then_similar_end_to_end(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            reg = registry.Registry(repo)
            reg.register({"pmid": "1", "title": "Exercise therapy for depression",
                         "journal": "J", "publication_date": "2026",
                         "abstract": "A trial of exercise for adolescent depression."})
            reg.register({"pmid": "2", "title": "CBT for anxiety disorders",
                         "journal": "J", "publication_date": "2026",
                         "abstract": "Cognitive behavioral therapy reduces anxiety symptoms."})
            reg.save()

            index_result = run_py(["scripts/embeddings.py", "index", "--repo", str(repo)])
            self.assertEqual(index_result.returncode, 0, index_result.stderr)
            index_payload = json.loads(index_result.stdout)
            self.assertEqual(index_payload["embedded"], 2)

            similar_result = run_py(["scripts/embeddings.py", "similar", "--repo", str(repo),
                                     "--evidence-id", "pmid:1", "--k", "1"])
            self.assertEqual(similar_result.returncode, 0, similar_result.stderr)
            similar_payload = json.loads(similar_result.stdout)
            self.assertEqual(len(similar_payload["results"]), 1)
            self.assertEqual(similar_payload["results"][0]["evidence_id"], "pmid:2")

            # A question, not a paper: `query` must reach the same vectors.
            query_result = run_py(["scripts/embeddings.py", "query", "--repo", str(repo),
                                   "--text", "does exercise help teenage depression?",
                                   "--k", "1"])
            self.assertEqual(query_result.returncode, 0, query_result.stderr)
            query_payload = json.loads(query_result.stdout)
            self.assertEqual(query_payload["results"][0]["evidence_id"], "pmid:1")

            # Re-running index without --force should skip already-current embeddings.
            reindex_result = run_py(["scripts/embeddings.py", "index", "--repo", str(repo)])
            reindex_payload = json.loads(reindex_result.stdout)
            self.assertEqual(reindex_payload["embedded"], 0)
            self.assertEqual(reindex_payload["skipped_current"], 2)


if __name__ == "__main__":
    unittest.main()
