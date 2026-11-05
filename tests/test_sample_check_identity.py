"""Compatibility cache inputs include hardware source bytes beyond catalogue metadata."""

from concurrent.futures import CancelledError
from copy import deepcopy
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects import sample_check_identity as identity


class SampleInputIdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="sample-inputs-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.zephyr = self.root / "zephyr"
        self.module = self.root / "module"
        self.files = ["boards/vendor/board/board.dts", "boards/vendor/board/board-pinctrl.dtsi",
                      "boards/vendor/board/board_defconfig", "dts/vendor/chip.dtsi",
                      "include/zephyr/dt-bindings/pins.h", "drivers/Kconfig.driver", "drivers/CMakeLists.txt",
                      "cmake/configuration.cmake", "scripts/dts/generate.py", "dts/bindings/example.yaml"]
        for path in self.files:
            self.write(self.zephyr / path)
        self.write(self.module / "zephyr/module.yml")
        self.write(self.module / "dts/module.dtsi")
        self.write(self.module / "include/module.h")
        self.profile = {"zephyr": {"path": str(self.zephyr)},
                        "modules": [{"path": str(self.module), "is_module": True, "active": True, "status": "discovered"}],
                        "roots": {"boards": [{"path": str(self.zephyr)}],
                                  "dts": [{"path": str(self.module)}]}}

    def write(self, path, content="synthetic configuration\n"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def guard(self):
        return identity.sample_input_guard(self.profile)

    def test_existing_hardware_headers_and_build_configuration_edits_invalidate(self):
        self.assertEqual(self.guard(), self.guard())
        for path in [*(self.zephyr / name for name in self.files), self.module / "dts/module.dtsi",
                     self.module / "include/module.h", self.module / "zephyr/module.yml"]:
            with self.subTest(path=path):
                before = self.guard()
                path.write_text(path.read_text().replace("synthetic", "different"))
                self.assertNotEqual(self.guard(), before)

    def test_addition_deletion_and_same_bytes_replacement_invalidate(self):
        path = self.zephyr / "dts/optional.dtsi"
        absent = self.guard()
        self.write(path)
        present = self.guard()
        self.assertNotEqual(present, absent)
        replacement = path.with_suffix(".temporary")
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
        self.assertNotEqual(self.guard(), present)
        path.unlink()
        self.assertEqual(self.guard(), absent)

    def test_build_outputs_hidden_caches_and_root_examples_do_not_affect_evidence(self):
        for relative in ("build/zephyr/zephyr.dts", "build-test/zephyr/.config", ".cache/generated.h",
                         "tests/test.dts", "samples/sample/app.overlay", "doc/examples/example.dts",
                         "custom-output/CMakeCache.txt", "custom-output/zephyr/zephyr.dts"):
            self.write(self.zephyr / relative)
        before = self.guard()
        for path in (self.zephyr / "build/zephyr/zephyr.dts", self.zephyr / "custom-output/zephyr/zephyr.dts",
                     self.zephyr / "tests/test.dts"):
            path.write_text("changed output\n")
        self.assertEqual(self.guard(), before)

    def test_custom_hardware_and_module_roots_are_included_without_application_outputs(self):
        custom = self.root / "custom"
        self.profile["roots"]["boards"].append({"path": str(custom)})
        absent = self.guard()
        board = self.write(custom / "boards/demo/demo.dts")
        before = self.guard()
        self.assertNotEqual(before, absent)
        board.write_text("changed board\n")
        self.assertNotEqual(self.guard(), before)
        self.write(custom / "applications/my_app/src/main.c")
        unchanged = self.guard()
        self.write(custom / "applications/my_app/prj.conf", "different application\n")
        self.assertEqual(self.guard(), unchanged)

    def test_explicit_source_root_inside_otherwise_excluded_tests_is_still_inspected(self):
        custom = self.zephyr / "tests/custom"
        self.profile["roots"]["boards"].append({"path": str(custom)})
        board = self.write(custom / "boards/demo/demo.dts")
        before = self.guard()
        board.write_text("changed explicit board\n")
        self.assertNotEqual(self.guard(), before)

    def test_internal_links_track_their_target_and_external_links_reject_partial_evidence(self):
        target = self.write(self.module / "shared/real.h")
        link = self.module / "include/linked.h"
        link.symlink_to(target)
        before = self.guard()
        target.write_text("changed header\n")
        self.assertNotEqual(self.guard(), before)
        link.unlink()
        outside = self.write(self.root / "external.h")
        link.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "leaves the selected"):
            self.guard()

    def test_internal_directory_links_do_not_loop_or_hide_target_changes(self):
        (self.module / "include/shared").symlink_to(self.module / "dts", target_is_directory=True)
        (self.module / "dts/recursive").symlink_to(self.module / "dts", target_is_directory=True)
        before = self.guard()
        (self.module / "dts/module.dtsi").write_text("changed transitive header\n")
        self.assertNotEqual(self.guard(), before)

    def test_scan_limits_cancellation_and_invalid_roots_do_not_produce_partial_guard(self):
        for constant, value in (("MAX_ENTRIES", 1), ("MAX_DIRECTORIES", 1), ("MAX_DEPTH", 1)):
            with self.subTest(constant=constant), patch.object(identity, constant, value):
                with self.assertRaisesRegex(ValueError, "limit|depth"):
                    self.guard()
        cancel = Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            identity.sample_input_guard(self.profile, cancel=cancel)
        profile = deepcopy(self.profile)
        profile["modules"][0]["active"] = False
        with self.assertRaisesRegex(ValueError, "active modules"):
            identity.sample_input_guard(profile)
        profile["zephyr"]["path"] = "relative/path"
        with self.assertRaisesRegex(ValueError, "absolute"):
            identity.sample_input_guard(profile)

    def test_change_between_scans_requires_a_fresh_check(self):
        original = identity._scan
        calls = 0

        def scan(*args):
            nonlocal calls
            records = original(*args)
            calls += 1
            if calls == 1:
                (self.zephyr / self.files[0]).write_text("changed during inspection\n")
            return records

        with patch.object(identity, "_scan", side_effect=scan):
            with self.assertRaisesRegex(ValueError, "changed during inspection"):
                self.guard()

    def test_large_files_are_tracked_without_reading_unbounded_bytes(self):
        large = self.write(self.module / "large.cmake", "a" * (identity.MAX_HASH_FILE_BYTES + 1))
        before = self.guard()
        with large.open("r+b") as stream:
            stream.write(b"b")
        self.assertNotEqual(self.guard(), before)

    def test_hash_budget_falls_back_to_stat_evidence_including_ctime(self):
        path = self.zephyr / self.files[0]
        with patch.object(identity, "MAX_HASH_BYTES", 0):
            before = self.guard()
            info = path.stat()
            path.write_text(path.read_text().replace("synthetic", "different"))
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            self.assertNotEqual(self.guard(), before)
