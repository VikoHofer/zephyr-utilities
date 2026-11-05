"""External folder changes and explicit metadata removal preserve source files."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch
import uuid

from zephyr_utilities.app import Application
from zephyr_utilities.features.projects.creator import source_inputs

import test_project_creator as creator_tests
import test_project_configurations as configuration_tests
from test_projects_api import wait_job


class ProjectLifecycleTests(unittest.TestCase):
    setUp = creator_tests.CreatorServiceTests.setUp
    call = creator_tests.CreatorServiceTests.call
    body = creator_tests.CreatorServiceTests.body
    preview = creator_tests.CreatorServiceTests.preview
    create = creator_tests.CreatorServiceTests.create
    build_plan = creator_tests.CreatorServiceTests.build_plan
    save = configuration_tests.BuildConfigurationTests.save
    selected_plan = configuration_tests.BuildConfigurationTests.selected_plan
    run_plan = configuration_tests.BuildConfigurationTests.run_plan

    def attempt(self, project, configuration=None):
        output = Path(project["path"]) / "build" / uuid.uuid4().hex
        output.mkdir(parents=True)
        (output / "evidence.txt").write_text("Retained output.\n")
        attempt = {"schema_version": 1, "id": uuid.uuid4().hex, "project_id": project["id"],
                   "workspace": str(self.workspace), "status": "built", "build_dir": str(output),
                   "logs": ["Saved build evidence."], "profile": deepcopy(project["profile"])}
        if configuration:
            attempt["configuration"] = {"id": configuration["id"], "name": configuration["name"]}
        self.creator.store.save_attempt(attempt)
        return attempt

    def remove(self, project, kind="", **fields):
        return self.call("POST", f"{kind + '/' if kind else ''}remove",
                         self.body(project_id=project["id"], **fields)).data

    def records(self):
        return {path: path.read_bytes() for path in self.creator.store.root.rglob("*.json")}

    def test_three_creations_at_the_same_path_only_show_the_current_folder(self):
        projects = []
        for index in range(3):
            projects.append(self.create())
            if index < 2:
                shutil.rmtree(projects[-1]["path"])
        before = self.records()
        current = self.creator.current()
        self.assertEqual([item["id"] for item in current["projects"]], [projects[-1]["id"]])
        self.assertEqual({item["id"] for item in current["unavailable_projects"]},
                         {item["id"] for item in projects[:-1]})
        self.assertTrue(all(item["availability"] == "replaced" for item in current["unavailable_projects"]))
        self.assertEqual([job["result"]["project"]["id"] for job in current["jobs"]], [projects[-1]["id"]])
        self.assertEqual(self.records(), before)

    def test_moved_or_redirected_folder_is_separate_and_original_can_be_restored(self):
        project = self.create()
        attempt = self.attempt(project)
        source = Path(project["path"])
        moved = self.root / "moved-app"
        source.rename(moved)
        for redirected in (False, True):
            if redirected:
                source.symlink_to(moved, target_is_directory=True)
            current = self.creator.current()
            self.assertEqual(current["projects"], [])
            self.assertEqual(current["unavailable_projects"][0]["id"], project["id"])
            self.assertEqual({key: current["unavailable_projects"][0]["attempts"][0][key] for key in attempt}, attempt)
            self.assertEqual(current["unavailable_projects"][0]["availability"], "replaced" if redirected else "missing")
        source.unlink()
        moved.rename(source)
        current = self.creator.current()
        self.assertEqual([item["id"] for item in current["projects"]], [project["id"]])
        self.assertEqual(current["unavailable_projects"], [])

    def test_reused_directory_identity_does_not_duplicate_the_live_list(self):
        project = self.create()
        newer = {**project, "id": uuid.uuid4().hex, "created_at": "9999-01-01T00:00:00+00:00"}
        self.creator.store.save_project(newer)
        current = self.creator.current()
        self.assertEqual([item["id"] for item in current["projects"]], [newer["id"]])
        self.assertEqual([item["id"] for item in current["unavailable_projects"]], [project["id"]])
        with self.assertRaisesRegex(ValueError, "older unavailable"):
            self.remove(newer)
        self.remove(project)
        self.remove(newer)
        self.assertEqual(self.creator.current()["projects"], [])
        self.assertEqual(self.creator.current()["unavailable_projects"], [])
        self.assertTrue(Path(project["path"]).is_dir())

    def test_project_removal_cascades_only_its_records_and_survives_restart(self):
        project = self.create()
        configuration = self.save(project)
        attempt = self.attempt(project, configuration)
        other = {**project, "id": uuid.uuid4().hex, "path": str(self.workspace / "other")}
        self.creator.store.save_project(other)
        before_sources = source_inputs(project["path"])
        output = Path(attempt["build_dir"]) / "evidence.txt"
        plan = self.build_plan(project)
        self.assertEqual(self.remove(project), {"removed": project["id"]})
        self.assertEqual(source_inputs(project["path"]), before_sources)
        self.assertEqual(output.read_text(), "Retained output.\n")
        self.assertEqual(self.creator.store.project(other["id"], self.workspace), other)
        remaining = self.records()
        self.assertEqual(len(remaining), 1)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.run_plan(plan)
        reopened = Application(self.workspace, self.root / "data")
        self.addCleanup(reopened.close)
        current = reopened.context.services["project_creator"].current()
        self.assertEqual(current["projects"], [])
        self.assertEqual([item["id"] for item in current["unavailable_projects"]], [other["id"]])
        self.assertEqual(self.creator.current()["jobs"], [])

    def test_unavailable_project_can_be_removed_without_touching_replacement(self):
        project = self.create()
        source = Path(project["path"])
        source.rename(self.root / "moved-app")
        source.mkdir()
        replacement = source / "keep.txt"
        replacement.write_text("Unrelated application.\n")
        self.remove(project)
        self.assertEqual(replacement.read_text(), "Unrelated application.\n")
        self.assertTrue((self.root / "moved-app" / "CMakeLists.txt").exists())
        self.assertEqual(self.creator.current()["unavailable_projects"], [])

    def test_configuration_removal_preserves_attempt_capture_and_allows_name_reuse(self):
        project = self.create()
        saved = self.save(project)
        attempt = self.attempt(project, saved)
        plan = self.selected_plan(project, saved)
        original_plan = self.build_plan(project)
        before_sources = source_inputs(project["path"])
        self.assertEqual(self.remove(project, "configurations", configuration_id=saved["id"]), {"removed": saved["id"]})
        current = self.creator.current()["projects"][0]
        self.assertEqual(current["configurations"], [])
        self.assertEqual({key: current["attempts"][0][key] for key in attempt}, attempt)
        self.assertEqual(source_inputs(project["path"]), before_sources)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.run_plan(plan)
        self.assertIn(original_plan["id"], self.creator.plans)
        self.assertEqual(self.save(project)["name"], saved["name"])

    def test_attempt_removal_retains_outputs_and_hides_completed_job(self):
        project = self.create()
        plan = self.build_plan(project)
        with patch("zephyr_utilities.features.projects.creator.execute_build", return_value={"status": "built"}):
            job = self.run_plan(plan)
        self.assertEqual(job["status"], "completed")
        attempt = job["result"]["attempt"]
        output = Path(attempt["build_dir"])
        output.mkdir()
        (output / "keep.elf").write_bytes(b"synthetic output")
        self.assertEqual(self.remove(project, "attempts", attempt_id=attempt["id"]), {"removed": attempt["id"]})
        self.assertEqual((output / "keep.elf").read_bytes(), b"synthetic output")
        current = self.creator.current()
        self.assertEqual(current["projects"][0]["attempts"], [])
        self.assertNotIn(job["id"], [item["id"] for item in current["jobs"]])

    def test_removal_validates_workspace_parent_identity_and_options(self):
        project = self.create()
        saved = self.save(project)
        attempt = self.attempt(project)
        other = {**project, "id": uuid.uuid4().hex}
        self.creator.store.save_project(other)
        before = self.records()
        invalid = [("remove", self.body(project_id="../bad")),
                   ("remove", self.body(project_id=project["id"], delete_files=True)),
                   ("remove", {"workspace": "/other", "project_id": project["id"]}),
                   ("configurations/remove", self.body(project_id=other["id"], configuration_id=saved["id"])),
                   ("configurations/remove", self.body(project_id=project["id"], configuration_id=None)),
                   ("attempts/remove", self.body(project_id=other["id"], attempt_id=attempt["id"])),
                   ("attempts/remove", self.body(project_id=project["id"], attempt_id="../bad"))]
        for endpoint, body in invalid:
            with self.subTest(endpoint=endpoint, body=body), self.assertRaises(ValueError):
                self.call("POST", endpoint, body)
        self.assertEqual(self.records(), before)

    def test_active_and_queued_work_prevents_all_project_removals(self):
        project = self.create()
        configuration = self.save(project)
        attempt = self.attempt(project)
        for status in ("queued", "running"):
            active = {"status": status, "metadata": {"workspace": str(self.workspace), "project_id": project["id"]}}
            with patch.object(self.app.context.jobs, "list", return_value=[active]):
                for kind, fields in (("", {}), ("configurations", {"configuration_id": configuration["id"]}),
                                     ("attempts", {"attempt_id": attempt["id"]})):
                    with self.subTest(status=status, kind=kind), self.assertRaisesRegex(ValueError, "operation is using"):
                        self.remove(project, kind, **fields)
        self.assertEqual(len(self.records()), 3)

    def test_actual_build_job_blocks_removal_until_its_terminal_result(self):
        project = self.create()
        plan = self.build_plan(project)
        started, release = threading.Event(), threading.Event()

        def execute(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release build.")
            return {"status": "built"}

        with patch("zephyr_utilities.features.projects.creator.execute_build", side_effect=execute):
            job = self.call("POST", "build", self.body(build_plan_id=plan["id"])).data
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaisesRegex(ValueError, "operation is using"):
                    self.remove(project)
            finally:
                release.set()
            self.assertEqual(wait_job(self.app.context.jobs, job["id"])["status"], "completed")
        self.remove(project)
        self.assertEqual(self.creator.current()["jobs"], [])

    def test_build_submission_cannot_race_project_removal(self):
        project = self.create()
        plan = self.build_plan(project)
        entered, release, submitted = threading.Event(), threading.Event(), threading.Event()
        original = self.creator.store.remove_project

        def remove(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release removal.")
            return original(*args)

        def build():
            submitted.set()
            return self.run_plan(plan)

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(self.creator.store, "remove_project", side_effect=remove):
            removal = pool.submit(self.remove, project)
            try:
                self.assertTrue(entered.wait(5))
                submission = pool.submit(build)
                self.assertTrue(submitted.wait(5))
            finally:
                release.set()
            self.assertEqual(removal.result(timeout=5), {"removed": project["id"]})
            with self.assertRaisesRegex(ValueError, "expired"):
                submission.result(timeout=5)
        self.assertFalse((Path(project["path"]) / "build").exists())

    def test_unknown_or_unreadable_children_block_cascade_before_any_deletion(self):
        project = self.create()
        configuration = self.save(project)
        attempt = self.attempt(project)
        path = self.creator.store.root / "attempts" / f"{attempt['id']}.json"
        for content in (json.dumps({**attempt, "schema_version": 99}), "not json",
                        json.dumps({**attempt, "profile": {"schema_version": 99}})):
            path.write_text(content)
            before = self.records()
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, "preserved"):
                self.remove(project)
            self.assertEqual(self.records(), before)
            self.assertEqual(self.creator.store.configuration(configuration["id"], project["id"], self.workspace), configuration)

    def test_symlinked_child_or_storage_directory_cannot_be_deleted(self):
        project = self.create()
        configuration = self.save(project)
        path = self.creator.store.root / "configurations" / f"{configuration['id']}.json"
        external = self.root / "external.json"
        path.rename(external)
        path.symlink_to(external)
        expected = external.read_bytes()
        with self.assertRaisesRegex(ValueError, "preserved"):
            self.remove(project)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.remove(project, "configurations", configuration_id=configuration["id"])
        path.unlink()
        external.rename(path)
        directory = path.parent
        moved = self.root / "configurations"
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.remove(project)
        self.assertEqual((moved / path.name).read_bytes(), expected)
        self.assertEqual(self.creator.store.project(project["id"], self.workspace), project)
