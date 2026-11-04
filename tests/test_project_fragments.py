"""Fragment discovery, explicit ordered build selection and source-change guards."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.features.projects.builds import command_recipe
from zephyr_utilities.features.projects.creator import source_inputs
from zephyr_utilities.features.projects.fragments import automatic_fragments, normalize_fragments

import test_project_configurations as configurations
import test_project_creator as creator_tests


class ProjectFragmentTests(unittest.TestCase):
    setUp = creator_tests.CreatorServiceTests.setUp
    call = creator_tests.CreatorServiceTests.call
    body = creator_tests.CreatorServiceTests.body
    preview = creator_tests.CreatorServiceTests.preview
    create = creator_tests.CreatorServiceTests.create
    save = configurations.BuildConfigurationTests.save
    selected_plan = configurations.BuildConfigurationTests.selected_plan
    run_plan = configurations.BuildConfigurationTests.run_plan

    def app_files(self):
        project = self.create()
        root = Path(project["path"])
        for name, content in {"configs/first.conf": "CONFIG_MAIN_STACK_SIZE=1024\n",
                              "configs/last.conf": "CONFIG_MAIN_STACK_SIZE=2048\n",
                              "app.overlay": "/ { zephyr,user { automatic; }; };\n",
                              "overlays/first.overlay": "/ { zephyr,user { order = <1>; }; };\n",
                              "overlays/last.overlay": "/ { zephyr,user { order = <2>; }; };\n"}.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return project, root

    def choices(self, mode="append"):
        return {"schema_version": 1,
                "kconfig": {"mode": mode, "files": ["configs/first.conf", "configs/last.conf"]},
                "overlays": {"mode": mode, "files": ["overlays/first.overlay", "overlays/last.overlay"]}}

    def catalogue(self, project):
        return self.call("POST", "configuration-files", self.body(project_id=project["id"])).data

    def test_catalogue_is_sorted_read_only_and_excludes_build_hidden_and_tool_inputs(self):
        project, root = self.app_files()
        for folder in ("build", ".hidden", "node_modules", "generated", "tools", "build-debug", "out"):
            (root / folder).mkdir()
            (root / folder / "skip.conf").write_text("# excluded\n")
        (root / "custom-output").mkdir()
        (root / "custom-output/CMakeCache.txt").write_text("# generated\n")
        (root / "custom-output/skip.conf").write_text("# excluded\n")
        (root / "alias.conf").symlink_to(root / "prj.conf")
        before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        jobs = self.app.context.jobs.list()
        with patch("zephyr_utilities.features.projects.creator.execute_build") as execute:
            data = self.catalogue(project)
        execute.assert_not_called()
        self.assertEqual(data["workspace"], str(self.workspace))
        self.assertEqual(data["project_id"], project["id"])
        self.assertEqual(data["files"]["kconfig"], ["configs/first.conf", "configs/last.conf", "prj.conf"])
        self.assertEqual(data["files"]["overlays"], ["app.overlay", "overlays/first.overlay", "overlays/last.overlay"])
        self.assertEqual(data["diagnostics"], [])
        self.assertEqual(before, {path: path.read_bytes() for path in root.rglob("*") if path.is_file()})
        self.assertEqual(jobs, self.app.context.jobs.list())

    def test_append_and_replace_preserve_requested_order_and_persist_profile_version_two(self):
        project, root = self.app_files()
        original = self.creator.store.project(project["id"], self.workspace)
        before = source_inputs(root)
        for mode in ("append", "replace"):
            selected = self.choices(mode)
            selected["overlays"]["files"].reverse()
            saved = self.save(project, name=mode, fragments=selected)
            self.assertEqual(saved["profile"]["schema_version"], 2)
            self.assertEqual(saved["profile"]["fragments"], selected)
            plan = self.selected_plan(project, saved)
            args = plan["recipe"]["configure"]
            prefix = "EXTRA_" if mode == "append" else ""
            for kind, variable in (("kconfig", "CONF_FILE"), ("overlays", "DTC_OVERLAY_FILE")):
                value = ";".join(str(root / name) for name in selected[kind]["files"])
                self.assertIn(f"-D{prefix}{variable}={value}", args)
                self.assertEqual(sum(variable + "=" in arg for arg in args), 1)
            self.assertFalse(Path(plan["build_dir"]).exists())
            self.assertFalse((root / "build").exists())
        reopened = Application(self.workspace, self.root / "data")
        self.addCleanup(reopened.close)
        stored = reopened.context.services["project_creator"].current()["projects"][0]
        self.assertEqual({item["name"] for item in stored["configurations"]}, {"append", "replace"})
        self.assertEqual(stored["profile"], project["profile"])
        self.assertEqual(self.creator.store.project(project["id"], self.workspace), original)
        self.assertEqual(before, source_inputs(root))

    def test_mixed_modes_and_legacy_automatic_emit_only_the_intended_variables(self):
        project, root = self.app_files()
        legacy = self.save(project, name="Legacy")
        self.assertEqual(legacy["profile"]["schema_version"], 1)
        self.assertNotIn("fragments", legacy["profile"])
        explicit = self.save(project, name="Automatic", fragments=automatic_fragments())
        for saved in (legacy, explicit):
            self.assertFalse(any("CONF_FILE=" in arg or "OVERLAY_FILE=" in arg
                                 for arg in self.selected_plan(project, saved)["recipe"]["configure"]))
        selected = self.choices("replace")
        selected["kconfig"] = {"mode": "auto", "files": []}
        saved = self.save(project, name="Only overlay", fragments=selected)
        args = self.selected_plan(project, saved)["recipe"]["configure"]
        self.assertFalse(any("CONF_FILE=" in arg for arg in args))
        self.assertIn(f"-DDTC_OVERLAY_FILE={root}/overlays/first.overlay;{root}/overlays/last.overlay", args)

    def test_fragment_schema_modes_empty_nonautomatic_lists_and_duplicates_are_rejected(self):
        invalid = [None, [], {}, {**self.choices(), "schema_version": 2}, {**self.choices(), "extra": True}]
        for choice in ({"mode": "none", "files": []}, {"mode": "replace", "files": []},
                       {"mode": "append", "files": []}, {"mode": "auto", "files": ["prj.conf"]},
                       {"mode": "append", "files": ["prj.conf", "prj.conf"]},
                       {"mode": "append", "files": ["prj.conf"] * 33},
                       {"mode": "append", "files": "prj.conf"}):
            invalid.append({**self.choices(), "kconfig": choice})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_fragments(value)

    def test_unsafe_paths_missing_symlink_binary_and_oversized_fragments_cannot_be_saved(self):
        project, root = self.app_files()
        (root / "binary.conf").write_bytes(b"\x00binary")
        (root / "invalid.conf").write_bytes(b"\xff")
        (root / "large.conf").write_bytes(b"x" * (256 * 1024 + 1))
        (root / "alias.conf").symlink_to(root / "prj.conf")
        (root / "alias-dir").symlink_to(root / "configs", target_is_directory=True)
        (root / "custom-output").mkdir()
        (root / "custom-output/build.ninja").write_text("# generated\n")
        (root / "custom-output/generated.conf").write_text("# generated\n")
        invalid = ["../escape.conf", "/tmp/escape.conf", "configs//first.conf", "./prj.conf", "app.overlay",
                   "x;prj.conf", "${X}.conf", "@X@.conf", "back\\slash.conf", "two words.conf", "line\nbreak.conf", "configs/[first.conf",
                   "build/generated.conf", ".hidden/hidden.conf", "missing.conf", "alias.conf", "alias-dir/first.conf",
                   "binary.conf", "invalid.conf", "large.conf", "custom-output/generated.conf"]
        for name in invalid:
            selected = automatic_fragments()
            selected["kconfig"] = {"mode": "append", "files": [name]}
            with self.subTest(name=name), self.assertRaises((OSError, ValueError)):
                self.save(project, fragments=selected)
        self.assertFalse((self.creator.store.root / "configurations").exists())
        catalogue = self.catalogue(project)
        self.assertNotIn("binary.conf", catalogue["files"]["kconfig"])
        self.assertNotIn("large.conf", catalogue["files"]["kconfig"])
        self.assertEqual(len(catalogue["diagnostics"]), 3)

    def test_catalogue_and_save_reject_replaced_app_and_workspace_mismatch(self):
        project, root = self.app_files()
        with self.assertRaisesRegex(ValueError, "workspace changed"):
            self.call("POST", "configuration-files", self.body(project_id=project["id"], workspace="/other"))
        root.rename(root.with_name("moved"))
        root.mkdir()
        with self.assertRaisesRegex(ValueError, "changed"):
            self.catalogue(project)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.save(project, fragments=self.choices())

    def test_total_fragment_read_limit_fails_without_a_partial_catalogue_or_saved_choice(self):
        project, _ = self.app_files()
        with patch("zephyr_utilities.features.projects.fragments.MAX_FRAGMENT_TOTAL", 1):
            with self.assertRaisesRegex(ValueError, "read limit"):
                self.catalogue(project)
            with self.assertRaisesRegex(ValueError, "read limit"):
                self.save(project, fragments=self.choices())
        self.assertFalse((self.creator.store.root / "configurations").exists())

    def test_recipe_is_pure_and_rejects_unsupported_profile_versions(self):
        project, root = self.app_files()
        profile = {**project["profile"], "schema_version": 2, "fragments": self.choices()}
        # Recipe preparation validates choices without touching their contents.
        (root / "configs/first.conf").unlink()
        args = command_recipe(profile, root, root / "build/new")["configure"]
        self.assertTrue(any(arg.startswith("-DEXTRA_CONF_FILE=") for arg in args))
        for candidate in ({**profile, "schema_version": 1}, {**profile, "schema_version": 3},
                          {**profile, "schema_version": True}, {**profile, "schema_version": 2.0},
                          {**profile, "fragments": {**self.choices(), "schema_version": 99}},
                          {key: value for key, value in profile.items() if key != "fragments"}):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                command_recipe(candidate, root, root / "build/new")
        with self.assertRaises(ValueError):
            command_recipe(profile, root.with_name("has space"), root / "build/new")

    def test_edits_before_preview_are_captured_and_edits_after_preview_stop_execution(self):
        project, root = self.app_files()
        saved = self.save(project, fragments=self.choices())
        selected = root / "configs/last.conf"
        selected.write_text("CONFIG_MAIN_STACK_SIZE=3072\n")
        plan = self.selected_plan(project, saved)
        self.assertEqual(plan["profile"]["fragments"], self.choices())
        with patch("zephyr_utilities.features.projects.creator.execute_build", return_value={"status": "built"}):
            job = self.run_plan(plan)
        self.assertEqual(job["status"], "completed", job.get("error"))
        attempt = job["result"]["attempt"]
        self.assertEqual(attempt["profile"]["fragments"], self.choices())
        plan = self.selected_plan(project, saved)
        selected.write_text("CONFIG_MAIN_STACK_SIZE=4096\n")
        with patch("zephyr_utilities.features.projects.creator.execute_build") as execute:
            job = self.run_plan(plan)
        self.assertEqual(job["status"], "failed")
        self.assertIn("changed since preview", job["error"])
        execute.assert_not_called()
        self.assertFalse(Path(plan["build_dir"]).exists())

    def test_missing_selected_file_is_rejected_at_preview(self):
        project, root = self.app_files()
        saved = self.save(project, fragments=self.choices())
        (root / "configs/first.conf").unlink()
        with self.assertRaises((OSError, ValueError)):
            self.selected_plan(project, saved)

    def test_future_profile_or_fragment_schemas_are_preserved_and_not_silently_automatic(self):
        project, _ = self.app_files()
        saved = self.save(project, fragments=self.choices())
        path = self.creator.store.root / "configurations" / f"{saved['id']}.json"
        for location in ("profile", "fragments"):
            future = deepcopy(saved)
            if location == "profile":
                future["profile"]["schema_version"] = 99
            else:
                future["profile"]["fragments"]["schema_version"] = 99
            path.write_text(json.dumps(future))
            before = path.read_bytes()
            current = self.creator.current()
            self.assertEqual(current["projects"][0]["configurations"], [])
            self.assertTrue(current["diagnostics"])
            with self.assertRaises(ValueError):
                self.selected_plan(project, saved)
            self.assertEqual(before, path.read_bytes())
