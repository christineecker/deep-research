"""Saved PubMed searches and the "what's new" diff (`alerts.py`).

No test here touches the network: `eutils.request` is patched with a fake that returns
recorded-shaped payloads, which also lets each test assert on the *term* that would have
been sent — the entry-date clause is the whole point of a rerun and is otherwise
invisible.
"""

from __future__ import annotations

import json
import sys
import unittest
import unittest.mock
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, run_py

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import eutils  # noqa: E402  (patched in-process; alerts.py imports the same module object)

alerts = load_script("alerts.py")
registry = load_script("registry.py")
research = load_script("research.py")


def _ns(**kwargs):
    class NS:
        pass
    ns = NS()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _esearch_body(pmids: list[str], count: int | None = None) -> str:
    return json.dumps({
        "header": {"type": "esearch", "version": "0.3"},
        "esearchresult": {
            "count": str(count if count is not None else len(pmids)),
            "retmax": str(len(pmids)), "retstart": "0", "idlist": pmids,
            "translationset": [], "querytranslation": "translated form",
            "webenv": "MCID_TEST", "querykey": "1",
        },
    })


def _efetch_body(pmids: list[str]) -> str:
    articles = "".join(
        f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>"
        f"<ArticleTitle>Paper {pmid}</ArticleTitle>"
        f"<Journal><Title>Journal of Validation</Title>"
        f"<JournalIssue><PubDate><Year>2026</Year></PubDate></JournalIssue></Journal>"
        f"</Article></MedlineCitation></PubmedArticle>"
        for pmid in pmids
    )
    return f"<PubmedArticleSet>{articles}</PubmedArticleSet>"


class _FakeEutils:
    """Stands in for `eutils.request`, recording the terms it was asked for."""

    def __init__(self, pmids: list[str]):
        self.pmids = pmids
        self.terms: list[str] = []

    def __call__(self, endpoint, params, **kwargs):
        if endpoint == "esearch":
            self.terms.append(params["term"])
            return _esearch_body(self.pmids)
        if endpoint == "efetch":
            return _efetch_body(params["id"].split(","))
        raise AssertionError(f"unexpected endpoint {endpoint!r}")


def _init_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    research.cmd_init(_ns(path=str(repo), from_wiki=None))
    return repo


def _save(repo: Path, name: str = "cbt", query: str = "CBT AND adolescents",
          filters_json: str | None = None, force: bool = False) -> int:
    return alerts.cmd_save(_ns(repo=str(repo), name=name, query=query,
                               filters_json=filters_json, force=force))


def _run(repo: Path, fake: _FakeEutils, **kwargs) -> dict:
    import contextlib
    import io

    args = _ns(repo=str(repo), name=kwargs.get("name"), since=kwargs.get("since"),
               retmax=kwargs.get("retmax", 200), register=kwargs.get("register", False),
               dry_run=kwargs.get("dry_run", False), email=None)
    buf = io.StringIO()
    with unittest.mock.patch.object(eutils, "request", fake):
        with contextlib.redirect_stdout(buf):
            alerts.cmd_run(args)
    return json.loads(buf.getvalue())


class EntryDateClauseTest(unittest.TestCase):
    def test_clause_uses_entry_date_not_publication_date(self):
        term = alerts.with_entry_date("CBT AND adolescents", "2026/01/31")
        self.assertEqual(term,
                         '(CBT AND adolescents) AND ("2026/01/31"[edat] : "3000"[edat])')

    def test_iso_dates_are_normalized(self):
        self.assertIn('"2026/01/31"[edat]',
                      alerts.with_entry_date("q", "2026-01-31"))
        self.assertIn('"2026/01/31"[edat]',
                      alerts.with_entry_date("q", "2026-01-31T04:05:06Z"))

    def test_a_bare_year_is_a_valid_bound(self):
        self.assertIn('"2026"[edat]', alerts.with_entry_date("q", "2026"))


class RegistryBaselineTest(unittest.TestCase):
    def test_known_pmids_come_from_both_the_field_and_the_evidence_id(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "111", "title": "One"})
                reg.commit()
            reg.records["pmid:222"] = {"evidence_id": "pmid:222", "title": "Two"}
            self.assertEqual(alerts.registry_pmids(reg), {"111", "222"})


