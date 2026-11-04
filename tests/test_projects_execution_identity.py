"""Execution freshness covers selected metadata, not unrelated SDK search directories."""

from concurrent.futures import CancelledError
from copy import deepcopy
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects.execution_identity import execution_guard
from zephyr_utilities.features.projects import execution_identity


class ExecutionIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.zephyr = self.workspace / "zephyr"
        self.zephyr.mkdir(parents=True)
        self.write(self.workspace / ".west/config", "[manifest]\npath = zephyr\n")
        self.write(self.zephyr / "west.yml", "manifest:\n  projects: []\n")
        self.write(self.zephyr / "VERSION", "VERSION_MAJOR = 4\nVERSION_MINOR = 4\nPATCHLEVEL = 2\n")
        self.write(self.zephyr / "SDK_VERSION", "1.0.1\n")
        self.board_file = self.zephyr / "boards/vendor/demo/board.yml"
        self.write(self.board_file, "board:\n  name: demo\n  socs:\n    - name: chip\n")
        self.write(self.zephyr / "soc/vendor/soc.yml", "socs:\n  - name: chip\n")
        self.module = self.workspace / "modules/lib"
        self.write(self.module / "zephyr/module.yml", "name: lib\n")
        self.snapshot = {"status": "complete", "workspace": {
            "path": str(self.workspace), "west_topdir": str(self.workspace), "manifest_resolved": True,
            "manifest_path": str(self.zephyr / "west.yml")},
            "zephyr": {"path": str(self.zephyr), "version": "4.4.2", "commit": "abc", "dirty": False},
            "tools": [{"name": "cmake", "path": "/usr/bin/cmake", "version": "3.30", "status": "discovered"}],
            "roots": {"boards": [{"path": str(self.zephyr), "origin": "Zephyr checkout"}],
                      "socs": [{"path": str(self.zephyr), "origin": "Zephyr checkout"}]},
            "boards": [{"id": "board-id", "status": "discovered", "name": "demo", "targets": ["demo/chip"]}],
            "sdks": [{"id": "sdk-id", "path": str(self.root / "sdk"), "version": "1.0.1",
                      "status": "discovered", "fingerprint": "sdk-metadata-1",
                      "toolchains": [{"family": "GNU"}]}],
            "modules": [{"name": "lib", "path": str(self.module), "is_module": True,
                         "status": "discovered", "source": str(self.module / "zephyr/module.yml"), "commit": "def"}]}
        self.profile = {"workspace": str(self.workspace), "target": "demo/chip", "board_id": "board-id", "sdk": {"id": "sdk-id"}}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, contents):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)

    def guard(self, snapshot=None):
        return execution_guard(snapshot or self.snapshot, self.profile)

    def test_creating_an_application_does_not_invalidate_the_environment(self):
        before = self.guard()
        self.write(self.workspace / "applications/new/src/main.c", "int main(void) { return 0; }\n")
        self.write(self.workspace / "applications/new/build/attempt/CMakeCache.txt", "generated\n")
        self.write(self.root / "unrelated-sdk-search-entry", "unrelated\n")
        self.assertEqual(self.guard(), before)

    def test_added_and_dirty_board_or_soc_metadata_invalidates_without_new_commit(self):
        before = self.guard()
        self.write(self.board_file, "board:\n  name: demo\n  vendor: changed\n  socs:\n    - name: chip\n")
        after_edit = self.guard()
        self.assertNotEqual(before, after_edit)
        self.write(self.zephyr / "boards/vendor/added/board.yml", "board:\n  name: added\n")
        after_added = self.guard()
        self.assertNotEqual(after_edit, after_added)
        self.write(self.zephyr / "soc/vendor/soc.yml", "socs:\n  - name: another-chip\n")
        self.assertNotEqual(after_added, self.guard())

    def test_manifest_module_metadata_and_root_choices_participate(self):
        before = self.guard()
        self.write(self.module / "zephyr/module.yml", "name: lib\nbuild:\n  settings:\n    board_root: .\n")
        after_module = self.guard()
        self.assertNotEqual(before, after_module)
        self.write(self.zephyr / "west.yml", "manifest:\n  projects: []\n  version: '0.13'\n")
        after_manifest = self.guard()
        self.assertNotEqual(after_module, after_manifest)
        other = deepcopy(self.snapshot)
        other["roots"]["boards"].append({"path": str(self.workspace / "custom"), "origin": "explicit"})
        self.assertNotEqual(after_manifest, self.guard(other))

    def test_module_metadata_introduced_for_a_legacy_module_invalidates(self):
        self.snapshot["modules"][0]["source"] = None
        (self.module / "zephyr/module.yml").unlink()
        self.write(self.module / "zephyr/CMakeLists.txt", "# legacy module\n")
        before = self.guard()
        self.write(self.module / "zephyr/module.yml", "name: lib\n")
        self.assertNotEqual(before, self.guard())

    def test_selected_sdk_fingerprint_and_tool_identity_are_pinned(self):
        before = self.guard()
        other = deepcopy(self.snapshot)
        other["sdks"][0]["fingerprint"] = "sdk-metadata-2"
        self.assertNotEqual(before, self.guard(other))
        other = deepcopy(self.snapshot)
        other["tools"][0]["version"] = "3.31"
        self.assertNotEqual(before, self.guard(other))

    def test_unselected_sdk_installations_do_not_retarget_the_profile(self):
        before = self.guard()
        other = deepcopy(self.snapshot)
        extra = deepcopy(other["sdks"][0])
        extra.update(id="another-sdk", path=str(self.root / "another-sdk"), fingerprint="different")
        other["sdks"].append(extra)
        self.assertEqual(before, self.guard(other))

    def test_selected_board_record_change_invalidates_and_removal_is_actionable(self):
        before = self.guard()
        other = deepcopy(self.snapshot)
        other["boards"][0]["vendor"] = "different"
        self.assertNotEqual(before, self.guard(other))
        other["boards"][0]["targets"] = []
        with self.assertRaisesRegex(ValueError, "target is no longer available"):
            self.guard(other)

    def test_missing_selected_sdk_is_actionable(self):
        self.snapshot["sdks"] = []
        with self.assertRaisesRegex(ValueError, "SDK is no longer available"):
            self.guard()

    def test_incomplete_or_concurrently_changed_input_scan_cannot_be_used(self):
        with patch.object(execution_identity, "metadata_inputs", return_value=([], [{"code": "limit"}])):
            with self.assertRaisesRegex(ValueError, "inspected completely"):
                self.guard()
        real_fingerprint = execution_identity.fingerprint
        calls = 0

        def changing_inputs(*args, **kwargs):
            nonlocal calls
            calls += 1
            result = real_fingerprint(*args, **kwargs)
            if calls == 1:
                self.write(self.board_file, "board: changed during execution guard\n")
            return result

        with patch.object(execution_identity, "fingerprint", side_effect=changing_inputs):
            with self.assertRaisesRegex(ValueError, "changed during inspection"):
                self.guard()

    def test_stale_unresolved_and_cancelled_guards_are_rejected(self):
        self.snapshot["status"] = "stale"
        with self.assertRaisesRegex(ValueError, "stable environment"):
            self.guard()
        self.snapshot["status"] = "complete"
        self.snapshot["workspace"]["manifest_resolved"] = False
        with self.assertRaisesRegex(ValueError, "resolved manifest"):
            self.guard()
        self.snapshot["workspace"]["manifest_resolved"] = True
        cancel = Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            execution_guard(self.snapshot, self.profile, cancel=cancel)
