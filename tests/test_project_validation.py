"""Editor validation reuses reviewed Projects jobs with exact saved source evidence."""

from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects.creation import path_identity
from zephyr_utilities.features.projects.builds import _artifact
from zephyr_utilities.features.projects.editor_validation import ADAPTER_FILES, _diagnostics

import test_project_context as context_fixture


class ProjectValidationTests(unittest.TestCase):
    def setUp(self):
        context_fixture.ProjectContextTests.setUp(self)
        self.selections = self.provider
        self.provider = self.app.context.project_validation
        self.overlay = self.source / "app.overlay"
        self.profile["tools"] = []
        for name in ("cmake", "ninja", "Python"):
            path = self.workspace / name
            path.write_text("synthetic executable\n")
            path.chmod(0o755)
            self.profile["tools"].append({"name": name, "path": str(path), "status": "discovered"})
        module = self.workspace / "module"
        module.mkdir()
        self.profile["modules"] = [{"path": str(module), "is_module": True, "status": "discovered", "active": True}]
        self.profile["roots"] = {"dts": [{"path": str(self.workspace / "custom")} ]}
        for relative in ADAPTER_FILES:
            path = self.workspace / "zephyr" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Synthetic adapter capability\n")
        self.binding = self.workspace / "custom/dts/bindings/test.yaml"
        self.binding.parent.mkdir(parents=True)
        self.binding.write_text("description: test\n")
        self.project["profile"] = deepcopy(self.profile)
        self.creator.store._write(self.creator.store.root / "projects" / f"{self.project['id']}.json", self.project)
        self.execution = []
        self.addCleanup(patch.stopall)
        patch.object(self.creator, "_recheck", return_value=None).start()
        patch.object(self.creator, "_execute_build", side_effect=self.execute).start()

    def candidate(self):
        text = self.overlay.read_bytes().decode()
        return {"path": str(self.overlay), "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()}

    def selection(self):
        return self.selections.capture(str(self.workspace), self.project["id"], None)

    def review(self):
        return self.provider.review(self.selection(), self.candidate())["plan"]

    def execute(self, profile, source, output, operation, **options):
        self.execution.append((profile, source, output, operation, self.overlay.read_bytes()))
        Path(output).mkdir()
        options["on_output_ready"](path_identity(output))
        return {"status": "configured", "context": {"configured": True, "source_dir": str(source),
            "application_source_dir": str(source), "requested_target": profile["target"],
            "effective_target": profile["target"], "board": "demo", "revision": "1.0", "qualifiers": "chip",
            "zephyr_base": profile["zephyr"]["path"], "sdk_path": profile["sdk"]["path"], "toolchain": "zephyr",
            "devicetree": {"dependencies": [str(self.overlay)], "dependency_coverage": "complete"}},
            "artifacts": [], "diagnostics": []}

    def finish(self, plan):
        submitted = self.provider.submit(plan["id"])
        for _ in range(200):
            job = self.app.context.jobs.get(submitted["job"]["id"])
            if job["status"] not in {"queued", "running"}:
                return self.provider.result(plan["id"])
            time.sleep(.005)
        self.fail("Synthetic configuration did not complete.")

    def test_review_does_not_execute_and_saved_source_preserves_removal_and_relative_include(self):
        self.overlay.write_text('#include "relative.h"\n&uart0 { /delete-property/ current-speed; };\n')
        (self.source / "relative.h").write_text("#define VALUE 1\n")
        plan = self.review()
        self.assertEqual(self.execution, [])
        self.assertEqual(self.app.context.jobs.list(), [])
        self.assertFalse(plan["capabilities"]["staging"])
        result = self.finish(plan)
        self.assertEqual(self.execution[0][1], str(self.source))
        self.assertEqual(self.execution[0][3], "configure")
        self.assertEqual(self.execution[0][4].decode(), plan["candidate"]["text"])
        self.assertTrue(result["configured"])
        self.assertTrue(result["overlay_used"])
        self.assertTrue(result["context_matches"])
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(result["freshness"], "unknown")
        self.assertIn("applications/", result["handoff"]["view"])
        self.assertEqual(result["handoff"]["configuration_id"], None)
        self.assertEqual(self.creator.store.attempt(plan["id"], self.project["id"], str(self.workspace))["editor_validation"]["candidate"], plan["candidate"])

    def test_unsaved_candidate_is_refused_without_jobs_or_source_changes(self):
        candidate = self.candidate()
        candidate["text"] += "// unsaved\n"
        candidate["sha256"] = hashlib.sha256(candidate["text"].encode()).hexdigest()
        before = self.overlay.read_bytes()
        with self.assertRaisesRegex(ValueError, "Save the exact overlay"):
            self.provider.review(self.selection(), candidate)
        self.assertEqual(self.overlay.read_bytes(), before)
        self.assertEqual(self.execution, [])

    def test_unknown_adapter_or_missing_selected_tool_is_not_admitted(self):
        (self.workspace / "zephyr" / ADAPTER_FILES[0]).unlink()
        with self.assertRaisesRegex(ValueError, "adapter"):
            self.review()
        (self.workspace / "zephyr" / ADAPTER_FILES[0]).write_text("synthetic")
        (self.workspace / "cmake").chmod(0o644)
        with self.assertRaisesRegex(ValueError, "selected cmake"):
            self.review()

    def test_changed_binding_header_kconfig_and_tool_block_reviewed_submission(self):
        for path in (self.binding, self.workspace / "zephyr/include/test.h", self.workspace / "zephyr/Kconfig.test", self.workspace / "cmake"):
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text("before\n")
                plan = self.review()
                path.write_text(path.read_text() + "changed\n")
                with self.assertRaisesRegex(ValueError, "inputs changed"):
                    self.provider.submit(plan["id"])
        self.assertEqual(self.execution, [])

    def test_identical_overlay_replacement_blocks_submission(self):
        plan = self.review()
        replacement = self.source / "replacement"
        replacement.write_bytes(self.overlay.read_bytes())
        replacement.replace(self.overlay)
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            self.provider.submit(plan["id"])

    def test_configuration_revision_change_blocks_submission(self):
        plan = self.review()
        self.project["profile_revision"] += 1
        self.creator.store._write(self.creator.store.root / "projects" / f"{self.project['id']}.json", self.project)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.provider.submit(plan["id"])

    def test_changed_inputs_while_queued_block_tool_execution(self):
        def recheck(*args, **kwargs):
            self.binding.write_text("description: changed before execution\n")
        with patch.object(self.creator, "_recheck", side_effect=recheck):
            result = self.finish(self.review())
        self.assertEqual(result["status"], "stale")
        self.assertEqual(self.execution, [])

    def test_effective_target_mismatch_and_missing_metadata_remain_inconclusive(self):
        for value in ("another-board", None):
            with self.subTest(board=value):
                execute = self.execute
                def mismatched(*args, **kwargs):
                    result = execute(*args, **kwargs)
                    result["context"]["board"] = value
                    return result
                with patch.object(self.creator, "_execute_build", side_effect=mismatched):
                    result = self.finish(self.review())
                self.assertEqual(result["status"], "inconclusive")
                self.assertIs(result["context_matches"], False if value else None)
                self.assertTrue(result["overlay_used"])
                self.assertIn("does not prove", result["message"])

    def test_duplicate_submit_retrieves_same_job(self):
        plan = self.review()
        first = self.provider.submit(plan["id"])
        second = self.provider.submit(plan["id"])
        self.assertEqual(first["job"]["id"], second["job"]["id"])
        self.assertEqual(len(self.app.context.jobs.list()), 1)

    def test_changed_relative_include_during_execution_marks_result_stale(self):
        included = self.source / "relative.h"
        included.write_text("before")
        execute = self.execute
        def changing(*args, **kwargs):
            result = execute(*args, **kwargs)
            included.write_text("after")
            return result
        with patch.object(self.creator, "_execute_build", side_effect=changing):
            result = self.finish(self.review())
        self.assertEqual(result["status"], "stale")
        self.assertTrue(result["stale"])
        self.assertTrue(result["configured"])

    def test_changed_external_binding_after_finish_invalidates_result(self):
        plan = self.review()
        self.finish(plan)
        self.binding.write_text("description: changed\n")
        self.assertEqual(self.provider.result(plan["id"])["status"], "stale")

    def test_changed_generated_output_invalidates_captured_evidence(self):
        execute = self.execute
        def output(*args, **kwargs):
            result = execute(*args, **kwargs)
            base = Path(args[2])
            path = base / "zephyr/zephyr.dts"
            path.parent.mkdir()
            path.write_text('/dts-v1/; / { value = <1>; };\n')
            result["artifacts"] = [_artifact(path, base, "resolved-dts")[0]]
            return result
        with patch.object(self.creator, "_execute_build", side_effect=output):
            plan = self.review()
            self.finish(plan)
        (Path(plan["build_dir"]) / "zephyr/zephyr.dts").write_text("changed generated evidence\n")
        result = self.provider.result(plan["id"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["output_freshness"], "stale")

    def test_missing_candidate_or_partial_dependency_metadata_never_claims_usage(self):
        for metadata in ({"dependencies": [], "dependency_coverage": "complete"},
                         {"dependencies": [str(self.overlay)], "dependency_coverage": "partial"}):
            with self.subTest(metadata=metadata):
                execute = self.execute
                def unknown(*args, **kwargs):
                    result = execute(*args, **kwargs)
                    result["context"]["devicetree"] = metadata
                    return result
                with patch.object(self.creator, "_execute_build", side_effect=unknown):
                    result = self.finish(self.review())
                self.assertEqual(result["status"], "inconclusive")
                self.assertFalse(result["overlay_used"])

    def test_failed_configure_retains_selected_overlay_diagnostic_link(self):
        def failing(*args, **kwargs):
            kwargs["emit"](f"devicetree error: {self.overlay}:1 (column 3): parse error")
            raise ValueError("Configure failed")
        with patch.object(self.creator, "_execute_build", side_effect=failing):
            result = self.finish(self.review())
        self.assertEqual(result["status"], "failed")
        diagnostic = next(item for item in result["diagnostics"] if item.get("path"))
        self.assertEqual(diagnostic["path"], str(self.overlay))
        self.assertEqual(diagnostic["line"], 1)
        self.assertEqual(diagnostic["column"], 3)
        self.assertEqual(diagnostic["sha256"], self.candidate()["sha256"])

    def test_workspace_switch_keeps_captured_job_and_cancellation(self):
        started = threading.Event()
        def waiting(*args, **kwargs):
            started.set()
            if kwargs["cancel"].wait(2):
                raise CancelledError()
            self.fail("Cancellation was not delivered.")
        with patch.object(self.creator, "_execute_build", side_effect=waiting):
            plan = self.review()
            job = self.provider.submit(plan["id"])["job"]
            self.assertTrue(started.wait(2))
            elsewhere = self.root / "elsewhere"
            elsewhere.mkdir()
            self.app.context.workspace.set(elsewhere)
            self.provider.cancel(plan["id"])
            for _ in range(200):
                if self.app.context.jobs.get(job["id"])["status"] == "cancelled":
                    break
                time.sleep(.005)
            result = self.provider.result(plan["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["handoff"]["workspace"], str(self.workspace))

    def test_new_submission_after_workspace_switch_is_refused(self):
        plan = self.review()
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.app.context.workspace.set(elsewhere)
        with self.assertRaisesRegex(ValueError, "workspace changed"):
            self.provider.submit(plan["id"])

    def test_expired_shared_job_uses_durable_attempt_evidence(self):
        plan = self.review()
        self.finish(plan)
        with patch.object(self.app.context.jobs, "get", side_effect=FileNotFoundError()):
            result = self.provider.result(plan["id"])
        self.assertEqual(result["status"], "inconclusive")
        self.assertTrue(result["configured"])

    def test_diagnostic_paths_are_data_and_unobserved_files_never_get_links(self):
        result = _diagnostics(["/etc/secret.overlay:1:2: error", "<script>alert(1)</script>: error"], self.candidate(), self.source)
        self.assertTrue(all("path" not in item for item in result))


if __name__ == "__main__":
    unittest.main()
