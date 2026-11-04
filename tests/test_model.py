"""Accounting and identity invariants for footprint reports and comparisons."""

import copy
import unittest

from zephyr_utilities.memory.compare import compare_snapshots
from zephyr_utilities.memory.model import MAX_DEPTH, normalize_report


def leaf(name, size, *, identifier=None, section=".bss", address=0x20000000):
    return {"name": name, "identifier": identifier or name, "size": size, "section": section, "address": address}


def report(*nodes, total=None):
    total = sum(node["size"] for node in nodes) if total is None else total
    return {"total_size": total, "symbols": {"name": "Root", "identifier": "root", "size": total, "children": list(nodes)}}


def snapshot(ram=None, rom=None, **kwargs):
    value = {"id": "test", "name": "Test", "reports": {"ram": ram, "rom": rom}, "build": None, "config": {}}
    value.update(kwargs)
    return value


class NormalizeReportTests(unittest.TestCase):
    def test_preserves_hidden_and_adds_positive_remainder(self):
        raw = report(leaf("arena", 60), leaf("(hidden)", 5, section=None), total=80)
        original = copy.deepcopy(raw)
        result = normalize_report(raw)
        self.assertEqual(raw, original)
        self.assertEqual(sum(row["size"] for row in result["leaves"]), 80)
        self.assertEqual([row["size"] for row in result["leaves"] if row["name"] == "(hidden)"], [5])
        self.assertEqual([row["size"] for row in result["leaves"] if row["name"] == "(unattributed)"], [15])
        self.assertTrue(result["warnings"])

    def test_parent_remainder_is_counted_only_once(self):
        group = {"name": "app", "identifier": "app", "size": 20, "children": [leaf("buffer", 12)]}
        result = normalize_report(report(group, total=30))
        self.assertEqual(sum(row["size"] for row in result["leaves"]), 30)
        self.assertEqual(sorted(row["size"] for row in result["leaves"]), [8, 10, 12])

    def test_overlapping_attribution_keeps_declared_total_and_warning(self):
        result = normalize_report(report(leaf("a", 8), leaf("b", 8), total=12))
        self.assertEqual(result["total_size"], 12)
        self.assertEqual(len(result["leaves"]), 2)
        self.assertTrue(any("overlap" in warning for warning in result["warnings"]))

    def test_duplicated_native_identifiers_receive_unique_ids(self):
        result = normalize_report(report(leaf("same", 3), leaf("same", 5)))
        self.assertEqual(len({row["id"] for row in result["leaves"]}), 2)
        self.assertEqual(len({row["identifier"] for row in result["leaves"]}), 1)

    def test_longest_root_normalization_and_boundary(self):
        result = normalize_report(report(
            leaf("a", 1, identifier="/old/work/app/src/main.c/a"),
            leaf("b", 1, identifier="/old/work/app-other/src/main.c/b"),
        ), {"/old/work": "WORKSPACE", "/old/work/app": "APPLICATION"})
        self.assertEqual(result["leaves"][0]["path"], "APPLICATION/src/main.c/a")
        self.assertEqual(result["leaves"][1]["path"], "WORKSPACE/app-other/src/main.c/b")

    def test_semantic_native_tree_re_normalizes_application_root(self):
        raw = report({"name": "WORKSPACE", "identifier": "/work", "size": 4, "children": [
            {"name": "app", "identifier": "app", "size": 4, "children": [leaf("buffer", 4)]}
        ]})
        result = normalize_report(raw, {"/work": "WORKSPACE", "/work/app": "APPLICATION"})
        self.assertEqual(result["leaves"][0]["path"], "APPLICATION/buffer")

    def test_large_addresses_remain_exact(self):
        result = normalize_report(report(leaf("a", 1, address=2**63 + 1)))
        self.assertEqual(result["leaves"][0]["address"], "0x8000000000000001")

    def test_empty_report_is_valid(self):
        result = normalize_report(report())
        self.assertEqual(result["total_size"], 0)
        self.assertEqual(result["leaves"], [])

    def test_invalid_reports_rejected(self):
        invalid = [None, [], {}, {"total_size": True, "symbols": {}}, report(leaf("a", -1)), report(leaf("a", 1.5)), report(leaf("a", float("nan")))]
        malformed = report(leaf("a", 1))
        malformed["symbols"]["children"] = "not a list"
        invalid.append(malformed)
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_report(raw)

    def test_bounded_depth(self):
        raw = report(leaf("bottom", 1))
        for _ in range(MAX_DEPTH + 1):
            raw["symbols"] = {"name": "nested", "size": 1, "children": [raw["symbols"]]}
        with self.assertRaisesRegex(ValueError, "depth"):
            normalize_report(raw)


