"""Reusable build selections preserve application sources and reviewed execution."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from zephyr_utilities.app import Application
from zephyr_utilities.features.projects.creator import source_inputs

import test_project_creator as creator_tests
from test_projects_api import wait_job


class BuildConfigurationTests(unittest.TestCase):
    setUp = creator_tests.CreatorServiceTests.setUp
    call = creator_tests.CreatorServiceTests.call
    body = creator_tests.CreatorServiceTests.body
    preview = creator_tests.CreatorServiceTests.preview
    create = creator_tests.CreatorServiceTests.create
    build_plan = creator_tests.CreatorServiceTests.build_plan

    def save(self, project, **values):
        body = self.body(project_id=project["id"], name="Alternate", board_id="board-one",
                         target="demo/chip", sdk_id="sdk-one")
        body.update(values)
        return self.call("POST", "configurations", body).data["configuration"]

    def alternate_environment(self):
        (self.workspace / "sdk-two").mkdir()
        self.snapshot["boards"].append({"id": "board-two", "name": "other", "status": "discovered",
                                        "targets": ["other@2/chip/core"]})
        self.snapshot["sdks"].append({**self.snapshot["sdks"][0], "id": "sdk-two",
                                      "path": str(self.workspace / "sdk-two"), "fingerprint": "sdk-two-fingerprint"})
        self.snapshot["id"] = uuid.uuid4().hex
        self.snapshot["fingerprint"] = "environment-2"
        self.app.context.services["projects"].store.save(self.snapshot)

    def selected_plan(self, project, configuration, operation="build"):
        return self.call("POST", "build-preview", self.body(project_id=project["id"], operation=operation,
                         configuration_id=configuration["id"])).data["plan"]

    def run_plan(self, plan):
        job = self.call("POST", "build", self.body(build_plan_id=plan["id"])).data
        return wait_job(self.app.context.jobs, job["id"])

    def test_save_and_reopen_only_add_metadata_and_leave_original_unchanged(self):
        project = self.create()
        sources = source_inputs(project["path"])
        project_path = self.creator.store.root / "projects" / f"{project['id']}.json"
        original = project_path.read_bytes()
        jobs = self.app.context.jobs.list()
        self.analyze.reset_mock()
        with patch("zephyr_utilities.features.projects.creator.execute_build") as execute:
            saved = self.save(project, name="  Sensor board  ")
            execute.assert_not_called()
        self.analyze.assert_not_called()
        self.assertEqual(saved["name"], "Sensor board")
        self.assertEqual(source_inputs(project["path"]), sources)
        self.assertFalse((Path(project["path"]) / "build").exists())
        self.assertEqual(project_path.read_bytes(), original)
        self.assertEqual(self.app.context.jobs.list(), jobs)
        reopened = Application(self.workspace, self.root / "data")
        self.addCleanup(reopened.close)
        data = reopened.context.services["project_creator"].current()["projects"][0]
        self.assertEqual(data["configurations"], [saved])
        self.assertEqual(data["profile"], project["profile"])
        self.assertEqual(data["attempts"], [])

    def test_selected_target_sdk_and_profile_are_captured_for_configure_and_build(self):
        project = self.create()
        self.alternate_environment()
        saved = self.save(project, board_id="board-two", target="other@2/chip/core", sdk_id="sdk-two")
        self.save(project, name="First board")
        for operation in ("configure", "build"):
            with self.subTest(operation=operation):
                plan = self.selected_plan(project, saved, operation)
                self.assertIn("-DBOARD=other@2/chip/core", plan["recipe"]["configure"])
                self.assertIn(f"-DZEPHYR_SDK_INSTALL_DIR={self.workspace / 'sdk-two'}", plan["recipe"]["configure"])
                self.assertEqual(plan["configuration"], {"id": saved["id"], "name": "Alternate"})
                with patch.object(self.creator, "_recheck", wraps=self.creator._recheck) as recheck, patch(
                        "zephyr_utilities.features.projects.creator.execute_build",
                        return_value={"status": "configured" if operation == "configure" else "built"}) as execute:
                    result = self.run_plan(plan)
                self.assertEqual(result["status"], "completed", result.get("error"))
                self.assertEqual(recheck.call_args.args[0], saved["profile"])
                self.assertEqual(execute.call_args.args[0], saved["profile"])
                self.assertEqual(execute.call_args.args[3], operation)
                attempt = result["result"]["attempt"]
                self.assertEqual(attempt["profile"], saved["profile"])
                self.assertEqual(attempt["configuration"], plan["configuration"])
        self.assertEqual(self.creator.store.project(project["id"], self.workspace), project)

    def test_omitted_null_and_empty_configuration_keep_original_behavior(self):
        project = self.create()
        self.save(project)
        for selected in ({}, {"configuration_id": None}, {"configuration_id": ""}):
            with self.subTest(selection=selected):
                plan = self.call("POST", "build-preview", self.body(project_id=project["id"], operation="build",
                                                                     **selected)).data["plan"]
                self.assertEqual(plan["configuration"], {"id": None, "name": "Original"})
                self.assertEqual(plan["profile"], project["profile"])
                self.assertIn("-DBOARD=demo/chip", plan["recipe"]["configure"])

    def test_configuration_names_and_selection_are_validated_without_writes(self):
        project = self.create()
        invalid = [{"name": name} for name in (None, 42, "", "   ", "Original", " ORIGINAL ", "a" * 65,
                                                "Line\nbreak", "Tab\tname", "Control\x85", "\u202ename")]
        invalid.extend(({"board_id": "missing"}, {"target": "demo"}, {"sdk_id": "missing"},
                        {"target": []}, {"project_id": "../bad"}, {"workspace": "/another-workspace"}))
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.save(project, **change)
        self.assertFalse((self.creator.store.root / "configurations").exists())
        self.save(project, name="Debug board")
        with self.assertRaisesRegex(ValueError, "already has"):
            self.save(project, name="  DEBUG BOARD ")
        self.assertEqual(len(self.creator.current()["projects"][0]["configurations"]), 1)

    def test_configuration_limit_and_immutable_identity(self):
        project = self.create()
        saved = self.save(project)
        self.creator.store.save_configuration(saved)
        with self.assertRaisesRegex(ValueError, "different configuration"):
            self.creator.store.save_configuration({**saved, "name": "Changed"})
        with patch("zephyr_utilities.features.projects.records.MAX_CONFIGURATIONS", 2):
            self.save(project, name="Second")
            with self.assertRaisesRegex(ValueError, "limit"):
                self.save(project, name="Third")
        self.assertEqual(self.creator.store.configuration(saved["id"], project["id"], self.workspace), saved)

    def test_foreign_missing_and_invalid_configuration_ids_are_rejected(self):
        project = self.create()
        saved = self.save(project)
        other = deepcopy(project)
        other["id"] = uuid.uuid4().hex
        self.creator.store.save_project(other)
        self.save(other)
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.selected_plan(other, saved)
        for identity in ("../bad", [], False, 0, uuid.uuid4().hex):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.selected_plan(project, {"id": identity})
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.creator.store.configuration(saved["id"], project["id"], "/another-workspace")
        listed = self.creator.current()["projects"]
        self.assertEqual([len(item["configurations"]) for item in listed], [1, 1])

    def test_stale_or_unresolved_environment_cannot_save_configuration(self):
        project = self.create()
        original = deepcopy(self.snapshot)
        for change in ("stale", "unresolved", "other-workspace", "unsupported-sdk", "no-modules", "inactive-module"):
            snapshot = deepcopy(original)
            if change == "stale":
                snapshot["status"] = "stale"
            elif change == "unresolved":
                snapshot["workspace"]["manifest_resolved"] = False
            elif change == "other-workspace":
                snapshot["workspace"]["path"] = "/another-workspace"
            elif change == "unsupported-sdk":
                snapshot["sdks"][0]["toolchains"] = [{"family": "LLVM"}]
            elif change == "no-modules":
                snapshot["modules"] = []
            else:
                snapshot["modules"][0]["active"] = False
            with self.subTest(change=change), patch.object(self.creator.environments, "latest", return_value=snapshot):
                with self.assertRaises(ValueError):
                    self.save(project)
        self.assertFalse((self.creator.store.root / "configurations").exists())

    def test_replaced_application_directory_cannot_receive_configuration(self):
        project = self.create()
        source = Path(project["path"])
        source.rename(self.root / "moved-application")
        source.mkdir()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.save(project)
        self.assertFalse((self.creator.store.root / "configurations").exists())

    def test_selected_configuration_still_rechecks_environment_and_source_inputs(self):
        project = self.create()
        saved = self.save(project)
        for changed in ("environment", "source"):
            with self.subTest(changed=changed):
                plan = self.selected_plan(project, saved)
                if changed == "environment":
                    self.analyze.return_value = {**self.snapshot, "zephyr": {**self.snapshot["zephyr"], "version": "5.0.0"}}
                else:
                    self.analyze.return_value = self.snapshot
                    (Path(project["path"]) / "prj.conf").write_text("# changed after preview\n")
                with patch("zephyr_utilities.features.projects.creator.execute_build") as execute:
                    result = self.run_plan(plan)
                    execute.assert_not_called()
                self.assertEqual(result["status"], "failed")
                self.assertIn("changed", result["error"])
                self.assertFalse(Path(plan["build_dir"]).exists())

    def test_preview_captures_configuration_even_if_saved_record_changes(self):
        project = self.create()
        saved = self.save(project)
        plan = self.selected_plan(project, saved)
        record = self.creator.store.root / "configurations" / f"{saved['id']}.json"
        record.unlink()
        plan["profile"]["target"] = "changed-client-copy"
        plan["configuration"]["name"] = "Changed client copy"
        with patch("zephyr_utilities.features.projects.creator.execute_build", return_value={"status": "built"}) as execute:
            result = self.run_plan(plan)
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(execute.call_args.args[0], saved["profile"])
        self.assertEqual(result["result"]["attempt"]["configuration"], {"id": saved["id"], "name": "Alternate"})

    def test_future_configuration_schema_is_reported_and_preserved(self):
        project = self.create()
        saved = self.save(project)
        path = self.creator.store.root / "configurations" / f"{saved['id']}.json"
        future = {**saved, "schema_version": 99}
        path.write_text(json.dumps(future))
        original = path.read_bytes()
        current = self.creator.current()
        self.assertEqual(current["projects"][0]["configurations"], [])
        self.assertTrue(any("Unsupported" in message for message in current["diagnostics"]))
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.creator.store.save_configuration(saved)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.selected_plan(project, saved)
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.save(project, name="New")
        self.assertEqual(path.read_bytes(), original)

    def test_configuration_symlinks_are_rejected_and_preserve_destination(self):
        project = self.create()
        saved = self.save(project)
        path = self.creator.store.root / "configurations" / f"{saved['id']}.json"
        destination = self.root / "saved-configuration.json"
        path.rename(destination)
        path.symlink_to(destination)
        original = destination.read_bytes()
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.selected_plan(project, saved)
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.save(project, name="New")
        self.assertEqual(destination.read_bytes(), original)
