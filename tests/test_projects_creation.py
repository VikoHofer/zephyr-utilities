"""Reviewed source generation preserves destinations and concurrent user changes."""

from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects.creation import (
    apply_project, open_directory, path_identity, preview_project, validate_identity,
)
from zephyr_utilities.features.projects import creation


class ProjectCreationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / ".west").mkdir()
        self.zephyr = self.workspace / "zephyr"
        self.zephyr.mkdir()
        self.sdk = self.root / "sdk"
        self.sdk.mkdir()
        self.snapshot = {
            "schema_version": 1, "id": "e" * 32, "fingerprint": "synthetic-environment",
            "status": "complete", "workspace": {"path": str(self.workspace),
                "west_topdir": str(self.workspace), "manifest_resolved": True,
                "manifest_path": str(self.zephyr / "west.yml")},
            "zephyr": {"path": str(self.zephyr), "version": "4.4.2"},
            "boards": [{"id": "board-id", "name": "board", "status": "discovered",
                        "targets": ["board/chip/cpu", "board@1.0.0/chip/cpu"]}],
            "sdks": [{"id": "sdk-id", "path": str(self.sdk), "version": "1.0.1",
                      "fingerprint": "sdk-fingerprint", "status": "discovered",
                      "toolchains": [{"name": "arm-zephyr-eabi", "family": "GNU"}]}],
            "tools": [{"name": name, "path": f"/usr/bin/{name.lower()}", "status": "discovered"}
                      for name in ("Python", "cmake", "ninja")],
            "modules": [], "roots": {}, "request": {"board_roots": [], "sdk_paths": [str(self.sdk)]},
        }
        self.body = {"name": "hello_test", "board_id": "board-id", "target": "board@1.0.0/chip/cpu", "sdk_id": "sdk-id"}

    def tearDown(self):
        self.temp.cleanup()

    def recipe(self, profile, source, output):
        return {"cwd": str(self.workspace), "configure": ["cmake", "-S", str(source), "-B", str(output)],
                "build": ["cmake", "--build", str(output)], "environment": {"ZEPHYR_SDK_INSTALL_DIR": profile["sdk"]["path"]},
                "display_configure": f"cmake -S '{source}' -B '{output}'",
                "display_build": f"cmake --build '{output}'"}

    def preview(self, **changes):
        return preview_project(str(self.workspace), {**self.body, **changes}, self.snapshot, recipe_factory=self.recipe)

    def test_preview_is_read_only_and_contains_exact_generated_files_and_commands(self):
        before = list(self.workspace.iterdir())
        plan = self.preview()
        self.assertEqual(list(self.workspace.iterdir()), before)
        files = {item["path"]: item for item in plan["files"]}
        self.assertEqual(set(files), {"CMakeLists.txt", "prj.conf", "src/main.c", ".gitignore", "README.md"})
        for item in files.values():
            self.assertEqual(item["sha256"], hashlib.sha256(item["content"].encode()).hexdigest())
        self.assertIn(plan["recipe"]["display_configure"], files["README.md"]["content"])
        self.assertIn(plan["recipe"]["display_build"], files["README.md"]["content"])
        self.assertNotIn(self.body["target"], files["CMakeLists.txt"]["content"])
        self.assertNotIn(str(self.sdk), files["src/main.c"]["content"])
        self.assertEqual(plan["profile"]["environment_fingerprint"], self.snapshot["fingerprint"])

    def test_apply_publishes_reviewed_bytes_and_directory_identity(self):
        plan = self.preview()
        project = apply_project(plan)
        destination = Path(project["path"])
        self.assertEqual(project["directory_identity"], path_identity(destination))
        self.assertEqual(project["profile"]["target"], self.body["target"])
        self.assertEqual(project["template"], {"id": "minimal-c", "version": 1})
        for item in plan["files"]:
            self.assertEqual((destination / item["path"]).read_text(), item["content"])
        self.assertEqual({path.name for path in destination.parent.iterdir()}, {destination.name})

    def test_default_and_explicit_c_preserve_version_one_generated_bytes(self):
        # Recorded before introducing template selection; only the fixture root
        # is normalized so the published version-one README is covered as well.
        expected = "a8fc55462439aa95cdd0430653030ef4e2bfe0934aef20240f8af763a9757b36"
        for changes in ({}, {"template_id": "minimal-c"}):
            with self.subTest(changes=changes):
                plan = self.preview(**changes)
                contents = {item["path"]: item["content"].replace(str(self.root), "<ROOT>")
                            for item in plan["files"]}
                self.assertEqual(hashlib.sha256(json.dumps(contents, sort_keys=True).encode()).hexdigest(), expected)
                self.assertEqual(plan["template"], {"id": "minimal-c", "version": 1})

    def test_cpp_preview_and_publication_use_independent_cpp_sources(self):
        plan = self.preview(template_id="minimal-cpp")
        self.assertFalse(Path(plan["destination"]).parent.exists())
        self.assertEqual(plan["template"], {"id": "minimal-cpp", "version": 1})
        files = {item["path"]: item["content"] for item in plan["files"]}
        self.assertEqual(set(files), {"CMakeLists.txt", "prj.conf", "src/main.cpp", ".gitignore", "README.md"})
        self.assertIn("project(hello_test LANGUAGES C CXX)", files["CMakeLists.txt"])
        self.assertIn("target_sources(app PRIVATE src/main.cpp)", files["CMakeLists.txt"])
        self.assertEqual(files["prj.conf"].splitlines()[1:], ["CONFIG_CPP=y", "CONFIG_PRINTK=y"])
        self.assertIn("constexpr", files["src/main.cpp"])
        self.assertIn("A minimal Zephyr C++ application generated from the minimal-cpp template, version 1.",
                      files["README.md"])
        self.assertIn(plan["recipe"]["display_configure"], files["README.md"])
        self.assertIn(plan["recipe"]["display_build"], files["README.md"])
        for path in ("CMakeLists.txt", "prj.conf", "src/main.cpp"):
            self.assertNotIn(self.body["target"], files[path])
            self.assertNotIn(str(self.sdk), files[path])
        project = apply_project(plan)
        self.assertEqual(project["template"], {"id": "minimal-cpp", "version": 1})
        for item in plan["files"]:
            self.assertEqual((Path(project["path"]) / item["path"]).read_text(), item["content"])
        self.assertFalse((Path(project["path"]) / "src/main.c").exists())
        plan["template"]["id"] = "minimal-c"
        self.assertEqual(project["template"]["id"], "minimal-cpp")

    def test_invalid_template_selection_is_read_only(self):
        for template_id in ("", None, "unknown", "MINIMAL-C", 1, {}, ["minimal-c"]):
            with self.subTest(template_id=template_id), self.assertRaisesRegex(ValueError, "application template"):
                self.preview(template_id=template_id)
        self.assertFalse((self.workspace / "applications").exists())

    def test_unknown_template_identity_or_version_cannot_be_published(self):
        plan = self.preview()
        identities = [None, {}, {"id": "unknown", "version": 1}, {"id": "minimal-c", "version": 2},
                      {"id": "minimal-c", "version": True}, {"id": "minimal-c", "version": 1.0},
                      {"id": "minimal-c", "version": "1"}, {"id": "minimal-c", "version": 1, "path": "custom"}]
        for identity in identities:
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "application template"):
                apply_project({**plan, "template": identity})
        without_template = {key: value for key, value in plan.items() if key != "template"}
        with self.assertRaisesRegex(ValueError, "application template"):
            apply_project(without_template)
        self.assertFalse((self.workspace / "applications").exists())

    def test_template_switches_or_forged_paths_cannot_be_published(self):
        for template_id, other in (("minimal-c", "minimal-cpp"), ("minimal-cpp", "minimal-c")):
            plan = self.preview(template_id=template_id)
            with self.subTest(template_id=template_id):
                with self.assertRaisesRegex(ValueError, "unsupported generated files"):
                    apply_project({**plan, "template": {"id": other, "version": 1}})
                for path in ("src/extra.cpp", "../outside.c", "src/nested/main.cpp", "CMakeLists.txt"):
                    forged = deepcopy(plan)
                    forged["files"][2]["path"] = path
                    with self.subTest(path=path), self.assertRaisesRegex(ValueError, "unsupported generated files"):
                        apply_project(forged)
        self.assertFalse((self.workspace / "applications").exists())

    def test_publication_refreshes_a_filesystems_initial_empty_directory_listing(self):
        plan = self.preview()
        original_validate = creation._validate_staging
        listdir = os.listdir

        def validate(directory, source, source_identity, files):
            # Reproduce a filesystem whose pre-write directory descriptors retain
            # an empty initial listing even after child files are flushed.
            def stale_listing(path):
                return [] if isinstance(path, int) and path in {directory, source} else listdir(path)

            with patch.object(creation.os, "listdir", side_effect=stale_listing):
                return original_validate(directory, source, source_identity, files)

        with patch.object(creation, "_validate_staging", side_effect=validate):
            project = apply_project(plan)
        self.assertTrue((Path(project["path"]) / "src/main.c").is_file())

    def test_destination_whitespace_is_rejected_for_the_supported_zephyr_build_system(self):
        with self.assertRaisesRegex(ValueError, "application paths containing whitespace"):
            self.preview(destination="my applications/a project")
        self.assertFalse((self.workspace / "my applications").exists())

    def test_invalid_names_and_payloads_are_rejected(self):
        for name in ("", "../bad", "1bad", "two words", "bad;thing", "bad\nname", "${BAD}", "a" * 65):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.preview(name=name)
        for changes in ({"unexpected": True}, {"sdk_id": []}, {"destination": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.preview(**changes)

    def test_traversal_and_outside_destinations_are_rejected(self):
        for destination in ("../elsewhere", "apps/../demo", str(self.root / "outside"), ".", "bad;path/demo", "bad\npath/demo"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.preview(destination=destination)

    def test_dependencies_tools_and_hidden_directories_cannot_be_destinations(self):
        dependency = self.workspace / "modules/lib"
        dependency.mkdir(parents=True)
        self.snapshot["modules"] = [{"path": str(dependency), "status": "discovered", "is_module": True}]
        for destination in ("zephyr/demo", "modules/lib/demo", ".west/demo", ".venv/demo", "apps/build/demo", "apps/.git/demo"):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.preview(destination=destination)

    def test_existing_empty_directory_or_file_is_never_replaced(self):
        destination = self.workspace / "hello"
        destination.mkdir()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.preview(destination="hello")
        destination.rmdir()
        destination.write_text("keep")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.preview(destination="hello")
        self.assertEqual(destination.read_text(), "keep")

    def test_existing_or_dangling_symlink_is_never_a_destination(self):
        destination = self.workspace / "hello"
        destination.symlink_to(self.root / "absent")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.preview(destination="hello")

    def test_symlinked_parent_is_rejected_at_preview(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.workspace / "apps").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            self.preview(destination="apps/hello")
        self.assertEqual(list(outside.iterdir()), [])

    def test_unknown_conflicting_and_unqualified_targets_are_rejected(self):
        for target in ("unknown", "board", "board@2.0.0/chip/cpu", "board/chip/other"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                self.preview(target=target)
        self.snapshot["boards"][0]["status"] = "conflict"
        with self.assertRaises(ValueError):
            self.preview()

    def test_sdk_requires_an_installed_gnu_component_and_exact_identity(self):
        with self.assertRaises(ValueError):
            self.preview(sdk_id="another-sdk")
        self.snapshot["sdks"][0]["toolchains"] = [{"name": "llvm", "family": "LLVM"}]
        with self.assertRaisesRegex(ValueError, "GNU"):
            self.preview()

    def test_equal_version_sdk_installations_retain_the_chosen_path(self):
        another = deepcopy(self.snapshot["sdks"][0])
        another.update(id="second-sdk", path=str(self.root / "other-sdk"), fingerprint="second-fingerprint")
        self.snapshot["sdks"].append(another)
        plan = self.preview(sdk_id="second-sdk")
        self.assertEqual(plan["profile"]["sdk"]["path"], another["path"])
        self.assertEqual(plan["recipe"]["environment"]["ZEPHYR_SDK_INSTALL_DIR"], another["path"])

    def test_stale_unresolved_or_other_workspace_snapshots_are_rejected(self):
        for change in ("stale", "unresolved", "other-workspace"):
            with self.subTest(change=change):
                snapshot = deepcopy(self.snapshot)
                if change == "stale":
                    snapshot["status"] = "stale"
                elif change == "unresolved":
                    snapshot["workspace"]["manifest_resolved"] = False
                else:
                    snapshot["workspace"]["path"] = str(self.root)
                with self.assertRaises(ValueError):
                    preview_project(str(self.workspace), self.body, snapshot, recipe_factory=self.recipe)

    def test_missing_tool_is_actionable(self):
        self.snapshot["tools"][1]["status"] = "unavailable"
        with self.assertRaisesRegex(ValueError, "cmake"):
            self.preview()

    def test_changed_existing_ancestor_blocks_apply(self):
        applications = self.workspace / "applications"
        applications.mkdir()
        plan = self.preview()
        applications.rename(self.workspace / "old-applications")
        applications.mkdir()
        with self.assertRaisesRegex(ValueError, "ancestors changed"):
            apply_project(plan)
        self.assertEqual(list(applications.iterdir()), [])

    def test_new_parent_after_preview_requires_a_new_preview(self):
        plan = self.preview()
        (self.workspace / "applications").mkdir()
        with self.assertRaisesRegex(ValueError, "ancestors changed"):
            apply_project(plan)

    def test_symlink_swap_after_preview_does_not_write_to_its_target(self):
        applications = self.workspace / "applications"
        applications.mkdir()
        plan = self.preview()
        applications.rmdir()
        outside = self.root / "outside"
        outside.mkdir()
        applications.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            apply_project(plan)
        self.assertEqual(list(outside.iterdir()), [])

    def test_destination_appearing_during_publication_is_preserved(self):
        plan = self.preview()
        original = creation._rename_noreplace

        def concurrent_directory(parent, source, destination):
            os.mkdir(destination, dir_fd=parent)
            original(parent, source, destination)

        with patch.object(creation, "_rename_noreplace", side_effect=concurrent_directory):
            with self.assertRaisesRegex(ValueError, "appeared after preview"):
                apply_project(plan)
        destination = Path(plan["destination"])
        self.assertTrue(destination.is_dir())
        self.assertEqual(list(destination.iterdir()), [])
        self.assertEqual(list(destination.parent.iterdir()), [destination])

    def test_cancel_before_and_during_creation_cleans_owned_files(self):
        for template_id in ("minimal-c", "minimal-cpp"):
            with self.subTest(template_id=template_id):
                plan = self.preview(template_id=template_id)
                cancel = Event()
                cancel.set()
                with self.assertRaises(CancelledError):
                    apply_project(plan, cancel=cancel)
                self.assertFalse((self.workspace / "applications").exists())
                original = creation.check_cancel
                calls = 0

                def cancel_midway(event):
                    nonlocal calls
                    calls += 1
                    # Cancel after the source was written, covering cleanup of
                    # both main.c and main.cpp through their pinned directory.
                    if calls == 5:
                        raise CancelledError()
                    original(event)

                with patch.object(creation, "check_cancel", side_effect=cancel_midway):
                    with self.assertRaises(CancelledError):
                        apply_project(plan)
                self.assertFalse((self.workspace / "applications").exists())

    def test_partial_write_failure_cleans_only_created_files_and_parents(self):
        plan = self.preview()
        with patch.object(creation.os, "fsync", side_effect=OSError("synthetic disk failure")):
            with self.assertRaisesRegex(OSError, "synthetic disk failure"):
                apply_project(plan)
        self.assertFalse((self.workspace / "applications").exists())

    def test_failure_preserves_foreign_edits_inside_the_staging_directory(self):
        plan = self.preview()

        def edit_and_fail(parent, source, destination):
            folder = os.open(source, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent)
            try:
                fd = os.open("README.md", os.O_WRONLY | os.O_TRUNC, dir_fd=folder)
                with os.fdopen(fd, "w") as stream:
                    stream.write("concurrent user edit")
            finally:
                os.close(folder)
            raise ValueError("synthetic collision")

        with patch.object(creation, "_rename_noreplace", side_effect=edit_and_fail):
            with self.assertRaisesRegex(ValueError, "synthetic collision"):
                apply_project(plan)
        remaining = list((self.workspace / "applications").glob(".zephyr-create-*/README.md"))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].read_text(), "concurrent user edit")
        self.assertFalse(Path(plan["destination"]).exists())

    def test_sync_failure_after_publication_returns_the_created_project(self):
        plan = self.preview()
        fsync = creation.os.fsync
        rename = creation._rename_noreplace
        published = False

        def publish(*args):
            nonlocal published
            rename(*args)
            published = True

        def synchronize(fd):
            if published:
                raise OSError("synthetic directory sync failure")
            fsync(fd)

        with patch.object(creation, "_rename_noreplace", side_effect=publish), \
                patch.object(creation.os, "fsync", side_effect=synchronize):
            project = apply_project(plan)
        self.assertTrue(Path(project["path"]).is_dir())
        self.assertEqual(project["diagnostics"][0]["code"], "directory-sync")

    def test_modified_plan_bytes_are_rejected(self):
        plan = self.preview()
        plan["files"][0]["content"] += "unexpected code\n"
        with self.assertRaisesRegex(ValueError, "contents changed"):
            apply_project(plan)
        self.assertFalse(Path(plan["destination"]).parent.exists())

    def test_directory_identity_rejects_replacement_and_symlinks(self):
        path = self.workspace / "app"
        path.mkdir()
        identity = path_identity(path)
        validate_identity(path, identity)
        path.rename(self.workspace / "old-app")
        path.mkdir()
        with self.assertRaisesRegex(ValueError, "ancestors changed"):
            validate_identity(path, identity)
        path.rmdir()
        path.symlink_to(self.workspace / "old-app", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            validate_identity(path, identity)

    def test_directory_descriptor_pins_the_previewed_path_without_following_links(self):
        parent = self.workspace / "applications"
        path = parent / "app"
        path.mkdir(parents=True)
        identity = path_identity(path)
        descriptor = open_directory(path, identity)
        try:
            self.assertEqual(os.fstat(descriptor).st_ino, identity[-1]["inode"])
        finally:
            os.close(descriptor)
        parent.rename(self.workspace / "old-applications")
        path.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "ancestors changed"):
            open_directory(path, identity)
        path.rmdir()
        parent.rmdir()
        parent.symlink_to(self.workspace / "old-applications", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinks"):
            open_directory(path, identity)
