"""No-replacement creation preserves absent destinations and external races."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from zephyr_utilities.features.devicetree import publication
from zephyr_utilities.features.devicetree.publication import OverlayPublisher


class OverlayCreationPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.application.mkdir(parents=True)
        self.path = self.application / "board.overlay"
        self.publisher = OverlayPublisher(self.root / "data")
        self.operation = uuid.uuid4().hex
        self.source = b'&uart0 { status = "okay"; };\n'

    def observe(self):
        self.observation = self.publisher.observe(self.workspace, self.path, allow_absent=True)
        return self.observation

    def publish(self, callback=lambda: None):
        return self.publisher.publish(self.workspace, self.path, self.observation, self.source, self.operation, callback)

    def test_absence_observation_is_readonly_and_distinct_from_empty_file(self):
        self.path = self.application / "overlays" / "qualified" / "board.overlay"
        observed = self.observe()
        self.assertFalse(observed["exists"])
        self.assertEqual(observed["missing_directories"], [str(self.application / "overlays"), str(self.path.parent)])
        self.assertEqual(list(self.application.iterdir()), [])
        self.assertFalse(self.publisher.directory.exists())
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"")
        current = self.publisher.observe(self.workspace, self.path, allow_absent=True)
        self.assertTrue(current.get("exists", True))
        self.assertEqual(current["sha256"], observed["sha256"])
        self.assertNotEqual(current, observed)

    def test_empty_creation_is_a_real_publication_and_retry_never_replays(self):
        self.source = b""
        self.observe()
        result = self.publish()
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["operation"], "create")
        self.assertTrue(self.path.is_file())
        self.assertEqual(self.path.read_bytes(), b"")
        self.path.write_bytes(b"external")
        with patch.object(publication, "_noreplace", side_effect=AssertionError("must not repeat")):
            self.assertEqual(self.publish(), result)
        self.assertEqual(self.path.read_bytes(), b"external")
        restarted = OverlayPublisher(self.root / "data")
        self.assertEqual(restarted.publish(self.workspace, self.path, self.observation, self.source,
                                           self.operation, lambda: None)["status"], "uncertain")
        self.assertEqual(self.path.read_bytes(), b"external")
        record = json.loads((self.publisher.directory / (self.operation + ".json")).read_text())
        self.assertEqual(record["schema_version"], 2)
        self.assertFalse(record["original"]["exists"])

    def test_missing_parent_subtree_appears_atomically_with_overlay(self):
        self.path = self.application / "overlays" / "target" / "board.overlay"
        self.observe()
        rename = publication._noreplace

        def checked(parent, left, right):
            self.assertEqual(right, "overlays")
            self.assertFalse((self.application / "overlays").exists())
            staged = self.application / left / "target" / "board.overlay"
            self.assertEqual(staged.read_bytes(), self.source)
            return rename(parent, left, right)

        with patch.object(publication, "_noreplace", checked):
            result = self.publish()
        self.assertEqual(result["status"], "saved", result)
        self.assertEqual(self.path.read_bytes(), self.source)
        self.assertEqual(result["observation"], self.publisher.observe(self.workspace, self.path))

    def test_appeared_destination_even_identical_bytes_is_not_replaced(self):
        self.observe()
        self.path.write_bytes(self.source)
        inode = self.path.stat().st_ino
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.publish()
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertFalse(self.publisher.directory.exists())

    def test_directory_opened_empty_uses_a_fresh_directory_view(self):
        self.path = self.application / "overlays" / "target" / "board.overlay"
        self.observe()
        open_directory, list_directory = os.open, os.listdir
        opened_empty = set()

        def open_with_initial_view(name, flags, *args, **kwargs):
            descriptor = open_directory(name, flags, *args, **kwargs)
            if (flags & os.O_DIRECTORY and str(name) in {f".zephyr-dte-{self.operation}.new", "target"}
                    and not list_directory(descriptor)):
                opened_empty.add(descriptor)
            return descriptor

        def list_with_initial_view(directory):
            if directory in opened_empty:
                return []
            return list_directory(directory)

        with patch.object(publication.os, "open", open_with_initial_view), patch.object(publication.os, "listdir", list_with_initial_view):
            result = self.publish()
        self.assertEqual(result["status"], "saved", result)
        self.assertEqual(self.path.read_bytes(), self.source)

    def test_leaf_race_preserves_external_file_and_staged_candidate(self):
        self.observe()
        rename = publication._noreplace

        def race(parent, left, right):
            self.path.write_bytes(b"external")
            return rename(parent, left, right)

        with patch.object(publication, "_noreplace", race):
            result = self.publish()
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.path.read_bytes(), b"external")
        self.assertEqual(Path(result["recovery"]["staged_path"]).read_bytes(), self.source)

    def test_parent_race_preserves_external_tree(self):
        self.path = self.application / "overlays" / "target" / "board.overlay"
        self.observe()
        rename = publication._noreplace

        def race(parent, left, right):
            self.path.parent.mkdir(parents=True)
            self.path.write_bytes(b"external")
            return rename(parent, left, right)

        with patch.object(publication, "_noreplace", race):
            result = self.publish()
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.path.read_bytes(), b"external")
        self.assertEqual((Path(result["recovery"]["staged_path"]) / "target" / "board.overlay").read_bytes(), self.source)

    def test_unexpected_staged_tree_contents_are_not_published(self):
        self.path = self.application / "overlays" / "board.overlay"
        self.observe()
        calls = 0

        def add_unexpected():
            nonlocal calls
            calls += 1
            if calls == 2:
                stage = self.application / f".zephyr-dte-{self.operation}.new"
                (stage / "unrelated.conf").write_text("external")

        result = self.publish(add_unexpected)
        self.assertEqual(result["status"], "conflict")
        self.assertFalse(self.path.parent.exists())

    def test_symlink_destination_and_parent_are_never_followed(self):
        outside = self.root / "outside.overlay"
        outside.write_bytes(b"outside")
        self.path.symlink_to(outside)
        with self.assertRaises(OSError):
            self.observe()
        self.path.unlink()
        self.path = self.application / "overlays" / "board.overlay"
        self.path.parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.observe()
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_symlink_appearing_at_atomic_creation_is_not_replaced(self):
        self.observe()
        outside = self.root / "outside.overlay"
        outside.write_bytes(b"outside")
        rename = publication._noreplace

        def race(parent, left, right):
            self.path.symlink_to(outside)
            return rename(parent, left, right)

        with patch.object(publication, "_noreplace", race):
            result = self.publish()
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_swapped_ancestor_leaves_both_destinations_uncreated(self):
        self.observe()
        moved = self.workspace / "moved"
        calls = 0

        def race():
            nonlocal calls
            calls += 1
            if calls == 1:
                self.application.rename(moved)
                self.application.mkdir()

        result = self.publish(race)
        self.assertEqual(result["status"], "conflict")
        self.assertFalse(self.path.exists())
        self.assertFalse((moved / self.path.name).exists())

    def test_failure_after_visibility_is_uncertain_and_retains_journal(self):
        self.observe()
        store = self.publisher._store

        def fail_saved(record, **kwargs):
            if record["status"] == "saved":
                raise OSError("metadata unavailable")
            return store(record, **kwargs)

        with patch.object(self.publisher, "_store", fail_saved):
            result = self.publish()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.path.read_bytes(), self.source)
        self.assertTrue((self.publisher.directory / (self.operation + ".json")).exists())
        with patch.object(publication, "_noreplace", side_effect=AssertionError("must not repeat")):
            self.assertEqual(self.publish()["status"], "uncertain")

    def test_separate_publishers_cannot_overwrite_first_creation(self):
        self.observe()
        self.assertEqual(self.publish()["status"], "saved")
        second = OverlayPublisher(self.root / "other-data")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            second.publish(self.workspace, self.path, self.observation, b"second", uuid.uuid4().hex, lambda: None)
        self.assertEqual(self.path.read_bytes(), self.source)

    def test_readonly_parent_refuses_before_staging(self):
        self.application.chmod(0o500)
        self.addCleanup(self.application.chmod, 0o700)
        self.assertFalse(self.observe()["writable"])
        with self.assertRaisesRegex(ValueError, "read only"):
            self.publish()
        self.assertFalse(self.path.exists())
        self.assertFalse(self.publisher.directory.exists())
