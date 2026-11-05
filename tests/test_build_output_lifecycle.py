"""Incremental builds and reviewed cleanup preserve source files and history."""

from concurrent.futures import CancelledError
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from zephyr_utilities.features.projects.build_outputs import inventory_output, remove_output
from zephyr_utilities.features.projects.build_workflow import BuildWorkflow, annotate_outputs
import test_project_creator as creator_tests
from test_projects_api import wait_job
from test_projects_builds import FAKE_CMAKE


class OutputFilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="review-output-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "application/build" / uuid.uuid4().hex
        (self.output / "nested").mkdir(parents=True)
        (self.output / "nested/object.o").write_bytes(b"object")
        (self.output / "cache.txt").write_text("cache")

    def test_review_counts_bytes_and_removes_only_managed_output_without_following_links(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "keep").write_text("private")
        (self.output / "external").symlink_to(outside, target_is_directory=True)
        inventory = inventory_output(self.output)
        self.assertEqual(inventory["file_count"], 2)
        self.assertEqual(inventory["directory_count"], 2)
        self.assertEqual(inventory["symlink_count"], 1)
        remove_output(self.output, inventory)
        self.assertFalse(self.output.exists())
        self.assertEqual((outside / "keep").read_text(), "private")
        self.assertTrue(self.output.parent.exists())

    def test_changed_tree_or_replaced_directory_fails_before_removing_anything(self):
        inventory = inventory_output(self.output)
        (self.output / "new").write_text("unreviewed")
        with self.assertRaisesRegex(ValueError, "changed since"):
            remove_output(self.output, inventory)
        self.assertTrue((self.output / "nested/object.o").exists())
        inventory = inventory_output(self.output)
        moved = self.output.with_name(uuid.uuid4().hex)
        self.output.rename(moved)
        self.output.mkdir()
        (self.output / "new").write_text("replacement")
        with self.assertRaises(ValueError):
            remove_output(self.output, inventory)
        self.assertEqual((self.output / "new").read_text(), "replacement")
        self.assertTrue((moved / "nested/object.o").exists())

    def test_parent_symlink_special_file_and_source_directory_are_rejected(self):
        inventory = inventory_output(self.output)
        parent = self.output.parent
        moved = parent.with_name("retained")
        parent.rename(moved)
        parent.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            remove_output(self.output, inventory)
        parent.unlink()
        moved.rename(parent)
        os.mkfifo(self.output / "pipe")
        with self.assertRaisesRegex(ValueError, "special"):
            inventory_output(self.output)
        with self.assertRaises(ValueError):
            inventory_output(parent.parent)

    def test_cancelled_preflight_preserves_every_entry(self):
        inventory = inventory_output(self.output)
        cancel = Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            remove_output(self.output, inventory, cancel=cancel)
        self.assertTrue((self.output / "nested/object.o").exists())

    def test_directory_replaced_during_cleanup_is_not_entered(self):
        inventory = inventory_output(self.output)
        def replaced(*args, **kwargs):
            current = inventory_output(*args, **kwargs)
            (self.output / "nested").rename(self.root / "original-nested")
            (self.output / "nested").mkdir()
            (self.output / "nested/object.o").write_text("replacement data")
            return current
        with patch("zephyr_utilities.features.projects.build_outputs.inventory_output", side_effect=replaced), \
                self.assertRaisesRegex(ValueError, "subdirectory was replaced"):
            remove_output(self.output, inventory)
        self.assertEqual((self.output / "nested/object.o").read_text(), "replacement data")


