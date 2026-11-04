"""Creation/build API, persistence and captured-operation boundaries."""

from concurrent.futures import CancelledError
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import uuid

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.projects.creator import source_inputs
from zephyr_utilities.features.projects.records import ProjectStore

import test_projects_api as existing_api
from test_projects_api import wait_job


class CreatorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        for part in (".west", "zephyr", "module", "sdk"):
            (self.workspace / part).mkdir(parents=True)
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.creator = self.app.context.services["project_creator"]
        self.snapshot = {
            "schema_version": 1, "id": uuid.uuid4().hex, "status": "complete", "fingerprint": "environment-1",
            "cache": {"hit": False}, "request": {},
            "workspace": {"path": str(self.workspace), "west_topdir": str(self.workspace), "manifest_resolved": True,
                          "manifest_path": str(self.workspace / "zephyr/west.yml")},
            "zephyr": {"path": str(self.workspace / "zephyr"), "version": "4.4.2"},
            "boards": [{"id": "board-one", "name": "demo", "status": "discovered", "targets": ["demo/chip"]}],
            "sdks": [{"id": "sdk-one", "path": str(self.workspace / "sdk"), "status": "discovered", "version": "1.0.1",
                      "fingerprint": "sdk-fingerprint", "toolchains": [{"name": "arm-zephyr-eabi", "family": "GNU"}]}],
            "modules": [{"path": str(self.workspace / "module"), "is_module": True, "status": "discovered", "active": True}],
            "roots": {}, "tools": [{"name": name, "path": sys.executable, "status": "discovered"}
                                     for name in ("Python", "cmake", "ninja")],
        }
        self.app.context.services["projects"].store.save(self.snapshot)
        self.recheck = patch("zephyr_utilities.features.projects.creator.analyze_environment", return_value=self.snapshot)
        self.analyze = self.recheck.start()
        self.addCleanup(self.recheck.stop)

    def call(self, method, path, body=None):
        return self.app.router.dispatch(Request(method, "/api/projects/" + path, body=body or {}))

    def body(self, **values):
        return {"workspace": str(self.workspace), **values}

    def preview(self):
        return self.call("POST", "preview", self.body(name="demo_app", board_id="board-one", target="demo/chip", sdk_id="sdk-one")).data["plan"]

    def create(self):
        plan = self.preview()
        response = self.call("POST", "create", self.body(plan_id=plan["id"]))
        self.assertEqual(response.status, 202)
        job = wait_job(self.app.context.jobs, response.data["id"])
        self.assertEqual(job["status"], "completed", job.get("error"))
        return job["result"]["project"]

    def build_plan(self, project):
        return self.call("POST", "build-preview", self.body(project_id=project["id"], operation="build")).data["plan"]

    def test_open_and_preview_never_create_files_or_execute_tools(self):
        initial = self.call("GET", "creator").data
        self.assertEqual(initial["projects"], [])
        plan = self.preview()
        self.assertEqual(len(plan["files"]), 5)
        self.assertFalse(Path(plan["destination"]).exists())
        self.assertIn("display_configure", plan["recipe"])
        self.analyze.assert_not_called()
        self.assertFalse(self.creator.store.root.exists())

    def test_create_rechecks_then_persists_and_remains_separate_from_build(self):
        with patch("zephyr_utilities.features.projects.creator.execute_build") as execute:
            project = self.create()
            execute.assert_not_called()
        self.analyze.assert_called_once()
        self.assertTrue((Path(project["path"]) / "CMakeLists.txt").is_file())
        reopened = Application(self.workspace, self.root / "data")
        self.addCleanup(reopened.close)
        data = reopened.context.services["project_creator"].current()
        self.assertEqual(data["projects"][0]["id"], project["id"])
        self.assertEqual(data["projects"][0]["attempts"], [])
        self.assertIsNone(self.call("GET", "environment").data["job"])

    def test_missing_application_is_hidden_and_restored_with_saved_history(self):
        project = self.create()
        attempt = {"schema_version": 1, "id": uuid.uuid4().hex, "project_id": project["id"],
                   "workspace": str(self.workspace), "status": "built", "logs": ["Saved build evidence."]}
        self.creator.store.save_attempt(attempt)
        records = {path: path.read_bytes() for path in self.creator.store.root.rglob("*.json")}
        source = Path(project["path"])
        moved = self.root / "moved-application"
        source.rename(moved)
        self.analyze.reset_mock()
        with patch("zephyr_utilities.features.projects.creator.execute_build") as execute:
            data = self.call("GET", "creator").data
            self.assertEqual(data["projects"], [])
            self.assertEqual(data["diagnostics"], [])
            self.assertEqual(self.creator.store.project(project["id"], self.workspace), project)
            moved.rename(source)
            restored = self.call("GET", "creator").data["projects"]
            self.assertEqual([item["id"] for item in restored], [project["id"]])
            self.assertEqual(restored[0]["attempts"], [attempt])
            execute.assert_not_called()
        self.analyze.assert_not_called()
        self.assertEqual({path: path.read_bytes() for path in records}, records)

    def test_application_path_or_parent_replaced_by_file_is_hidden(self):
        project = self.create()
        source = Path(project["path"])
        source.rename(self.root / "moved-application")
        source.write_text("This is no longer an application directory.\n")
        self.assertEqual(self.call("GET", "creator").data["projects"], [])
        source.unlink()
        source.parent.rmdir()
        source.parent.write_text("This is no longer the application parent directory.\n")
        self.assertEqual(self.call("GET", "creator").data["projects"], [])
        self.assertEqual(self.creator.store.project(project["id"], self.workspace), project)

    def test_application_check_errors_keep_entry_and_report_diagnostic(self):
        project = self.create()
        source = Path(project["path"])
        original_stat = Path.stat
        for error in (PermissionError("Access denied."), OSError("Filesystem unavailable.")):
            with self.subTest(error=type(error).__name__):
                def checked_stat(path, *args, **kwargs):
                    if path == source:
                        raise error
                    return original_stat(path, *args, **kwargs)

                with patch.object(Path, "stat", checked_stat):
                    data = self.call("GET", "creator").data
                self.assertEqual([item["id"] for item in data["projects"]], [project["id"]])
                self.assertTrue(any(str(source) in message and str(error) in message
                                    for message in data["diagnostics"]))

    def test_changed_environment_prevents_creation_and_consumes_preview(self):
        plan = self.preview()
        self.analyze.return_value = {**self.snapshot, "zephyr": {**self.snapshot["zephyr"], "version": "5.0.0"}}
        job = self.call("POST", "create", self.body(plan_id=plan["id"])).data
        result = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("environment changed", result["error"])
        self.assertFalse(Path(plan["destination"]).exists())
        with self.assertRaisesRegex(ValueError, "expired|used"):
            self.call("POST", "create", self.body(plan_id=plan["id"]))

    def test_workspace_guard_rejects_old_preview_before_job_submission(self):
        plan = self.preview()
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaisesRegex(ValueError, "workspace changed"):
            self.call("POST", "create", self.body(plan_id=plan["id"]))
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_queued_creation_keeps_workspace_and_captured_environment(self):
        plan = self.preview()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def recheck(*args, **kwargs):
            entered.set()
            release.wait(2)
            self.assertEqual(args[0], str(self.workspace))
            return self.snapshot

        self.analyze.side_effect = recheck
        job = self.call("POST", "create", self.body(plan_id=plan["id"])).data
        self.assertTrue(entered.wait(1))
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        self.assertEqual(self.call("GET", "creator").data["jobs"], [])
        release.set()
        result = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertTrue(Path(plan["destination"]).exists())

    def test_build_needs_review_and_preserves_result_and_application_sources(self):
        project = self.create()
        inputs = source_inputs(project["path"])
        plan = self.build_plan(project)
        self.assertFalse(Path(plan["build_dir"]).exists())
        with patch("zephyr_utilities.features.projects.creator.execute_build", return_value={"status": "built", "context": {}, "artifacts": []}) as execute:
            job = self.call("POST", "build", self.body(build_plan_id=plan["id"])).data
            result = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(execute.call_args.args[2], plan["build_dir"])
        self.assertEqual(job["metadata"]["view"], "applications")
        self.assertEqual(source_inputs(project["path"]), inputs)
        attempt = self.call("GET", "creator").data["projects"][0]["attempts"][0]
        self.assertEqual(attempt["status"], "built")
        self.assertEqual(attempt["id"], plan["id"])
        self.assertIsNone(self.call("GET", "environment").data["job"])
        next_plan = self.build_plan(project)
        self.assertNotEqual(next_plan["build_dir"], plan["build_dir"])

    def test_source_edit_or_inserted_build_symlink_invalidates_build_preview(self):
        project = self.create()
        plan = self.build_plan(project)
        (Path(project["path"]) / "prj.conf").write_text("# changed\n")
        with patch("zephyr_utilities.features.projects.creator.execute_build") as execute:
            job = self.call("POST", "build", self.body(build_plan_id=plan["id"])).data
            result = wait_job(self.app.context.jobs, job["id"])
            execute.assert_not_called()
        self.assertEqual(result["status"], "failed")
        self.assertIn("changed since preview", result["error"])
        plan = self.build_plan(project)
        (Path(project["path"]) / "build").symlink_to(self.root, target_is_directory=True)
        with patch("zephyr_utilities.features.projects.creator.execute_build") as execute:
            job = self.call("POST", "build", self.body(build_plan_id=plan["id"])).data
            self.assertEqual(wait_job(self.app.context.jobs, job["id"])["status"], "failed")
            execute.assert_not_called()
        self.assertFalse((self.root / plan["id"]).exists())

    def test_cancel_and_failure_attempts_survive_job_errors(self):
        project = self.create()
        for status in ("cancelled", "failed"):
            plan = self.build_plan(project)
            with patch("zephyr_utilities.features.projects.creator.execute_build", return_value={"status": status, "error": "synthetic result"}):
                job = self.call("POST", "build", self.body(build_plan_id=plan["id"])).data
                result = wait_job(self.app.context.jobs, job["id"])
            self.assertEqual(result["status"], status)
            attempts = self.call("GET", "creator").data["projects"][0]["attempts"]
            self.assertEqual(next(a for a in attempts if a["id"] == plan["id"])["status"], status)

    def test_cancellation_before_creation_preserves_destination(self):
        plan = self.preview()
        self.analyze.side_effect = CancelledError()
        job = self.call("POST", "create", self.body(plan_id=plan["id"])).data
        self.assertEqual(wait_job(self.app.context.jobs, job["id"])["status"], "cancelled")
        self.assertFalse(Path(plan["destination"]).exists())

    def test_invalid_operations_are_not_submitted(self):
        for endpoint, body in [("preview", self.body(name="../bad")), ("create", self.body(plan_id="../bad")),
                               ("build", self.body(build_plan_id="missing")),
                               ("build-preview", self.body(project_id="x", operation="flash"))]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                self.call("POST", endpoint, body)
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_nested_build_named_source_directory_participates_in_freshness(self):
        project = self.create()
        nested = Path(project["path"]) / "src/build"
        nested.mkdir()
        source = nested / "helper.c"
        source.write_text("int helper = 1;\n")
        before = source_inputs(project["path"])
        source.write_text("int helper = 2;\n")
        self.assertNotEqual(source_inputs(project["path"]), before)


