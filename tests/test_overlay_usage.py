"""Overlay labels distinguish expected inputs, captured inputs and missing evidence."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from zephyr_utilities.features.projects.builds import capture_context
from zephyr_utilities.features.projects.default_overlay import add_default_overlay
from zephyr_utilities.features.projects.overlay_usage import overlay_usage


SIMPLE_CMAKE = ("cmake_minimum_required(VERSION 3.20.0)\n"
                "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n"
                "project(application)\ntarget_sources(app PRIVATE src/main.c)\n")


class OverlayUsageTests(unittest.TestCase):
    def setUp(self):
        self.source = "/workspace/application"
        self.profile = {"schema_version": 1, "target": "board/chip/cpu"}

    def files(self, *paths, cmake=SIMPLE_CMAKE):
        return [{"path": "CMakeLists.txt", "content": cmake}, *paths]

    def report(self, files, **kwargs):
        return overlay_usage(self.profile, self.source, files=files, **kwargs)

    def rows(self, result):
        return {entry["path"]: entry for entry in result["files"]}

    def context(self, paths, **changes):
        return {"configured": True, "source_dir": self.source, "requested_target": self.profile["target"],
                "devicetree": {"files_observed": True, "files": paths, "dependencies": paths,
                               "dependency_coverage": "complete"}, **changes}

    def fragments(self, mode, paths):
        self.profile.update(schema_version=2, fragments={"schema_version": 1,
            "kconfig": {"mode": "auto", "files": []}, "overlays": {"mode": mode, "files": paths}})

    def test_confirmed_inputs_use_captured_order_and_separate_external_board_sources(self):
        files = self.files("app.overlay", "boards/other.overlay", "upstream/board.overlay", "tuning.overlay")
        captured = ["/zephyr/boards/vendor/board.dts", self.source + "/tuning.overlay",
                    "/module/board-extension.overlay", self.source + "/app.overlay"]
        result = self.report(files, context=self.context(captured))
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual([row["path"] for row in result["files"][:2]], ["tuning.overlay", "app.overlay"])
        self.assertEqual([row["role"] for row in result["board_files"]], ["board_source", "external_overlay"])
        rows = self.rows(result)
        self.assertEqual(rows["boards/other.overlay"]["state"], "not_used")
        self.assertIn("Board-specific", rows["boards/other.overlay"]["reason"])
        self.assertEqual(rows["upstream/board.overlay"]["role"], "preserved")
        self.assertEqual(rows["upstream/board.overlay"]["state"], "not_used")

    def test_captured_overlay_not_in_catalogue_is_still_reported(self):
        result = self.report([], context=self.context([self.source + "/newly-added.overlay"]))
        self.assertEqual(self.rows(result)["newly-added.overlay"]["state"], "active")

    def test_transitive_overlays_are_active_and_headers_do_not_fill_board_list(self):
        direct = ["/zephyr/board.dts", self.source + "/app.overlay"]
        context = self.context(direct)
        context["devicetree"]["dependencies"] = [*direct, self.source + "/pins.overlay", "/module/included.overlay",
                                                   "/zephyr/chip.dtsi", "/zephyr/include/header.h"]
        result = self.report(self.files("app.overlay", "pins.overlay", "unused.overlay"), context=context)
        rows = self.rows(result)
        self.assertEqual(rows["pins.overlay"]["state"], "active")
        self.assertIn("transitive", rows["pins.overlay"]["reason"])
        self.assertEqual(rows["unused.overlay"]["state"], "not_used")
        self.assertEqual([row["path"] for row in result["board_files"]], ["/zephyr/board.dts", "/module/included.overlay"])

    def test_without_complete_include_coverage_non_direct_overlays_are_unknown(self):
        context = self.context([self.source + "/app.overlay"])
        for coverage in ("unknown", "partial"):
            context["devicetree"]["dependency_coverage"] = coverage
            result = self.report(self.files("app.overlay", "pins.overlay", "boards/other.overlay"), context=context)
            rows = self.rows(result)
            self.assertEqual(rows["app.overlay"]["state"], "active")
            self.assertEqual(rows["pins.overlay"]["state"], "unknown")
            self.assertEqual(rows["boards/other.overlay"]["state"], "unknown")
            self.assertIn("transitive use is unverified", rows["pins.overlay"]["reason"])
            self.assertEqual(result["scope"], "direct_inputs")

    def test_missing_invalid_and_legacy_empty_capture_never_claim_no_overlays(self):
        values = ({}, {"files": []}, {"files_observed": False, "files": []},
                  {"files_observed": True, "files": "bad"},
                  {"files_observed": True, "files": ["relative.overlay"]},
                  {"files_observed": True, "files": ["/path/../escape.overlay"]})
        for tree in values:
            with self.subTest(tree=tree):
                result = self.report(self.files("app.overlay"), context=self.context([], devicetree=tree))
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(self.rows(result)["app.overlay"]["state"], "unknown")
        result = self.report(self.files("app.overlay"), context=self.context([]))
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.rows(result)["app.overlay"]["state"], "not_used")
        result = self.report(self.files("app.overlay"), context=self.context([], devicetree={"files": [self.source + "/app.overlay"]}))
        self.assertEqual(result["status"], "confirmed")

    def test_wrong_source_target_and_unconfigured_capture_do_not_apply(self):
        for changes in ({"source_dir": "/different/app"}, {"requested_target": "other/chip"}, {"configured": False}):
            with self.subTest(changes=changes):
                result = self.report(self.files("app.overlay"), context=self.context([self.source + "/app.overlay"], **changes))
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(self.rows(result)["app.overlay"]["state"], "unknown")

    def test_expected_standard_search_selects_soc_and_exact_board_before_app(self):
        result = self.report(self.files("socs/chip_cpu.overlay", "boards/board_chip_cpu.overlay", "app.overlay",
                                        "boards/other.overlay", "upstream/board.overlay"))
        self.assertEqual(result["status"], "expected")
        rows = self.rows(result)
        for name in ("socs/chip_cpu.overlay", "boards/board_chip_cpu.overlay"):
            self.assertEqual(rows[name]["state"], "expected")
        for name in ("app.overlay", "boards/other.overlay", "upstream/board.overlay"):
            self.assertEqual(rows[name]["state"], "unknown")
            self.assertIn("Transitive use needs", rows[name]["reason"])

    def test_created_hyphenated_project_name_keeps_standard_overlay_prediction(self):
        cmake = SIMPLE_CMAKE.replace("project(application)", "project(sensor-node)")
        result = self.report(self.files("app.overlay", cmake=cmake))
        self.assertEqual(result["status"], "expected")
        self.assertEqual(self.rows(result)["app.overlay"]["state"], "expected")
        self.fragments("replace", ["pins.overlay"])
        result = self.report(self.files("pins.overlay", cmake=cmake))
        self.assertEqual(result["status"], "expected")
        self.assertEqual(self.rows(result)["pins.overlay"]["state"], "expected")
        self.assertNotIn("active", {row["state"] for row in result["files"]})

    def test_revision_and_root_fallback_are_expected_without_claiming_confirmation(self):
        self.profile["target"] = "board@1.2.0/chip/cpu"
        result = self.report(self.files("boards/board_chip_cpu.overlay", "boards/board_chip_cpu_1_2_0.overlay", "app.overlay"))
        self.assertEqual(self.rows(result)["boards/board_chip_cpu_1_2_0.overlay"]["state"], "expected")
        self.assertEqual(self.rows(result)["boards/board_chip_cpu.overlay"]["state"], "expected")
        result = self.report(self.files("board_chip_cpu.overlay", "app.overlay"))
        self.assertEqual(self.rows(result)["board_chip_cpu.overlay"]["state"], "expected")
        self.assertEqual(self.rows(result)["app.overlay"]["state"], "unknown")
        result = self.report(self.files("boards/another.overlay", "app.overlay"))
        self.assertEqual(self.rows(result)["app.overlay"]["state"], "expected")

    def test_shortened_board_names_require_missing_soc_metadata(self):
        result = self.report(self.files("boards/board_cpu.overlay", "app.overlay"))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.rows(result)["boards/board_cpu.overlay"]["state"], "unknown")
        self.assertEqual(self.rows(result)["app.overlay"]["state"], "unknown")

    def test_unknown_or_unavailable_cmake_does_not_claim_automatic_selection(self):
        for files in (["app.overlay"], self.files("app.overlay", cmake=SIMPLE_CMAKE + "include(custom.cmake)\n")):
            result = self.report(files)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(self.rows(result)["app.overlay"]["state"], "unknown")

    def test_append_and_replace_describe_explicit_selection_without_claiming_active(self):
        self.fragments("append", ["tuning.overlay"])
        files = self.files("app.overlay", "tuning.overlay", "unused.overlay")
        result = self.report(files)
        self.assertEqual(self.rows(result)["app.overlay"]["state"], "expected")
        self.assertEqual(self.rows(result)["tuning.overlay"]["state"], "expected")
        self.fragments("replace", ["tuning.overlay"])
        result = self.report(files)
        self.assertEqual(self.rows(result)["app.overlay"]["state"], "unknown")
        self.assertEqual(self.rows(result)["tuning.overlay"]["state"], "expected")
        result = self.report(self.files("app.overlay", "tuning.overlay", cmake=SIMPLE_CMAKE + "include(custom.cmake)\n"))
        self.assertEqual(result["status"], "unknown")

    def test_verified_default_block_preserves_automatic_inputs_and_respects_exact_target_replace(self):
        contents, default = add_default_overlay({"CMakeLists.txt": SIMPLE_CMAKE, "app.overlay": "// existing"}, self.profile["target"])
        files = [{"path": path, "content": content} for path, content in contents.items()]
        result = self.report(files, default_overlay=default)
        self.assertEqual(result["status"], "expected")
        self.assertEqual(self.rows(result)[default["path"]]["state"], "expected")
        self.assertEqual(self.rows(result)["app.overlay"]["state"], "expected")
        self.fragments("replace", ["app.overlay"])
        result = self.report(files, default_overlay=default)
        self.assertEqual(self.rows(result)[default["path"]]["state"], "unknown")
        self.fragments("auto", [])
        self.profile["target"] = "another/chip"
        result = self.report(files, default_overlay=default)
        self.assertEqual(self.rows(result)[default["path"]]["state"], "unknown")
        self.assertIn("different exact target", self.rows(result)[default["path"]]["reason"])

    def test_default_metadata_only_predicts_default_and_changed_block_stays_unknown(self):
        contents, default = add_default_overlay({"CMakeLists.txt": SIMPLE_CMAKE}, self.profile["target"])
        result = self.report(list(contents), default_overlay=default)
        self.assertEqual(self.rows(result)[default["path"]]["state"], "expected")
        self.assertEqual(result["status"], "unknown")
        contents["CMakeLists.txt"] = contents["CMakeLists.txt"].replace("list(PREPEND", "list(APPEND")
        result = self.report([{"path": path, "content": content} for path, content in contents.items()], default_overlay=default)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.rows(result)[default["path"]]["state"], "unknown")
        changed = {**default, "cmake_block_sha256": "0" * 64}
        result = self.report(list(contents), default_overlay=changed)
        self.assertNotIn("default_overlay", result)

    def test_reporting_is_pure_and_keeps_malformed_inputs_unknown(self):
        files = self.files("app.overlay")
        context = self.context([self.source + "/app.overlay"])
        before = deepcopy((files, context, self.profile))
        self.report(files, context=context)
        self.assertEqual((files, context, self.profile), before)
        for files in (None, "app.overlay", ["../outside.overlay"], [{"path": None}]):
            self.assertEqual(self.report(files)["status"], "unknown")

    def test_context_preserves_presence_of_build_info_device_tree_files(self):
        with tempfile.TemporaryDirectory(prefix="overlay-context-") as name:
            root = Path(name)
            info = root / "build_info.yml"
            for text, observed, paths in (("cmake: {}\n", False, []),
                                         ("cmake:\n  devicetree:\n    files: []\n", True, []),
                                         ("cmake:\n  devicetree:\n    files: bad\n", False, "bad"),
                                         ("cmake:\n  devicetree:\n    files: [/board/base.dts, /application/app.overlay]\n"
                                          "    user-files: [/application/app.overlay]\n    extra-user-files: [/application/tuning.overlay]\n",
                                          True, ["/board/base.dts", "/application/app.overlay"])):
                info.write_text(text)
                context, _, _ = capture_context(self.profile, self.source, root, configured=True)
                self.assertEqual(context["devicetree"]["files_observed"], observed)
                self.assertEqual(context["devicetree"]["files"], paths)
            self.assertEqual(context["devicetree"]["extra_user_files"], ["/application/tuning.overlay"])
            info.unlink()
            outside = root / "outside.yml"
            outside.write_text("cmake:\n  devicetree:\n    files: []\n")
            info.symlink_to(outside)
            context, _, _ = capture_context(self.profile, self.source, root, configured=True)
            self.assertFalse(context["devicetree"]["files_observed"])

    def test_context_captures_bounded_dependency_evidence_and_rejects_incomplete_sources(self):
        with tempfile.TemporaryDirectory(prefix="overlay-dependencies-") as name:
            root = Path(name)
            (root / "zephyr").mkdir()
            (root / "build_info.yml").write_text("cmake:\n  devicetree:\n    files: [/board/base.dts, /workspace/application/app.overlay]\n")
            depfile, pre = root / "zephyr/zephyr.dts.d", root / "zephyr/zephyr.dts.pre"
            good = "empty_file.o: /board/base.dts \\\n /workspace/application/app.overlay pins\\ with\\ spaces.overlay\n"
            depfile.write_text(good)
            pre.write_text('# 1 "/zephyr/include/header.h"\n/dts-v1/; / { value = "/include/example"; };\n')
            context, _, _ = capture_context(self.profile, self.source, root, configured=True)
            self.assertEqual(context["devicetree"]["dependency_coverage"], "complete")
            self.assertIn(self.source + "/pins with spaces.overlay", context["devicetree"]["dependencies"])
            pre.write_text('/include/ "extra.overlay"\n')
            context, _, _ = capture_context(self.profile, self.source, root, configured=True)
            self.assertEqual(context["devicetree"]["dependency_coverage"], "partial")
            pre.write_text("/dts-v1/; / {};\n")
            depfile.write_text("empty_file.o: /board/base.dts\n")
            context, _, _ = capture_context(self.profile, self.source, root, configured=True)
            self.assertEqual(context["devicetree"]["dependency_coverage"], "partial")
            for invalid in ("bad", "target: $(shell command)", "target: /one\nother: /two", "target: /one\\", "x" * (2 * 1024 * 1024 + 1)):
                depfile.write_text(invalid)
                context, _, _ = capture_context(self.profile, self.source, root, configured=True)
                self.assertEqual(context["devicetree"]["dependency_coverage"], "unknown")
            depfile.unlink()
            outside = root / "outside.d"
            outside.write_text(good)
            depfile.symlink_to(outside)
            context, _, _ = capture_context(self.profile, self.source, root, configured=True)
            self.assertEqual(context["devicetree"]["dependency_coverage"], "unknown")


if __name__ == "__main__":
    unittest.main()
