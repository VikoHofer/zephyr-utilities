"""Core integration checks for discovery, read-only generation and durable capture."""

from concurrent.futures import CancelledError
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest import mock

from zephyr_utilities.memory.discovery import discover_builds, inspect_build, list_folders
from zephyr_utilities.memory.runner import file_hash, generate_reports, _run
from zephyr_utilities.memory.snapshots import SnapshotStore


GENERATOR = '''
import argparse, json
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--kernel', required=True)
p.add_argument('--zephyrbase', required=True)
p.add_argument('--output', required=True)
p.add_argument('--json', required=True)
p.add_argument('--workspace')
p.add_argument('--quiet', action='store_true')
p.add_argument('target')
a=p.parse_args()
if b'no-dwarf' in Path(a.kernel).read_bytes():
    raise AssertionError('ELF file has no DWARF information')
size=64 if a.target=='ram' else 160
report={'total_size':size,'symbols':{'name':'Root','size':size,'identifier':'root','children':[
    {'name':'buffer','identifier':'buffer','size':size,'section':'.bss' if a.target=='ram' else '.text','address':4096}
]}}
Path(a.json).write_text(json.dumps(report))
'''


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.zephyr = self.root / "zephyr"
        script = self.zephyr / "scripts" / "footprint" / "size_report"
        script.parent.mkdir(parents=True)
        script.write_text(GENERATOR)
        self.build = self.make_build(self.root / "project" / "build")
        self.store = SnapshotStore(self.root / "snapshots")

    def tearDown(self):
        self.temporary.cleanup()

    def make_build(self, build, binary="zephyr"):
        (build / "zephyr").mkdir(parents=True)
        (build / "CMakeCache.txt").write_text(
            f"ZEPHYR_BASE:PATH={self.zephyr}\n"
            f"WEST_PYTHON:FILEPATH={sys.executable}\n"
            f"APPLICATION_SOURCE_DIR:PATH={build.parent}\n"
            "BOARD:STRING=test_board/cpu0\n"
        )
        (build / "zephyr" / ".config").write_text(
            f'CONFIG_KERNEL_BIN_NAME="{binary}"\nCONFIG_MAIN_STACK_SIZE=1024\n# CONFIG_LOG is not set\n'
        )
        (build / "zephyr" / f"{binary}.elf").write_bytes(b"\x7fELFfixture")
        (build / "zephyr" / f"{binary}.map").write_text("map evidence")
        return build

    def test_discovers_nested_builds_and_preserves_interpreter_path(self):
        other = self.make_build(self.build / "nested", binary="custom")
        result = discover_builds(self.root / "project")
        self.assertEqual(len(result["builds"]), 2)
        build = inspect_build(other)
        self.assertTrue(build["ready"])
        self.assertEqual(build["elf_path"], str(other / "zephyr" / "custom.elf"))
        self.assertEqual(build["python"], sys.executable)
        self.assertEqual(build["board"], "test_board/cpu0")

    def test_sysbuild_domains_use_declared_paths_and_survive_reinspection(self):
        image = self.make_build(self.root / "sysbuild" / "unrelated-folder")
        (image.parent / "domains.yaml").write_text("domains:\n  - name: app_core\n    build_dir: unrelated-folder\n")
        result = discover_builds(image.parent)
        self.assertEqual(len(result["builds"]), 1)
        self.assertEqual(result["builds"][0]["domain"], "app_core")
        self.assertEqual(inspect_build(image)["domain"], "app_core")
        self.assertEqual(self.store.capture(image)["build"]["domain"], "app_core")

    def test_missing_elf_is_not_ready(self):
        (self.build / "zephyr" / "zephyr.elf").unlink()
        result = inspect_build(self.build)
        self.assertFalse(result["ready"])
        self.assertIn("No final ELF", result["error"])

    def test_unrecognized_folder_and_yaml_error_are_useful(self):
        with self.assertRaisesRegex(ValueError, "not a recognized"):
            inspect_build(self.root)
        (self.build / "build_info.yml").write_text("cmake: [oops")
        result = discover_builds(self.root / "project")
        self.assertEqual(result["builds"], [])
        self.assertIn("metadata", result["warnings"][0])

    def test_bounded_discovery_does_not_follow_symlink_loops(self):
        (self.root / "project" / "loop").symlink_to(self.root / "project", target_is_directory=True)
        self.assertEqual(len(discover_builds(self.root / "project")["builds"]), 1)
        self.assertEqual(discover_builds(self.root / "project", max_depth=0)["builds"], [])

    def test_folder_browser(self):
        (self.root / "project" / ".hidden").mkdir()
        folders = list_folders(self.root / "project")
        self.assertEqual([item["name"] for item in folders["folders"]], ["build"])

    def test_capture_keeps_build_unchanged_and_retains_evidence(self):
        before = {str(path): file_hash(path) for path in self.build.rglob("*") if path.is_file()}
        logs = []
        snapshot = self.store.capture(self.build, "Baseline", emit=logs.append)
        after = {str(path): file_hash(path) for path in self.build.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertEqual(snapshot["reports"]["ram"]["total_size"], 64)
        self.assertEqual(snapshot["reports"]["rom"]["total_size"], 160)
        self.assertEqual(snapshot["config"]["CONFIG_LOG"], "n")
        self.assertTrue(logs)
        retained_elf = self.store.root / snapshot["metadata"]["artifacts"]["elf"]["object"]
        self.assertTrue(retained_elf.is_file())
        (self.build / "zephyr" / "zephyr.elf").unlink()
        loaded = SnapshotStore(self.store.root).load(snapshot["id"])
        self.assertEqual(loaded["reports"], snapshot["reports"])
        self.assertEqual(self.store.list()[0]["totals"], {"ram": 64, "rom": 160})
        self.assertEqual(retained_elf.read_bytes(), b"\x7fELFfixture")

    def test_annotations_do_not_rewrite_immutable_snapshot(self):
        snapshot = self.store.capture(self.build)
        raw = self.store.root / snapshot["id"] / "snapshot.json"
        before = file_hash(raw)
        self.store.rename(snapshot["id"], "Named baseline", "experiment notes")
        self.assertEqual(file_hash(raw), before)
        self.assertEqual(self.store.load(snapshot["id"])["name"], "Named baseline")
        self.assertEqual(self.store.list()[0]["notes"], "experiment notes")

    def test_delete_removes_one_capture_and_preserves_other_evidence_and_build(self):
        before = {str(path): file_hash(path) for path in self.build.rglob("*") if path.is_file()}
        first = self.store.capture(self.build, "Old baseline")
        second = self.store.capture(self.build, "Retained candidate")
        self.store.rename(first["id"], "Old baseline", "Old annotations")
        directory = self.store.root / first["id"]
        self.assertTrue((directory / "raw" / "ram.json").is_file())
        self.assertTrue((directory / "annotations.json").is_file())
        objects = {path.name: file_hash(path) for path in self.store.objects.iterdir()}
        self.assertEqual(self.store.delete(first["id"]), {"id": first["id"], "deleted": True})
        self.assertFalse(directory.exists())
        reopened = SnapshotStore(self.store.root)
        with self.assertRaisesRegex(FileNotFoundError, "Snapshot not found"):
            reopened.load(first["id"])
        self.assertEqual([item["id"] for item in reopened.list()], [second["id"]])
        self.assertEqual(reopened.load(second["id"])["reports"], second["reports"])
        self.assertEqual(objects, {path.name: file_hash(path) for path in self.store.objects.iterdir()})
        self.assertEqual(before, {str(path): file_hash(path) for path in self.build.rglob("*") if path.is_file()})

    def test_delete_rejects_invalid_or_missing_snapshot_ids(self):
        for identifier in ("../objects", "objects", "", None, "f" * 33):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "identifier"):
                self.store.delete(identifier)
        with self.assertRaisesRegex(FileNotFoundError, "Snapshot not found"):
            self.store.delete("f" * 32)
        self.assertTrue(self.store.objects.is_dir())

    def test_delete_does_not_follow_snapshot_or_nested_symlinks(self):
        outside = self.root / "outside"
        outside.mkdir()
        evidence = outside / "keep.txt"
        evidence.write_text("keep this file")
        identifier = "e" * 32
        link = self.store.root / identifier
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.store.delete(identifier)
        self.assertTrue(link.is_symlink())
        raw = {"total_size": 4, "symbols": {"name": "Root", "size": 4}}
        snapshot = self.store.import_reports(raw, None)
        (self.store.root / snapshot["id"] / "external").symlink_to(outside, target_is_directory=True)
        self.store.delete(snapshot["id"])
        self.assertEqual(evidence.read_text(), "keep this file")

    def test_capture_rejects_changed_inputs_without_publishing(self):
        original = generate_reports

        def modify(build, directory, **kwargs):
            result = original(build, directory, **kwargs)
            Path(build["elf_path"]).write_bytes(b"\x7fELFchanged")
            return result

        with mock.patch("zephyr_utilities.memory.snapshots.generate_reports", side_effect=modify):
            with self.assertRaisesRegex(ValueError, "changed during report"):
                self.store.capture(self.build)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(list(self.store.root.glob(".pending-*")), [])

    def test_no_dwarf_failure_does_not_save_snapshot(self):
        (self.build / "zephyr" / "zephyr.elf").write_bytes(b"\x7fELFno-dwarf")
        with self.assertRaisesRegex(ValueError, "DWARF"):
            self.store.capture(self.build)
        self.assertEqual(self.store.list(), [])

    def test_upstream_unclassified_warnings_are_visible_and_logs_retained(self):
        script = self.zephyr / "scripts" / "footprint" / "size_report"
        script.write_text(GENERATOR + '\nprint("WARN: Symbol rom_function section None is not in RAM or ROM.")\n')
        snapshot = self.store.capture(self.build)
        self.assertTrue(any("1 symbols outside RAM/ROM classification" in warning for warning in snapshot["warnings"]))
        self.assertIn("rom_function", (self.store.root / snapshot["id"] / "generation.log").read_text())

    def test_cancel_and_timeout_stop_process(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            self.store.capture(self.build, cancel=cancel)
        with self.assertRaisesRegex(ValueError, "exceeded"):
            _run([sys.executable, "-c", "import time; time.sleep(10)"], str(self.root), timeout=0.1)
        cancel.clear()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        try:
            with self.assertRaises(CancelledError):
                _run([sys.executable, "-c", "import os,time; os.close(1); os.close(2); time.sleep(10)"], str(self.root), cancel=cancel)
        finally:
            timer.join()
        self.assertEqual(self.store.list(), [])

    def test_import_allows_one_report_and_marks_unverified(self):
        raw = {"total_size": 4, "symbols": {"name": "Root", "size": 4, "children": [{"name": "x", "size": 4}]}}
        snapshot = self.store.import_reports(raw, None, name="Imported RAM")
        self.assertIsNone(snapshot["reports"]["rom"])
        self.assertIsNone(snapshot["build"])
        self.assertEqual(snapshot["metadata"]["source"], "import")
        self.assertEqual(self.store.list()[0]["totals"]["ram"], 4)
        self.assertIsNone(self.store.list()[0]["totals"]["rom"])
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.store.import_reports(None, None)

    def test_invalid_ids_and_text_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "identifier"):
            self.store.load("../../etc/passwd")
        with self.assertRaisesRegex(ValueError, "at most"):
            self.store.capture(self.build, name="x" * 161)


if __name__ == "__main__":
    unittest.main()
