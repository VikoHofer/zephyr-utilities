"""Sample compatibility evidence is explicit and bound to the reviewed selection."""

from concurrent.futures import CancelledError
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

import test_project_creator as creator_tests
from test_project_samples import sample_fixture
from test_projects_api import wait_job


class SampleCheckServiceTests(unittest.TestCase):
    setUp = creator_tests.CreatorServiceTests.setUp
    call = creator_tests.CreatorServiceTests.call
    body = creator_tests.CreatorServiceTests.body
    preview = creator_tests.CreatorServiceTests.preview
    sample_selection = creator_tests.CreatorServiceTests.sample_selection

    def selection(self):
        sample_fixture(self.workspace / "zephyr")
        self.sample = self.sample_selection()
        return self.body(**{key: value for key, value in self.sample.items() if key != "template_id"},
                         board_id="board-one", target="demo/chip", sdk_id="sdk-one")

    def check(self, body, status="compatible"):
        with patch("zephyr_utilities.features.projects.creator.check_sample", return_value={
                "status": status, "message": "Required sample hardware is missing." if status == "incompatible" else "Checked requirements.",
                "details": ["Synthetic configured Device Tree evidence."]}) as check:
            response = self.call("POST", "sample-check", body)
            self.assertEqual(response.status, 202)
            job = wait_job(self.app.context.jobs, response.data["id"])
        self.assertEqual(job["status"], "completed", job.get("error"))
        return job["result"], check

    def test_check_is_explicit_and_never_creates_an_application_or_ordinary_creator_job(self):
        body = self.selection()
        plan = self.preview(**self.sample)
        self.assertEqual(plan["sample_compatibility"]["status"], "unknown")
        self.analyze.assert_not_called()
        result, check = self.check(body)
        self.assertEqual(result["compatibility"]["target"], body["target"])
        self.assertEqual(check.call_args.args[0]["target"], body["target"])
        self.assertFalse(Path(plan["destination"]).exists())
        self.assertEqual(self.call("GET", "creator").data["jobs"], [])
        reviewed = self.preview(**self.sample, sample_check_id=result["check_id"])
        self.assertEqual(reviewed["sample_compatibility"]["status"], "compatible")
        self.assertEqual(self.preview(**self.sample)["sample_compatibility"]["status"], "compatible")

    def test_known_incompatibility_blocks_preview_with_or_without_its_token(self):
        result, _ = self.check(self.selection(), "incompatible")
        for extra in ({}, {"sample_check_id": result["check_id"]}):
            with self.assertRaisesRegex(ValueError, "Required sample hardware is missing"):
                self.preview(**self.sample, **extra)
        self.assertFalse((self.workspace / "applications").exists())

    def test_unknown_is_retained_as_a_warning_without_claiming_compatibility(self):
        result, _ = self.check(self.selection(), "unknown")
        plan = self.preview(**self.sample, sample_check_id=result["check_id"])
        self.assertEqual(plan["sample_compatibility"]["status"], "unknown")

    def test_changed_metadata_invalidates_a_check_even_if_saved_environment_id_is_unchanged(self):
        result, _ = self.check(self.selection())
        (self.workspace / "zephyr/VERSION").write_text("changed metadata\n")
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.preview(**self.sample, sample_check_id=result["check_id"])
        self.assertEqual(self.preview(**self.sample)["sample_compatibility"]["status"], "unknown")

    def test_expired_unknown_and_wrong_template_tokens_are_rejected(self):
        result, _ = self.check(self.selection())
        self.creator.sample_checks[result["check_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.preview(**self.sample, sample_check_id=result["check_id"])
        with self.assertRaisesRegex(ValueError, "only apply to local samples"):
            self.preview(sample_check_id=result["check_id"])

    def test_board_dts_edits_invalidate_cached_results_and_reviewed_creation(self):
        body = self.selection()
        board_dts = self.workspace / "zephyr/boards/demo.dts"
        board_dts.parent.mkdir()
        board_dts.write_text("/dts-v1/; / { aliases { led0 = &led; }; };\n")
        for status in ("compatible", "incompatible"):
            result, _ = self.check(body, status)
            board_dts.write_text(board_dts.read_text() + "/* changed board wiring */\n")
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                self.preview(**self.sample, sample_check_id=result["check_id"])
            self.assertEqual(self.preview(**self.sample)["sample_compatibility"]["status"], "unknown")
        result, _ = self.check(body)
        plan = self.preview(**self.sample, sample_check_id=result["check_id"])
        board_dts.write_text(board_dts.read_text() + "/* changed after review */\n")
        job = self.call("POST", "create", self.body(plan_id=plan["id"])).data
        created = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(created["status"], "failed")
        self.assertIn("compatibility inputs changed", created["error"])
        self.assertFalse(Path(plan["destination"]).exists())

    def test_board_input_change_during_configuration_does_not_issue_evidence(self):
        body = self.selection()
        def check(*args, **kwargs):
            (self.workspace / "zephyr/Kconfig.extra").write_text("config NEW_INPUT\n")
            return {"status": "compatible", "message": "Checked.", "details": []}
        with patch("zephyr_utilities.features.projects.creator.check_sample", side_effect=check):
            job = self.call("POST", "sample-check", body).data
            checked = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(checked["status"], "failed")
        self.assertIn("board configuration inputs changed", checked["error"])
        self.assertEqual(self.creator.sample_checks, {})

    def test_job_captures_selection_and_workspace_while_user_changes_views(self):
        body = self.selection()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def check(profile, files, sample_id, **kwargs):
            started.set()
            release.wait(2)
            self.assertEqual(profile["target"], "demo/chip")
            self.assertEqual(sample_id, "hello_world")
            return {"status": "compatible", "message": "Checked.", "details": []}

        with patch("zephyr_utilities.features.projects.creator.check_sample", side_effect=check):
            job = self.call("POST", "sample-check", body).data
            self.assertTrue(started.wait(1))
            body["sample_id"] = "basic/button"
            other = self.root / "other"
            other.mkdir()
            self.app.context.workspace.set(str(other))
            release.set()
            done = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(done["status"], "completed", done.get("error"))
        self.assertEqual(done["result"]["compatibility"]["workspace"], str(self.workspace))
        self.assertEqual(done["result"]["compatibility"]["sample_id"], "hello_world")

    def test_upstream_change_during_check_does_not_issue_evidence(self):
        body = self.selection()

        def check(*args, **kwargs):
            path = self.workspace / "zephyr/samples/hello_world/src/main.c"
            path.write_text(path.read_text() + "\n/* changed during check */\n")
            return {"status": "compatible", "message": "Checked.", "details": []}

        with patch("zephyr_utilities.features.projects.creator.check_sample", side_effect=check):
            job = self.call("POST", "sample-check", body).data
            result = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("sample changed during", result["error"])
        self.assertEqual(self.creator.sample_checks, {})

    def test_cancelled_check_does_not_issue_evidence(self):
        body = self.selection()
        with patch("zephyr_utilities.features.projects.creator.check_sample", side_effect=CancelledError):
            job = self.call("POST", "sample-check", body).data
            result = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(self.creator.sample_checks, {})

    def test_build_checks_original_main_but_does_not_enforce_old_requirements_on_edited_sources(self):
        self.selection()
        for change in ("original", "edited", "removed"):
            with self.subTest(change=change):
                plan = self.preview(**self.sample, name="sample_" + change)
                job = self.call("POST", "create", self.body(plan_id=plan["id"])).data
                created = wait_job(self.app.context.jobs, job["id"])
                self.assertEqual(created["status"], "completed", created.get("error"))
                project = created["result"]["project"]
                main = Path(project["path"]) / "src/main.c"
                if change == "edited":
                    main.write_text("/* The user has changed this application's requirements. */\n")
                elif change == "removed":
                    main.unlink()
                build_plan = self.call("POST", "build-preview", self.body(project_id=project["id"], operation="build")).data["plan"]

                def execute(profile, source, build, operation, **options):
                    result = options["after_configure"](Path(build))
                    return {"status": "built", "sample_compatibility": result, "context": {}, "artifacts": [], "diagnostics": []}

                with patch("zephyr_utilities.features.projects.creator.execute_build", side_effect=execute), \
                        patch("zephyr_utilities.features.projects.creator.inspect_sample_requirements",
                              return_value={"status": "compatible", "message": "Configured requirements checked.", "details": []}) as inspect:
                    job = self.call("POST", "build", self.body(build_plan_id=build_plan["id"])).data
                    built = wait_job(self.app.context.jobs, job["id"])
                self.assertEqual(built["status"], "completed", built.get("error"))
                evidence = built["result"]["attempt"]["sample_compatibility"]
                self.assertEqual(evidence["status"], "compatible" if change == "original" else "unknown")
                if change == "original":
                    self.assertEqual(inspect.call_args.kwargs["source_dir"], project["path"])
                else:
                    inspect.assert_not_called()
