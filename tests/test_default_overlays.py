"""Generated board overlays preserve upstream files and CMake selection semantics."""

from copy import deepcopy
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_project_samples import sample_fixture
from test_sample_adaptations import rgb_fixture
from zephyr_utilities.features.projects import sample_adaptations
from zephyr_utilities.features.projects.default_overlay import ARCHIVED_CMAKE, add_default_overlay, default_overlay, extend_default_overlays
from zephyr_utilities.features.projects.samples import preview_sample, validate_sample_plan
from zephyr_utilities.features.projects.templates import render_template, select_template, template_paths, validate_template


class DefaultOverlayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="default-overlay-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.recipe = {"cwd": str(self.root), "display_configure": "cmake configure", "display_build": "cmake build"}
        self.target = "nrf54l15dk/nrf54l15/cpuapp"

    def test_target_names_revisions_custom_boards_and_long_targets_are_readable_and_bounded(self):
        names = {self.target: "nrf54l15dk_nrf54l15_cpuapp",
                 "custom_board@1.2/soc_x/core-y": "custom_board_rev_1.2_soc_x_core-y"}
        for target, expected in names.items():
            with self.subTest(target=target):
                descriptor = default_overlay(target)
                self.assertEqual(descriptor["target"], target)
                self.assertEqual(descriptor["path"], f"overlays/{expected}.overlay")
        one = default_overlay("b" * 220 + "/soc_one")
        two = default_overlay("b" * 220 + "/soc_two")
        self.assertLess(len(Path(one["path"]).name), 255)
        self.assertNotEqual(one["path"], two["path"])
        for target in (None, "", "../board", "board/../chip", "board//chip", "board@/chip", "board;command", "board\nchip"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                default_overlay(target)

    def test_existing_overlay_or_archive_paths_are_never_overwritten(self):
        cmake = "cmake_minimum_required(VERSION 3.20.0)\nfind_package(Zephyr REQUIRED)\n"
        path = default_overlay(self.target)["path"]
        for collision in (path, "overlays", ARCHIVED_CMAKE, "upstream/original"):
            with self.subTest(collision=collision), self.assertRaisesRegex(ValueError, "replace an existing"):
                add_default_overlay({"CMakeLists.txt": cmake, collision: "existing evidence"}, self.target, archive_cmake=True)

    def test_cmake_insertion_preserves_original_bytes_and_precedes_existing_overlay_settings(self):
        original = ("# upstream license\ncmake_minimum_required(VERSION 3.20.0) # supported comment\n"
                    'set(DTC_OVERLAY_FILE "app.overlay")\nfind_package(Zephyr REQUIRED)\n')
        contents, descriptor = add_default_overlay({"CMakeLists.txt": original, "app.overlay": "/* hardware */\n"},
                                                    self.target, archive_cmake=True)
        self.assertEqual(contents[ARCHIVED_CMAKE], original)
        self.assertEqual(contents["app.overlay"], "/* hardware */\n")
        self.assertEqual(hashlib.sha256(contents[descriptor["path"]].encode()).hexdigest(), descriptor["sha256"])
        self.assertLess(contents["CMakeLists.txt"].index("ZEPHYR_UTILS_DEFAULT_OVERLAY"),
                        contents["CMakeLists.txt"].index('set(DTC_OVERLAY_FILE "app.overlay")'))
        self.assertIn(self.target, contents[descriptor["path"]])
        self.assertNotIn("gpios", contents[descriptor["path"]])
        with self.assertRaisesRegex(ValueError, "CMake layout"):
            add_default_overlay(contents, self.target)

    def test_minimal_template_v2_includes_overlay_and_v1_is_retained(self):
        for template_id in ("minimal-c", "minimal-cpp"):
            with self.subTest(template=template_id):
                current = select_template(template_id)
                self.assertEqual(current["version"], 2)
                profile = {"target": self.target}
                files = render_template(current, "example", profile, self.recipe)
                descriptor = default_overlay(self.target)
                self.assertEqual(set(files), template_paths(current, profile))
                self.assertIn(descriptor["path"], files)
                self.assertIn(descriptor["path"], files["README.md"])
                legacy = {"id": template_id, "version": 1}
                self.assertEqual(validate_template(legacy), legacy)
                original = render_template(legacy, "example", profile, self.recipe)
                self.assertEqual(set(original), template_paths(legacy))
                self.assertNotIn("ZEPHYR_UTILS_DEFAULT_OVERLAY", original["CMakeLists.txt"])
                self.assertIn("template, version 1", original["README.md"])
        with self.assertRaises(ValueError):
            validate_template({"id": "local-sample", "version": 2})

    def test_sample_provenance_preserves_catalogue_identity_and_original_cmake(self):
        zephyr = self.root / "zephyr"
        sample = sample_fixture(zephyr)
        snapshot = {"zephyr": {"path": str(zephyr)}}
        profile = {**snapshot, "target": self.target}
        base = preview_sample(snapshot, "hello_world", self.recipe)
        current = preview_sample(snapshot, "hello_world", self.recipe, profile=profile)
        files = {item["path"]: item for item in current["files"]}
        provenance = current["provenance"]
        self.assertEqual(provenance["schema_version"], 3)
        self.assertEqual(provenance["source_fingerprint"], base["provenance"]["fingerprint"])
        self.assertEqual(provenance["files"], base["provenance"]["files"])
        self.assertEqual(provenance["default_overlay"], default_overlay(self.target))
        self.assertEqual(files[ARCHIVED_CMAKE]["content"], (sample / "CMakeLists.txt").read_text())
        for item in provenance["overlay_files"]:
            self.assertEqual(item["sha256"], files[item["path"]]["sha256"])
        plan = {"profile": profile, "recipe": self.recipe, "sample": provenance, "files": current["files"]}
        self.assertEqual(validate_sample_plan(plan), provenance)
        legacy = {**plan, "sample": base["provenance"], "files": base["files"]}
        self.assertEqual(validate_sample_plan(legacy), base["provenance"])
        for change in (lambda value: value["profile"].update(target="different/chip"),
                       lambda value: value["sample"]["default_overlay"].update(path="other.overlay"),
                       lambda value: value["files"][0].update(content="changed")):
            changed = deepcopy(plan)
            change(changed)
            with self.assertRaises(ValueError):
                validate_sample_plan(changed)

    def test_rgb_sample_retains_hardware_overlay_and_schema_two_replay(self):
        zephyr = self.root / "zephyr"
        sample_fixture(zephyr, "basic/blinky")
        known = rgb_fixture(zephyr)
        snapshot = {"zephyr": {"path": str(zephyr)}}
        board = "esp32c3_devkitc"
        profile = {**snapshot, "target": board + "/esp32c3",
                   "board_id": hashlib.sha256(f"{zephyr}/boards/espressif/{board}/board.yml:{board}".encode()).hexdigest()[:20]}
        with patch.object(sample_adaptations, "_KNOWN_INPUTS", known):
            legacy = preview_sample(snapshot, "basic/blinky", self.recipe, profile=profile, provenance_version=2)
            current = preview_sample(snapshot, "basic/blinky", self.recipe, profile=profile)
            original_files = {item["path"]: item["content"] for item in legacy["files"]}
            files = {item["path"]: item["content"] for item in current["files"]}
            self.assertEqual(legacy["provenance"]["schema_version"], 2)
            self.assertEqual(current["provenance"]["schema_version"], 3)
            self.assertEqual(files["app.overlay"], original_files["app.overlay"])
            self.assertEqual(files[ARCHIVED_CMAKE], original_files["CMakeLists.txt"])
            self.assertIn(default_overlay(profile["target"])["path"], files)
            for result in (legacy, current):
                plan = {"profile": profile, "recipe": self.recipe, "sample": result["provenance"], "files": result["files"]}
                self.assertEqual(validate_sample_plan(plan), plan["sample"])


@unittest.skipUnless(shutil.which("cmake"), "CMake is required for generated overlay-selection behavior checks")
class DefaultOverlayCmakeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="default-overlay-cmake-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "source app"
        self.source.mkdir()
        self.target = "custom_board@1.2/chip/core"
        self.fake_zephyr = self.root / "package"
        self.fake_zephyr.mkdir()
        # Model Zephyr's automatic DTC cache and its ordered final base/extra lists.
        # The generator block is evaluated by CMake itself, including subsequent runs.
        (self.fake_zephyr / "ZephyrConfig.cmake").write_text(
            'if(NOT DEFINED DTC_OVERLAY_FILE)\n'
            '  set(DTC_OVERLAY_FILE "${CMAKE_CURRENT_SOURCE_DIR}/app.overlay" CACHE STRING "Automatic overlay")\n'
            'endif()\n'
            'file(WRITE "${CMAKE_BINARY_DIR}/selected.txt" "${DTC_OVERLAY_FILE}\\n${EXTRA_DTC_OVERLAY_FILE}")\n')
        contents, self.descriptor = add_default_overlay({"CMakeLists.txt":
            "cmake_minimum_required(VERSION 3.20.0)\nfind_package(Zephyr REQUIRED)\nproject(synthetic NONE)\n",
            "app.overlay": "/* upstream hardware */\n"}, self.target)
        for name, content in contents.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)

    def configure(self, build, *args, target=None):
        subprocess.run([shutil.which("cmake"), "-S", str(self.source), "-B", str(build),
                        f"-DZephyr_DIR={self.fake_zephyr}", f"-DBOARD={target or self.target}", *args],
                       check=True, capture_output=True, text=True, timeout=30)
        base, extra = (build / "selected.txt").read_text().split("\n", 1)
        return base.split(";") if base else [], extra.split(";") if extra else []

    def test_automatic_preserves_hardware_overlay_and_default_on_reconfigure(self):
        build = self.root / "automatic"
        expected = ([str(self.source / "app.overlay")], [str(self.source / self.descriptor["path"])])
        self.assertEqual(self.configure(build), expected)
        self.assertEqual(self.configure(build), expected)
        self.assertEqual(self.configure(build, target="other/chip")[1], [])

    def test_append_preserves_requested_order_after_default_and_replace_omits_default(self):
        default = str(self.source / self.descriptor["path"])
        append = self.root / "append"
        self.assertEqual(self.configure(append, "-DEXTRA_DTC_OVERLAY_FILE=one.overlay;two.overlay")[1],
                         [default, "one.overlay", "two.overlay"])
        self.assertEqual(self.configure(append)[1], [default, "one.overlay", "two.overlay"])
        self.assertEqual(self.configure(self.root / "replace", "-DDTC_OVERLAY_FILE=one.overlay;two.overlay"),
                         (["one.overlay", "two.overlay"], []))
        self.assertEqual(self.configure(self.root / "replace-empty", "-DDTC_OVERLAY_FILE="), ([], []))

    def test_existing_build_can_explicitly_disable_default(self):
        build = self.root / "existing"
        self.assertTrue(self.configure(build)[1])
        self.assertEqual(self.configure(build, "-DZEPHYR_UTILS_DEFAULT_OVERLAY=OFF")[1], [])

    def test_extending_a_moved_intact_block_preserves_it_and_both_targets_configure(self):
        contents = {str(path.relative_to(self.source)): path.read_text()
                    for path in self.source.rglob("*") if path.is_file()}
        minimum, rest = contents["CMakeLists.txt"].split("\n", 1)
        previous_block, remainder = rest.split("find_package", 1)
        # Moving the existing block is valid CMake and does not edit its bytes.
        contents["CMakeLists.txt"] = previous_block + minimum + "\nfind_package" + remainder
        updated, second = extend_default_overlays(contents, "other/chip", [self.descriptor])
        self.assertIn(previous_block, updated["CMakeLists.txt"])
        self.assertTrue(updated["CMakeLists.txt"].startswith(previous_block + minimum + "\n"))
        for name, content in updated.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.assertEqual(self.configure(self.root / "original-target")[1], [str(self.source / self.descriptor["path"])])
        self.assertEqual(self.configure(self.root / "second-target", target="other/chip")[1], [str(self.source / second["path"])])
