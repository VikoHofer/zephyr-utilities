"""Reference board creation preserves hardware choices and rejects unsafe copies."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from concurrent.futures import CancelledError

import yaml

from zephyr_utilities.features.projects.board_scaffold import apply_scaffold, catalogue, preview_scaffold
from zephyr_utilities.features.projects.hardware import discover_hardware


class BoardScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        (self.workspace / ".west").mkdir()
        self.zephyr = self.workspace / "zephyr"
        self.source = self.zephyr / "boards/vendor/reference"
        self.write(self.zephyr / "LICENSE", "Apache License\nVersion 2.0, January 2004\nSynthetic test license marker.\n")
        self.write(self.zephyr / "REUSE.toml", 'version = 1\n[[annotations]]\npath = ["**/*"]\n'
                   'SPDX-License-Identifier = "Apache-2.0"\nSPDX-FileCopyrightText = "Synthetic fixture"\n')
        self.write(self.zephyr / "soc/vendor/chip/soc.yml", "socs:\n  - name: chip\n")
        self.write(self.source / "board.yml", "# SPDX-License-Identifier: Apache-2.0\nboard:\n  name: reference\n  vendor: vendor\n  socs:\n    - name: chip\n")
        self.write(self.source / "reference.dts", '#include <vendor/chip.dtsi>\n#include "reference-pinctrl.dtsi"\n/ { model = "Reference"; };\n')
        self.write(self.source / "reference-pinctrl.dtsi", "/* SPDX-License-Identifier: Apache-2.0 */\n&pinctrl {};\n")
        self.write(self.source / "Kconfig.reference", "# Copyright reference contributors\nconfig BOARD_REFERENCE\n\tselect SOC_CHIP\n")
        self.write(self.source / "reference_defconfig", "CONFIG_GPIO=y\n")
        self.write(self.source / "board.cmake", "if(CONFIG_BOARD_REFERENCE_CHIP)\n  include(${ZEPHYR_BASE}/boards/common/example.board.cmake)\nendif()\n")
        self.write(self.source / "support/reference.cfg", "# Reference runner settings\nset CHIP chip\n")
        self.snapshot = self.scan()
        self.body = {"name": "my_board", "vendor": "custom", "full_name": "My prototype",
                     "reference_id": self.snapshot["boards"][0]["id"]}

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def scan(self):
        roots = [{"path": str(self.zephyr), "origin": "Zephyr"}]
        hardware = discover_hardware(roots, roots)
        return {**hardware, "schema_version": 1, "status": "complete", "id": "a" * 32,
                "workspace": {"path": str(self.workspace), "west_topdir": str(self.workspace), "manifest_resolved": True},
                "zephyr": {"path": str(self.zephyr), "version": "test"}, "modules": [], "sdks": []}

    def preview(self, **changes):
        return preview_scaffold(str(self.workspace), {**self.body, **changes}, self.snapshot)

    def test_copy_preserves_source_and_discovers_new_board(self):
        original = {str(path): path.read_bytes() for path in self.source.rglob("*") if path.is_file()}
        plan = self.preview(soc="chip")
        destination = Path(plan["path"])
        self.assertFalse(destination.exists())
        self.assertEqual(plan["targets"], ["my_board/chip"])
        result = apply_scaffold(plan)
        self.assertEqual(result["path"], str(destination))
        root = destination / "boards/custom/my_board"
        self.assertEqual(yaml.safe_load((root / "board.yml").read_text())["board"]["full_name"], "My prototype")
        self.assertIn("CONFIG_BOARD_MY_BOARD_CHIP", (root / "board.cmake").read_text())
        self.assertIn("config BOARD_MY_BOARD", (root / "Kconfig.my_board").read_text())
        self.assertIn("SOC_CHIP", (root / "Kconfig.my_board").read_text())
        self.assertIn("Copyright reference contributors", (root / "Kconfig.my_board").read_text())
        self.assertIn('"my_board-pinctrl.dtsi"', (root / "my_board.dts").read_text())
        self.assertIn("SPDX-License-Identifier", (root / "board.yml").read_text())
        provenance = json.loads((destination / "zephyr-board-origin.json").read_text())
        self.assertEqual(provenance["selected_soc"], "chip")
        self.assertTrue(provenance["source_files"])
        self.assertEqual((destination / "LICENSE").read_text(), (self.zephyr / "LICENSE").read_text())
        self.assertTrue(provenance["attribution"])
        hardware = discover_hardware([{"path": str(destination)}], [{"path": str(self.zephyr)}])
        self.assertEqual(hardware["boards"][0]["targets"], ["my_board/chip"])
        self.assertEqual(original, {str(path): path.read_bytes() for path in self.source.rglob("*") if path.is_file()})

    def generated_snapshot(self):
        first = apply_scaffold(self.preview())
        roots = [{"path": first["path"]}, {"path": str(self.zephyr)}]
        snapshot = {**self.snapshot, **discover_hardware(roots, [{"path": str(self.zephyr)}]), "roots": {"boards": roots}}
        reference = next(board for board in snapshot["boards"] if board["name"] == "my_board")
        body = {**self.body, "name": "second_board", "reference_id": reference["id"]}
        return Path(first["path"]), snapshot, body

    def test_generated_board_can_be_used_as_a_reference(self):
        first, snapshot, body = self.generated_snapshot()
        plan = preview_scaffold(str(self.workspace), body, snapshot)
        second = Path(apply_scaffold(plan)["path"])
        self.assertEqual(plan["targets"], ["second_board/chip"])
        self.assertIn("BOARD_SECOND_BOARD", (second / "boards/custom/second_board/Kconfig.second_board").read_text())
        self.assertIn('"boards/custom/second_board/Kconfig.second_board"', (second / "REUSE.toml").read_text())
        self.assertNotIn("boards/custom/my_board", (second / "REUSE.toml").read_text())
        self.assertEqual((second / "licenses/reference-REUSE.toml").read_text(), (first / "REUSE.toml").read_text())

    def test_generated_license_annotations_do_not_cover_added_files(self):
        first, snapshot, body = self.generated_snapshot()
        self.write(first / "boards/custom/my_board/driver.c", "/* New source without attribution. */\n")
        with self.assertRaisesRegex(ValueError, "no supported license attribution"):
            preview_scaffold(str(self.workspace), body, snapshot)

    def test_generated_provenance_does_not_replace_actual_license_annotations(self):
        first, snapshot, body = self.generated_snapshot()
        (first / "REUSE.toml").unlink()
        self.assertTrue((first / "zephyr-board-origin.json").is_file())
        with self.assertRaisesRegex(ValueError, "no supported license attribution"):
            preview_scaffold(str(self.workspace), body, snapshot)

    def test_generated_reference_license_changes_invalidate_review(self):
        first, snapshot, body = self.generated_snapshot()
        plan = preview_scaffold(str(self.workspace), body, snapshot)
        self.write(first / "REUSE.toml", (first / "REUSE.toml").read_text() + "# Changed after review\n")
        with self.assertRaisesRegex(ValueError, "hardware metadata changed"):
            apply_scaffold(plan)

    def test_soc_selection_must_match_reference(self):
        with self.assertRaisesRegex(ValueError, "does not use"):
            self.preview(soc="other")

    def test_review_rechecks_all_reference_runtime_files(self):
        plan = self.preview()
        self.write(self.source / "reference_defconfig", "CONFIG_GPIO=n\n")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            apply_scaffold(plan)
        self.assertFalse(Path(plan["path"]).exists())

    def test_added_reference_file_invalidates_review(self):
        plan = self.preview()
        self.write(self.source / "added.conf", "CONFIG_GPIO=n\n")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            apply_scaffold(plan)

    def test_soc_metadata_changes_invalidate_review(self):
        plan = self.preview()
        self.write(self.zephyr / "soc/vendor/chip/soc.yml", "socs:\n  - name: chip\n# changed\n")
        with self.assertRaisesRegex(ValueError, "hardware metadata changed"):
            apply_scaffold(plan)

    def test_destination_collision_preserves_existing_files(self):
        plan = self.preview()
        marker = Path(plan["path"]) / "keep.txt"
        self.write(marker, "user data")
        with self.assertRaisesRegex(ValueError, "already exists"):
            apply_scaffold(plan)
        self.assertEqual(marker.read_text(), "user data")

    def test_parent_symlink_and_outside_workspace_rejected(self):
        outside = self.workspace / "outside"
        outside.mkdir()
        (self.workspace / "linked").symlink_to(outside, target_is_directory=True)
        for path in (str(self.workspace / "linked/new"), str(self.workspace.parent / "outside-board"), "../escape"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.preview(path=path)

    def test_reference_symlink_and_binary_runtime_file_rejected(self):
        target = self.source / "bad.h"
        target.symlink_to(self.source / "reference_defconfig")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.preview()
        target.unlink()
        target.write_bytes(b"\xff\x00")
        with self.assertRaisesRegex(ValueError, "text"):
            self.preview()

    def test_docs_are_excluded_but_declared_local_include_must_exist(self):
        doc = self.source / "doc/image.png"
        doc.parent.mkdir()
        doc.write_bytes(b"\xff\x00")
        plan = self.preview()
        self.assertEqual(plan["provenance"]["omitted_directories"], ["doc"])
        self.assertFalse(any("doc/" in item["path"] for item in plan["files"]))
        self.write(self.source / "reference.dts", '#include "missing.dtsi"\n')
        with self.assertRaisesRegex(ValueError, "outside the copied"):
            self.preview()

    def test_parent_dependencies_rejected(self):
        self.write(self.source / "reference.dts", '#include "../shared.dtsi"\n')
        with self.assertRaisesRegex(ValueError, "parent-directory"):
            self.preview()

    def test_cmake_local_dependencies_must_be_copied(self):
        for content in ('include(${CMAKE_CURRENT_LIST_DIR}/missing.cmake)',
                        'board_runner_args(tool "--config=${BOARD_DIR}/support/missing.cfg")',
                        'include(missing.cmake)',
                        'include(${ZEPHYR_BASE}/boards/vendor/reference/helper.cmake)'):
            with self.subTest(content=content):
                self.write(self.source / "board.cmake", content)
                with self.assertRaisesRegex(ValueError, "dependency|outside the copied|original board-directory"):
                    self.preview()

    def test_required_board_kconfig_is_not_optional(self):
        (self.source / "Kconfig.reference").unlink()
        with self.assertRaisesRegex(ValueError, "board Kconfig"):
            self.preview()

    def test_unknown_license_and_changed_license_fail_safely(self):
        self.write(self.source / "reference.dts", "/* SPDX-License-Identifier: Unknown-License */\n")
        with self.assertRaisesRegex(ValueError, "license not supported"):
            self.preview()
        self.write(self.source / "reference.dts", "/* SPDX-License-Identifier: Apache-2.0 */\n")
        plan = self.preview()
        self.write(self.zephyr / "LICENSE", "Changed license")
        with self.assertRaisesRegex(ValueError, "hardware metadata changed"):
            apply_scaffold(plan)

    def test_multiple_board_definitions_rejected(self):
        board = yaml.safe_load((self.source / "board.yml").read_text())["board"]
        second = deepcopy(board)
        second["name"] = "second"
        self.write(self.source / "board.yml", yaml.safe_dump({"boards": [board, second]}))
        with self.assertRaisesRegex(ValueError, "one unchanged board"):
            self.preview()

    def test_changed_targets_require_analysis(self):
        board = yaml.safe_load((self.source / "board.yml").read_text())["board"]
        board["socs"][0]["variants"] = [{"name": "variant"}]
        self.write(self.source / "board.yml", yaml.safe_dump({"board": board}))
        with self.assertRaisesRegex(ValueError, "targets changed"):
            self.preview()

    def test_multiple_socs_variants_and_declared_revisions_retained(self):
        self.write(self.zephyr / "soc/vendor/other/soc.yml", "socs:\n  - name: other\n")
        metadata = yaml.safe_load((self.source / "board.yml").read_text())
        metadata["board"]["socs"].append({"name": "other", "variants": [{"name": "fast"}]})
        metadata["board"]["revision"] = {"format": "letter", "default": "A", "revisions": [{"name": "A"}]}
        self.write(self.source / "board.yml", yaml.safe_dump(metadata))
        self.snapshot = self.scan()
        plan = self.preview(soc="chip")
        self.assertIn("my_board@A/other/fast", plan["targets"])
        self.assertIn("my_board/chip", plan["targets"])

    def test_duplicate_board_identifier_and_invalid_names_rejected(self):
        for changes in ({"name": "reference"}, {"name": "Bad Board"}, {"name": "my-board"},
                        {"vendor": "../vendor"}, {"full_name": "bad\nname"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.preview(**changes)

    def test_catalogue_handles_unavailable_and_extended_references(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["boards"][0]["directories"].append("/another/root")
        self.assertFalse(catalogue(snapshot)["references"][0]["eligible"])
        self.assertEqual(catalogue(None), {"references": [], "socs": []})

    def test_cancellation_publishes_nothing(self):
        plan = self.preview()
        cancel = Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            apply_scaffold(plan, cancel=cancel)
        self.assertFalse(Path(plan["path"]).exists())

    def test_changed_preview_bytes_are_not_written(self):
        plan = self.preview()
        plan["files"][0]["content"] += "Changed after review"
        with self.assertRaisesRegex(ValueError, "contents changed"):
            apply_scaffold(plan)
        self.assertFalse(Path(plan["path"]).exists())
