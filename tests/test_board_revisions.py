"""Automatic revision selection agrees with supported declarative Zephyr rules."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from zephyr_utilities.core.board_revisions import parse_board_revisions, resolve_board_revision
from zephyr_utilities.core.hardware_metadata import MetadataError


def metadata(fmt="major.minor.patch", revisions=("0.7.0", "0.14.0"), default="0.14.0", exact=False):
    return {"format": fmt, "default": default, "exact": exact,
            "revisions": [{"name": revision} for revision in revisions]}


class BoardRevisionTests(unittest.TestCase):
    def test_undeclared_board_remains_revisionless_and_rejects_explicit_revision(self):
        self.assertEqual(parse_board_revisions(None), ([], None, None, False))
        self.assertEqual(resolve_board_revision(None), {"requested": None, "effective": None,
                         "default": None, "format": None, "match": "none", "exact": False})
        with self.assertRaisesRegex(MetadataError, "does not declare"):
            resolve_board_revision(None, "1")

    def test_default_and_explicit_old_revision_preserve_requested_identity(self):
        value = metadata()
        before = deepcopy(value)
        result = resolve_board_revision(value)
        self.assertEqual((result["requested"], result["effective"], result["match"]),
                         (None, "0.14.0", "default"))
        result = resolve_board_revision(value, "0.7.0")
        self.assertEqual((result["requested"], result["effective"], result["match"]),
                         ("0.7.0", "0.7.0", "exact"))
        self.assertEqual(value, before)

    def test_semver_fuzzy_selection_uses_numeric_order_and_never_rounds_up(self):
        value = metadata(revisions=("0.14.0", "0.7.0", "0.10.0"))
        for requested, effective in (("0.9.3", "0.7.0"), ("0.11.0", "0.10.0"), ("9.0.0", "0.14.0")):
            with self.subTest(requested=requested):
                result = resolve_board_revision(value, requested)
                self.assertEqual((result["requested"], result["effective"], result["match"]),
                                 (requested, effective, "compatible"))
        with self.assertRaisesRegex(MetadataError, "older"):
            resolve_board_revision(value, "0.6.99")

    def test_short_semver_requests_normalize_only_for_selection(self):
        value = metadata(revisions=("1.0.0", "1.2.0"), default="1.2.0")
        for requested, effective in (("1", "1.0.0"), ("1.2", "1.2.0")):
            with self.subTest(requested=requested):
                result = resolve_board_revision(value, requested)
                self.assertEqual((result["requested"], result["effective"], result["match"]),
                                 (requested, effective, "exact"))

    def test_exact_mode_still_normalizes_semver_shorthand_but_rejects_fuzzy(self):
        value = metadata(exact=True)
        self.assertEqual(resolve_board_revision(value, "0.14")["effective"], "0.14.0")
        with self.assertRaisesRegex(MetadataError, "exact revision"):
            resolve_board_revision(value, "0.13.0")

    def test_letter_defaults_and_predecessors_use_declared_uppercase_letters(self):
        value = metadata("letter", ("F", "A", "D"), "D")
        self.assertEqual(resolve_board_revision(value)["effective"], "D")
        self.assertEqual(resolve_board_revision(value, "E")["effective"], "D")
        self.assertEqual(resolve_board_revision(value, "Z")["effective"], "F")
        for requested in ("d", "AA", "1"):
            with self.subTest(requested=requested), self.assertRaisesRegex(MetadataError, "format"):
                resolve_board_revision(value, requested)

    def test_numeric_declared_values_work_without_claiming_cmake_fuzzy_parity(self):
        value = metadata("number", ("0", "2", "10"), "0")
        self.assertEqual(resolve_board_revision(value)["effective"], "0")
        self.assertEqual(resolve_board_revision(value, "10")["match"], "exact")
        for requested in ("1", "3", "20", "02"):
            with self.subTest(requested=requested), self.assertRaisesRegex(MetadataError, "Compatible numeric"):
                resolve_board_revision(value, requested)

    def test_custom_declarations_remain_catalogue_data_but_never_execute_scripts(self):
        value = metadata("custom", ("", "alpha", "beta"), "")
        self.assertEqual(parse_board_revisions(value), (["", "alpha", "beta"], "", "custom", False))
        self.assertEqual(parse_board_revisions({"format": "custom"}), ([], None, "custom", False))
        for requested in (None, "alpha"):
            with self.subTest(requested=requested), self.assertRaisesRegex(MetadataError, "revision.cmake"):
                resolve_board_revision(value, requested)

    def test_numerically_equal_semver_declarations_keep_cmake_tie_order(self):
        value = metadata(revisions=("1.02.0", "1.2.0"), default="1.2.0")
        self.assertEqual(resolve_board_revision(value, "1.02.0")["effective"], "1.02.0")
        self.assertEqual(resolve_board_revision(value, "1.2.1")["effective"], "1.2.0")

    def test_metadata_rejects_ambiguous_unbounded_and_unsafe_declarations(self):
        bad = [[], {"format": "unknown"}, {"format": []}, {"format": {}}, {**metadata(), "extra": True},
               {**metadata(), "exact": "false"}, metadata(default="0.8.0"),
               metadata(revisions=("0.14.0", "0.14.0")), metadata(revisions=("01.2.3",), default="01.2.3"),
               metadata(revisions=("../bad",), default="../bad"),
               {**metadata(), "revisions": [{"name": "0.14.0", "revisions": []}]}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(MetadataError):
                parse_board_revisions(value)
        with patch("zephyr_utilities.core.board_revisions.MAX_RECORDS", 1), self.assertRaises(MetadataError):
            parse_board_revisions(metadata())

    def test_requested_revisions_reject_nontext_path_tokens_and_malformed_versions(self):
        for requested in (True, 1, "", "../1", "1/2", "1;message", "0" * 161,
                          "01.2.3", "1.2.3.4", "1.", "1.0-beta"):
            with self.subTest(requested=requested), self.assertRaises(MetadataError):
                resolve_board_revision(metadata(), requested)


if __name__ == "__main__":
    unittest.main()
