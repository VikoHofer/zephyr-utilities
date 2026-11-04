"""An explicit Memory handoff retains one inspected image and its original evidence."""

from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.memory.runner import file_hash, generate_reports
from zephyr_utilities.features.memory.snapshots import inspect_selected_build

from test_core import GENERATOR
from test_platform import wait_for_job
import test_projects_api as existing_api


class MemoryHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.zephyr = self.workspace / "zephyr"
        self.generator = self.zephyr / "scripts/footprint/size_report"
        self.generator.parent.mkdir(parents=True)
        self.generator.write_text(GENERATOR)
        self.build = self.make_build(self.workspace / "build")
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.store = self.app.context.services["memory"].store

    def make_build(self, path):
        (path / "zephyr").mkdir(parents=True)
        (path / "CMakeCache.txt").write_text(
            f"ZEPHYR_BASE:PATH={self.zephyr}\nWEST_PYTHON:FILEPATH={sys.executable}\n"
            f"APPLICATION_SOURCE_DIR:PATH={path.parent}\nBOARD:STRING=demo/chip\n")
        (path / "build_info.yml").write_text("cmake:\n  board:\n    name: demo\n    qualifiers: chip\n")
        (path / "zephyr/.config").write_text('CONFIG_KERNEL_BIN_NAME="zephyr"\nCONFIG_MAIN_STACK_SIZE=1024\n')
        (path / "zephyr/zephyr.elf").write_bytes(b"\x7fELF" + path.name.encode())
        (path / "zephyr/zephyr.map").write_text("map evidence")
        (path / "zephyr/zephyr.dts").write_text('/dts-v1/; / { model = "Fixture"; };')
        return path

    def call(self, path, body):
        return self.app.router.dispatch(Request("POST", path, body=body)).data

    def selection(self, build=None, domain=None):
        observed = self.app.context.builds.inspect(self.workspace, build or self.build)
        return {"workspace": str(self.workspace), "inspection_id": observed["id"],
                "domain": domain or observed["default_domain"]}

    def start(self, selection, build=None, **extra):
        return self.call("/api/memory/jobs", {"build_path": str(build or self.build),
                                             "selection": selection, "name": "Selected", **extra})

    def queued_task(self, selection, build=None):
        tasks = []
        with patch.object(self.app.context.jobs, "submit", side_effect=lambda feature, task, **kwargs: (
                tasks.append(task) or {"id": "queued", "status": "queued"})):
            self.start(selection, build)
        return tasks[0]

    def test_preview_is_read_only_and_shares_exact_context_and_elf(self):
        selected = self.selection()
        observed = self.app.context.builds.get(self.workspace, selected["inspection_id"])
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("subprocess.Popen", side_effect=AssertionError("Preview must not run tools")):
            preview = self.call("/api/memory/inspection", selected)
        self.assertEqual({key: preview[key] for key in selected}, selected)
        self.assertEqual(preview["context"], observed["domains"][0]["context"])
        self.assertEqual(preview["elf"], {"path": str(self.build / "zephyr/zephyr.elf"),
                                          "sha256": file_hash(self.build / "zephyr/zephyr.elf")})
        self.assertTrue(preview["build"]["ready"])
        self.assertEqual(self.app.context.jobs.list(), [])
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_unavailable_report_environment_is_a_read_only_preview(self):
        selected = self.selection()
        self.generator.unlink()
        preview = self.call("/api/memory/inspection", selected)
        self.assertFalse(preview["build"]["ready"])
        self.assertIn("generator", preview["build"]["error"])
        with self.assertRaisesRegex(ValueError, "generator"):
            self.start(selected)
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_effective_target_is_preserved_in_preview_and_saved_snapshot(self):
        (self.build / "build_info.yml").write_text(
            "cmake:\n  board:\n    name: effective\n    revision: '2'\n    qualifiers: soc/cpu\n")
        selected = self.selection()
        preview = self.call("/api/memory/inspection", selected)
        self.assertEqual(preview["context"]["cached_board"], "demo/chip")
        self.assertEqual(preview["build"]["board"], "effective@2/soc/cpu")
        result = self.queued_task(selected)(lambda message: None, threading.Event())
        self.assertEqual(self.store.load(result["snapshot_id"])["build"]["board"], "effective@2/soc/cpu")
        (self.build / "build_info.yml").unlink()
        preview = self.call("/api/memory/inspection", self.selection())
        self.assertIsNone(preview["build"]["board"])

    def test_conflicting_application_or_zephyr_context_does_not_choose_another_environment(self):
        for category, key in (("application", "source-dir"), ("zephyr", "zephyr-base")):
            (self.build / "build_info.yml").write_text(f"cmake:\n  {category}:\n    {key}: /different/location\n")
            with self.subTest(category=category), self.assertRaisesRegex(ValueError, "metadata disagrees"):
                self.call("/api/memory/inspection", self.selection())

    def test_selection_inputs_cannot_override_workspace_image_or_paths(self):
        selected = self.selection()
        for invalid in ({**selected, "workspace": str(self.root)}, {**selected, "domain": "other"},
                        {**selected, "domain": []}, {**selected, "inspection_id": "unknown"},
                        {**selected, "path": str(self.build)}, {"inspection_id": selected["inspection_id"]}, None):
            with self.subTest(selection=invalid), self.assertRaises(ValueError):
                self.call("/api/memory/inspection", invalid)
            with self.subTest(job_selection=invalid), self.assertRaises(ValueError):
                self.start(invalid)
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.start(selected, self.workspace)
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.start(selected, elf_path=str(self.build / "zephyr/zephyr.elf"))
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_changed_or_missing_consumed_evidence_requires_a_new_inspection(self):
        for relative in ("CMakeCache.txt", "build_info.yml", "zephyr/zephyr.elf", "zephyr/.config",
                         "zephyr/zephyr.map", "zephyr/zephyr.dts"):
            path = self.build / relative
            original = path.read_bytes()
            for missing in (False, True):
                selected = self.selection()
                if missing:
                    path.unlink()
                else:
                    path.write_bytes(original + b"\nchanged")
                try:
                    with self.subTest(path=relative, missing=missing), self.assertRaises(ValueError):
                        self.call("/api/memory/inspection", selected)
                    with self.subTest(job_path=relative, missing=missing), self.assertRaises(ValueError):
                        self.start(selected)
                finally:
                    path.write_bytes(original)
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_new_evidence_is_not_attributed_to_the_previous_inspection(self):
        for relative in ("build_info.yml", "zephyr/.config", "zephyr/zephyr.map", "zephyr/zephyr.dts", "domains.yaml"):
            path = self.build / relative
            original = path.read_bytes() if path.exists() else None
            path.unlink(missing_ok=True)
            selected = self.selection()
            path.write_bytes(original or b"new evidence")
            try:
                with self.subTest(path=relative), self.assertRaises(ValueError):
                    self.call("/api/memory/inspection", selected)
            finally:
                if original is None:
                    path.unlink()

    def test_unrelated_binary_changes_do_not_redirect_the_selected_elf(self):
        binary = self.build / "zephyr/zephyr.bin"
        binary.write_bytes(b"old")
        selected = self.selection()
        binary.write_bytes(b"new")
        (self.build / "zephyr/zephyr.hex").write_text("new unrelated output")
        preview = self.call("/api/memory/inspection", selected)
        self.assertTrue(preview["build"]["ready"])
        self.assertEqual(preview["elf"]["sha256"], file_hash(self.build / "zephyr/zephyr.elf"))

    def test_alternate_final_elf_is_rejected_without_falling_back(self):
        alternate = self.build / "zephyr/custom.elf"
        alternate.write_bytes(b"\x7fELFcustom")
        (self.build / "zephyr/.config").write_text('CONFIG_KERNEL_BIN_NAME="custom"\n')
        selected = self.selection()
        for action in (lambda: self.call("/api/memory/inspection", selected), lambda: self.start(selected)):
            with self.assertRaisesRegex(ValueError, "different ELF"):
                action()
        # The pre-existing directory-based workflow still supports custom binary names.
        snapshot = self.store.capture(self.build)
        self.assertEqual(snapshot["build"]["elf_path"], str(alternate))
        self.assertEqual(snapshot["schema_version"], 1)

    def test_missing_elf_at_inspection_cannot_be_added_to_the_old_selection(self):
        elf = self.build / "zephyr/zephyr.elf"
        elf.unlink()
        selected = self.selection()
        elf.write_bytes(b"\x7fELFnew")
        with self.assertRaisesRegex(ValueError, "not available"):
            self.call("/api/memory/inspection", selected)

    def test_symlink_and_replaced_directory_cannot_reuse_original_identity(self):
        selected = self.selection()
        elf = self.build / "zephyr/zephyr.elf"
        original = elf.read_bytes()
        outside = self.root / "same.elf"
        outside.write_bytes(original)
        elf.unlink()
        elf.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.call("/api/memory/inspection", selected)
        elf.unlink()
        elf.write_bytes(original)
        previous = self.build.with_name("previous")
        self.build.rename(previous)
        self.make_build(self.build)
        with self.assertRaisesRegex(ValueError, "directory changed"):
            self.call("/api/memory/inspection", selected)

    def test_submitted_selection_survives_eviction_expiry_and_workspace_switch(self):
        selected = self.selection()
        task = self.queued_task(selected)
        # A submitted task retains its detached guard, not a reference to this cache.
        self.app.context.builds.observations[selected["inspection_id"]]["expires"] = 0
        self.app.context.builds._expire()
        self.app.context.workspace.set(self.root)
        result = task(lambda message: None, threading.Event())
        snapshot = self.store.load(result["snapshot_id"])
        self.assertEqual(snapshot["build"]["path"], str(self.build))
        self.assertEqual(snapshot["metadata"]["artifacts"]["elf"]["sha256"], file_hash(self.build / "zephyr/zephyr.elf"))
        with self.assertRaisesRegex(ValueError, "workspace changed"):
            self.start(selected)
        self.app.context.workspace.set(self.workspace)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.start(selected)

    def test_output_changed_while_queued_is_rejected_before_any_tool(self):
        task = self.queued_task(self.selection())
        (self.build / "zephyr/zephyr.elf").write_bytes(b"\x7fELFchanged")
        with patch("subprocess.Popen", side_effect=AssertionError("Must not run tools")):
            with self.assertRaisesRegex(ValueError, "output changed"):
                task(lambda message: None, threading.Event())
        self.assertEqual(self.store.list(), [])

    def test_selected_reports_use_archived_bytes_during_a_transient_live_elf_change(self):
        selected = self.selection()
        live = self.build / "zephyr/zephyr.elf"
        original = live.read_bytes()
        expected_hash = file_hash(live)
        analyzed_paths = []

        def transient_change(build, directory, **kwargs):
            analyzed = Path(build["elf_path"])
            analyzed_paths.append(analyzed)
            self.assertNotEqual(analyzed, live)
            self.assertEqual(analyzed.read_bytes(), original)
            live.write_bytes(b"\x7fELFno-dwarf")
            try:
                # The fixture generator fails on no-dwarf input, so success proves it read the archive.
                return generate_reports(build, directory, **kwargs)
            finally:
                live.write_bytes(original)

        with patch("zephyr_utilities.features.memory.snapshots.generate_reports", side_effect=transient_change):
            result = self.queued_task(selected)(lambda message: None, threading.Event())
        snapshot = self.store.load(result["snapshot_id"])
        self.assertEqual(snapshot["build"]["elf_path"], str(live))
        self.assertEqual(snapshot["metadata"]["artifacts"]["elf"]["path"], str(live))
        self.assertEqual(snapshot["metadata"]["artifacts"]["elf"]["sha256"], expected_hash)
        self.assertEqual(analyzed_paths, [self.store.root / snapshot["metadata"]["artifacts"]["elf"]["object"]])
        self.assertEqual(live.read_bytes(), original)
        for command in snapshot["metadata"]["commands"]:
            self.assertEqual(command[command.index("--kernel") + 1], str(analyzed_paths[0]))

    def test_corrupted_existing_objects_are_rejected_before_tools_run(self):
        for relative in ("zephyr/zephyr.elf", "zephyr/.config", "zephyr/zephyr.map", "zephyr/zephyr.dts",
                         "CMakeCache.txt", "build_info.yml"):
            selected = self.selection()
            corrupted = self.store.objects / file_hash(self.build / relative)
            corrupted.write_bytes(b"corrupted retained evidence")
            try:
                with self.subTest(path=relative), patch("subprocess.Popen", side_effect=AssertionError("Must not run tools")):
                    with self.assertRaisesRegex(ValueError, "Retained build evidence.*corrupted"):
                        self.queued_task(selected)(lambda message: None, threading.Event())
            finally:
                corrupted.unlink()
        self.assertEqual(self.store.list(), [])
        self.assertFalse(list(self.store.root.glob(".pending-*")))

    def test_retained_object_change_during_reports_prevents_publication(self):
        def corrupt_retained_elf(build, directory, **kwargs):
            result = generate_reports(build, directory, **kwargs)
            Path(build["elf_path"]).write_bytes(b"corrupted after analysis")
            return result

        selected = self.selection()
        with patch("zephyr_utilities.features.memory.snapshots.generate_reports", side_effect=corrupt_retained_elf):
            with self.assertRaisesRegex(ValueError, "Retained build evidence.*corrupted"):
                self.queued_task(selected)(lambda message: None, threading.Event())
        self.assertEqual(self.store.list(), [])
        self.assertFalse(list(self.store.root.glob(".pending-*")))

    def test_captured_evidence_must_match_even_if_live_files_are_restored(self):
        for relative in ("zephyr/zephyr.elf", "zephyr/.config", "CMakeCache.txt"):
            selected = self.selection()
            guard = self.app.context.services["memory"]._selection(selected)
            path = self.build / relative
            original = path.read_bytes()
            archive = self.store._archive

            def changed_after_inspection(selection):
                build = inspect_selected_build(selection)
                path.write_bytes(original + b"\nchanged")
                return build

            def restored_after_archive(source, staging):
                captured = archive(source, staging)
                if source == path:
                    path.write_bytes(original)
                return captured

            with self.subTest(path=relative), patch(
                    "zephyr_utilities.features.memory.snapshots.inspect_selected_build", side_effect=changed_after_inspection), patch.object(
                    self.store, "_archive", side_effect=restored_after_archive), patch(
                    "subprocess.Popen", side_effect=AssertionError("Must not run tools")):
                with self.assertRaisesRegex(ValueError, "evidence differs"):
                    self.store.capture(self.build, selection=guard)
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.store.list(), [])
        self.assertFalse(list(self.store.root.glob(".pending-*")))

    def test_sysbuild_handoff_uses_selected_child_and_checks_manifest_before_publish(self):
        root = self.workspace / "sysbuild"
        self.make_build(root / "app")
        boot = self.make_build(root / "boot")
        manifest = root / "domains.yaml"
        manifest.write_text("default: app\ndomains:\n  - name: app\n    build_dir: app\n  - name: boot\n    build_dir: boot\n")
        selected = self.selection(root, "boot")
        preview = self.call("/api/memory/inspection", selected)
        self.assertEqual(preview["build"]["path"], str(boot))
        self.assertEqual(preview["build"]["domain"], "boot")
        job = self.start(selected, boot)
        completed = wait_for_job(self.app.context.jobs, job["id"])
        self.assertEqual(completed["status"], "completed", completed)
        snapshot = self.store.load(completed["result"]["snapshot_id"])
        self.assertEqual(snapshot["build"]["domain"], "boot")
        self.assertEqual(snapshot["metadata"]["artifacts"]["elf"]["sha256"], preview["elf"]["sha256"])

        def change_manifest(build, directory, **kwargs):
            result = generate_reports(build, directory, **kwargs)
            manifest.write_text(manifest.read_text().replace("default: app", "default: boot"))
            return result

        with patch("zephyr_utilities.features.memory.snapshots.generate_reports", side_effect=change_manifest):
            job = self.start(selected, boot)
            failed = wait_for_job(self.app.context.jobs, job["id"])
        self.assertEqual(failed["status"], "failed", failed)
        self.assertIn("context changed", failed["error"])
        self.assertEqual(len(self.store.list()), 1)

    def test_legacy_and_namespaced_directory_jobs_remain_supported(self):
        with patch.object(self.store, "capture", return_value={"id": "captured"}) as capture:
            for endpoint in ("/api/jobs", "/api/memory/jobs"):
                job = self.call(endpoint, {"build_path": str(self.build), "name": "Legacy"})
                result = wait_for_job(self.app.context.jobs, job["id"])
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["snapshot_id"], "captured")
        self.assertEqual(capture.call_count, 2)
        self.assertTrue(all("selection" not in call.kwargs for call in capture.call_args_list))


class MemoryHandoffHttpTests(unittest.TestCase):
    requires_loopback = True
    setUp = existing_api.ProjectsHttpTests.setUp
    close = existing_api.ProjectsHttpTests.close
    request = existing_api.ProjectsHttpTests.request

    def test_inspection_uses_shared_authentication_and_validation(self):
        with self.assertRaises(HTTPError) as error:
            self.request("/api/memory/inspection", method="POST", body={}, authorized=False)
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            self.request("/api/memory/inspection", method="POST", body={})
        self.assertEqual(error.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