class ProjectRecordTests(unittest.TestCase):
    def test_future_schema_preserved_and_interrupted_attempt_reported(self):
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(Path(root))
            project = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": "/workspace", "path": "/workspace/app"}
            store.save_project(project)
            attempt = {"schema_version": 1, "id": uuid.uuid4().hex, "project_id": project["id"], "workspace": "/workspace", "status": "running"}
            store.save_attempt(attempt)
            self.assertEqual(store.list("/workspace")[0][0]["attempts"][0]["status"], "interrupted")
            self.assertEqual(store.list("/workspace", [attempt["id"]])[0][0]["attempts"][0]["status"], "running")
            path = store.root / "projects" / f"{project['id']}.json"
            future = deepcopy(project)
            future["schema_version"] = 99
            path.write_text(json.dumps(future))
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                store.save_project(project)
            self.assertEqual(json.loads(path.read_text()), future)
            self.assertTrue(store.list("/workspace")[1])


class CreatorHttpTests(unittest.TestCase):
    requires_loopback = True
    setUp = existing_api.ProjectsHttpTests.setUp
    close = existing_api.ProjectsHttpTests.close
    request = existing_api.ProjectsHttpTests.request

    def test_creator_routes_use_shared_guards_and_need_explicit_inputs(self):
        for endpoint in ("preview", "create", "build-preview", "build"):
            with self.subTest(endpoint=endpoint), self.assertRaises(HTTPError) as error:
                self.request("/api/projects/" + endpoint, method="POST", body={}, authorized=False)
            self.assertEqual(error.exception.code, 403)
            with self.assertRaises(HTTPError) as error:
                self.request("/api/projects/" + endpoint, method="POST", body={})
            self.assertEqual(error.exception.code, 400)
        with self.request("/api/projects/creator") as response:
            self.assertEqual(json.load(response)["projects"], [])
