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
from zephyr_utilities.features.projects.templates import render_template


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

    def sample_plan(self):
        # Sample scanning has its own tests. These fixtures isolate publication
        # after the scanner has accepted exact nested source and license bytes.
        plan = self.preview()
        plan.pop("default_overlay", None)
        plan["template"] = {"id": "local-sample", "version": 1}
        plan["sample"] = {"schema_version": 1, "id": "hello-world", "fingerprint": "a" * 64}
        for path, content in {
                "boards/vendor/test_board.overlay": "/* copied board overlay */\n",
                "config/debug.conf": "CONFIG_DEBUG=y\n",
                "LICENSES/Apache-2.0.txt": "Synthetic license text\n"}.items():
            plan["files"].append({"path": path, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()})
        return plan

    def test_preview_is_read_only_and_contains_exact_generated_files_and_commands(self):
        before = list(self.workspace.iterdir())
        plan = self.preview()
        self.assertEqual(list(self.workspace.iterdir()), before)
        files = {item["path"]: item for item in plan["files"]}
        self.assertEqual(set(files), {"CMakeLists.txt", "prj.conf", "src/main.c", ".gitignore", "README.md", plan["default_overlay"]["path"]})
        for item in files.values():
            self.assertEqual(item["sha256"], hashlib.sha256(item["content"].encode()).hexdigest())
        self.assertIn(plan["recipe"]["display_configure"], files["README.md"]["content"])
        self.assertIn(plan["recipe"]["display_build"], files["README.md"]["content"])
        self.assertIn(self.body["target"], files["CMakeLists.txt"]["content"])
        self.assertEqual(plan["default_overlay"]["target"], self.body["target"])
        self.assertNotIn(str(self.sdk), files["src/main.c"]["content"])
        self.assertEqual(plan["profile"]["environment_fingerprint"], self.snapshot["fingerprint"])

    def test_apply_publishes_reviewed_bytes_and_directory_identity(self):
        plan = self.preview()
        project = apply_project(plan)
        destination = Path(project["path"])
        self.assertEqual(project["directory_identity"], path_identity(destination))
        self.assertEqual(project["profile"]["target"], self.body["target"])
        self.assertEqual(project["template"], {"id": "minimal-c", "version": 2})
        self.assertEqual(project["default_overlay"], plan["default_overlay"])
        for item in plan["files"]:
            self.assertEqual((destination / item["path"]).read_text(), item["content"])
        self.assertEqual({path.name for path in destination.parent.iterdir()}, {destination.name})

    def test_v2_overlay_activation_cannot_be_removed_or_edited_with_a_matching_file_hash(self):
        for template_id in ("minimal-c", "minimal-cpp"):
            for change in ("removed", "edited"):
                with self.subTest(template=template_id, change=change):
                    plan = self.preview(template_id=template_id)
                    cmake = next(item for item in plan["files"] if item["path"] == "CMakeLists.txt")
                    if change == "removed":
                        cmake["content"] = render_template({"id": template_id, "version": 1}, plan["name"],
                                                           plan["profile"], plan["recipe"])["CMakeLists.txt"]
                    else:
                        cmake["content"] = cmake["content"].replace("list(PREPEND EXTRA_DTC_OVERLAY_FILE",
                                                                  "list(APPEND EXTRA_DTC_OVERLAY_FILE")
                    cmake["sha256"] = hashlib.sha256(cmake["content"].encode()).hexdigest()
                    with self.assertRaisesRegex(ValueError, "overlay activation changed"):
                        apply_project(plan)
                    self.assertFalse(Path(plan["destination"]).exists())
        self.assertFalse((self.workspace / "applications").exists())

    def test_generic_publication_revalidates_source_after_staging_before_publication(self):
        destination = self.workspace / "hardware" / "custom"
        content = "board:\n  name: custom\n"
        files = [{"path": "boards/vendor/custom/board.yml", "content": content,
                  "sha256": hashlib.sha256(content.encode()).hexdigest()}]
        identity = creation._destination_identity(destination)
        calls = []

        def changed():
            calls.append(True)
            self.assertFalse(destination.exists())
            self.assertEqual(len(list(destination.parent.glob(".zephyr-create-*"))), 1)
            raise ValueError("Reference changed after review")

        with self.assertRaisesRegex(ValueError, "Reference changed"):
            creation.publish_files(destination, identity, files, revalidate=changed)
        self.assertEqual(calls, [True])
        self.assertFalse(destination.parent.exists())

    def test_generic_publication_keeps_concurrent_destination_created_by_revalidation(self):
        destination = self.workspace / "custom"
        content = "custom board source"
        files = [{"path": "board.txt", "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}]
        identity = creation._destination_identity(destination)

        def competing_creation():
            destination.mkdir()
            (destination / "keep.txt").write_text("User content")

        with self.assertRaisesRegex(ValueError, "already exists"):
            creation.publish_files(destination, identity, files, revalidate=competing_creation)
        self.assertEqual((destination / "keep.txt").read_text(), "User content")
        self.assertFalse((destination / "board.txt").exists())
        self.assertFalse(list(self.workspace.glob(".zephyr-create-*")))

    def test_retained_version_one_c_preserves_generated_bytes_and_remains_publishable(self):
        # Recorded before introducing template selection; only the fixture root
        # is normalized so the published version-one README is covered as well.
        expected = "a8fc55462439aa95cdd0430653030ef4e2bfe0934aef20240f8af763a9757b36"
        plan = self.preview()
        plan["template"] = {"id": "minimal-c", "version": 1}
        plan.pop("default_overlay")
        contents = render_template(plan["template"], plan["name"], plan["profile"], plan["recipe"])
        normalized = {name: value.replace(str(self.root), "<ROOT>") for name, value in contents.items()}
        self.assertEqual(hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest(), expected)
        plan["files"] = [{"path": name, "content": value, "sha256": hashlib.sha256(value.encode()).hexdigest()}
                         for name, value in contents.items()]
        project = apply_project(plan)
        self.assertEqual(project["template"], {"id": "minimal-c", "version": 1})
        self.assertNotIn("default_overlay", project)

    def test_cpp_preview_and_publication_use_independent_cpp_sources(self):
        plan = self.preview(template_id="minimal-cpp")
        self.assertFalse(Path(plan["destination"]).parent.exists())
        self.assertEqual(plan["template"], {"id": "minimal-cpp", "version": 2})
        files = {item["path"]: item["content"] for item in plan["files"]}
        self.assertEqual(set(files), {"CMakeLists.txt", "prj.conf", "src/main.cpp", ".gitignore", "README.md", plan["default_overlay"]["path"]})
        self.assertIn("project(hello_test LANGUAGES C CXX)", files["CMakeLists.txt"])
        self.assertIn("target_sources(app PRIVATE src/main.cpp)", files["CMakeLists.txt"])
        self.assertEqual(files["prj.conf"].splitlines()[1:], ["CONFIG_CPP=y", "CONFIG_PRINTK=y"])
        self.assertIn("constexpr", files["src/main.cpp"])
        self.assertIn("A minimal Zephyr C++ application generated from the minimal-cpp template, version 2.",
                      files["README.md"])
        self.assertIn(plan["recipe"]["display_configure"], files["README.md"])
        self.assertIn(plan["recipe"]["display_build"], files["README.md"])
        for path in ("prj.conf", "src/main.cpp"):
            self.assertNotIn(self.body["target"], files[path])
            self.assertNotIn(str(self.sdk), files[path])
        project = apply_project(plan)
        self.assertEqual(project["template"], {"id": "minimal-cpp", "version": 2})
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
        identities = [None, {}, {"id": "unknown", "version": 1}, {"id": "minimal-c", "version": 3},
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

    def test_sample_preview_retains_the_current_source_and_rejects_stale_selection(self):
        fixture = self.sample_plan()
        result = {"files": fixture["files"], "provenance": fixture["sample"]}
        with patch("zephyr_utilities.features.projects.samples.preview_sample", return_value=result) as preview:
            plan = self.preview(template_id="local-sample", sample_id="hello-world", sample_fingerprint="a" * 64)
            self.assertEqual(plan["files"], fixture["files"])
            self.assertEqual(plan["sample"], fixture["sample"])
            self.assertEqual(plan["template"], {"id": "local-sample", "version": 1})
            preview.assert_called_once_with(self.snapshot, "hello-world", plan["recipe"], profile=plan["profile"])
            with self.assertRaisesRegex(ValueError, "sample changed"):
                self.preview(template_id="local-sample", sample_id="hello-world", sample_fingerprint="b" * 64)
        self.assertFalse((self.workspace / "applications").exists())

    def test_sample_selection_fields_are_required_and_not_accepted_for_builtins(self):
        for changes in ({"sample_id": "hello-world"}, {"sample_fingerprint": "a" * 64},
                        {"template_id": "minimal-cpp", "sample_id": "hello-world"}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "Sample selection"):
                self.preview(**changes)
        for changes in ({}, {"sample_id": None, "sample_fingerprint": "a" * 64},
                        {"sample_id": "hello-world", "sample_fingerprint": ""},
                        {"sample_id": "hello-world", "sample_fingerprint": "g" * 64},
                        {"sample_id": "hello-world", "sample_fingerprint": []}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "current local sample"):
                self.preview(template_id="local-sample", **changes)
        self.assertFalse((self.workspace / "applications").exists())

    def test_sample_publication_preserves_nested_files_and_provenance(self):
        plan = self.sample_plan()
        with patch("zephyr_utilities.features.projects.samples.validate_sample_plan") as validate:
            project = apply_project(plan)
        self.assertEqual(validate.call_count, 2)
        destination = Path(project["path"])
        for item in plan["files"]:
            self.assertEqual((destination / item["path"]).read_bytes(), item["content"].encode())
        self.assertEqual(project["directory_identity"], path_identity(destination))
        self.assertEqual(project["sample"], plan["sample"])
        plan["sample"]["fingerprint"] = "changed"
        self.assertEqual(project["sample"]["fingerprint"], "a" * 64)
        self.assertEqual({path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()},
                         {item["path"] for item in project["files"]})

    def test_sample_change_before_or_during_publication_leaves_no_owned_files(self):
        for checks in ([ValueError("Sample inputs changed")], [None, ValueError("Sample inputs changed")]):
            with self.subTest(checks=checks):
                plan = self.sample_plan()
                with patch("zephyr_utilities.features.projects.samples.validate_sample_plan", side_effect=checks):
                    with self.assertRaisesRegex(ValueError, "Sample inputs changed"):
                        apply_project(plan)
                self.assertFalse((self.workspace / "applications").exists())

    def test_nested_manifest_rejects_traversal_collisions_and_hidden_build_inputs_before_writes(self):
        plan = self.sample_plan()
        bad_paths = ["../outside", "/outside", "src//extra.c", "src/./extra.c", "src/../extra.c",
                     "src\\outside.c", "src;outside.c", "src/line\nbreak.c", ".git/config", "build/config",
                     "src/.hidden/file.c", "src", "src/main.c", "/".join(["deep"] * 9)]
        for path in bad_paths:
            forged = deepcopy(plan)
            forged["files"][-1]["path"] = path
            with self.subTest(path=path), patch("zephyr_utilities.features.projects.samples.validate_sample_plan") as validate:
                with self.assertRaisesRegex(ValueError, "unsupported generated files"):
                    apply_project(forged)
                validate.assert_not_called()
        self.assertFalse((self.workspace / "applications").exists())

    def test_sample_publication_bounds_reviewed_bytes_and_file_count(self):
        plan = self.sample_plan()
        for limit, value in (("MAX_APPLICATION_FILES", len(plan["files"]) - 1),
                             ("MAX_APPLICATION_FILE_BYTES", 1), ("MAX_APPLICATION_BYTES", 1)):
            with self.subTest(limit=limit), patch.object(creation, limit, value):
                with self.assertRaises(ValueError):
                    apply_project(plan)
        self.assertFalse((self.workspace / "applications").exists())

    def test_cancellation_after_nested_files_are_staged_cleans_only_owned_tree(self):
        plan = self.sample_plan()
        cancel = Event()
        original = creation._validate_staging

        def cancel_after_validation(*args):
            original(*args)
            cancel.set()

        with patch("zephyr_utilities.features.projects.samples.validate_sample_plan"), \
                patch.object(creation, "_validate_staging", side_effect=cancel_after_validation):
            with self.assertRaises(CancelledError):
                apply_project(plan, cancel=cancel)
        self.assertFalse((self.workspace / "applications").exists())

    def test_nested_foreign_edit_survives_failed_publication(self):
        plan = self.sample_plan()

        def edit_and_fail(parent, source, destination):
            folder = os.open(source, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent)
            try:
                fd = os.open("boards/vendor/test_board.overlay", os.O_WRONLY | os.O_TRUNC, dir_fd=folder)
                with os.fdopen(fd, "w") as stream:
                    stream.write("concurrent nested edit")
            finally:
                os.close(folder)
            raise ValueError("synthetic collision")

        with patch("zephyr_utilities.features.projects.samples.validate_sample_plan"), \
                patch.object(creation, "_rename_noreplace", side_effect=edit_and_fail):
            with self.assertRaisesRegex(ValueError, "synthetic collision"):
                apply_project(plan)
        remaining = list((self.workspace / "applications").glob(".zephyr-create-*/boards/vendor/test_board.overlay"))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].read_text(), "concurrent nested edit")
        self.assertFalse(Path(plan["destination"]).exists())

    def test_replaced_nested_directory_is_never_followed_or_cleaned(self):
        plan = self.sample_plan()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "test_board.overlay").write_text("external file must survive")
        validate = creation._validate_staging

        def redirect(directory, directories, files):
            os.rename("boards/vendor", "boards/saved", src_dir_fd=directory, dst_dir_fd=directory)
            os.symlink(outside, "boards/vendor", dir_fd=directory)
            validate(directory, directories, files)

        with patch("zephyr_utilities.features.projects.samples.validate_sample_plan"), \
                patch.object(creation, "_validate_staging", side_effect=redirect):
            with self.assertRaisesRegex(ValueError, "staged application changed"):
                apply_project(plan)
        self.assertEqual((outside / "test_board.overlay").read_text(), "external file must survive")
        remaining = list((self.workspace / "applications").glob(".zephyr-create-*/boards/saved/test_board.overlay"))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].read_text(), "/* copied board overlay */\n")
        self.assertFalse(Path(plan["destination"]).exists())

    def test_foreign_file_added_to_a_nested_directory_prevents_publication_and_survives(self):
        plan = self.sample_plan()
        validate = creation._validate_staging

        def add_file(directory, directories, files):
            descriptor = directories[Path("boards/vendor")]["descriptor"]
            fd = os.open("user.overlay", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644, dir_fd=descriptor)
            with os.fdopen(fd, "w") as stream:
                stream.write("concurrent additional source")
            validate(directory, directories, files)

        with patch("zephyr_utilities.features.projects.samples.validate_sample_plan"), \
                patch.object(creation, "_validate_staging", side_effect=add_file):
            with self.assertRaisesRegex(ValueError, "foreign files were preserved"):
                apply_project(plan)
        remaining = list((self.workspace / "applications").glob(".zephyr-create-*/boards/vendor/user.overlay"))
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].read_text(), "concurrent additional source")
        self.assertFalse(Path(plan["destination"]).exists())

    def test_publication_refreshes_a_filesystems_initial_empty_directory_listing(self):
        plan = self.preview()
        original_validate = creation._validate_staging
        listdir = os.listdir

        def validate(directory, directories, files):
            # Reproduce a filesystem whose pre-write directory descriptors retain
            # an empty initial listing even after child files are flushed.
            def stale_listing(path):
                pinned = {directory, *(entry["descriptor"] for entry in directories.values())}
                return [] if isinstance(path, int) and path in pinned else listdir(path)

            with patch.object(creation.os, "listdir", side_effect=stale_listing):
                return original_validate(directory, directories, files)

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
