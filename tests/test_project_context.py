"""Read-only editor selection uses saved intent without executing build workflows."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from zephyr_utilities.app import Application
from zephyr_utilities.core.source_files import WorkspaceSourceReader
from zephyr_utilities.features.projects.creation import path_identity
from zephyr_utilities.features.projects.default_overlay import add_default_overlay
from zephyr_utilities.features.projects.editor_context import ProjectsEditorContextProvider
from zephyr_utilities.features.projects.fragments import automatic_fragments


class ProjectContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        for name in ("zephyr", "sdk", "custom"):
            (self.workspace / name).mkdir()
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.creator = self.app.context.services["project_creator"]
        self.provider = ProjectsEditorContextProvider(self.app.context, self.creator)
        self.observation = {"schema_version": 1, "id": uuid.uuid4().hex, "fingerprint": "saved-environment",
                            "workspace": {"path": str(self.workspace)}, "status": "complete"}
        self.creator.environments.save(self.observation)
        self.profile = {"schema_version": 2, "workspace": str(self.workspace), "target": "demo@1.0/chip",
                        "board_id": "custom-board", "sdk": {"path": str(self.workspace / "sdk"), "version": "test"},
                        "zephyr": {"path": str(self.workspace / "zephyr"), "version": "test"},
                        "tools": [{"name": "Python", "path": sys.executable}],
                        "roots": {"board_roots": [{"path": str(self.workspace / "custom"), "origin": "registered"}]},
                        "environment_id": self.observation["id"], "environment_fingerprint": self.observation["fingerprint"],
                        "fragments": automatic_fragments()}
        self.source = self.workspace / "sample"
        self.source.mkdir()
        contents, default = add_default_overlay({"CMakeLists.txt": (
            "cmake_minimum_required(VERSION 3.20.0)\n"
            "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\nproject(sample)\n")}, self.profile["target"])
        for relative, content in contents.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (self.source / "app.overlay").write_text('&uart0 { status = "okay"; };\n')
        self.project = {"schema_version": 2, "id": uuid.uuid4().hex, "revision": 0, "profile_revision": 0,
                        "workspace": str(self.workspace), "path": str(self.source), "name": "Sample",
                        "directory_identity": path_identity(self.source), "profile": deepcopy(self.profile),
                        "default_overlay": default}
        self.creator.store.save_project(self.project)

    def capture(self, configuration_id=None):
        return self.provider.capture(str(self.workspace), self.project["id"], configuration_id)

    def configuration(self, **changes):
        value = {"schema_version": 2, "id": uuid.uuid4().hex, "revision": 0,
                 "project_id": self.project["id"], "workspace": str(self.workspace), "name": "Debug",
                 "profile": deepcopy(self.profile), **changes}
        self.creator.store.save_configuration(value)
        return value

    def bytes_in_tree(self):
        return {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}

    def test_open_catalog_capture_and_revalidation_do_not_write_or_execute(self):
        before = self.bytes_in_tree()
        with patch.object(self.creator, "current", side_effect=AssertionError("No creator view")), \
                patch.object(self.creator, "build_preview", side_effect=AssertionError("No build preview")), \
                patch.object(self.app.context.jobs, "submit", side_effect=AssertionError("No jobs")), \
                patch("subprocess.run", side_effect=AssertionError("No tools")), \
                patch.object(self.creator.environments, "latest", side_effect=AssertionError("No environment substitution")):
            catalog = self.provider.catalog(str(self.workspace))
            selected = self.capture()
            self.assertEqual(self.provider.revalidate(selected), selected)
        self.assertEqual(before, self.bytes_in_tree())
        self.assertEqual(self.app.context.jobs.list(), [])
        self.assertEqual(catalog["projects"][0]["configurations"], [{"id": None, "name": "Original", "revision": 0}])
        self.assertEqual(selected["application"]["availability"]["state"], "available")
        self.assertEqual(selected["workspace_identity"], [self.workspace.stat().st_dev, self.workspace.stat().st_ino])
        self.assertEqual(selected["target"]["requested"], "demo@1.0/chip")
        self.assertEqual(selected["target"]["revision"], "1.0")
        self.assertEqual(selected["target"]["qualifiers"], ["chip"])

    def test_original_and_named_configuration_keep_separate_revisions_and_exact_targets(self):
        named_profile = {**self.profile, "target": "custom_board@2.1/chip/core"}
        named = self.configuration(revision=3, profile=named_profile)
        selected = self.capture(named["id"])
        self.assertEqual(selected["project"], {"id": self.project["id"], "revision": 0})
        self.assertEqual(selected["configuration"]["revision"], 3)
        self.assertEqual(selected["configuration"]["name"], "Debug")
        self.assertEqual(selected["target"]["requested"], named_profile["target"])
        self.assertEqual(selected["target"]["qualifiers"], ["chip", "core"])
        self.assertIsNone(selected["default_overlay"])
        self.assertEqual(self.capture()["configuration"]["revision"], 0)
        self.assertEqual(len(self.provider.catalog(str(self.workspace))["projects"][0]["configurations"]), 2)

    def test_newer_environment_does_not_replace_captured_selection(self):
        newer = {**self.observation, "id": uuid.uuid4().hex, "fingerprint": "newer"}
        self.creator.environments.save(newer)
        selected = self.capture()
        self.assertEqual(selected["environment"]["id"], self.observation["id"])
        self.assertEqual(selected["environment"]["fingerprint"], "saved-environment")
        self.assertEqual(selected["environment"]["freshness"], "saved-observation")

    def test_missing_toolchain_or_observation_does_not_require_build_readiness(self):
        shutil.rmtree(self.workspace / "sdk")
        (self.creator.environments.root / "snapshots" / f"{self.observation['id']}.json").unlink()
        selected = self.capture()
        self.assertEqual(selected["application"]["availability"]["state"], "available")
        self.assertEqual(selected["environment"]["availability"]["state"], "unavailable")
        self.assertEqual(selected["environment"]["freshness"], "stale")
        self.assertEqual(selected["environment"]["observation_status"], "unavailable")
        self.assertTrue(selected["diagnostics"])

    def test_custom_board_registry_changes_mark_captured_environment_stale(self):
        selected = self.capture()
        registry = self.creator.environments.custom_boards
        previous = registry.read(str(self.workspace))
        registry.write({**previous, "revision": previous["revision"] + 1}, previous)
        current = self.capture()
        self.assertEqual(current["environment"]["id"], selected["environment"]["id"])
        self.assertEqual(current["environment"]["freshness"], "stale")
        self.assertIn("custom-boards-changed", {item["code"] for item in current["diagnostics"]})
        with self.assertRaisesRegex(ValueError, "changed"):
            self.provider.revalidate(selected)

    def test_environment_record_with_wrong_identity_is_not_accepted_as_selected_evidence(self):
        path = self.creator.environments.root / "snapshots" / f"{self.observation['id']}.json"
        path.write_text(json.dumps({**self.observation, "id": uuid.uuid4().hex}))
        selected = self.capture()
        self.assertEqual(selected["environment"]["observation_status"], "unavailable")
        self.assertEqual(selected["environment"]["freshness"], "unknown")

    def test_malformed_environment_and_attempt_evidence_do_not_prevent_source_context(self):
        path = self.creator.environments.root / "snapshots" / f"{self.observation['id']}.json"
        path.write_text(json.dumps({**self.observation, "workspace": []}))
        self.creator.store.save_attempt({"schema_version": 1, "id": uuid.uuid4().hex,
            "project_id": self.project["id"], "workspace": str(self.workspace), "configuration": None,
            "profile": deepcopy(self.profile), "finished_at": None})
        selected = self.capture()
        self.assertEqual(selected["application"]["availability"]["state"], "available")
        self.assertEqual(selected["environment"]["observation_status"], "unavailable")
        self.assertEqual(selected["input_evidence"]["captured"], [])

    def test_invalid_saved_target_spelling_is_not_promoted_to_a_context(self):
        changed = {**self.project, "revision": 1, "profile": {**self.profile, "target": "../outside"}}
        self.creator.store.update_project(changed, self.project)
        with self.assertRaisesRegex(ValueError, "target"):
            self.capture()

    def test_missing_and_replaced_applications_retain_context_without_reading_replacement(self):
        selected = self.capture()
        self.source.rename(self.workspace / "moved")
        missing = self.capture()
        self.assertEqual(missing["application"]["availability"]["state"], "missing")
        self.assertEqual(missing["target"], selected["target"])
        self.source.mkdir()
        (self.source / "CMakeLists.txt").write_text("DO NOT READ AS THE OLD APPLICATION")
        replaced = self.capture()
        self.assertEqual(replaced["application"]["availability"]["state"], "replaced")
        self.assertEqual(replaced["input_evidence"]["source_fingerprints"], [])
        self.assertEqual(self.provider.catalog(str(self.workspace))["projects"][0]["availability"]["state"], "replaced")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.provider.revalidate(selected)

    def test_mutating_returned_data_cannot_change_saved_records_or_later_capture(self):
        selected = self.capture()
        selected["fragments"]["overlays"]["files"].append("injected.overlay")
        selected["environment"]["tools"].clear()
        selected["application"]["identity"].clear()
        unchanged = self.capture()
        self.assertEqual(unchanged["fragments"]["overlays"]["files"], [])
        self.assertTrue(unchanged["environment"]["tools"])
        self.assertTrue(unchanged["application"]["identity"])
        self.assertEqual(self.creator.store.project(self.project["id"], self.workspace), self.project)

    def test_fragment_order_and_historical_evidence_stay_distinct(self):
        for name in ("first.overlay", "second.overlay"):
            (self.source / name).write_text("/* synthetic */\n")
        fragments = automatic_fragments()
        fragments["overlays"] = {"mode": "replace", "files": ["second.overlay", "first.overlay"]}
        named = self.configuration(profile={**self.profile, "fragments": fragments})
        attempt = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": str(self.workspace),
                   "project_id": self.project["id"], "profile": deepcopy(named["profile"]), "status": "configured",
                   "configuration": {"id": named["id"], "name": named["name"]}, "configuration_revision": 0,
                   "finished_at": "2026-01-01T00:00:00+00:00", "context": {"configured": True,
                   "source_dir": str(self.source), "requested_target": self.profile["target"],
                   "devicetree": {"files_observed": True,
                                  "files": [str(self.source / "second.overlay"), str(self.source / "first.overlay")]}}}
        self.creator.store.save_attempt(attempt)
        selected = self.capture(named["id"])
        self.assertEqual(selected["fragments"]["overlays"], fragments["overlays"])
        predicted = selected["input_evidence"]["predicted"][0]
        captured = selected["input_evidence"]["captured"][0]
        self.assertEqual(predicted["provenance"]["kind"], "current-source-prediction")
        self.assertEqual(captured["provenance"]["kind"], "historical-attempt")
        self.assertEqual(captured["provenance"]["source_freshness"], "unknown")
        self.assertEqual([item["path"] for item in captured["files"]], ["second.overlay", "first.overlay"])
        self.assertEqual(selected["input_evidence"]["coverage"], "partial")
        self.assertEqual(self.capture()["input_evidence"]["captured"], [])

    def test_current_source_changes_invalidate_context_without_rewriting_history(self):
        selected = self.capture()
        overlay = self.source / "app.overlay"
        overlay.write_text('&uart0 { status = "disabled"; };\n')
        with self.assertRaisesRegex(ValueError, "changed"):
            self.provider.revalidate(selected)
        self.assertNotEqual(self.capture()["input_evidence"]["source_fingerprints"],
                            selected["input_evidence"]["source_fingerprints"])

    def test_new_configuration_history_does_not_invalidate_existing_editor_selection(self):
        selected = self.capture()
        attempt = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": str(self.workspace),
                   "project_id": self.project["id"], "profile": deepcopy(self.profile), "status": "configured",
                   "configuration": {"id": None, "name": "Original"}, "configuration_revision": 0,
                   "finished_at": "2026-01-01T00:00:00+00:00", "context": {"configured": True,
                   "source_dir": str(self.source), "requested_target": self.profile["target"],
                   "devicetree": {"files_observed": True, "files": [str(self.source / "app.overlay")]}}}
        self.creator.store.save_attempt(attempt)
        current = self.provider.revalidate(selected)
        self.assertTrue(current["input_evidence"]["captured"])
        self.assertEqual(selected["input_evidence"]["captured"], [])
        (self.source / "app.overlay").write_text("// Changed source still invalidates the selection.\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.provider.revalidate(selected)

    def test_hidden_overlay_discovery_and_explicit_selection_never_read_hidden_files(self):
        hidden = (".secret.overlay", "boards/.secret.overlay", ".private/secret.overlay")
        for name in hidden:
            path = self.source / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("/* excluded */\n")
        observed = []
        original = WorkspaceSourceReader.read_text

        def observe(reader, path, **kwargs):
            observed.append(Path(path))
            return original(reader, path, **kwargs)

        with patch.object(WorkspaceSourceReader, "read_text", observe):
            selected = self.capture()
            named = self.configuration()
            record = self.creator.store.root / "configurations" / f"{named['id']}.json"
            for name in hidden:
                with self.subTest(path=name):
                    fragments = automatic_fragments()
                    fragments["overlays"] = {"mode": "replace", "files": [name]}
                    named["profile"]["fragments"] = fragments
                    record.write_text(json.dumps(named))
                    with self.assertRaisesRegex(ValueError, "hidden"):
                        self.capture(named["id"])
        self.assertTrue(observed)
        self.assertFalse({self.source / name for name in hidden} & set(observed))
        self.assertFalse(set(hidden) & {item["path"] for item in selected["input_evidence"]["source_fingerprints"]})

    def test_registration_and_configuration_changes_independently_invalidate_context(self):
        named = self.configuration()
        for which in ("project", "configuration", "original"):
            with self.subTest(which=which):
                selected = self.capture(named["id"] if which != "original" else None)
                if which == "configuration":
                    changed = {**named, "revision": 1}
                    self.creator.store.update_configuration(changed, named)
                    named = changed
                else:
                    current = self.creator.store.project(self.project["id"], self.workspace)
                    updated = {**current, "revision": current["revision"] + 1}
                    if which == "original":
                        updated["profile_revision"] = 1
                    self.creator.store.update_project(updated, current)
                with self.assertRaisesRegex(ValueError, "changed"):
                    self.provider.revalidate(selected)

    def test_foreign_configurations_invalid_ids_and_workspace_switches_are_rejected(self):
        named = self.configuration()
        other = {**self.project, "id": uuid.uuid4().hex}
        self.creator.store.save_project(other)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.provider.capture(str(self.workspace), other["id"], named["id"])
        for bad in ("", [], False, "../bad"):
            with self.subTest(identity=bad), self.assertRaises(ValueError):
                self.capture(bad)
        selected = self.capture()
        elsewhere = self.root / "other"
        elsewhere.mkdir()
        self.app.context.workspace.set(elsewhere)
        with self.assertRaisesRegex(ValueError, "workspace changed"):
            self.provider.revalidate(selected)

    def test_legacy_profile_and_revisions_remain_readable_without_migration(self):
        path = self.creator.store.root / "projects" / f"{self.project['id']}.json"
        legacy = deepcopy(self.project)
        legacy.update(schema_version=1, profile={key: value for key, value in self.profile.items() if key != "fragments"})
        legacy["profile"]["schema_version"] = 1
        legacy.pop("revision")
        legacy.pop("profile_revision")
        path.write_text(json.dumps(legacy))
        before = path.read_bytes()
        selected = self.capture()
        self.assertEqual(selected["configuration"]["profile_version"], 1)
        self.assertEqual(selected["configuration"]["revision"], 0)
        self.assertEqual(selected["project"]["revision"], 0)
        self.assertEqual(selected["fragments"], automatic_fragments())
        self.assertEqual(path.read_bytes(), before)

    def test_empty_catalog_does_not_create_storage(self):
        application = Application(self.workspace, self.root / "empty-data")
        self.addCleanup(application.close)
        provider = ProjectsEditorContextProvider(application.context, application.context.services["project_creator"])
        before = self.bytes_in_tree()
        self.assertEqual(provider.catalog(str(self.workspace))["projects"], [])
        self.assertEqual(before, self.bytes_in_tree())
        self.assertFalse((self.root / "empty-data/.features/projects").exists())
