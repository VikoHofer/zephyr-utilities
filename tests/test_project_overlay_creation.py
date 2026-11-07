"""New-overlay inclusion is scoped, reviewed and recoverable across interruptions."""

from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch
import uuid

from zephyr_utilities.features.projects.editor_overlays import ProjectsOverlayProvider
from zephyr_utilities.features.projects.default_overlay import add_default_overlay, default_overlay
from zephyr_utilities.features.projects.overlay_creation import draft_destination

import test_project_context as fixtures


class ProjectOverlayCreationTests(unittest.TestCase):
    configuration = fixtures.ProjectContextTests.configuration
    capture = fixtures.ProjectContextTests.capture

    def setUp(self):
        fixtures.ProjectContextTests.setUp(self)
        for path in self.source.rglob("*.overlay"):
            path.unlink()
        (self.source / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20.0)\nfind_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\nproject(sample)\n")
        original = deepcopy(self.project)
        self.project = {**self.project, "revision": 1, "user_annotation": {"preserve": True}}
        self.project.pop("default_overlay")
        self.creator.store.update_project(self.project, original)
        self.overlays = ProjectsOverlayProvider(self.app.context, self.creator)
        self.selection = self.capture()
        self.relative = self.selection["overlay_creation"]["path"]
        self.destination = self.source / self.relative
        self.content = '&uart0 { status = "okay"; };\n'
        self.digest = hashlib.sha256(self.content.encode()).hexdigest()

    def review(self, selection=None, *, creating=True):
        return self.overlays.review(selection or self.selection, self.relative, creating=creating)

    def publish_fixture(self):
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        self.destination.write_text(self.content)

    def register(self, descriptor, **changes):
        values = {"workspace": str(self.workspace), "path": str(self.destination), "sha256": self.digest, **changes}
        return self.overlays.register(descriptor, **values)

    def record_path(self, descriptor):
        return self.creator.store.root / "overlay_registrations" / (descriptor["id"] + ".json")

    def test_review_only_records_private_intent_and_reuses_the_same_plan(self):
        source = {path.relative_to(self.source): path.read_bytes() for path in self.source.rglob("*") if path.is_file()}
        descriptor = self.review()
        self.assertTrue(descriptor["required"])
        self.assertEqual(descriptor["state"], "planned")
        self.assertEqual(self.review(), descriptor)
        self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)
        self.assertEqual(source, {path.relative_to(self.source): path.read_bytes() for path in self.source.rglob("*") if path.is_file()})
        self.assertEqual(len(list(self.record_path(descriptor).parent.glob("*.json"))), 1)

    def test_malformed_or_future_default_metadata_does_not_propose_a_destination(self):
        known = default_overlay(self.profile["target"])
        files = [{"path": "CMakeLists.txt", "content": (self.source / "CMakeLists.txt").read_text()}]
        for metadata in ({"target": self.profile["target"]}, {**known, "schema_version": 99},
                         {**known, "path": "arbitrary.overlay"}, {**known, "path": "../outside.overlay"},
                         {**known, "sha256": "0" * 64}, {**known, "future_field": True}, "invalid"):
            with self.subTest(metadata=metadata):
                self.assertIsNone(draft_destination(self.profile, str(self.source), files, metadata))
                if isinstance(metadata, dict):
                    changed = {**self.project, "default_overlay": metadata}
                    self.creator.store._write(self.creator.store.root / "projects" / (changed["id"] + ".json"), changed)
                    selected = self.capture()
                    self.assertIsNone(selected["overlay_creation"])
                    self.assertEqual(selected["target"]["requested"], self.profile["target"])
                    self.assertEqual(selected["application"]["availability"]["state"], "available")
                    self.assertEqual(self.creator.store.project(changed["id"], str(self.workspace)), changed)
        self.assertFalse(self.destination.exists())

    def test_missing_explicit_input_and_default_overlay_do_not_claim_single_overlay_support(self):
        contents, metadata = add_default_overlay({"CMakeLists.txt": (self.source / "CMakeLists.txt").read_text()}, self.profile["target"])
        profile = deepcopy(self.profile)
        profile["fragments"]["overlays"] = {"mode": "append", "files": ["custom.overlay"]}
        files = [{"path": "CMakeLists.txt", "content": contents["CMakeLists.txt"]}]
        proposal = draft_destination(profile, str(self.source), files, metadata)
        self.assertEqual(proposal["path"], "custom.overlay")
        self.assertFalse(proposal["supported"])
        self.assertIn(metadata["path"], proposal["reason"])
        self.assertFalse(proposal["registration_required"])
        profile["fragments"]["overlays"]["mode"] = "replace"
        self.assertTrue(draft_destination(profile, str(self.source), files, metadata)["supported"])

    def test_register_adds_only_original_overlay_selection_and_preserves_cmake(self):
        cmake = (self.source / "CMakeLists.txt").read_bytes()
        descriptor = self.review()
        self.publish_fixture()
        receipt = self.register(descriptor)
        self.assertEqual(receipt["state"], "included")
        changed = self.creator.store.project(self.project["id"], str(self.workspace))
        self.assertEqual(changed["revision"], self.project["revision"] + 1)
        self.assertEqual(changed["profile_revision"], self.project["profile_revision"] + 1)
        self.assertEqual(changed["profile"]["fragments"]["overlays"], {"mode": "append", "files": [self.relative]})
        expected = deepcopy(self.project)
        expected.update(revision=changed["revision"], profile_revision=changed["profile_revision"], profile=changed["profile"])
        self.assertEqual(changed, expected)
        self.assertEqual((self.source / "CMakeLists.txt").read_bytes(), cmake)
        self.assertEqual(self.destination.read_text(), self.content)
        self.assertEqual(receipt["selection"]["configuration"]["revision"], changed["profile_revision"])

    def test_named_configuration_changes_only_its_own_record(self):
        named = self.configuration(user_annotation="retain")
        other = self.configuration(name="Release")
        self.selection = self.capture(named["id"])
        descriptor = self.review()
        self.publish_fixture()
        receipt = self.register(descriptor)
        changed = self.creator.store.configuration(named["id"], self.project["id"], str(self.workspace))
        expected = deepcopy(named)
        expected.update(revision=named["revision"] + 1, profile=changed["profile"])
        self.assertEqual(changed, expected)
        self.assertEqual(changed["profile"]["fragments"]["overlays"], {"mode": "append", "files": [self.relative]})
        self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)
        self.assertEqual(self.creator.store.configuration(other["id"], self.project["id"], str(self.workspace)), other)
        self.assertEqual(receipt["selection"]["configuration"]["id"], named["id"])

    def test_legacy_profile_upgrades_only_when_new_selection_is_registered(self):
        legacy = deepcopy(self.project)
        legacy.update(schema_version=1, profile={key: value for key, value in legacy["profile"].items() if key != "fragments"})
        legacy["profile"]["schema_version"] = 1
        legacy.pop("revision")
        legacy.pop("profile_revision")
        self.creator.store._write(self.creator.store.root / "projects" / (legacy["id"] + ".json"), legacy)
        self.selection = self.capture()
        descriptor = self.review()
        self.assertEqual(self.creator.store.project(legacy["id"], str(self.workspace)), legacy)
        self.publish_fixture()
        self.register(descriptor)
        changed = self.creator.store.project(legacy["id"], str(self.workspace))
        self.assertEqual((changed["schema_version"], changed["profile"]["schema_version"]), (2, 2))
        self.assertEqual((changed["revision"], changed["profile_revision"]), (1, 1))
        self.assertEqual(changed["user_annotation"], legacy["user_annotation"])

    def test_missing_explicit_input_already_included_needs_no_configuration_write(self):
        profile = deepcopy(self.profile)
        profile["fragments"]["overlays"] = {"mode": "replace", "files": [self.relative]}
        named = self.configuration(profile=profile)
        self.selection = self.capture(named["id"])
        descriptor = self.review()
        self.assertFalse(descriptor["required"])
        self.assertEqual(descriptor["state"], "already_included")
        self.publish_fixture()
        with patch.object(self.creator.store, "update_configuration", side_effect=AssertionError("No configuration mutation")):
            self.assertEqual(self.register(descriptor)["state"], "included")
            self.assertEqual(self.register(descriptor)["state"], "included")
        self.assertEqual(self.creator.store.configuration(named["id"], self.project["id"], str(self.workspace)), named)

    def test_ordinary_existing_overlay_save_does_not_invent_registration(self):
        self.publish_fixture()
        current = self.capture()
        self.assertIsNone(self.review(current, creating=False))
        self.assertFalse((self.creator.store.root / "overlay_registrations").exists())
        self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)

    def test_registration_requires_the_exact_proposed_missing_destination(self):
        with self.assertRaisesRegex(ValueError, "proposed new overlay"):
            self.overlays.review(self.selection, "different.overlay", creating=True)
        self.publish_fixture()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.review()

    def test_restart_finds_pending_registration_after_file_save(self):
        descriptor = self.review()
        self.publish_fixture()
        self.overlays = ProjectsOverlayProvider(self.app.context, self.creator)
        current = self.capture()
        self.assertEqual(self.review(current, creating=False), descriptor)
        self.assertEqual(self.register(descriptor)["state"], "included")
        self.assertIsNone(self.review(self.capture(), creating=False))

    def test_lost_registration_acknowledgement_reuses_exact_after_record_without_rewrite(self):
        descriptor = self.review()
        self.publish_fixture()
        write = self.overlays._write

        def interrupted(record):
            if record["state"] == "included":
                raise OSError("Interrupted after configuration write")
            return write(record)

        with patch.object(self.overlays, "_write", side_effect=interrupted), self.assertRaisesRegex(OSError, "Interrupted"):
            self.register(descriptor)
        after = self.creator.store.project(self.project["id"], str(self.workspace))
        self.assertEqual(after["revision"], self.project["revision"] + 1)
        self.overlays = ProjectsOverlayProvider(self.app.context, self.creator)
        self.assertEqual(self.review(self.capture(), creating=False), descriptor)
        with patch.object(self.creator.store, "update_project", side_effect=AssertionError("Do not replay committed CAS")):
            self.assertEqual(self.register(descriptor)["state"], "included")
            self.assertEqual(self.register(descriptor)["state"], "included")
        self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), after)
        self.assertEqual(json.loads(self.record_path(descriptor).read_text())["state"], "included")

    def test_changed_project_or_named_configuration_preserves_saved_file_and_pending_record(self):
        for named_selection in (False, True):
            with self.subTest(named=named_selection):
                fixture = ProjectOverlayCreationTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                named = fixture.configuration() if named_selection else None
                fixture.selection = fixture.capture(named["id"] if named else None)
                descriptor = fixture.review()
                fixture.publish_fixture()
                if named:
                    fixture.creator.store.update_configuration({**named, "revision": 1, "name": "Renamed"}, named)
                else:
                    fixture.creator.store.update_project({**fixture.project, "revision": 2, "name": "Renamed"}, fixture.project)
                with self.assertRaisesRegex(ValueError, "changed after overlay review"):
                    fixture.register(descriptor)
                self.assertEqual(fixture.destination.read_text(), fixture.content)
                self.assertEqual(json.loads(fixture.record_path(descriptor).read_text())["state"], "planned")

    def test_retry_completes_failed_durability_without_repeating_the_configuration_write(self):
        descriptor = self.review()
        self.publish_fixture()
        with patch.object(self.overlays, "_sync", side_effect=OSError("sync interrupted")):
            with self.assertRaisesRegex(OSError, "sync interrupted"):
                self.register(descriptor)
        changed = self.creator.store.project(self.project["id"], str(self.workspace))
        self.assertEqual(changed["revision"], self.project["revision"] + 1)
        with patch.object(self.overlays, "_sync", wraps=self.overlays._sync) as synced, \
                patch.object(self.creator.store, "update_project", side_effect=AssertionError("No second CAS")):
            self.assertEqual(self.register(descriptor)["state"], "included")
        self.assertIn(self.creator.store.root / "projects" / (self.project["id"] + ".json"),
                      [call.args[0] for call in synced.call_args_list])

    def test_replaced_application_directory_blocks_registration(self):
        descriptor = self.review()
        self.publish_fixture()
        previous = self.source.with_name("old-sample")
        self.source.rename(previous)
        self.source.mkdir()
        with self.assertRaisesRegex(ValueError, "directory identity changed"):
            self.register(descriptor)
        self.assertEqual((previous / self.relative).read_text(), self.content)
        self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)

    def test_cmake_or_unrelated_overlay_change_blocks_inclusion_without_removing_file(self):
        for target in ("CMakeLists.txt", "unrelated.overlay"):
            fixture = ProjectOverlayCreationTests()
            fixture.setUp()
            self.addCleanup(fixture.doCleanups)
            descriptor = fixture.review()
            fixture.publish_fixture()
            (fixture.source / target).write_text("/* externally changed */\n")
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "CMake or other overlay"):
                fixture.register(descriptor)
            self.assertEqual(fixture.destination.read_text(), fixture.content)
            self.assertEqual(fixture.creator.store.project(fixture.project["id"], str(fixture.workspace)), fixture.project)

    def test_wrong_hash_missing_file_and_link_refuse_registration(self):
        descriptor = self.review()
        with self.assertRaises(FileNotFoundError):
            self.register(descriptor)
        self.publish_fixture()
        with self.assertRaisesRegex(ValueError, "saved overlay changed"):
            self.register(descriptor, sha256="f" * 64)
        self.destination.unlink()
        self.destination.symlink_to(self.source / "CMakeLists.txt")
        with self.assertRaises((OSError, ValueError)):
            self.register(descriptor)
        self.assertTrue(self.destination.is_symlink())
        self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)

    def test_descriptor_cannot_cross_workspace_or_destination(self):
        descriptor = self.review()
        self.publish_fixture()
        for changes in ({"path": str(self.source / "other.overlay")}, {"workspace": str(self.root)}, {"sha256": "bad"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.register(descriptor, **changes)
        self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)

    def test_tampered_scope_or_unknown_journal_version_is_preserved(self):
        descriptor = self.review()
        self.publish_fixture()
        path = self.record_path(descriptor)
        original = json.loads(path.read_text())
        changed = deepcopy(original)
        changed["after_selected"]["name"] = "Not an overlay inclusion"
        for record in (changed, {**original, "schema_version": 99}):
            content = json.dumps(record).encode()
            path.write_bytes(content)
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.register(descriptor)
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)

    def test_completed_receipt_never_reapplies_a_reverted_configuration(self):
        descriptor = self.review()
        self.publish_fixture()
        self.register(descriptor)
        self.creator.store._write(self.creator.store.root / "projects" / (self.project["id"] + ".json"), self.project)
        with patch.object(self.creator.store, "update_project", side_effect=AssertionError("No replay")):
            with self.assertRaisesRegex(ValueError, "already completed"):
                self.register(descriptor)
        self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)

    def test_registration_limit_and_future_records_preserve_existing_recovery(self):
        descriptor = self.review()
        before = self.record_path(descriptor).read_bytes()
        named = self.configuration()
        with patch("zephyr_utilities.features.projects.editor_overlays.MAX_REGISTRATIONS", 1):
            with self.assertRaisesRegex(ValueError, "64-record limit"):
                self.review(self.capture(named["id"]))
        self.assertEqual(self.record_path(descriptor).read_bytes(), before)
        unknown = self.record_path({"id": uuid.uuid4().hex})
        unknown.write_text('{"schema_version": 99}')
        with self.assertRaisesRegex(ValueError, "could not be read completely"):
            self.review(self.capture(named["id"]))
        self.assertEqual(unknown.read_text(), '{"schema_version": 99}')
