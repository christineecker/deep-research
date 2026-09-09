from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from helpers import load_script, make_run, minimal_corpus_record, write_digest_receipt, \
    write_jsonl

okf = load_script("okf.py")


# --------------------------------------------------------------------- fixtures ---


def init_bundle(wiki: Path) -> Path:
    wiki.mkdir(parents=True, exist_ok=True)
    okf.cmd_init(argparse.Namespace(wiki=str(wiki)))
    return wiki / "research"


NOW = "2026-01-01T00:00:00Z"


def make_front(ctype: str = "Evidence Claim", *, sources=None, **overrides) -> dict:
    sources = sources if sources is not None else [
        {"id": "src-1", "resource": "https://example.org/a", "title": "Example source"},
    ]
    front = okf.base_front(
        ctype, "A fixture claim", "A fixture claim used for validator tests.",
        "https://example.org/a", ["fixture"], NOW, NOW, "stable", sources)
    front.update(overrides)
    return front


def make_body(source_ids=("src-1",), *, with_footnote_ref=True) -> str:
    refs = " ".join(f"[^{sid}]" for sid in source_ids) if with_footnote_ref else ""
    footnotes = "\n".join(f"[^{sid}]: Example source. https://example.org/a"
                          for sid in source_ids)
    return f"# A fixture claim\n\nSome claim text {refs}\n\n{footnotes}\n"