class CompareReportTests(unittest.TestCase):
    def test_add_remove_change_and_signed_totals(self):
        baseline = snapshot(normalize_report(report(leaf("a", 10), leaf("gone", 4))))
        candidate = snapshot(normalize_report(report(leaf("a", 6), leaf("new", 2))))
        result = compare_snapshots(baseline, candidate)
        self.assertEqual(result["totals"]["ram"]["delta"], -6)
        rows = {row["name"]: row for row in result["changes"]["ram"]}
        self.assertEqual((rows["a"]["status"], rows["a"]["delta"]), ("changed", -4))
        self.assertEqual((rows["gone"]["status"], rows["gone"]["delta"]), ("removed", -4))
        self.assertEqual((rows["new"]["status"], rows["new"]["delta"]), ("added", 2))

    def test_moved_workspace_and_addresses_match(self):
        before = normalize_report(report(leaf("x", 12, identifier="/old/app/main.c/x", address=100)), {"/old/app": "APPLICATION"})
        after = normalize_report(report(leaf("x", 14, identifier="/new/app/main.c/x", address=200)), {"/new/app": "APPLICATION"})
        rows = compare_snapshots(snapshot(before), snapshot(after))["changes"]["ram"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delta"], 2)
        self.assertEqual(rows[0]["status"], "changed")

    def test_duplicate_identity_aggregated_and_flagged(self):
        before = normalize_report(report(leaf("x", 3), leaf("x", 5)))
        after = normalize_report(report(leaf("x", 10)))
        result = compare_snapshots(snapshot(before), snapshot(after))
        self.assertEqual(len(result["changes"]["ram"]), 1)
        row = result["changes"]["ram"][0]
        self.assertTrue(row["ambiguous"])
        self.assertEqual((row["before"], row["after"], row["delta"]), (8, 10, 2))
        self.assertTrue(any("duplicate" in warning for warning in result["warnings"]))

    def test_alias_or_source_attribution_change_is_flagged_not_merged(self):
        before = normalize_report(report(leaf("stdout", 4, identifier="/app/stdio.c/stdout", address=0x1000)))
        after = normalize_report(report(leaf("stderr", 4, identifier="/app/stdio.c/stderr", address=0x1000)))
        result = compare_snapshots(snapshot(before), snapshot(after))
        rows = result["changes"]["ram"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["status"] for row in rows}, {"added", "removed"})
        self.assertTrue(all(row.get("attribution_warning") for row in rows))
        self.assertEqual(result["totals"]["ram"]["delta"], 0)

    def test_different_section_or_size_is_not_same_address_range(self):
        before = normalize_report(report(leaf("a", 4, section=".bss", address=0)))
        after = normalize_report(report(leaf("b", 4, section=".data", address=0)))
        rows = compare_snapshots(snapshot(before), snapshot(after))["changes"]["ram"]
        self.assertFalse(any(row.get("attribution_warning") for row in rows))

    def test_unknown_report_is_not_zero(self):
        result = compare_snapshots(snapshot(), snapshot(normalize_report(report(leaf("x", 4)))))
        self.assertEqual(result["totals"]["ram"], {"before": None, "after": 4, "delta": None, "percent": None})
        self.assertEqual(result["changes"]["ram"], [])

    def test_zero_baseline_has_no_percentage(self):
        result = compare_snapshots(snapshot(normalize_report(report())), snapshot(normalize_report(report(leaf("x", 4)))))
        self.assertEqual(result["totals"]["ram"]["delta"], 4)
        self.assertIsNone(result["totals"]["ram"]["percent"])

    def test_initialized_data_is_separate_in_ram_and_rom(self):
        before = normalize_report(report(leaf("initialized", 4, section=".data")))
        after = normalize_report(report(leaf("initialized", 8, section=".data")))
        result = compare_snapshots(snapshot(before, before), snapshot(after, after))
        self.assertEqual(set(result["totals"]), {"ram", "rom"})
        self.assertEqual(result["totals"]["ram"]["delta"], 4)
        self.assertEqual(result["totals"]["rom"]["delta"], 4)

    def test_metadata_and_profiling_mismatches_exposed(self):
        ram = normalize_report(report(leaf("x", 4)))
        left = snapshot(ram, build={"board": "board-a", "toolchain": "gcc", "zephyr_version": "4.4"}, config={"CONFIG_LTO": "n"})
        right = snapshot(ram, build={"board": "board-b", "toolchain": "gcc", "zephyr_version": "4.4"}, config={"CONFIG_LTO": "y"})
        result = compare_snapshots(left, right)
        self.assertEqual(result["config_changes"], [{"key": "CONFIG_LTO", "before": "n", "after": "y"}])
        self.assertTrue(any("Board differs" in warning for warning in result["warnings"]))
        self.assertTrue(any("profiling" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
