from __future__ import annotations

import unittest

from helpers import load_script

identity = load_script("refmgr/identity.py")


class NewIdTest(unittest.TestCase):
    def test_returns_hex_uuid4_format(self):
        value = identity.new_id()
        self.assertEqual(len(value), 32)
        self.assertRegex(value, r"^[0-9a-f]{32}$")

    def test_two_calls_are_unique(self):
        self.assertNotEqual(identity.new_id(), identity.new_id())


class NormalizeDoiTest(unittest.TestCase):
    VALID = [
        ("10.1000/xyz123", "10.1000/xyz123"),
        ("https://doi.org/10.1000/xyz123", "10.1000/xyz123"),
        ("http://doi.org/10.1000/xyz123", "10.1000/xyz123"),
        ("https://dx.doi.org/10.1000/xyz123", "10.1000/xyz123"),
        ("doi:10.1000/xyz123", "10.1000/xyz123"),
        ("DOI: 10.1000/xyz123", "10.1000/xyz123"),
        ("  10.1000/XYZ123  ", "10.1000/xyz123"),
        ("HTTPS://DOI.ORG/10.1000/XYZ123", "10.1000/xyz123"),
        ("10.12345/Some.Complex-ID_1", "10.12345/some.complex-id_1"),
    ]

    def test_valid_forms_normalize_identically(self):
        for raw, expected in self.VALID:
            with self.subTest(raw=raw):
                self.assertEqual(identity.normalize_doi(raw), expected)

    def test_malformed_raises(self):
        for bad in ["not-a-doi", "10.abc/xyz", "10.1000/", "", "doi:"]:
            with self.subTest(bad=bad):
                with self.assertRaises(identity.IdentifierError):
                    identity.normalize_doi(bad)


class NormalizePmidTest(unittest.TestCase):
    VALID = [
        ("12345678", "12345678"),
        ("PMID: 12345678", "12345678"),
        ("pmid:12345678", "12345678"),
        ("  pmid: 12345678  ", "12345678"),
        ("PMID:12345678", "12345678"),
    ]

    def test_valid_forms_normalize_identically(self):
        for raw, expected in self.VALID:
            with self.subTest(raw=raw):
                self.assertEqual(identity.normalize_pmid(raw), expected)

    def test_malformed_raises(self):
        for bad in ["", "pmid:", "12345abc", "pmid: abc", "  "]:
            with self.subTest(bad=bad):
                with self.assertRaises(identity.IdentifierError):
                    identity.normalize_pmid(bad)


class NormalizePmcidTest(unittest.TestCase):
    VALID = [
        ("PMC1234567", "PMC1234567"),
        ("pmc1234567", "PMC1234567"),
        ("1234567", "PMC1234567"),
        ("  PMC1234567  ", "PMC1234567"),
        ("  1234567  ", "PMC1234567"),
    ]

    def test_valid_forms_normalize_identically(self):
        for raw, expected in self.VALID:
            with self.subTest(raw=raw):
                self.assertEqual(identity.normalize_pmcid(raw), expected)

    def test_malformed_raises(self):
        for bad in ["", "PMC", "abc1234", "PMC12AB", "  "]:
            with self.subTest(bad=bad):
                with self.assertRaises(identity.IdentifierError):
                    identity.normalize_pmcid(bad)


class NormalizeUrlTest(unittest.TestCase):
    def test_lowercases_scheme_and_host_only(self):
        self.assertEqual(
            identity.normalize_url("HTTPS://Example.COM/Path/To/Page"),
            "https://example.com/Path/To/Page",
        )

    def test_strips_single_trailing_slash(self):
        self.assertEqual(
            identity.normalize_url("https://example.com/path/"),
            "https://example.com/path",
        )

    def test_root_path_slash_is_preserved(self):
        self.assertEqual(
            identity.normalize_url("https://example.com/"),
            "https://example.com/",
        )

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(
            identity.normalize_url("  https://example.com/path  "),
            "https://example.com/path",
        )

    def test_query_and_fragment_case_preserved(self):
        self.assertEqual(
            identity.normalize_url("https://Example.com/Path?Q=Val#Frag"),
            "https://example.com/Path?Q=Val#Frag",
        )

    def test_malformed_raises_missing_scheme(self):
        with self.assertRaises(identity.IdentifierError):
            identity.normalize_url("example.com/path")

    def test_malformed_raises_missing_host(self):
        with self.assertRaises(identity.IdentifierError):
            identity.normalize_url("https:///path")

    def test_malformed_raises_empty(self):
        with self.assertRaises(identity.IdentifierError):
            identity.normalize_url("")


class NormalizeIdentifierDispatchTest(unittest.TestCase):
    def test_dispatches_doi_case_insensitively(self):
        self.assertEqual(
            identity.normalize_identifier("DOI", "doi:10.1000/xyz123"),
            "10.1000/xyz123",
        )

    def test_dispatches_pmid(self):
        self.assertEqual(identity.normalize_identifier("pmid", "PMID: 42"), "42")

    def test_dispatches_pmcid(self):
        self.assertEqual(identity.normalize_identifier("pmcid", "1234567"), "PMC1234567")

    def test_dispatches_url(self):
        self.assertEqual(
            identity.normalize_identifier("url", "HTTPS://Example.com/x/"),
            "https://example.com/x",
        )

    def test_unknown_scheme_passes_through_stripped(self):
        self.assertEqual(
            identity.normalize_identifier("other", "  some-custom-value  "),
            "some-custom-value",
        )

    def test_known_scheme_still_raises_on_malformed_value(self):
        with self.assertRaises(identity.IdentifierError):
            identity.normalize_identifier("doi", "not-a-doi")


if __name__ == "__main__":
    unittest.main()
