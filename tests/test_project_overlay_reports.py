"""Overlay evidence follows the reviewed target and the specific saved configuration."""

from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import test_project_creator as creator_tests
from test_project_samples import sample_fixture
from test_projects_api import wait_job
from zephyr_utilities.features.projects.creator import source_inputs


class ProjectOverlayReportTests(unittest.TestCase):
    setUp = creator_tests.CreatorServiceTests.setUp
    call = creator_tests.CreatorServiceTests.call
    body = creator_tests.CreatorServiceTests.body
    preview = creator_tests.CreatorServiceTests.preview
    sample_selection = creator_tests.CreatorServiceTests.sample_selection

    def create_sample(self):
        sample = sample_fixture(self.workspace / "zephyr")
        (sample / "boards").mkdir()
        (sample / "boards/other_chip.overlay").write_text("/* SPDX-License-Identifier: Apache-2.0 */\n")
        plan = self.preview(**self.sample_selection())
        job = self.call("POST", "create", self.body(plan_id=plan["id"])).data
        result = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        return plan, result["result"]["project"]

    def test_creation_reviews_and_persists_default_overlay_without_running_configuration(self):
        with patch("zephyr_utilities.features.projects.creator.execute_build") as execute:
            plan, project = self.create_sample()
        execute.assert_not_called()
        report = plan["overlay_usage"]
        self.assertEqual(report["status"], "expected")
        rows = {item["path"]: item for item in report["files"]}
        self.assertEqual(rows[plan["default_overlay"]["path"]]["state"], "expected")
        self.assertEqual(rows["boards/other_chip.overlay"]["state"], "unknown")
        self.assertIn("does not match the selected target", rows["boards/other_chip.overlay"]["reason"])
        self.assertIn("Transitive use needs configuration evidence", rows["boards/other_chip.overlay"]["reason"])
        self.assertEqual(project["default_overlay"], plan["default_overlay"])
        self.assertTrue((Path(project["path"]) / project["default_overlay"]["path"]).is_file())

    def test_current_reports_use_captured_inputs_for_their_configuration_and_preserve_records(self):
        _, project = self.create_sample()
        alternate = self.call("POST", "configurations", self.body(project_id=project["id"], name="Alternate",
            board_id="board-one", target="demo/chip", sdk_id="sdk-one")).data["configuration"]
        source = Path(project["path"])
        default = str(source / project["default_overlay"]["path"])
        board = str(self.workspace / "zephyr/boards/demo.dts")
        attempt = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": str(self.workspace),
            "project_id": project["id"], "status": "built", "profile": deepcopy(project["profile"]),
            "configuration": {"id": None, "name": "Original"}, "sources": source_inputs(source),
            "finished_at": "2026-01-01T12:00:00+00:00", "context": {"configured": True,
                "source_dir": str(source), "requested_target": "demo/chip",
                "devicetree": {"files": [board, default], "files_observed": True,
                               "dependencies": [board, default], "dependency_coverage": "complete"}}}
        self.creator.store.save_attempt(attempt)
        records = {path: path.read_bytes() for path in self.creator.store.root.rglob("*.json")}
        current = self.call("GET", "creator").data["projects"][0]
        report = current["overlay_usage_by_configuration"][""]
        self.assertEqual(report["status"], "confirmed")
        self.assertEqual(report["evidence"]["attempt_id"], attempt["id"])
        self.assertIn("Configure again", report["evidence"]["note"])
        rows = {item["path"]: item for item in report["files"]}
        self.assertEqual(rows[project["default_overlay"]["path"]]["state"], "active")
        self.assertEqual(rows["boards/other_chip.overlay"]["state"], "not_used")
        self.assertNotEqual(current["overlay_usage_by_configuration"][alternate["id"]]["status"], "confirmed")
        self.assertEqual(current["overlay_usage_by_attempt"][attempt["id"]], report)
        self.assertEqual(current["attempts"], [attempt])
        self.assertEqual(records, {path: path.read_bytes() for path in records})

    def test_build_review_uses_current_files_and_explicit_replacement(self):
        _, project = self.create_sample()
        source = Path(project["path"])
        (source / "custom.overlay").write_text("/* User overlay */\n")
        fragments = {"schema_version": 1, "kconfig": {"mode": "auto", "files": []},
                     "overlays": {"mode": "replace", "files": ["custom.overlay"]}}
        configuration = self.call("POST", "configurations", self.body(project_id=project["id"], name="Custom",
            board_id="board-one", target="demo/chip", sdk_id="sdk-one", fragments=fragments)).data["configuration"]
        plan = self.call("POST", "build-preview", self.body(project_id=project["id"],
            configuration_id=configuration["id"], operation="configure")).data["plan"]
        rows = {item["path"]: item for item in plan["overlay_usage"]["files"]}
        self.assertEqual(rows["custom.overlay"]["state"], "expected")
        default = rows[project["default_overlay"]["path"]]
        self.assertEqual(default["state"], "unknown")
        self.assertIn("Replace mode does not automatically add", default["reason"])
        self.assertIn("Transitive use needs configuration evidence", default["reason"])
        self.assertEqual(plan["overlay_usage"]["evidence"]["kind"], "build-preview")
        self.assertFalse((source / "build").exists())

    def test_compatibility_check_overlay_evidence_is_reused_only_for_its_reviewed_selection(self):
        sample_fixture(self.workspace / "zephyr")
        selection = self.sample_selection()
        report = {"schema_version": 1, "target": "demo/chip", "status": "confirmed", "message": "Captured.",
                  "files": [], "board_files": []}
        with patch("zephyr_utilities.features.projects.creator.check_sample", return_value={
                "status": "compatible", "message": "Checked.", "details": [], "overlay_usage": report}):
            job = self.call("POST", "sample-check", self.body(board_id="board-one", target="demo/chip", sdk_id="sdk-one",
                **{key: value for key, value in selection.items() if key != "template_id"})).data
            checked = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(checked["status"], "completed", checked.get("error"))
        plan = self.preview(**selection, sample_check_id=checked["result"]["check_id"])
        self.assertEqual(plan["overlay_usage"]["status"], "confirmed")
        self.assertEqual(plan["overlay_usage"]["evidence"]["kind"], "compatibility-check")
