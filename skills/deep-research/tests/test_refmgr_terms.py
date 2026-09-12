"""Controlled-vocabulary facets (`refmgr/repositories/terms.py`, `registry.py facets`).

MeSH headings, keywords, article types and authors already travelled with every record
but lived only inside a JSON blob. These tests pin the properties that make the term
index worth having: case-insensitive matching, substring lookup (headings are long and
people type fragments), and identical results whether the index is present or not.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, run_py

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from refmgr.repositories import terms  # noqa: E402
from refmgr.service import ReferenceManagerService  # noqa: E402

registry = load_script("registry.py")
research = load_script("research.py")


def _ns(**kwargs):
    class NS:
        pass
    ns = NS()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


PAPERS = [
    {"pmid": "1", "title": "CBT for adolescent depression", "journal": "J A",
     "publication_date": "2024", "mesh_terms": ["Adolescent", "Depressive Disorder, Major"],
     "article_types": ["Randomized Controlled Trial"], "authors": ["Smith JA", "Doe R"],
     "keywords": ["psychotherapy"]},
    {"pmid": "2", "title": "Exercise in older adults", "journal": "J B",
     "publication_date": "2025", "mesh_terms": ["Aged", "Exercise"],
     "article_types": ["Observational Study"], "authors": ["Smith JA"]},
]


def _repo_with_terms(tmp: Path) -> Path:
    repo = tmp / "repo"
    research.cmd_init(_ns(path=str(repo), from_wiki=None))
    reg = registry.Registry(repo)
    with reg.locked():
        for paper in PAPERS:
            reg.register(paper)
        reg.commit()
    return repo


def _search(repo: Path, **kwargs) -> dict:
    argv = ["scripts/registry.py", "search", "--repo", str(repo)]
    for key, value in kwargs.items():
        argv.extend(["--" + key.replace("_", "-"), str(value)])
    result = run_py(argv)
    assert result.returncode in (0, 1), result.stderr
    return json.loads(result.stdout)


class NormalizationTest(unittest.TestCase):
    def test_case_and_whitespace_collapse(self):
        self.assertEqual(terms.normalize("  Depressive   Disorder,  Major "),
                         "depressive disorder, major")

    def test_terms_from_metadata_covers_every_scheme(self):
        pairs = terms.terms_from_metadata(PAPERS[0])
        self.assertIn(("mesh", "Adolescent"), pairs)
        self.assertIn(("article_type", "Randomized Controlled Trial"), pairs)
        self.assertIn(("author", "Smith JA"), pairs)
        self.assertIn(("keyword", "psychotherapy"), pairs)

    def test_malformed_fields_are_skipped_not_coerced(self):
        pairs = terms.terms_from_metadata(
            {"mesh_terms": "Adolescent", "authors": [None, 42, "Real Name"]})
        self.assertEqual(pairs, [("author", "Real Name")])


class TermRepositoryTest(unittest.TestCase):
    def _service(self, tmp: Path) -> ReferenceManagerService:
        service = ReferenceManagerService(tmp / "refmgr")
        self.addCleanup(service.close)
        return service

    def test_set_terms_replaces_rather_than_accumulates(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            service.terms.set_terms(paper_id, [("mesh", "Adolescent"), ("mesh", "Aged")])
            service.terms.set_terms(paper_id, [("mesh", "Aged")])
            self.assertEqual(service.terms.for_paper(paper_id), {"mesh": ["Aged"]})

    def test_lookup_is_case_insensitive_and_substring(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            service.terms.set_terms(paper_id, [("mesh", "Depressive Disorder, Major")])
            for needle in ("depressive disorder, major", "DEPRESSIVE", "disorder"):
                self.assertEqual(service.terms.papers_with_term("mesh", needle), {paper_id})
            self.assertEqual(service.terms.papers_with_term("mesh", "exercise"), set())

    def test_duplicate_spellings_collapse_to_one_row(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            written = service.terms.set_terms(
                paper_id, [("mesh", "Adolescent"), ("mesh", "adolescent")])
            self.assertEqual(written, 1)

    def test_facets_count_papers_not_rows(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            for pmid in ("1", "2"):
                paper_id = service.add_paper(title=f"T{pmid}", paper_type="article",
                                             identifiers=[("pmid", pmid)])
                service.terms.set_terms(paper_id, [("mesh", "Adolescent")])
            facets = service.terms.facets("mesh")
            self.assertEqual(facets, [{"value": "Adolescent", "papers": 2}])

    def test_wildcards_in_a_query_are_literal(self):
        with TemporaryDirectory() as tmp:
            service = self._service(Path(tmp))
            paper_id = service.add_paper(title="T", paper_type="article",
                                         identifiers=[("pmid", "1")])
            service.terms.set_terms(paper_id, [("mesh", "Adolescent")])
            self.assertEqual(service.terms.papers_with_term("mesh", "%"), set())
            self.assertEqual(service.terms.papers_with_term("mesh", "_dolescent"), set())


class SearchFacetTest(unittest.TestCase):
    def test_mesh_filter_matches_a_heading_fragment(self):
        with TemporaryDirectory() as tmp:
            repo = _repo_with_terms(Path(tmp))
            payload = _search(repo, mesh="depress")
            self.assertEqual([r["evidence_id"] for r in payload["results"]], ["pmid:1"])

    def test_author_and_article_type_filters(self):
        with TemporaryDirectory() as tmp:
            repo = _repo_with_terms(Path(tmp))
            self.assertEqual(_search(repo, author="smith ja")["count"], 2)
            self.assertEqual(
                [r["evidence_id"] for r in _search(repo, article_type="observational")["results"]],
                ["pmid:2"])

    def test_facets_combine_as_and(self):
        with TemporaryDirectory() as tmp:
            repo = _repo_with_terms(Path(tmp))
            self.assertEqual(
                [r["evidence_id"] for r in
                 _search(repo, mesh="aged", article_type="observational")["results"]],
                ["pmid:2"])
            self.assertEqual(_search(repo, mesh="aged", article_type="randomized")["count"], 0)

    def test_results_are_identical_without_the_index(self):
        with TemporaryDirectory() as tmp:
            repo = _repo_with_terms(Path(tmp))
            indexed = _search(repo, mesh="depress")
            (repo / "data" / "refmgr" / "library.sqlite3").unlink()
            fallback = _search(repo, mesh="depress")
            self.assertEqual([r["evidence_id"] for r in fallback["results"]],
                             [r["evidence_id"] for r in indexed["results"]])

    def test_facets_command_reports_values_with_counts(self):
        with TemporaryDirectory() as tmp:
            repo = _repo_with_terms(Path(tmp))
            result = run_py(["scripts/registry.py", "facets", "--repo", str(repo),
                             "--scheme", "author"])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["values"][0], {"value": "Smith JA", "papers": 2})

    def test_facets_command_without_a_library_says_what_to_run(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            research.cmd_init(_ns(path=str(repo), from_wiki=None))
            result = run_py(["scripts/registry.py", "facets", "--repo", str(repo)])
            self.assertEqual(result.returncode, 1)
            self.assertIn("reindex", json.loads(result.stdout)["error"])

    def test_reindex_rebuilds_terms(self):
        with TemporaryDirectory() as tmp:
            repo = _repo_with_terms(Path(tmp))
            (repo / "data" / "refmgr" / "library.sqlite3").unlink()
            result = run_py(["scripts/registry.py", "reindex", "--repo", str(repo)])
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["coverage"]["terms"]["papers_with_terms"], 2)
            self.assertEqual([r["evidence_id"] for r in _search(repo, mesh="depress")["results"]],
                             ["pmid:1"])


if __name__ == "__main__":
    unittest.main()
