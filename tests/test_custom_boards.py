"""Reviewed custom board registration, persistence and analysis integration."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.projects.custom_board_records import CustomBoardStore
from zephyr_utilities.features.projects.creation import path_identity, select_profile
from zephyr_utilities.features.projects.hardware import discover_hardware

from test_projects_api import wait_job


class CustomBoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.zephyr = self.workspace / "zephyr"
        self.zephyr.mkdir()
        soc = self.zephyr / "soc/vendor/chip/soc.yml"
        soc.parent.mkdir(parents=True)
        soc.write_text("socs:\n  - name: chip\n")
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.service = self.app.context.services["custom_boards"]
        self.environments = self.app.context.services["projects"].store
        self.snapshot = {"schema_version": 1, "id": uuid.uuid4().hex,
                         "workspace": {"path": str(self.workspace)}, "zephyr": {"path": str(self.zephyr)},
                         "roots": {"boards": [], "socs": [{"path": str(self.zephyr), "origin": "Zephyr"}]},
                         "boards": [], "socs": [], "sdks": [], "modules": [], "diagnostics": [],
                         "request": {}, "cache": {"hit": False}, "status": "complete"}
        self.environments.save(self.snapshot)

    def call(self, method, endpoint, **body):
        return self.app.router.dispatch(Request(method, "/api/projects/custom-boards" + endpoint,
                                               body={"workspace": str(self.workspace), **body})).data

    def hardware(self, name="custom", base=None):
        root = base or self.workspace / "hardware" / name
        board = root / "boards/vendor" / name / "board.yml"
        board.parent.mkdir(parents=True)
        board.write_text(f"board:\n  name: {name}\n  vendor: vendor\n  socs:\n    - name: chip\n")
        return root

    def preview(self, **body):
        return self.call("POST", "/preview", **body)["plan"]

    def apply(self, plan):
        return self.call("POST", "/apply", plan_id=plan["id"])

    def register(self, name="custom", path=None):
        path = path or self.hardware(name)
        plan = self.preview(operation="register", name=name, path=str(path))
        return self.apply(plan)["registration"]

    def analyze(self):
        received = {}

        def observe(workspace, options, **kwargs):
            received.update(deepcopy(options))
            result = deepcopy(self.snapshot)
            result.update(id=uuid.uuid4().hex, request=deepcopy(options))
            result["roots"]["boards"] = [{"path": path, "origin": "explicit"} for path in options["board_roots"]]
            return result

        with patch("zephyr_utilities.features.projects.service.analyze_environment", side_effect=observe):
            job = self.app.context.services["projects"].start({})
            result = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        return received, result["result"]["snapshot"]

    def test_open_and_review_leave_sources_and_storage_unchanged(self):
        root = self.hardware()
        before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        self.assertEqual(self.call("GET", "")["registrations"], [])
        plan = self.preview(operation="register", name="Prototype", path=str(root))
        self.assertEqual(plan["targets"], ["custom/chip"])
        self.assertEqual(plan["files"], [])
        self.assertFalse(self.service.store.root.exists())
        self.assertEqual(before, {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()})
        self.assertFalse(self.app.context.jobs.list())

    def test_registration_survives_restart_and_is_workspace_scoped(self):
        record = self.register()
        restarted = Application(self.workspace, self.root / "data")
        self.addCleanup(restarted.close)
        result = restarted.context.services["custom_boards"].current()
        self.assertEqual(result["registrations"][0]["id"], record["id"])
        self.assertEqual(result["registrations"][0]["availability"], "available")
        other = self.root / "other"
        other.mkdir()
        restarted.context.workspace.set(other)
        self.assertEqual(restarted.context.services["custom_boards"].current()["registrations"], [])

    def test_registry_addition_stales_previous_snapshot_without_rewriting_it(self):
        path = self.environments.root / "snapshots" / f"{self.snapshot['id']}.json"
        before = path.read_bytes()
        self.register()
        current = self.environments.latest(self.workspace)
        self.assertEqual(current["status"], "stale")
        self.assertEqual(current["diagnostics"][-1]["code"], "custom-boards-changed")
        with self.assertRaisesRegex(ValueError, "Analyze the environment"):
            select_profile(str(self.workspace), {}, current)
        self.assertEqual(path.read_bytes(), before)

    def test_analysis_merges_registry_roots_without_persisting_as_manual_paths(self):
        record = self.register()
        received, result = self.analyze()
        self.assertEqual(received["board_roots"], [record["path"]])
        self.assertEqual(result["request"]["board_roots"], [])
        self.assertIn("custom_board_registry", result)
        self.assertEqual(self.environments.latest(self.workspace)["status"], "complete")
        self.assertEqual(self.environments.analysis_options(self.workspace, result["request"])["board_roots"], [record["path"]])

    def test_removal_leaves_files_and_reanalysis_excludes_root(self):
        record = self.register()
        self.analyze()
        root = Path(record["path"])
        before = {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}
        result = self.apply(self.preview(operation="remove", id=record["id"]))
        self.assertEqual(result["removed"], record["id"])
        self.assertEqual(self.environments.latest(self.workspace)["status"], "stale")
        received, _ = self.analyze()
        self.assertEqual(received["board_roots"], [])
        self.assertEqual(self.environments.latest(self.workspace)["status"], "complete")
        self.assertEqual(before, {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()})

    def test_moved_root_is_missing_until_reviewed_reconnection(self):
        record = self.register()
        self.analyze()
        old = Path(record["path"])
        moved = old.with_name("moved")
        old.rename(moved)
        item = self.call("GET", "")["registrations"][0]
        self.assertEqual(item["availability"], "missing")
        self.assertEqual(self.environments.analysis_options(self.workspace, {})["board_roots"], [])
        self.assertEqual(self.environments.latest(self.workspace)["status"], "stale")
        updated = self.apply(self.preview(operation="update", id=record["id"], name="Moved", path=str(moved)))["registration"]
        self.assertEqual(updated["id"], record["id"])
        self.assertEqual(updated["created_at"], record["created_at"])
        self.assertEqual(updated["path"], str(moved))
        self.assertEqual(self.call("GET", "")["registrations"][0]["availability"], "available")

    def test_recreated_root_and_replaced_boards_directory_are_unavailable(self):
        record = self.register()
        root = Path(record["path"])
        root.rename(root.with_name("old"))
        self.hardware()
        self.assertEqual(self.call("GET", "")["registrations"][0]["availability"], "replaced")
        updated = self.apply(self.preview(operation="update", id=record["id"], name="custom", path=str(root)))["registration"]
        (root / "boards").rename(root / "old-boards")
        (root / "boards").mkdir()
        self.assertNotEqual(updated["boards_identity"], path_identity(root / "boards"))
        self.assertEqual(self.call("GET", "")["registrations"][0]["availability"], "replaced")

    def test_symlink_replacement_is_not_followed(self):
        record = self.register()
        root = Path(record["path"])
        moved = root.with_name("old")
        root.rename(moved)
        root.symlink_to(moved, target_is_directory=True)
        self.assertEqual(self.call("GET", "")["registrations"][0]["availability"], "replaced")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.preview(operation="update", id=record["id"], name="custom", path=str(root))

    def test_duplicates_overlaps_and_duplicate_names_are_rejected(self):
        record = self.register()
        for name, root in (("other", Path(record["path"])), ("other", self.hardware("nested", Path(record["path"]) / "nested")),
                           ("CUSTOM", self.hardware("other"))):
            with self.subTest(name=name, root=root), self.assertRaises(ValueError):
                self.preview(operation="register", name=name, path=str(root))

    def test_outside_hidden_tool_and_symlink_roots_are_rejected(self):
        external = self.hardware("outside", self.root / "outside")
        hidden = self.hardware("hidden", self.workspace / ".private")
        tool = self.hardware("tool", self.zephyr / "local")
        ordinary = self.hardware("ordinary")
        link = self.workspace / "link"
        link.symlink_to(ordinary, target_is_directory=True)
        for root in (external, hidden, tool, link, self.workspace):
            with self.subTest(root=root), self.assertRaises(ValueError):
                self.preview(operation="register", name="invalid", path=str(root))

    def test_registration_review_rejects_changed_metadata(self):
        root = self.hardware()
        plan = self.preview(operation="register", name="custom", path=str(root))
        board = next(root.rglob("board.yml"))
        board.write_text(board.read_text().replace("name: custom", "name: changed"))
        with self.assertRaisesRegex(ValueError, "files changed"):
            self.apply(plan)
        self.assertEqual(self.service.store.read(self.workspace)["registrations"], [])

    def test_files_changed_during_discovery_do_not_produce_a_review(self):
        root = self.hardware()
        hardware = self.service._hardware

        def changed(*args):
            result = hardware(*args)
            board = next(root.rglob("board.yml"))
            board.write_text(board.read_text() + "# changed during discovery\n")
            return result

        with patch.object(self.service, "_hardware", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "changed while preparing"):
                self.preview(operation="register", name="custom", path=str(root))
        self.assertEqual(self.service.plans, {})

    def test_registration_requires_a_previous_environment_analysis(self):
        root = self.hardware()
        self.environments._index_path(self.workspace).unlink()
        with self.assertRaisesRegex(ValueError, "Analyze this workspace"):
            self.preview(operation="register", name="custom", path=str(root))

    def test_changed_registered_metadata_stales_snapshot_and_pending_review(self):
        record = self.register()
        self.analyze()
        plan = self.preview(operation="remove", id=record["id"])
        board = next(Path(record["path"]).rglob("board.yml"))
        board.write_text(board.read_text() + "# changed\n")
        self.assertEqual(self.environments.latest(self.workspace)["status"], "stale")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.apply(plan)

    def test_changed_analyzed_environment_invalidates_a_registration_review(self):
        root = self.hardware()
        plan = self.preview(operation="register", name="custom", path=str(root))
        newer = {**self.snapshot, "id": uuid.uuid4().hex}
        self.environments.save(newer)
        with self.assertRaisesRegex(ValueError, "analyzed environment changed"):
            self.apply(plan)
        self.assertFalse(self.service.store.root.exists())

    def test_stale_or_cross_workspace_reviews_do_not_write(self):
        root = self.hardware()
        plan = self.preview(operation="register", name="custom", path=str(root))
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaisesRegex(ValueError, "workspace changed"):
            self.apply(plan)
        self.app.context.workspace.set(self.workspace)
        self.service.plans[plan["id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.apply(plan)
        self.assertFalse(self.service.store.root.exists())

    def test_active_operations_block_registration_and_apply(self):
        root = self.hardware()
        plan = self.preview(operation="register", name="custom", path=str(root))
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def wait(emit, cancel):
            started.set()
            release.wait(3)

        job = self.app.context.jobs.submit("projects", wait, metadata={"workspace": str(self.workspace), "operation": "build"})
        self.assertTrue(started.wait(1))
        with self.assertRaisesRegex(ValueError, "active project operations"):
            self.apply(plan)
        with self.assertRaisesRegex(ValueError, "active project operations"):
            self.preview(operation="register", name="custom", path=str(root))
        release.set()
        self.assertEqual(wait_job(self.app.context.jobs, job["id"])["status"], "completed")
        self.apply(plan)

    def test_preview_lists_affected_saved_configurations_without_modifying_them(self):
        record = self.register()
        project = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": str(self.workspace),
                   "name": "Application", "path": str(self.workspace / "app"), "created_at": "now",
                   "profile": {"schema_version": 1, "target": "custom/chip", "roots": {"boards": [{"path": record["path"]}]}}}
        self.service.creator.store.save_project(project)
        plan = self.preview(operation="remove", id=record["id"])
        self.assertEqual(plan["dependencies"][0]["target"], "custom/chip")
        self.assertEqual(plan["dependencies"][0]["name"], "Original")
        self.apply(plan)
        self.assertEqual(self.service.creator.store.project(project["id"], str(self.workspace)), project)

    def test_unknown_registry_schema_is_preserved(self):
        self.register()
        path = self.service.store._path(self.workspace)
        value = json.loads(path.read_text())
        value["schema_version"] = 99
        path.write_text(json.dumps(value))
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Unsupported custom board registry"):
            self.service.current()
        with self.assertRaisesRegex(ValueError, "Unsupported custom board registry"):
            self.environments.latest(self.workspace)
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_empty_registry_keeps_saved_environment_usable(self):
        self.assertEqual(self.environments.latest(self.workspace), self.snapshot)
        reopened = CustomBoardStore(self.service.store.root)
        self.assertIsNone(reopened.signature(self.workspace))
        self.assertFalse(reopened.root.exists())

    def reference(self):
        self.hardware("reference", self.zephyr)
        (self.zephyr / "LICENSE").write_text("Apache License\nVersion 2.0, January 2004\nSynthetic test license marker.\n")
        (self.zephyr / "REUSE.toml").write_text('version = 1\n[[annotations]]\npath = ["**/*"]\n'
                                             'SPDX-License-Identifier = "Apache-2.0"\nSPDX-FileCopyrightText = "Synthetic fixture"\n')
        board = self.zephyr / "boards/vendor/reference"
        (board / "reference.dts").write_text('#include <vendor/chip.dtsi>\n/ { model = "Reference"; };\n')
        (board / "Kconfig.reference").write_text("config BOARD_REFERENCE\n\tselect SOC_CHIP\n")
        (board / "reference_defconfig").write_text("CONFIG_GPIO=y\n")
        (self.workspace / ".west").mkdir()
        hardware = discover_hardware([{"path": str(self.zephyr)}], [{"path": str(self.zephyr)}])
        self.snapshot.update(id=uuid.uuid4().hex, boards=hardware["boards"], socs=hardware["socs"])
        self.snapshot["workspace"].update(west_topdir=str(self.workspace), manifest_resolved=True)
        self.environments.save(self.snapshot)
        return self.snapshot["boards"][0]["id"]

    def test_guided_creation_publishes_exact_files_and_registers_new_root(self):
        reference = self.reference()
        plan = self.preview(operation="create", name="prototype", vendor="custom", full_name="Prototype board",
                            reference_id=reference, soc="chip")
        root = Path(plan["path"])
        self.assertFalse(root.exists())
        self.assertEqual(plan["targets"], ["prototype/chip"])
        result = self.apply(plan)
        self.assertTrue(result["needs_analysis"])
        self.assertEqual(result["registration"]["name"], "Prototype board")
        self.assertEqual({path.relative_to(root).as_posix(): path.read_text() for path in root.rglob("*") if path.is_file()},
                         {entry["path"]: entry["content"] for entry in plan["files"]})
        current = self.call("GET", "")
        self.assertEqual(current["registrations"][0]["boards"][0]["targets"], ["prototype/chip"])
        self.assertEqual(current["snapshot"]["status"], "stale")
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_generated_files_survive_registration_failure_with_recovery_message(self):
        reference = self.reference()
        plan = self.preview(operation="create", name="prototype", vendor="custom", reference_id=reference)
        with patch.object(self.service.store, "write", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(ValueError, "files were created.*Register existing board root"):
                self.apply(plan)
        self.assertTrue((Path(plan["path"]) / "boards/custom/prototype/board.yml").is_file())
        self.assertEqual(self.service.store.read(self.workspace)["registrations"], [])
        record = self.register("recovered", Path(plan["path"]))
        self.assertEqual(record["path"], plan["path"])

    def test_generated_reference_change_invalidates_apply_without_publication(self):
        reference = self.reference()
        plan = self.preview(operation="create", name="prototype", reference_id=reference)
        (self.zephyr / "boards/vendor/reference/reference_defconfig").write_text("CONFIG_GPIO=n\n")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.apply(plan)
        self.assertFalse(Path(plan["path"]).exists())
        self.assertFalse(self.service.store.root.exists())

    def test_invalid_api_fields_are_rejected(self):
        for body in ({"operation": "unexpected"}, {"operation": "register", "name": "x", "path": "x", "run": "code"},
                     {"operation": "remove", "id": "bad"}, {"operation": "register", "id": uuid.uuid4().hex}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.preview(**body)
