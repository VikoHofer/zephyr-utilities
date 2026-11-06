"""Presentation persistence cannot mutate source or bypass current graph guards."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.layout import LayoutStore

import test_devicetree_peripherals as fixtures


class LayoutStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = LayoutStore(self.root)
        self.scope = {"workspace": "/workspace", "path": "/workspace/app/app.overlay"}
        self.points = {"node:/i2c/sensor@76": {"x": 1, "y": 2}}

    def test_restart_and_conflict_do_not_overwrite_newer_layout(self):
        self.assertEqual(self.store.read(self.scope)["revision"], 0)
        value = self.store.save(self.scope, 0, self.points)
        other = LayoutStore(self.root)
        self.assertEqual(other.read(self.scope), value)
        with self.assertRaisesRegex(ValueError, "changed"):
            other.save(self.scope, 0, {})
        self.assertEqual(other.read(self.scope), value)
        self.assertEqual(other.save(self.scope, 1, self.points), value)
        self.assertEqual(other.read({**self.scope, "path": "/workspace/app/other.overlay"})["positions"], {})

    def test_unknown_or_malformed_schema_is_preserved(self):
        value = self.store.save(self.scope, 0, self.points)
        path = self.store.root / (value["layout_id"] + ".json")
        for bad in ({**value, "schema_version": 2}, {**value, "revision": True}, {**value, "scope": {}}, {**value, "positions": []}):
            with self.subTest(bad=bad):
                path.write_text(json.dumps(bad))
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    self.store.save(self.scope, 1, {})
                self.assertEqual(path.read_bytes(), before)

    def test_invalid_positions_fail_before_storage(self):
        for bad in ({"node:/a": {"x": True, "y": 0}}, {"node:/a": {"x": 1.5, "y": 0}},
                    {"node:/a": {"x": 32, "y": 0}}, {"node:/a": {"x": 0, "y": -1}},
                    {"../../outside": {"x": 0, "y": 0}}, {"node:/a": {"x": 0, "y": 0, "z": 0}},
                    {f"node:/{i}": {"x": 0, "y": 0} for i in range(257)}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.save(self.scope, 0, bad)
        self.assertFalse(self.store.root.exists())

    def test_failed_replacement_retains_previous_record(self):
        value = self.store.save(self.scope, 0, self.points)
        with patch("zephyr_utilities.features.devicetree.layout.os.replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                self.store.save(self.scope, 1, {})
        self.assertEqual(self.store.read(self.scope), value)
        self.assertEqual(len(list(self.store.root.iterdir())), 1)

    def test_symlink_directory_and_record_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.store.root.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.store.save(self.scope, 0, self.points)
        self.assertEqual(list(outside.iterdir()), [])
        self.store.root.unlink()
        self.store.root.mkdir()
        destination = outside / "record"
        destination.write_text("Keep")
        (self.store.root / (self.store.identity(self.scope) + ".json")).symlink_to(destination)
        with self.assertRaises(OSError):
            self.store.save(self.scope, 0, self.points)
        self.assertEqual(destination.read_text(), "Keep")


class LayoutServiceTests(unittest.TestCase):
    setUp = fixtures.PeripheralTests.setUp
    binding = fixtures.PeripheralTests.binding
    guard = fixtures.PeripheralTests.guard

    def load_layout(self):
        self.modules = self.app.context.services["devicetree_modules"]
        self.layout = self.app.context.services["devicetree_layout"]
        self.schema = self.modules.inspect(self.guard())
        self.body = self.guard(schema_id=self.schema["schema_id"])
        return self.layout.read(self.body)

    def test_coordinates_do_not_change_source_history_or_overlay(self):
        initial = self.load_layout()
        node = next(node for node in self.schema["graph"]["nodes"] if node.get("key"))
        points = {node["key"]: {"x": 2, "y": 1}}
        request = {**self.binding(), "draft_id": self.draft["id"]}
        before = self.drafts.get(request)
        result = self.layout.save({**self.body, "expected_layout_revision": initial["revision"], "positions": points})
        self.assertEqual(result["positions"], points)
        self.assertEqual(self.drafts.get(request), before)
        self.assertEqual(self.overlay.read_text(), fixtures.SOURCE)
        self.assertEqual(self.load_layout()["positions"], points)

    def test_unknown_node_and_stale_draft_or_schema_cannot_save(self):
        self.load_layout()
        with self.assertRaisesRegex(ValueError, "current module graph"):
            self.layout.save({**self.body, "expected_layout_revision": 0, "positions": {"node:/missing": {"x": 0, "y": 0}}})
        self.draft = self.drafts.source(self.guard(text=fixtures.SOURCE + "\n/* New revision */\n"))
        with self.assertRaises(ValueError):
            self.layout.save({**self.body, "expected_layout_revision": 0, "positions": {}})
        with self.assertRaises(ValueError):
            self.layout.read(self.guard(schema_id=self.schema["schema_id"]))

    def test_new_context_for_same_document_keeps_layout(self):
        self.load_layout()
        key = next(node["key"] for node in self.schema["graph"]["nodes"] if node.get("key"))
        saved = self.layout.save({**self.body, "expected_layout_revision": 0, "positions": {key: {"x": 3, "y": 2}}})
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})
        self.assertEqual(self.load_layout(), saved)