class SaveListDeleteTest(unittest.TestCase):
    def test_save_then_list_then_delete(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            self.assertEqual(_save(repo), 0)

            result = run_py(["scripts/alerts.py", "list", "--repo", str(repo)])
            payload = json.loads(result.stdout)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["saved_searches"][0]["name"], "cbt")
            self.assertIsNone(payload["saved_searches"][0]["last_run"])

            result = run_py(["scripts/alerts.py", "delete", "--repo", str(repo),
                             "--name", "cbt"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(run_py(["scripts/alerts.py", "list", "--repo",
                                                str(repo)]).stdout)["count"], 0)

    def test_saving_a_duplicate_name_needs_force(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo)
            self.assertEqual(_save(repo), 1)
            self.assertEqual(_save(repo, query="CBT AND teenagers", force=True), 0)

    def test_a_bad_filter_fails_at_save_time_not_on_every_rerun(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            self.assertEqual(_save(repo, filters_json='{"nonsense": 1}'), 2)
            self.assertEqual(_save(repo, filters_json="not json"), 2)

    def test_deleting_an_unknown_name_is_an_error(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            result = run_py(["scripts/alerts.py", "delete", "--repo", str(repo),
                             "--name", "absent"])
            self.assertEqual(result.returncode, 1)


class RunTest(unittest.TestCase):
    def test_reports_only_pmids_the_registry_has_never_seen(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            reg = registry.Registry(repo)
            with reg.locked():
                reg.register({"pmid": "111", "title": "Already known"})
                reg.commit()
            _save(repo)

            payload = _run(repo, _FakeEutils(["111", "222", "333"]))
            result = payload["results"][0]
            self.assertEqual(result["new_pmids"], ["222", "333"])
            self.assertEqual(payload["new_total"], 2)
            self.assertEqual([r["pmid"] for r in result["new_records"]], ["222", "333"])

    def test_the_rerun_is_bounded_by_the_saved_searchs_own_last_run(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo)

            first = _FakeEutils(["222"])
            _run(repo, first)
            self.assertIn("[edat]", first.terms[0])

            second = _FakeEutils(["222"])
            _run(repo, second)
            # The second run's floor is the day the first run happened, not the
            # saved search's creation date.
            self.assertIn(alerts._today(), second.terms[0])

    def test_dry_run_leaves_the_baseline_where_it_was(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo)
            _run(repo, _FakeEutils(["222"]), dry_run=True)
            result = run_py(["scripts/alerts.py", "list", "--repo", str(repo)])
            self.assertIsNone(json.loads(result.stdout)["saved_searches"][0]["last_run"])

    def test_since_overrides_the_stored_baseline(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo)
            fake = _FakeEutils(["222"])
            _run(repo, fake, since="2020/01/01")
            self.assertIn('"2020/01/01"[edat]', fake.terms[0])

    def test_register_adds_the_new_hits_and_moves_the_baseline(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo)
            payload = _run(repo, _FakeEutils(["222", "333"]), register=True)
            self.assertEqual(payload["registered_total"], 2)

            records = registry.Registry(repo).records
            self.assertIn("pmid:222", records)
            self.assertEqual(records["pmid:222"]["title"], "Paper 222")
            # Mirrored too: registering through `alerts` is not a back door around the
            # index.
            self.assertTrue(records["pmid:222"].get("refmgr_paper_id"))

            # Second run over the same hits now reports nothing new.
            again = _run(repo, _FakeEutils(["222", "333"]), since="2020/01/01")
            self.assertEqual(again["new_total"], 0)

    def test_without_register_the_run_only_reports(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo)
            payload = _run(repo, _FakeEutils(["222"]))
            self.assertEqual(payload["registered_total"], 0)
            self.assertNotIn("pmid:222", registry.Registry(repo).records)
            self.assertTrue(any("--register" in note for note in payload["notes"]))

    def test_one_search_does_not_re_report_what_another_just_registered(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo, name="first")
            _save(repo, name="second", query="different query")
            payload = _run(repo, _FakeEutils(["222"]), register=True)
            counts = [r["new_count"] for r in payload["results"]]
            self.assertEqual(sorted(counts), [0, 1])
            self.assertEqual(payload["registered_total"], 1)

    def test_a_truncated_result_set_says_so(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo)

            class _Truncated(_FakeEutils):
                def __call__(self, endpoint, params, **kwargs):
                    if endpoint == "esearch":
                        return _esearch_body(self.pmids, count=500)
                    return super().__call__(endpoint, params, **kwargs)

            payload = _run(repo, _Truncated(["222"]), retmax=1)
            self.assertIn("retmax", payload["results"][0]["truncated"])

    def test_no_saved_searches_is_not_an_error(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            payload = _run(repo, _FakeEutils([]))
            self.assertEqual(payload["results"], [])
            self.assertTrue(any("save" in note for note in payload["notes"]))

    def test_an_unknown_name_is_an_error(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo)
            result = run_py(["scripts/alerts.py", "run", "--repo", str(repo),
                             "--name", "absent"])
            self.assertEqual(result.returncode, 1)

    def test_a_failing_search_is_reported_not_raised(self):
        with TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            _save(repo)

            def _boom(endpoint, params, **kwargs):
                raise eutils.EutilsError("http_error", "503 from NCBI")

            payload = _run(repo, _boom)
            self.assertEqual(payload["results"][0]["status"], "error")
            self.assertIn("503", payload["results"][0]["error"])


if __name__ == "__main__":
    unittest.main()
