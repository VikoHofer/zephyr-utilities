"""Synthetic hardware metadata cases; no SDK, workspace tools or device required."""

from concurrent.futures import CancelledError
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

import yaml

from zephyr_utilities.features.projects.hardware import discover_hardware


class HardwareDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.zephyr = self.root / "zephyr"
        self.extra = self.root / "custom"
        self.roots = [{"path": str(self.zephyr), "origin": "Zephyr"}]

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, data, root=None):
        path = (root or self.zephyr) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return path

    def chip(self, **extra):
        return self.write("soc/vendor/chip/soc.yml", {"socs": [{"name": "chip", **extra}]})

    def board(self, name="demo", **extra):
        return self.write(f"boards/vendor/{name}/board.yml", {"board": {
            "name": name, "socs": [{"name": "chip"}], **extra}})

    def discover(self, roots=None):
        roots = roots or self.roots
        return discover_hardware(roots, roots)

    def test_zephyr_37_shape_without_full_name(self):
        # Synthetic representation of the v3.7.0 pykwalify metadata shape.
        self.chip()
        self.board(vendor="example")
        result = self.discover()
        self.assertEqual(result["boards"][0]["full_name"], "demo")
        self.assertEqual(result["boards"][0]["targets"], ["demo/chip"])
        self.assertEqual(result["socs"][0]["name"], "chip")
        self.assertEqual(result["boards"][0]["evidence"], "declared")
        self.assertFalse(result["capabilities"]["build_validation"])

    def test_current_shape_clusters_variants_revisions_and_family(self):
        self.write("soc/vendor/chip/soc.yml", {"family": [{"name": "family", "series": [{
            "name": "series", "socs": [{"name": "chip", "cpuclusters": [{"name": "app"}, {"name": "net"}]}]}]}]})
        board_file = self.board(full_name="Development board", socs=[{"name": "chip", "variants": [
            {"name": "ns", "cpucluster": "app", "variants": [{"name": "fast"}]}]}],
            revision={"format": "letter", "default": "B", "exact": True,
                      "revisions": [{"name": "A"}, {"name": "B"}]})
        result = self.discover()
        board = result["boards"][0]
        self.assertEqual(board["qualifiers"], ["chip/app", "chip/app/ns", "chip/app/ns/fast", "chip/net"])
        self.assertIn("demo@B/chip/app/ns/fast", board["targets"])
        self.assertEqual(board["default_revision"], "B")
        self.assertTrue(board["revision_exact"])
        self.assertEqual(result["socs"][0]["family"], "family")
        self.assertEqual(result["socs"][0]["series"], "series")
        self.assertIn(str(board_file), result["inputs"])
        self.assertIn(str(board_file.parent), result["inputs"])
        self.assertFalse(result["diagnostics"])

    def test_board_extension_contributes_target_and_origins(self):
        self.chip()
        self.board()
        extension = self.write("boards/addon/board.yml", {"board": {"extend": "demo", "variants": [
            {"name": "special", "qualifier": "chip"}]}}, self.extra)
        roots = [{"path": str(self.extra), "origin": "Module addon"}, *self.roots]
        result = self.discover(roots)
        board = result["boards"][0]
        self.assertEqual(board["targets"], ["demo/chip", "demo/chip/special"])
        self.assertEqual(board["origins"], ["Zephyr", "Module addon"])
        self.assertIn(str(extension.parent), board["directories"])
        self.assertFalse(result["diagnostics"])

    def test_soc_extension_before_base_adds_cluster(self):
        self.chip(cpuclusters=[{"name": "app"}])
        self.board()
        self.write("soc/addon/soc.yml", {"socs": [{"extend": "chip", "cpuclusters": [{"name": "net"}]}]}, self.extra)
        result = self.discover([{"path": str(self.extra), "origin": "Backported extension"}, *self.roots])
        self.assertEqual(result["boards"][0]["targets"], ["demo/chip/app", "demo/chip/net"])
        self.assertEqual(result["socs"][0]["origins"], ["Zephyr", "Backported extension"])

    def test_duplicate_base_is_conflict_not_extension(self):
        self.chip()
        self.board()
        self.write("boards/local/board.yml", {"board": {"name": "demo", "socs": [{"name": "chip"}]}}, self.extra)
        result = self.discover([*self.roots, {"path": str(self.extra), "origin": "Local"}])
        board = result["boards"][0]
        self.assertEqual(board["status"], "conflict")
        self.assertEqual(board["targets"], [])
        self.assertEqual(len(board["source_files"]), 3)
        self.assertIn("duplicate-board", [item["code"] for item in result["diagnostics"]])

    def test_duplicate_soc_blocks_dependent_board(self):
        self.chip()
        self.board()
        self.write("soc/duplicate/soc.yml", {"socs": [{"name": "chip"}]})
        result = self.discover()
        self.assertEqual(result["socs"][0]["status"], "conflict")
        self.assertEqual(result["boards"][0]["targets"], [])

    def test_unknown_metadata_is_partial_and_does_not_guess(self):
        self.chip()
        self.board("supported")
        self.board("future", future_selection={"cpu": 1})
        self.board("missing", socs=[{"name": "not_installed"}])
        result = self.discover()
        by_name = {board["name"]: board for board in result["boards"]}
        self.assertEqual(by_name["supported"]["status"], "discovered")
        self.assertEqual(by_name["future"]["status"], "unsupported")
        self.assertEqual(by_name["missing"]["targets"], [])
        self.assertFalse(result["capabilities"]["complete"])

    def test_new_metadata_and_dirty_edits_are_seen_without_commit(self):
        self.chip()
        self.board()
        first = self.discover()
        self.board("new_board")
        self.board(vendor="changed")
        second = self.discover()
        self.assertEqual(len(first["boards"]), 1)
        self.assertEqual(len(second["boards"]), 2)
        self.assertEqual(second["boards"][0]["vendor"], "changed")

    def test_custom_revision_is_declared_not_evaluated(self):
        self.chip()
        self.board(revision={"format": "custom"})
        result = self.discover()
        self.assertEqual(result["boards"][0]["targets"], ["demo/chip"])
        self.assertIn("custom-revision", [item["code"] for item in result["diagnostics"]])

    def test_empty_custom_revision_preserves_default_without_invalid_target(self):
        self.chip()
        self.board(revision={"format": "custom", "default": "",
                             "revisions": [{"name": ""}, {"name": "psram"}]})
        result = self.discover()
        board = result["boards"][0]
        self.assertEqual(board["default_revision"], "")
        self.assertEqual(board["revisions"], ["", "psram"])
        self.assertEqual(board["targets"], ["demo/chip", "demo@psram/chip"])

    def test_architecture_is_declared_by_matching_platform_metadata_only(self):
        self.chip()
        self.board()
        matching = self.write("boards/vendor/demo/demo_chip.yaml", {"identifier": "demo/chip", "arch": "arm", "type": "mcu"})
        self.write("boards/vendor/demo/unrelated.yaml", {"identifier": "other/chip", "arch": "riscv"})
        result = self.discover()
        board = result["boards"][0]
        self.assertEqual(board["architectures"], ["arm"])
        self.assertEqual(board["platforms"][0]["target"], "demo/chip")
        self.assertIn(str(matching), board["source_files"])
        self.assertIn(str(matching), result["inputs"])

    def test_invalid_extension_blocks_base_instead_of_returning_partial_targets(self):
        self.chip()
        self.board()
        self.write("boards/addon/board.yml", {"board": {"extend": "demo", "variants": [
            {"name": "special", "qualifier": "missing"}]}})
        result = self.discover()
        self.assertEqual(result["boards"][0]["status"], "unsupported")
        self.assertEqual(result["boards"][0]["targets"], [])

    def test_invalid_extension_name_does_not_abort_catalogue(self):
        self.chip()
        self.board()
        self.write("boards/addon/board.yml", {"board": {"extend": {"invalid": "mapping"}}})
        result = self.discover()
        self.assertEqual(result["boards"][0]["status"], "discovered")
        self.assertIn("unsupported-board-extension", [item["code"] for item in result["diagnostics"]])

    def test_unknown_extension_schema_blocks_affected_board_targets(self):
        self.chip()
        self.board()
        self.write("boards/addon/board.yml", {"board": {"extend": "demo", "future_variants": ["new"]}})
        result = self.discover()
        self.assertEqual(result["boards"][0]["targets"], [])
        self.assertEqual(result["boards"][0]["status"], "unsupported")

    def test_malformed_yaml_duplicate_keys_and_aliases_are_diagnostics(self):
        self.chip()
        self.board()
        malformed = self.write("boards/bad/board.yml", {})
        for source in ["board: [", "board: 1\nboard: 2\n", "board: &b {name: demo, socs: [*b]}\n"]:
            with self.subTest(source=source):
                malformed.write_text(source)
                result = self.discover()
                self.assertEqual(len(result["boards"]), 1)
                self.assertIn("unsupported-board-metadata", [item["code"] for item in result["diagnostics"]])

    def test_legacy_and_symlink_directories_are_not_interpreted_as_v2(self):
        self.chip()
        self.board()
        legacy = self.zephyr / "boards" / "arm" / "old" / "old_defconfig"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("CONFIG_SOC_OLD=y\n")
        (self.zephyr / "boards" / "loop").symlink_to(self.zephyr / "boards", target_is_directory=True)
        result = self.discover()
        codes = [item["code"] for item in result["diagnostics"]]
        self.assertIn("legacy-board", codes)
        self.assertIn("symlink-directory", codes)
        self.assertEqual(len(result["boards"]), 1)

    def test_target_expansion_is_bounded(self):
        self.chip()
        self.board(revision={"format": "letter", "default": "A", "revisions": [{"name": "A"}, {"name": "B"}]})
        with patch("zephyr_utilities.features.projects.hardware.MAX_TARGETS", 2):
            result = self.discover()
        self.assertEqual(result["boards"][0]["targets"], [])
        self.assertIn("target-limit", [item["code"] for item in result["diagnostics"]])

    def test_scan_limits_and_cancellation(self):
        self.chip()
        self.board()
        with patch("zephyr_utilities.features.projects.hardware.MAX_DIRECTORIES", 1):
            result = self.discover()
        self.assertIn("scan-limit", [item["code"] for item in result["diagnostics"]])
        cancel = Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            discover_hardware(self.roots, self.roots, cancel=cancel)

    def test_discovery_does_not_modify_metadata(self):
        self.chip()
        self.board()
        before = {path: path.read_bytes() for path in self.zephyr.rglob("*") if path.is_file()}
        self.discover()
        after = {path: path.read_bytes() for path in self.zephyr.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO metadata case requires POSIX")
    def test_fifo_metadata_is_rejected_without_waiting_for_a_writer(self):
        self.chip()
        self.board()
        for relative in ("boards/fifo/board.yml", "soc/fifo/soc.yml"):
            path = self.zephyr / relative
            path.parent.mkdir(parents=True)
            os.mkfifo(path)
        # A child timeout turns a regression to blocking file reads into a bounded failure.
        script = """
import json, sys
from zephyr_utilities.features.projects.hardware import discover_hardware
roots = [{"path": sys.argv[1], "origin": "Fixture"}]
result = discover_hardware(roots, roots)
print(json.dumps({"boards": [b["name"] for b in result["boards"]], "diagnostics": result["diagnostics"]}))
"""
        environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        process = subprocess.run([sys.executable, "-c", script, str(self.zephyr)],
                                 env=environment, capture_output=True, text=True, timeout=5, check=True)
        result = json.loads(process.stdout)
        self.assertEqual(result["boards"], ["demo"])
        self.assertEqual({item["code"] for item in result["diagnostics"]},
                         {"unsupported-board-metadata", "unsupported-soc-metadata"})
        self.assertTrue(all("regular file" in item["message"] for item in result["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
