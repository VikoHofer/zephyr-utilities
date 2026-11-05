"""Saved choices can change without rewriting earlier attempts or unrelated sources."""

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import test_project_creator as creator_tests
import test_project_configurations as configuration_tests
from zephyr_utilities.features.projects.application_inputs import source_inputs
from zephyr_utilities.features.projects.default_overlay import default_overlay


class ConfigurationEditTests(unittest.TestCase):
    setUp = creator_tests.CreatorServiceTests.setUp
    call = creator_tests.CreatorServiceTests.call
    body = creator_tests.CreatorServiceTests.body
    preview = creator_tests.CreatorServiceTests.preview
    create = creator_tests.CreatorServiceTests.create
    alternate_environment = configuration_tests.BuildConfigurationTests.alternate_environment
    save = configuration_tests.BuildConfigurationTests.save

    def review(self, project, **values):
        return self.call("POST", "configurations/preview", self.body(project_id=project["id"], **values)).data["plan"]

    def apply(self, plan):
        return self.call("POST", "configurations/apply", self.body(plan_id=plan["id"])).data

    def test_reviewed_rename_preserves_identity_sources_and_captured_history(self):
        project = self.create()
        configuration = self.save(project)
        attempt = {"schema_version": 1, "id": uuid.uuid4().hex, "project_id": project["id"],
                   "workspace": str(self.workspace), "status": "configured", "started_at": "2026-01-01",
                   "profile": deepcopy(configuration["profile"]),
                   "configuration": {"id": configuration["id"], "name": configuration["name"]}}
        self.creator.store.save_attempt(attempt)
        before = source_inputs(project["path"])
        plan = self.review(project, configuration_id=configuration["id"], name="Renamed", expected_revision=0)
        self.assertEqual(plan["files"], [])
        self.assertEqual(self.creator.store.configuration(configuration["id"], project["id"], self.workspace), configuration)
        result = self.apply(plan)["configuration"]
        self.assertEqual((result["id"], result["name"], result["revision"]), (configuration["id"], "Renamed", 1))
        self.assertEqual(source_inputs(project["path"]), before)
        self.assertEqual(self.creator.store.attempt(attempt["id"], project["id"], self.workspace), attempt)
        self.assertEqual(self.creator.store.project(project["id"], self.workspace), project)

    def test_original_can_refresh_target_and_sdk_preserving_initial_profile(self):
        project = self.create()
        self.alternate_environment()
        plan = self.review(project, configuration_id="", name="Original", expected_revision=0,
                           board_id="board-two", target="other@2/chip/core", sdk_id="sdk-two")
        result = self.apply(plan)
        updated = result["project"]
        self.assertEqual(updated["profile"]["target"], "other@2/chip/core")
        self.assertEqual(updated["profile"]["sdk"]["id"], "sdk-two")
        self.assertEqual(updated["initial_profile"], project["profile"])
        self.assertEqual(updated["profile_revision"], 1)
        self.assertEqual(updated["schema_version"], 2)
        self.assertEqual(result["configuration"]["id"], None)
        self.assertEqual(updated["files"], project["files"])
        with self.assertRaisesRegex(ValueError, "changed"):
            self.review(updated, configuration_id="", expected_revision=0)
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.review(updated, configuration_id="", name="Renamed Original")

    def test_new_target_overlay_is_reviewed_then_added_without_replacing_original(self):
        project = self.create()
        source = Path(project["path"])
        before = (source / "CMakeLists.txt").read_bytes()
        self.alternate_environment()
        plan = self.review(project, name="Other board", board_id="board-two", target="other@2/chip/core",
                           sdk_id="sdk-two", create_default_overlay=True)
        default = default_overlay("other@2/chip/core")
        self.assertEqual({item["path"] for item in plan["files"]}, {"CMakeLists.txt", default["path"]})
        self.assertFalse((source / default["path"]).exists())
        result = self.apply(plan)
        self.assertTrue((source / default["path"]).is_file())
        self.assertIn(before.decode().split("# End Zephyr Utilities default overlay.")[0].split("cmake_minimum_required")[0],
                      (source / "CMakeLists.txt").read_text())
        self.assertIn('STREQUAL "demo/chip"', (source / "CMakeLists.txt").read_text())
        self.assertIn('STREQUAL "other@2/chip/core"', (source / "CMakeLists.txt").read_text())
        self.assertEqual(result["project"]["files"], project["files"])
        self.assertEqual(result["configuration"]["default_overlay"], default)
        next_plan = self.call("POST", "build-preview", self.body(project_id=project["id"], operation="configure",
            configuration_id=result["configuration"]["id"])).data["plan"]
        self.assertEqual(next_plan["profile"]["target"], default["target"])

    def test_existing_target_overlay_contents_are_preserved(self):
        project = self.create()
        path = Path(project["path"]) / project["default_overlay"]["path"]
        path.write_text("/* User changes */\n")
        plan = self.review(project, name="Same board", create_default_overlay=True)
        self.assertEqual(plan["files"], [])
        self.apply(plan)
        self.assertEqual(path.read_text(), "/* User changes */\n")

    def test_duplicate_name_and_concurrent_configuration_edit_are_rejected(self):
        project = self.create()
        saved = self.save(project)
        self.save(project, name="Other")
        with self.assertRaisesRegex(ValueError, "already has"):
            self.review(project, configuration_id=saved["id"], name="other")
        plan = self.review(project, configuration_id=saved["id"], name="First")
        changed = {**saved, "schema_version": 2, "revision": 1, "name": "External"}
        self.creator.store.update_configuration(changed, saved)
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.apply(plan)
        self.assertEqual(self.creator.store.configuration(saved["id"], project["id"], self.workspace), changed)

    def test_changed_source_after_review_does_not_write_files_or_metadata(self):
        project = self.create()
        self.alternate_environment()
        plan = self.review(project, name="Other", board_id="board-two", target="other@2/chip/core",
                           create_default_overlay=True)
        path = Path(project["path"]) / "CMakeLists.txt"
        changed = path.read_text() + "# External edit\n"
        path.write_text(changed)
        with self.assertRaisesRegex(ValueError, "files changed"):
            self.apply(plan)
        self.assertEqual(path.read_text(), changed)
        self.assertFalse((Path(project["path"]) / default_overlay("other@2/chip/core")["path"]).exists())

    def test_failed_metadata_save_rolls_back_only_reviewed_files(self):
        project = self.create()
        original = source_inputs(project["path"])
        self.alternate_environment()
        plan = self.review(project, name="Other", board_id="board-two", target="other@2/chip/core",
                           create_default_overlay=True)
        with patch.object(self.creator.store, "update_project", side_effect=OSError("Simulated storage failure")):
            with self.assertRaisesRegex(OSError, "storage failure"):
                self.apply(plan)
        self.assertEqual(source_inputs(project["path"]), original)
        self.assertEqual(self.creator.current()["projects"][0]["configurations"], [])

    def test_unmanaged_existing_overlay_is_never_overwritten(self):
        project = self.create()
        self.alternate_environment()
        path = Path(project["path"]) / default_overlay("other@2/chip/core")["path"]
        path.write_text("/* Existing external overlay */\n")
        with self.assertRaisesRegex(ValueError, "replace an existing"):
            self.review(project, name="Other", board_id="board-two", target="other@2/chip/core", create_default_overlay=True)
        self.assertEqual(path.read_text(), "/* Existing external overlay */\n")

    def test_edited_activation_block_requires_explicit_external_resolution(self):
        project = self.create()
        self.alternate_environment()
        path = Path(project["path"]) / "CMakeLists.txt"
        path.write_text(path.read_text().replace("list(PREPEND", "list(APPEND"))
        with self.assertRaisesRegex(ValueError, "CMake block changed"):
            self.review(project, name="Other", board_id="board-two", target="other@2/chip/core", create_default_overlay=True)

    def test_active_job_blocks_configuration_changes(self):
        project = self.create()
        with patch.object(self.creator.context.jobs, "list", return_value=[{"status": "running",
                "metadata": {"workspace": str(self.workspace), "project_id": project["id"]}}]):
            with self.assertRaisesRegex(ValueError, "operation is using"):
                self.review(project, name="Other")