class BuildOutputLifecycleTests(unittest.TestCase):
    setUpBase = creator_tests.CreatorServiceTests.setUp
    call = creator_tests.CreatorServiceTests.call
    body = creator_tests.CreatorServiceTests.body
    preview = creator_tests.CreatorServiceTests.preview
    create = creator_tests.CreatorServiceTests.create

    def setUp(self):
        self.setUpBase()
        if not isinstance(self.creator, BuildWorkflow):
            self.creator.__class__ = type("WorkflowTestService", (BuildWorkflow, type(self.creator)), {})
        self.cmake = self.root / "cmake-fixture"
        script = FAKE_CMAKE.replace('(root / "zephyr").mkdir()', '(root / "zephyr").mkdir(exist_ok=True)')
        script = script.replace("name: test_board", "name: demo").replace("qualifiers: chip/cpu0", "qualifiers: chip").replace("revision: '1.0.0'", "revision: ''")
        script = script.replace('"\\nBOARD:STRING="', '"\\nZEPHYR_BASE:PATH=" + values["ZEPHYR_BASE"] + "\\nBOARD:STRING="')
        self.cmake.write_text(f"#!{sys.executable}\n" + script)
        self.cmake.chmod(0o755)
        self.snapshot["tools"][1]["path"] = str(self.cmake)
        self.snapshot["id"] = uuid.uuid4().hex
        self.app.context.services["projects"].store.save(self.snapshot)
        self.project = self.create()

    def preview_build(self, **changes):
        return self.creator.build_preview(self.body(project_id=self.project["id"], operation="build", **changes))["plan"]

    def run_build(self, plan):
        response = self.creator.build(self.body(build_plan_id=plan["id"]))
        job = wait_job(self.app.context.jobs, response["id"])
        self.assertEqual(job["status"], "completed", job.get("error"))
        return job["result"]["attempt"]

    def current_project(self):
        projects, errors = self.creator.store.list(str(self.workspace))
        self.assertEqual(errors, [])
        project = next(item for item in projects if item["id"] == self.project["id"])
        return annotate_outputs(project, self.creator.store)

    def clean(self, plan):
        job = self.creator.outputs_clean(self.body(plan_id=plan["id"]))
        return wait_job(self.app.context.jobs, job["id"])

    def test_fresh_default_and_explicit_reuse_keep_separate_immutable_attempts(self):
        first = self.run_build(self.preview_build())
        first_file = self.creator.store.root / "attempts" / f"{first['id']}.json"
        original_record = first_file.read_bytes()
        marker = Path(first["build_dir"]) / "keep-incremental-object"
        marker.write_text("existing object")
        source = Path(self.project["path"]) / "src/main.c"
        source.write_text(source.read_text() + "\n/* edited input */\n")
        plan = self.preview_build(reuse_attempt_id=first["id"])
        self.assertTrue(plan["reuse"])
        self.assertNotEqual(plan["id"], first["id"])
        self.assertEqual(plan["build_dir"], first["build_dir"])
        second = self.run_build(plan)
        self.assertEqual(marker.read_text(), "existing object")
        self.assertEqual(first_file.read_bytes(), original_record)
        self.assertEqual(second["output_owner_id"], first["id"])
        self.assertEqual(second["configuration_revision"], 0)
        rows = {item["id"]: item for item in self.current_project()["attempts"]}
        self.assertEqual(rows[first["id"]]["output_state"], "superseded")
        self.assertFalse(rows[first["id"]]["reuse_allowed"])
        self.assertTrue(rows[second["id"]]["reuse_allowed"])
        with self.assertRaisesRegex(ValueError, "latest"):
            self.preview_build(reuse_attempt_id=first["id"])
        self.assertNotEqual(self.preview_build()["build_dir"], first["build_dir"])

    def test_cache_change_after_preview_cannot_be_reused_and_existing_files_remain(self):
        first = self.run_build(self.preview_build())
        plan = self.preview_build(reuse_attempt_id=first["id"])
        cache = Path(first["build_dir"]) / "CMakeCache.txt"
        cache.write_text(cache.read_text() + "CUSTOM:STRING=changed\n")
        job = self.creator.build(self.body(build_plan_id=plan["id"]))
        result = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("cache", result["error"])
        self.assertIn("CUSTOM", cache.read_text())
        with self.assertRaisesRegex(ValueError, "cache"):
            self.preview_build(reuse_attempt_id=first["id"])

    def test_profile_revision_changed_output_identity_and_legacy_attempts_block_reuse(self):
        first = self.run_build(self.preview_build())
        changed = deepcopy(first)
        changed["configuration_revision"] = 1
        self.creator.store.save_attempt(changed)
        with self.assertRaisesRegex(ValueError, "revision"):
            self.preview_build(reuse_attempt_id=first["id"])
        self.creator.store.save_attempt(first)
        moved = Path(first["build_dir"]).with_name(uuid.uuid4().hex)
        Path(first["build_dir"]).rename(moved)
        Path(first["build_dir"]).mkdir()
        with self.assertRaises(ValueError):
            self.preview_build(reuse_attempt_id=first["id"])
        Path(first["build_dir"]).rmdir()
        moved.rename(first["build_dir"])
        legacy = deepcopy(first)
        legacy.pop("output_identity")
        self.creator.store.save_attempt(legacy)
        with self.assertRaisesRegex(ValueError, "older attempt"):
            self.preview_build(reuse_attempt_id=first["id"])

    def test_cleanup_removes_only_reviewed_output_and_retains_attempt_evidence(self):
        first = self.run_build(self.preview_build())
        second = self.run_build(self.preview_build(reuse_attempt_id=first["id"]))
        record_files = [self.creator.store.root / "attempts" / f"{item['id']}.json" for item in (first, second)]
        history = [path.read_bytes() for path in record_files]
        sources = (Path(self.project["path"]) / "src/main.c").read_bytes()
        plan = self.creator.outputs_preview(self.body(project_id=self.project["id"], attempt_id=first["id"]))["plan"]
        self.assertGreater(plan["file_count"], 0)
        self.assertGreater(plan["bytes"], 0)
        self.assertEqual(set(plan["shared_attempt_ids"]), {first["id"], second["id"]})
        self.assertNotIn("_inventory", plan)
        result = self.clean(plan)
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertFalse(Path(first["build_dir"]).exists())
        self.assertEqual([path.read_bytes() for path in record_files], history)
        self.assertEqual((Path(self.project["path"]) / "src/main.c").read_bytes(), sources)
        self.assertTrue(all(item["output_state"] == "cleaned" for item in self.current_project()["attempts"]))

    def test_output_ownership_survives_history_removal_and_cleanup_uses_output_id(self):
        first = self.run_build(self.preview_build())
        self.creator.remove_attempt(self.body(project_id=self.project["id"], attempt_id=first["id"]))
        project = self.current_project()
        self.assertEqual(project["attempts"], [])
        self.assertEqual(len(project["build_outputs"]), 1)
        self.assertTrue(project["build_outputs"][0]["clean_allowed"])
        plan = self.creator.outputs_preview(self.body(project_id=self.project["id"], output_id=first["id"]))["plan"]
        self.assertEqual(self.clean(plan)["status"], "completed")

    def test_failed_partial_output_can_be_cleaned_and_changed_review_fails_safely(self):
        (Path(self.project["path"]) / "mode").write_text("fail-configure")
        plan = self.preview_build()
        job = self.creator.build(self.body(build_plan_id=plan["id"]))
        self.assertEqual(wait_job(self.app.context.jobs, job["id"])["status"], "failed")
        cleanup = self.creator.outputs_preview(self.body(project_id=self.project["id"], attempt_id=plan["id"]))["plan"]
        changed = Path(plan["build_dir"]) / "unreviewed"
        changed.write_text("retain")
        result = self.clean(cleanup)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(changed.exists())
        cleanup = self.creator.outputs_preview(self.body(project_id=self.project["id"], attempt_id=plan["id"]))["plan"]
        self.assertEqual(self.clean(cleanup)["status"], "completed")

    def test_interrupted_deletion_records_remaining_outputs_and_disables_reuse_until_cleanup(self):
        first = self.run_build(self.preview_build())
        cleanup = self.creator.outputs_preview(self.body(project_id=self.project["id"], output_id=first["id"]))["plan"]
        def interrupted(*args, **kwargs):
            (Path(first["build_dir"]) / "zephyr/zephyr.elf").unlink()
            raise ValueError("Cleanup stopped after removing 1 reviewed entries. Synthetic concurrent change.")
        with patch("zephyr_utilities.features.projects.build_workflow.remove_output", side_effect=interrupted):
            result = self.clean(cleanup)
        self.assertEqual(result["status"], "failed")
        output = self.current_project()["build_outputs"][0]
        self.assertTrue(output["clean_allowed"])
        self.assertFalse(output["reuse_allowed"])
        self.assertIn("Cleanup stopped", output["reason"])
        retry = self.creator.outputs_preview(self.body(project_id=self.project["id"], output_id=first["id"]))["plan"]
        self.assertEqual(self.clean(retry)["status"], "completed")
        saved = self.creator.store.outputs(self.project["id"], str(self.workspace))[0]
        self.assertEqual(saved["state"], "cleaned")
        self.assertNotIn("cleanup_error", saved)

    def test_active_job_guards_preview_submission_cleanup_and_removal(self):
        first = self.run_build(self.preview_build())
        plan = self.preview_build(reuse_attempt_id=first["id"])
        cleanup = self.creator.outputs_preview(self.body(project_id=self.project["id"], output_id=first["id"]))["plan"]
        release = Event()
        self.addCleanup(release.set)
        self.app.context.jobs.submit("projects", lambda emit, cancel: release.wait(2), resource_key=f"project:{self.project['id']}",
                                     metadata={"workspace": str(self.workspace), "project_id": self.project["id"]})
        for operation in (lambda: self.preview_build(), lambda: self.creator.build(self.body(build_plan_id=plan["id"])),
                          lambda: self.creator.outputs_preview(self.body(project_id=self.project["id"], output_id=first["id"])),
                          lambda: self.creator.outputs_clean(self.body(plan_id=cleanup["id"]))):
            with self.assertRaisesRegex(ValueError, "operation is using"):
                operation()

    def test_cleanup_rejects_outside_directory_and_other_workspace(self):
        first = self.run_build(self.preview_build())
        bad = {**first, "build_dir": self.project["path"]}
        self.creator.store.save_attempt(bad)
        with self.assertRaises(ValueError):
            self.creator.outputs_preview(self.body(project_id=self.project["id"], attempt_id=first["id"]))
        with self.assertRaisesRegex(ValueError, "workspace"):
            self.creator.outputs_preview({"workspace": str(self.root), "project_id": self.project["id"], "output_id": first["id"]})

    def test_failed_before_reservation_cannot_adopt_an_inserted_directory(self):
        plan = self.preview_build()
        output = Path(plan["build_dir"])
        output.mkdir(parents=True)
        (output / "unowned").write_text("keep")
        job = self.creator.build(self.body(build_plan_id=plan["id"]))
        self.assertEqual(wait_job(self.app.context.jobs, job["id"])["status"], "failed")
        project = self.current_project()
        self.assertEqual(project["build_outputs"], [])
        self.assertFalse(project["attempts"][0]["clean_allowed"])
        with self.assertRaises(ValueError):
            self.creator.outputs_preview(self.body(project_id=self.project["id"], attempt_id=plan["id"]))
        self.assertEqual((output / "unowned").read_text(), "keep")

    def test_legacy_record_allows_reviewed_cleanup_but_unreadable_records_block_it(self):
        first = self.run_build(self.preview_build())
        (self.creator.store.root / "outputs" / f"{first['id']}.json").unlink()
        first.pop("output_owner_id")
        first.pop("output_identity")
        self.creator.store.save_attempt(first)
        project = self.current_project()
        self.assertTrue(project["build_outputs"][0]["legacy"])
        self.assertTrue(project["build_outputs"][0]["clean_allowed"])
        invalid = self.creator.store.root / "attempts" / (uuid.uuid4().hex + ".json")
        invalid.write_text('{"schema_version":999}')
        with self.assertRaisesRegex(ValueError, "cannot be read completely"):
            self.creator.outputs_preview(self.body(project_id=self.project["id"], attempt_id=first["id"]))
        self.assertTrue(Path(first["build_dir"]).exists())
        invalid.unlink()
        plan = self.creator.outputs_preview(self.body(project_id=self.project["id"], attempt_id=first["id"]))["plan"]
        self.assertEqual(self.clean(plan)["status"], "completed")

    def test_retaining_legacy_output_before_history_removal_preserves_cleanup_affordance(self):
        first = self.run_build(self.preview_build())
        (self.creator.store.root / "outputs" / f"{first['id']}.json").unlink()
        first.pop("output_owner_id")
        first.pop("output_identity")
        self.creator.store.save_attempt(first)
        self.creator.retain_attempt_output(self.project, first)
        self.creator.remove_attempt(self.body(project_id=self.project["id"], attempt_id=first["id"]))
        project = self.current_project()
        self.assertEqual(project["attempts"], [])
        self.assertTrue(project["build_outputs"][0]["clean_allowed"])
        plan = self.creator.outputs_preview(self.body(project_id=self.project["id"], output_id=first["id"]))["plan"]
        self.assertEqual(self.clean(plan)["status"], "completed")

    def test_legacy_promotion_requires_captured_source_and_output_recipe(self):
        first = self.run_build(self.preview_build())
        (self.creator.store.root / "outputs" / f"{first['id']}.json").unlink()
        first.pop("output_owner_id")
        first.pop("output_identity")
        first["recipe"]["configure"][first["recipe"]["configure"].index("-B") + 1] = str(self.root / "different-output")
        self.creator.store.save_attempt(first)
        self.creator.retain_attempt_output(self.project, first)
        self.assertEqual(self.creator.store.outputs(self.project["id"], str(self.workspace)), [])
        with self.assertRaisesRegex(ValueError, "recipe"):
            self.creator.outputs_preview(self.body(project_id=self.project["id"], attempt_id=first["id"]))


if __name__ == "__main__":
    unittest.main()
