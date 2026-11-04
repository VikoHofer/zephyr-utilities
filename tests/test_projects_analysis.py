"""Environment refresh and storage behaviour using local synthetic metadata."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects.analyzer import (
    analyze_environment, catalogue_changes, fingerprint, metadata_inputs,
)
from zephyr_utilities.features.projects.service import EnvironmentStore
from zephyr_utilities.features.projects.hardware import discover_hardware


class EnvironmentAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.zephyr = self.workspace / "zephyr"
        self.zephyr.mkdir(parents=True)
        self.write("soc/vendor/soc.yml", "socs:\n  - name: chip\n")
        self.write("boards/vendor/demo/board.yml", "board:\n  name: demo\n  socs:\n    - name: chip\n")
        self.options = {"board_roots": [], "soc_roots": [], "sdk_paths": [], "force": False}

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, contents):
        path = self.zephyr / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
        return path

    def workspace_info(self, *_args, **_kwargs):
        roots = [{"path": str(self.zephyr), "origin": "Zephyr"}]
        return {"workspace": {"path": str(self.workspace)},
                "zephyr": {"path": str(self.zephyr), "version": "4.4.2"},
                "roots": {"boards": roots, "socs": roots},
                "tools": [], "modules": [], "inputs": [], "environment": {},
                "python": "synthetic-python", "diagnostics": []}

    def analyze(self, previous=None, force=False):
        sdk = {"sdks": [], "selection": {}, "inputs": [], "diagnostics": []}
        with patch("zephyr_utilities.features.projects.analyzer.inspect_workspace", side_effect=self.workspace_info), \
                patch("zephyr_utilities.features.projects.analyzer.discover_sdks", return_value=sdk):
            return analyze_environment(str(self.workspace), {**self.options, "force": force}, previous=previous)

    def test_unchanged_refresh_reuses_observation(self):
        first = self.analyze()
        with patch("zephyr_utilities.features.projects.analyzer.discover_hardware", side_effect=AssertionError("unnecessary parse")):
            second = self.analyze(previous=first)
        self.assertTrue(second["cache"]["hit"])
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["boards"], first["boards"])

    def test_added_and_dirty_metadata_invalidate_catalogue(self):
        first = self.analyze()
        self.write("boards/vendor/new/board.yml", "board:\n  name: added\n  socs:\n    - name: chip\n")
        self.write("boards/vendor/demo/board.yml", "board:\n  name: demo\n  vendor: edited\n  socs:\n    - name: chip\n")
        second = self.analyze(previous=first)
        self.assertFalse(second["cache"]["hit"])
        self.assertEqual([item["name"] for item in second["boards"]], ["added", "demo"])
        self.assertTrue(second["changes"]["added"])
        self.assertTrue(second["changes"]["changed"])

    def test_fingerprint_does_not_read_a_symlink_target(self):
        outside = self.root / "private.yml"
        outside.write_text("private: content\n")
        link = self.zephyr / "boards/link.yml"
        link.symlink_to(outside)
        read_bytes = Path.read_bytes

        def guarded_read(path):
            self.assertNotEqual(path, outside, "Fingerprint followed an unrelated symlink target")
            return read_bytes(path)

        with patch.object(Path, "read_bytes", guarded_read):
            fingerprint([str(link)])

    def test_symlinked_tree_root_does_not_enumerate_target_metadata(self):
        outside = self.root / "private-boards"
        outside.mkdir()
        (outside / "board.yml").write_text("private: metadata\n")
        link = self.zephyr / "linked-boards"
        link.symlink_to(outside, target_is_directory=True)
        paths, warnings = metadata_inputs([link])
        self.assertNotIn(str(link / "board.yml"), paths)
        self.assertTrue(warnings)

    def test_hardware_adapter_change_invalidates_cached_interpretation(self):
        first = self.analyze()
        with patch("zephyr_utilities.features.projects.analyzer.HARDWARE_ADAPTER_VERSION", "changed-adapter"):
            second = self.analyze(previous=first)
        self.assertFalse(second["cache"]["hit"])
        self.assertNotEqual(second["cache_key"], first["cache_key"])

    def test_incomplete_fingerprints_are_not_reused(self):
        with patch("zephyr_utilities.features.projects.analyzer.MAX_INPUTS", 2):
            first = self.analyze()
            second = self.analyze(previous=first)
        self.assertEqual(first["status"], "partial")
        self.assertFalse(second["cache"]["hit"])

    def test_deep_metadata_changes_are_observed_or_cache_is_disabled(self):
        relative = "boards/" + "/".join(f"nested{i}" for i in range(14)) + "/board.yml"
        path = self.write(relative, "board:\n  name: deep\n  socs:\n    - name: chip\n")
        first = self.analyze()
        path.write_text("board:\n  name: deep\n  vendor: changed\n  socs:\n    - name: chip\n")
        second = self.analyze(previous=first)
        self.assertFalse(second["cache"]["hit"])
        self.assertEqual(next(board for board in second["boards"] if board["name"] == "deep")["vendor"], "changed")

    def test_stale_then_settled_observation_is_preserved_separately(self):
        # A scan beginning before an input's timestamp is conservatively stale.
        with patch("zephyr_utilities.features.projects.analyzer.time.time_ns", return_value=0):
            stale = self.analyze()
        self.assertEqual(stale["status"], "stale")
        store = EnvironmentStore(self.root / "observations")
        store.save(stale)
        settled = self.analyze(previous=stale, force=True)
        self.assertEqual(settled["status"], "complete")
        store.save(settled)
        self.assertEqual(store.latest(str(self.workspace))["status"], "complete")
        observations = [json.loads(path.read_text()) for path in (store.root / "snapshots").glob("*.json")]
        self.assertEqual({value["status"] for value in observations}, {"stale", "complete"})

    def test_metadata_added_during_discovery_marks_result_stale(self):
        def concurrent_addition(*args, **kwargs):
            result = discover_hardware(*args, **kwargs)
            self.write("boards/vendor/new/board.yml", "board:\n  name: concurrent\n  socs:\n    - name: chip\n")
            return result

        with patch("zephyr_utilities.features.projects.analyzer.discover_hardware", side_effect=concurrent_addition):
            first = self.analyze()
        self.assertEqual(first["status"], "stale")
        second = self.analyze(previous=first)
        self.assertFalse(second["cache"]["hit"])
        self.assertIn("concurrent", [board["name"] for board in second["boards"]])

    def test_change_report_keeps_equal_version_sdk_installations_distinct(self):
        first = {"sdks": [{"id": "first", "version": "1.0", "path": "/sdk-a"},
                          {"id": "second", "version": "1.0", "path": "/sdk-b"}]}
        second = deepcopy(first)
        second["sdks"][1]["version"] = "1.1"
        changes = catalogue_changes(first, second)
        self.assertEqual(changes, {"added": [], "removed": [], "changed": ["sdks: second"]})

    def test_metadata_enumeration_includes_addition_detection(self):
        inputs, warnings = metadata_inputs([self.zephyr / "boards"])
        first, _, _ = fingerprint(inputs)
        self.write("boards/vendor/new/board.yml", "board: {}\n")
        second, _, _ = fingerprint(inputs)
        self.assertNotEqual(first, second)
        self.assertFalse(warnings)


class EnvironmentStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = EnvironmentStore(self.root / "environments")
        self.workspace = str(self.root / "workspace")
        self.snapshot = {"schema_version": 1, "id": "a" * 32,
                         "workspace": {"path": self.workspace}, "status": "complete"}

    def tearDown(self):
        self.temp.cleanup()

    def test_roundtrip_and_other_workspace_is_independent(self):
        self.store.save(self.snapshot)
        self.assertEqual(self.store.latest(self.workspace), self.snapshot)
        self.assertIsNone(self.store.latest(str(self.root / "other")))

    def test_future_index_schema_is_preserved_and_blocks_writes(self):
        self.store.save(self.snapshot)
        path = next((self.store.root / "workspaces").glob("*.json"))
        future = {"schema_version": 99, "snapshot_id": "a" * 32, "future": "data"}
        path.write_text(json.dumps(future))
        before = {file: file.read_bytes() for file in self.store.root.rglob("*.json")}
        with self.assertRaisesRegex(ValueError, "schema"):
            self.store.save({**self.snapshot, "id": "b" * 32})
        self.assertEqual(before, {file: file.read_bytes() for file in self.store.root.rglob("*.json")})

    def test_future_snapshot_schema_is_preserved(self):
        self.store.save(self.snapshot)
        path = self.store.root / "snapshots" / ("a" * 32 + ".json")
        future = {**self.snapshot, "schema_version": 99}
        path.write_text(json.dumps(future))
        with self.assertRaisesRegex(ValueError, "schema"):
            self.store.latest(self.workspace)
        self.assertEqual(json.loads(path.read_text()), future)

    def test_workspace_identity_mismatch_is_rejected(self):
        self.store.save(self.snapshot)
        path = self.store.root / "snapshots" / ("a" * 32 + ".json")
        path.write_text(json.dumps({**self.snapshot, "workspace": {"path": "other"}}))
        with self.assertRaisesRegex(ValueError, "another workspace"):
            self.store.latest(self.workspace)

    def test_snapshot_symlink_is_not_followed(self):
        self.store.save(self.snapshot)
        path = self.store.root / "snapshots" / ("a" * 32 + ".json")
        target = self.root / "elsewhere.json"
        target.write_text(json.dumps(self.snapshot))
        path.unlink()
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.latest(self.workspace)

    def test_malformed_workspace_record_is_reported_without_overwrite(self):
        self.store.save(self.snapshot)
        path = self.store.root / "snapshots" / ("a" * 32 + ".json")
        malformed = {**self.snapshot, "workspace": []}
        path.write_text(json.dumps(malformed))
        with self.assertRaises(ValueError):
            self.store.latest(self.workspace)
        self.assertEqual(json.loads(path.read_text()), malformed)

    def test_existing_observation_cannot_be_replaced_by_same_identifier(self):
        self.store.save(self.snapshot)
        with self.assertRaises(ValueError):
            self.store.save({**self.snapshot, "status": "stale"})
        self.assertEqual(self.store.latest(self.workspace), self.snapshot)

    def test_invalid_identity_cannot_escape_storage(self):
        with self.assertRaises(ValueError):
            self.store.save({**self.snapshot, "id": "../../outside"})
        self.assertFalse((self.root / "outside.json").exists())


if __name__ == "__main__":
    unittest.main()
