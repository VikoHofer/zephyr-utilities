"""Reviewed registration/reconnection never rewrites application or historical evidence."""

from copy import deepcopy
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import uuid

import test_project_creator as creator_tests


class ProjectImportTests(unittest.TestCase):
    def setUp(self):
        creator_tests.CreatorServiceTests.setUp(self)
        self.lifecycle = self.creator

    def body(self, **values):
        return {"workspace": str(self.workspace), **values}

    def application(self, path=None):
        path = path or self.workspace / "existing-app"
        (path / "src").mkdir(parents=True, exist_ok=True)
        (path / "CMakeLists.txt").write_text(
            'cmake_minimum_required(VERSION 3.20.0)\n'
            'find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n'
            'project(existing)\nmessage(FATAL_ERROR "Import must never execute CMake")\n'
            'target_sources(app PRIVATE src/main.c)\n')
        (path / "src/main.c").write_text("int main(void) { return 0; }\n")
        (path / "prj.conf").write_text("CONFIG_PRINTK=y\n")
        return path

    def preview(self, path=None, **extra):
        return self.lifecycle.import_preview(self.body(name="Existing application", path=str(path or self.workspace / "existing-app"),
            board_id="board-one", target="demo/chip", sdk_id="sdk-one", **extra))["plan"]

    def imported(self, path=None):
        path = self.application(path)
        plan = self.preview(path)
        return self.lifecycle.import_project(self.body(plan_id=plan["id"]))["project"]

    def snapshot_files(self, path):
        return {item.relative_to(path).as_posix(): item.read_bytes() for item in path.rglob("*") if item.is_file()}

    def test_import_is_reviewed_metadata_only_and_does_not_retrofit_generated_files(self):
        path = self.application()
        (path / "build").mkdir()
        (path / "build/firmware.bin").write_bytes(b"unchanged build output")
        (path / ".git").mkdir()
        (path / ".git/HEAD").write_text("ref: refs/heads/main\n")
        before = self.snapshot_files(path)
        with patch("subprocess.run", side_effect=AssertionError("Import must not execute tools")):
            plan = self.preview(path)
            self.assertFalse(self.lifecycle.store.root.exists())
            project = self.lifecycle.import_project(self.body(plan_id=plan["id"]))["project"]
        self.assertEqual(before, self.snapshot_files(path))
        self.assertEqual(project["schema_version"], 2)
        self.assertEqual(project["revision"], 0)
        self.assertEqual(project["template"], {"id": "imported", "version": 1})
        self.assertEqual(project["profile"], plan["profile"])
        self.assertEqual(project["initial_profile"], project["profile"])
        self.assertEqual(project["registration"]["origin"], "imported")
        self.assertEqual(project["registration"]["sources"], plan["sources"])
        self.assertEqual(project["registered_sources"], plan["sources"])
        self.assertEqual(self.lifecycle.store.project(project["id"], str(self.workspace)), project)
        self.assertNotIn("default_overlay", project)
        self.assertNotIn("sample", project)
        self.assertEqual({entry["path"] for entry in project["files"]}, {"CMakeLists.txt", "src/main.c", "prj.conf"})
        self.analyze.assert_not_called()
        with self.assertRaisesRegex(ValueError, "already used"):
            self.lifecycle.import_project(self.body(plan_id=plan["id"]))

    def test_import_rejects_changed_files_replaced_directory_and_duplicate_previews(self):
        path = self.application()
        plan = self.preview(path)
        (path / "prj.conf").write_text("CONFIG_PRINTK=n\n")
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            self.lifecycle.import_project(self.body(plan_id=plan["id"]))
        plan = self.preview(path)
        old = path.with_name("old-directory")
        path.rename(old)
        shutil.copytree(old, path)
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            self.lifecycle.import_project(self.body(plan_id=plan["id"]))
        first, second = self.preview(path), self.preview(path)
        project = self.lifecycle.import_project(self.body(plan_id=first["id"]))["project"]
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.lifecycle.import_project(self.body(plan_id=second["id"]))
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.preview(path)
        moved = path.with_name("moved-directory")
        path.rename(moved)
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.preview(moved)
        self.assertEqual(self.lifecycle.store.project(project["id"], str(self.workspace))["path"], str(path))

    def test_import_validates_exact_profile_workspace_and_protected_directories(self):
        path = self.application()
        for values in ({"target": "unknown/chip"}, {"sdk_id": "missing"}, {"board_id": "missing"}):
            body = self.body(name="App", path=str(path), board_id="board-one", target="demo/chip", sdk_id="sdk-one")
            body.update(values)
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.lifecycle.import_preview(body)
        for destination in (self.root / "outside-app", self.workspace / "zephyr/app", self.workspace / "module/app",
                            self.workspace / "sdk/app", self.workspace / "build/app", self.workspace / ".hidden/app"):
            self.application(destination)
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.preview(destination)
        link = self.workspace / "linked-app"
        link.symlink_to(path, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.preview(link)
        (path / "src/linked.c").symlink_to(path / "src/main.c")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.preview(path)

    def test_import_requires_root_cmake_and_unchanged_environment_at_apply(self):
        path = self.application()
        (path / "CMakeLists.txt").unlink()
        with self.assertRaisesRegex(ValueError, "root CMakeLists"):
            self.preview(path)
        self.application(path)
        plan = self.preview(path)
        changed = deepcopy(self.snapshot)
        changed["id"] = uuid.uuid4().hex
        changed["sdks"][0]["fingerprint"] = "changed-sdk"
        self.creator.environments.save(changed)
        with self.assertRaisesRegex(ValueError, "environment changed"):
            self.lifecycle.import_project(self.body(plan_id=plan["id"]))
        self.assertFalse(self.lifecycle.store.root.exists())

    def test_import_expiry_workspace_switch_and_active_destination_leave_sources_untouched(self):
        path = self.application()
        before = self.snapshot_files(path)
        plan = self.preview(path)
        self.lifecycle.plans[plan["id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.lifecycle.import_project(self.body(plan_id=plan["id"]))
        with patch.object(self.app.context.jobs, "list", return_value=[{"status": "running", "metadata": {
                "workspace": str(self.workspace), "destination": str(path)}}]):
            with self.assertRaisesRegex(ValueError, "operation is using"):
                self.preview(path)
        with patch.object(self.app.context.jobs, "list", return_value=[{"status": "running", "metadata": {
                "workspace": str(self.workspace), "destination": str(self.workspace / "unrelated")}}]):
            plan = self.preview(path)
        other = self.root / "other-workspace"
        other.mkdir()
        self.app.context.workspace.set(str(other))
        with self.assertRaisesRegex(ValueError, "workspace changed"):
            self.lifecycle.import_project(self.body(plan_id=plan["id"]))
        self.assertEqual(before, self.snapshot_files(path))

    def test_reconnect_preserves_ids_original_files_profiles_configurations_and_history(self):
        project = self.imported()
        path = Path(project["path"])
        configuration = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": str(self.workspace),
                         "project_id": project["id"], "name": "Debug", "profile": deepcopy(project["profile"])}
        self.lifecycle.store.save_configuration(configuration)
        attempt = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": str(self.workspace),
                   "project_id": project["id"], "status": "built", "profile": deepcopy(project["profile"]),
                   "configuration": {"id": configuration["id"], "name": "Debug"},
                   "context": {"source_dir": str(path)}, "build_dir": str(path / "build/previous")}
        self.lifecycle.store.save_attempt(attempt)
        history = {file: file.read_bytes() for folder in ("attempts", "configurations")
                   for file in (self.lifecycle.store.root / folder).glob("*.json")}
        pending = {"id": uuid.uuid4().hex, "project_id": project["id"], "workspace": str(self.workspace)}
        self.lifecycle._remember("build", pending, {})
        moved = path.with_name("moved-app")
        path.rename(moved)
        (moved / "src/main.c").write_text("int main(void) { return 1; }\n")
        before = self.snapshot_files(moved)
        with patch("subprocess.run", side_effect=AssertionError("Reconnect must not run tools")):
            plan = self.lifecycle.reconnect_preview(self.body(project_id=project["id"], path=str(moved)))["plan"]
            self.assertTrue(plan["same_directory"])
            self.assertEqual(plan["old_path"], project["path"])
            self.assertEqual(plan["file_changes"]["changed"], ["src/main.c"])
            updated = self.lifecycle.reconnect_project(self.body(plan_id=plan["id"]))["project"]
        self.assertEqual(updated["id"], project["id"])
        self.assertEqual(updated["path"], str(moved))
        self.assertEqual(updated["revision"], 1)
        for key in ("files", "profile", "initial_profile", "template", "created_at"):
            self.assertEqual(updated[key], project[key])
        self.assertEqual(updated["registration"]["initial_path"], str(path))
        self.assertEqual(updated["registration"]["reconnections"][0]["from_path"], str(path))
        self.assertEqual(updated["registration"]["sources"], plan["sources"])
        self.assertEqual(updated["registered_sources"], plan["sources"])
        self.assertEqual(before, self.snapshot_files(moved))
        self.assertEqual(history, {file: file.read_bytes() for file in history})
        self.assertNotIn(pending["id"], self.lifecycle.plans)

    def test_reconnect_same_path_replacement_is_explicit_and_legacy_provenance_survives(self):
        project = self.imported()
        project.update(schema_version=1, template={"id": "local-sample", "version": 1},
                       sample={"schema_version": 1, "id": "basic/blinky", "files": deepcopy(project["files"])},
                       default_overlay={"historical": "preserved"})
        for key in ("revision", "registration", "initial_profile"):
            project.pop(key)
        self.lifecycle.store._write(self.lifecycle.store.root / "projects" / f"{project['id']}.json", project)
        path = Path(project["path"])
        path.rename(path.with_name("original-app"))
        self.application(path)
        plan = self.lifecycle.reconnect_preview(self.body(project_id=project["id"], path=str(path)))["plan"]
        self.assertFalse(plan["same_directory"])
        self.assertTrue(any("different directory" in warning for warning in plan["warnings"]))
        updated = self.lifecycle.reconnect_project(self.body(plan_id=plan["id"]))["project"]
        self.assertEqual(updated["schema_version"], 2)
        self.assertEqual(updated["revision"], 1)
        for key in ("files", "sample", "default_overlay", "profile"):
            self.assertEqual(updated[key], project[key])

    def test_reconnect_rejects_duplicate_noop_changed_inputs_and_concurrent_registration_edit(self):
        project = self.imported()
        path = Path(project["path"])
        with self.assertRaisesRegex(ValueError, "already connected"):
            self.lifecycle.reconnect_preview(self.body(project_id=project["id"], path=str(path)))
        other = self.imported(self.workspace / "other-app")
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.lifecycle.reconnect_preview(self.body(project_id=project["id"], path=other["path"]))
        moved = path.with_name("moved-app")
        path.rename(moved)
        plan = self.lifecycle.reconnect_preview(self.body(project_id=project["id"], path=str(moved)))["plan"]
        (moved / "prj.conf").write_text("CONFIG_PRINTK=n\n")
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            self.lifecycle.reconnect_project(self.body(plan_id=plan["id"]))
        plan = self.lifecycle.reconnect_preview(self.body(project_id=project["id"], path=str(moved)))["plan"]
        updated = {**project, "schema_version": 2, "revision": 1, "name": "Renamed elsewhere"}
        self.lifecycle.store.update_project(updated, project)
        with self.assertRaisesRegex(ValueError, "saved application changed"):
            self.lifecycle.reconnect_project(self.body(plan_id=plan["id"]))
        self.assertEqual(self.lifecycle.store.project(project["id"], str(self.workspace)), updated)

    def test_reconnect_requires_idle_both_at_preview_and_apply(self):
        project = self.imported()
        moved = Path(project["path"]).with_name("moved-app")
        Path(project["path"]).rename(moved)
        active = [{"status": "running", "metadata": {"workspace": str(self.workspace), "project_id": project["id"]}}]
        with patch.object(self.app.context.jobs, "list", return_value=active):
            with self.assertRaisesRegex(ValueError, "operation is using"):
                self.lifecycle.reconnect_preview(self.body(project_id=project["id"], path=str(moved)))
        plan = self.lifecycle.reconnect_preview(self.body(project_id=project["id"], path=str(moved)))["plan"]
        with patch.object(self.app.context.jobs, "list", return_value=active):
            with self.assertRaisesRegex(ValueError, "operation is using"):
                self.lifecycle.reconnect_project(self.body(plan_id=plan["id"]))
        self.assertEqual(self.lifecycle.store.project(project["id"], str(self.workspace))["path"], project["path"])