def write_at(path: Path, front: dict, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(okf.render_document(front, body), encoding="utf-8")
    return path


def write_claim(wiki: Path, front: dict = None, body: str = None, slug="fixture-claim") -> Path:
    front = front if front is not None else make_front()
    body = body if body is not None else make_body()
    path = wiki / "research" / "claims" / f"{slug}.md"
    return write_at(path, front, body)


def run_validator(wiki: Path, *, corpus=None, only: Path = None) -> "okf.Validator":
    v = okf.Validator(wiki, corpus=corpus)
    v.run(only=only)
    return v


def violation_rules(validator) -> set:
    return {v.rule for v in validator.violations}


# ------------------------------------------------------------------------ V-rules ---


class ValidatorRuleTest(unittest.TestCase):
    def setUp(self):
        self._td = TemporaryDirectory()
        self.wiki = Path(self._td.name) / "wiki"
        init_bundle(self.wiki)

    def tearDown(self):
        self._td.cleanup()

    def test_valid_claim_produces_no_violations(self):
        path = write_claim(self.wiki)
        v = run_validator(self.wiki, only=path)
        self.assertEqual(v.violations, [], [x.as_dict() for x in v.violations])

    def test_v1_missing_frontmatter(self):
        path = self.wiki / "research" / "claims" / "no-frontmatter.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Just a heading\n\nNo frontmatter here.\n", encoding="utf-8")
        v = run_validator(self.wiki, only=path)
        self.assertIn("V1", violation_rules(v))

    def test_v2_source_id_fails_pattern(self):
        front = make_front(sources=[
            {"id": "Bad ID!", "resource": "https://example.org/a", "title": "Example"},
        ])
        path = write_claim(self.wiki, front=front, body=make_body(source_ids=("Bad ID!",)))
        v = run_validator(self.wiki, only=path)
        self.assertIn("V2", violation_rules(v))

    def test_v3_footnote_key_does_not_resolve(self):
        body = "# A fixture claim\n\nSome claim text [^ghost]\n\n[^src-1]: Example. https://x\n"
        path = write_claim(self.wiki, body=body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V3", violation_rules(v))

    def test_v4_concept_file_outside_research(self):
        front, body = make_front(), make_body()
        path = self.wiki / "elsewhere" / "fixture-claim.md"
        write_at(path, front, body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V4", violation_rules(v))

    def test_v4_concept_file_inside_forbidden_wiki_manager_tree(self):
        front, body = make_front(), make_body()
        path = self.wiki / "wiki" / "fixture-claim.md"
        write_at(path, front, body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V4", violation_rules(v))

    def test_v5_bundle_root_wrong_okf_version(self):
        root = self.wiki / "research" / "index.md"
        front, body = okf.load_document(root)[0], okf.load_document(root)[1]
        front["okf_version"] = "9.9"
        write_at(root, front, body)
        path = write_claim(self.wiki)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V5", violation_rules(v))

    def test_v6_required_field_missing(self):
        front = make_front()
        del front["description"]
        path = write_claim(self.wiki, front=front)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V6", violation_rules(v))

    def test_v7_timestamp_not_iso8601(self):
        front = make_front()
        front["generated"] = {"by": okf.GENERATED_BY, "at": "not-a-date"}
        path = write_claim(self.wiki, front=front)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V7", violation_rules(v))

    def test_v8_status_not_in_enum(self):
        front = make_front()
        front["status"] = "bogus-status"
        path = write_claim(self.wiki, front=front)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V8", violation_rules(v))

    def test_v9_stale_after_required_and_malformed(self):
        front = make_front()  # Evidence Claim is in STALE_REQUIRED_TYPES
        front["stale_after"] = "not-a-date"
        path = write_claim(self.wiki, front=front)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V9", violation_rules(v))

    def test_v10_unknown_concept_type(self):
        front = make_front(ctype="Not A Real Type")
        path = write_claim(self.wiki, front=front)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V10", violation_rules(v))

    def test_v11_study_filename_must_match_pmid_or_doi(self):
        record = {"pmid": "12345678", "title": "A study", "journal": "J",
                  "publication_date": "2025-01-01"}
        concept = okf.study_concept(record, self.wiki, NOW, NOW, "stable")
        path = self.wiki / "research" / "studies" / "wrong-slug-name.md"
        write_at(path, concept.front, concept.body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V11", violation_rules(v))

    def test_v12_corpus_supplies_field_concept_omits(self):
        record = {"pmid": "12345678", "title": "A study", "journal": "J",
                  "publication_date": "2025-01-01"}
        concept = okf.study_concept(record, self.wiki, NOW, NOW, "stable")
        path = concept.path(self.wiki / "research")
        write_at(path, concept.front, concept.body)
        corpus_rec = minimal_corpus_record("pmid:12345678")
        corpus_rec["mesh_terms"] = ["Humans"]
        v = run_validator(self.wiki, corpus=[corpus_rec], only=path)
        self.assertIn("V12", violation_rules(v))

    def test_v13_pubmed_field_not_a_string(self):
        record = {"pmid": "12345678", "title": "A study", "journal": "J",
                  "publication_date": "2025-01-01"}
        concept = okf.study_concept(record, self.wiki, NOW, NOW, "stable")
        concept.front["pmid"] = 12345678  # int, not string
        path = self.wiki / "research" / "studies" / "pmid-12345678.md"
        write_at(path, concept.front, concept.body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V13", violation_rules(v))

    def test_v14_pmid_fails_regex(self):
        record = {"pmid": "12345678", "title": "A study", "journal": "J",
                  "publication_date": "2025-01-01"}
        concept = okf.study_concept(record, self.wiki, NOW, NOW, "stable")
        concept.front["pmid"] = "not-numeric"
        path = self.wiki / "research" / "studies" / "pmid-12345678.md"
        write_at(path, concept.front, concept.body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V14", violation_rules(v))

    def test_v15_retraction_status_missing_for_retraction_type(self):
        record = {"pmid": "12345678", "title": "A study", "journal": "J",
                  "publication_date": "2025-01-01"}
        concept = okf.study_concept(record, self.wiki, NOW, NOW, "stable")
        concept.front["retraction_status"] = "invalid-value"
        path = self.wiki / "research" / "studies" / "pmid-12345678.md"
        write_at(path, concept.front, concept.body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V15", violation_rules(v))

    def test_v16_tags_missing_deep_research_tag(self):
        front = make_front()
        front["tags"] = ["only-this-tag"]
        path = write_claim(self.wiki, front=front)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V16", violation_rules(v))

    def test_v17_dangling_markdown_link(self):
        body = "# A fixture claim\n\nSee [nowhere](./does-not-exist.md) [^src-1]\n\n" \
               "[^src-1]: Example. https://x\n"
        path = write_claim(self.wiki, body=body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V17", violation_rules(v))

    def test_v18_wikilink_without_markdown_link(self):
        body = make_body() + "\n\nSee also [[some-other-concept]].\n"
        path = write_claim(self.wiki, body=body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V18", violation_rules(v))

    def test_v19_sources_present_but_no_footnote_reference(self):
        body = "# A fixture claim\n\nSome claim text with no footnote markers at all.\n"
        path = write_claim(self.wiki, body=body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V19", violation_rules(v))

    def test_v20_log_heading_malformed(self):
        log_path = self.wiki / "research" / "log.md"
        text = log_path.read_text(encoding="utf-8")
        log_path.write_text(text + "\n## not-a-date\n\n- something happened\n",
                            encoding="utf-8")
        v = run_validator(self.wiki)  # V20/V21 are whole-bundle checks
        self.assertIn("V20", violation_rules(v))

    def test_v21_directory_with_concepts_but_no_index(self):
        write_claim(self.wiki)
        (self.wiki / "research" / "claims" / "index.md").unlink()
        v = run_validator(self.wiki)
        self.assertIn("V21", violation_rules(v))

    def test_v22_slug_fails_pattern(self):
        front, body = make_front(), make_body()
        path = self.wiki / "research" / "claims" / "UPPER_Case.md"
        write_at(path, front, body)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V22", violation_rules(v))

    def test_v23_okf01_legacy_timestamp_field(self):
        front = make_front()
        front["timestamp"] = "2020-01-01"
        path = write_claim(self.wiki, front=front)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V23", violation_rules(v))

    def test_v23_okf01_sources_as_string(self):
        front = make_front()
        front["sources"] = "not-a-list"
        path = write_claim(self.wiki, front=front)
        v = run_validator(self.wiki, only=path)
        self.assertIn("V23", violation_rules(v))

    def test_v24_evidence_id_not_in_corpus(self):
        front = make_front(evidence_id="pmid:99999999")
        path = write_claim(self.wiki, front=front)
        corpus_rec = minimal_corpus_record("pmid:11111111")
        v = run_validator(self.wiki, corpus=[corpus_rec], only=path)
        self.assertIn("V24", violation_rules(v))

    def test_v25_abstract_only_basis_undeclared(self):
        record = {"pmid": "12345678", "title": "A study", "journal": "J",
                  "publication_date": "2025-01-01", "evidence_id": "pmid:12345678"}
        concept = okf.study_concept(record, self.wiki, NOW, NOW, "stable")
        path = concept.path(self.wiki / "research")
        write_at(path, concept.front, concept.body)
        corpus_rec = minimal_corpus_record("pmid:12345678")
        corpus_rec["fulltext"] = {"status": "abstract_only", "source_tier": None,
                                  "access_route": None, "local_path": None,
                                  "sha256": None, "truncation_detected": False}
        v = run_validator(self.wiki, corpus=[corpus_rec], only=path)
        self.assertIn("V25", violation_rules(v))

    def test_v25_abstract_only_basis_declared_passes(self):
        record = {"pmid": "12345678", "title": "A study", "journal": "J",
                  "publication_date": "2025-01-01", "evidence_id": "pmid:12345678"}
        concept = okf.study_concept(record, self.wiki, NOW, NOW, "stable")
        concept.front["evidence_basis"] = "abstract_only"
        path = concept.path(self.wiki / "research")
        write_at(path, concept.front, concept.body)
        corpus_rec = minimal_corpus_record("pmid:12345678")
        corpus_rec["fulltext"] = {"status": "abstract_only", "source_tier": None,
                                  "access_route": None, "local_path": None,
                                  "sha256": None, "truncation_detected": False}
        v = run_validator(self.wiki, corpus=[corpus_rec], only=path)
        self.assertNotIn("V25", violation_rules(v))


# -------------------------------------------------------------- frontmatter parser --


class FrontmatterRoundTripTest(unittest.TestCase):
    def test_split_and_render_round_trip_simple_mapping(self):
        front = {"type": "Evidence Claim", "title": "T", "tags": ["a", "b"],
                 "generated": {"by": "x", "at": NOW}, "sources": [
                     {"id": "s1", "resource": "https://x", "title": "X"}]}
        text = okf.render_document(front, "# Body\n\nHello.\n")
        fm_text, body = okf.split_frontmatter(text)
        self.assertIsNotNone(fm_text)
        parsed = okf.yaml_loads(fm_text)
        self.assertEqual(parsed, front)
        self.assertIn("Hello.", body)

    def test_round_trip_preserves_nested_lists_of_mappings(self):
        front = {
            "type": "Study",
            "authors": [{"family": "Smith", "given": "Jane", "initials": "JA",
                        "affiliation": None, "collective": None},
                       {"family": "Doe", "given": None, "initials": "B",
                        "affiliation": None, "collective": None}],
            "sources": [{"id": "s1", "resource": "https://x", "title": "X"}],
        }
        dumped = okf.yaml_dump(front)
        parsed = okf.yaml_loads(dumped)
        self.assertEqual(parsed, front)

    def test_round_trip_preserves_scalars_with_special_characters(self):
        front = {
            "title": "A study: colons, \"quotes\", and a trailing #hash",
            "description": "multi\nline is not expected but a colon: still is",
            "empty_list": [],
            "empty_map": {},
            "flag": True,
            "count": 3,
        }
        dumped = okf.yaml_dump(front)
        parsed = okf.yaml_loads(dumped)
        self.assertEqual(parsed, front)

    def test_missing_fences_returns_none_frontmatter(self):
        fm_text, body = okf.split_frontmatter("# No frontmatter\n\nJust body text.\n")
        self.assertIsNone(fm_text)
        self.assertIn("Just body text.", body)

    def test_unterminated_fence_returns_none_frontmatter(self):
        text = "---\ntype: Study\ntitle: X\n\n# Body without closing fence\n"
        fm_text, body = okf.split_frontmatter(text)
        self.assertIsNone(fm_text)

    def test_malformed_yaml_raises_okf_error(self):
        with self.assertRaises(okf.OkfError):
            okf.yaml_loads("type: Study\ntags: [unclosed\n")

    def test_yaml_loads_non_mapping_top_level_raises(self):
        with self.assertRaises(okf.OkfError):
            okf.yaml_loads("- just\n- a\n- list\n")

    def test_load_document_reports_parse_error_without_raising(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "broken.md"
            path.write_text("---\ntype: Study\ntags: [unclosed\n---\n\nBody\n",
                            encoding="utf-8")
            front, body, err = okf.load_document(path)
            self.assertIsNone(front)
            self.assertIsNotNone(err)

    def test_load_document_handles_missing_frontmatter(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "plain.md"
            path.write_text("# Just a heading\n", encoding="utf-8")
            front, body, err = okf.load_document(path)
            self.assertIsNone(front)
            self.assertEqual(err, "no YAML frontmatter block")


# ---------------------------------------------------------- preflight / transaction -


def _make_promotable_run(td: Path):
    store = load_script("store.py")
    wiki, run = make_run(td, slug="okf-promote-run")
    okf.cmd_init(argparse.Namespace(wiki=str(wiki)))
    text = "Methods. The trial enrolled 42 adults. Results were reported."
    snap = store.write_snapshot(
        run, url="https://example.org/fulltext", text=text, title="Full text",
        access="full_text", origin="pubmed",
        paper={"pmid": "12345678", "doi": "10.1000/promotetest", "pmcid": "PMC1234567"},
        event_type="fetch", fresh=True, actor="test")
    rec = minimal_corpus_record("pmid:12345678")
    rec["source_ids"] = [snap["source_id"]]
    write_jsonl(run / "corpus.jsonl", [rec])
    (run / "outputs" / "report.md").write_text(
        "# Report\n\nThe trial enrolled 42 adults.[^pubmed-12345678]\n\n"
        "## References\n\n[^pubmed-12345678]: Smith JA. Journal of Validation. "
        "PMID 12345678.\n", encoding="utf-8")
    write_digest_receipt(run)
    return wiki, run


def _promote_args(run_dir: Path, wiki: Path, **overrides) -> argparse.Namespace:
    defaults = dict(run_dir=str(run_dir), wiki=str(wiki), status=None,
                    allow_unverified=True, force=False, check=False, gate=None,
                    no_log=False, dry_run=False)
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class PromoteTransactionTest(unittest.TestCase):
    def test_successful_promote_writes_expected_file_set(self):
        with TemporaryDirectory() as td:
            wiki, run = _make_promotable_run(Path(td))
            rc = okf.cmd_promote(_promote_args(run, wiki))
            self.assertEqual(rc, 0)
            study_path = wiki / "research" / "studies" / "pmid-12345678.md"
            self.assertTrue(study_path.exists())
            self.assertTrue((wiki / "research" / "studies" / "index.md").exists())
            self.assertTrue((wiki / "research" / "index.md").exists())
            self.assertTrue((run / "outputs" / "okf-validation.md").exists())
            front, _body, err = okf.load_document(study_path)
            self.assertIsNone(err)
            self.assertEqual(front["pmid"], "12345678")

    def test_rollback_on_post_write_validation_failure_leaves_bundle_unchanged(self):
        with TemporaryDirectory() as td:
            wiki, run = _make_promotable_run(Path(td))
            research = wiki / "research"
            before = sorted(str(p.relative_to(wiki)) for p in research.rglob("*"))
            before_bytes = {
                str(p.relative_to(wiki)): p.read_bytes()
                for p in research.rglob("*") if p.is_file()
            }

            call_count = {"n": 0}
            real_run = okf.Validator.run

            def flaky_run(self, only=None):
                real_run(self, only=only)
                call_count["n"] += 1
                if call_count["n"] == 2:  # second Validator() is the post-write check
                    self.violations.append(
                        okf.Violation("V-INJECTED", "injected", "forced failure for "
                                     "rollback characterization test"))

            original = okf.Validator.run
            okf.Validator.run = flaky_run
            try:
                rc = okf.cmd_promote(_promote_args(run, wiki))
            finally:
                okf.Validator.run = original

            self.assertEqual(rc, 1)
            after = sorted(str(p.relative_to(wiki)) for p in research.rglob("*"))
            self.assertEqual(after, before)
            after_bytes = {
                str(p.relative_to(wiki)): p.read_bytes()
                for p in research.rglob("*") if p.is_file()
            }
            self.assertEqual(after_bytes, before_bytes)
            study_path = wiki / "research" / "studies" / "pmid-12345678.md"
            self.assertFalse(study_path.exists())
            report = (run / "outputs" / "okf-validation.md").read_text(encoding="utf-8")
            self.assertIn("rolled back", report.lower())

    def test_check_mode_writes_nothing_on_success(self):
        with TemporaryDirectory() as td:
            wiki, run = _make_promotable_run(Path(td))
            research = wiki / "research"
            before = sorted(str(p.relative_to(wiki)) for p in research.rglob("*"))
            rc = okf.cmd_promote(_promote_args(run, wiki, check=True))
            self.assertEqual(rc, 0)
            after = sorted(str(p.relative_to(wiki)) for p in research.rglob("*"))
            self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
