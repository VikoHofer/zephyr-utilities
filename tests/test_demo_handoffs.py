"""The TinyML chooser selects normal creator inputs without publishing source."""

from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

import test_project_creator as creator_tests
from test_mnist_sample import mnist_fixture
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.projects.demo_handoffs import DemoHandoffService
from zephyr_utilities.features.projects.sample_check_identity import sample_input_guard


class DemoHandoffTests(unittest.TestCase):
    call = creator_tests.CreatorServiceTests.call
    body = creator_tests.CreatorServiceTests.body

    def setUp(self):
        creator_tests.CreatorServiceTests.setUp(self)
        self.snapshot = deepcopy(self.snapshot)
        self.snapshot.update(id=uuid.uuid4().hex, fingerprint="e" * 64)
        self.snapshot["boards"][0]["targets"].append("demo/other")
        self.environments = self.app.context.services["projects"].store
        self.environments.save(self.snapshot)
        self.analyze.return_value = self.snapshot
        self.source, self.module, _, _ = mnist_fixture(self.workspace, assets_root=self.creator.mnist_assets.path)
        template = patch("zephyr_utilities.features.projects.mnist_sample.TEMPLATE_ROOT", self.source)
        template.start()
        self.addCleanup(template.stop)
        self.demos = self.app.context.services["project_demo_handoffs"]

    def catalogue(self):
        response = self.call("POST", "demo-catalog", self.body())
        self.assertEqual(response.status, 200)
        return response.data

    def selection(self, **changes):
        catalogue = self.catalogue()
        self.assertEqual(catalogue["demos"][0]["status"], "available", catalogue["demos"][0]["diagnostics"])
        return self.body(demo_id="mnist-shell", variant_id="reference",
                         source_fingerprint=catalogue["demos"][0]["source_fingerprint"],
                         environment_fingerprint=catalogue["environment_fingerprint"],
                         selection={"board_id": "board-one", "target": "demo/chip", "sdk_id": "sdk-one"}, **changes)

    def create(self, body=None):
        response = self.call("POST", "creation-handoffs", self.selection() if body is None else body)
        self.assertEqual(response.status, 200)
        self.assertRegex(response.data["id"], r"^[a-f0-9]{32}$")
        self.assertEqual(response.data["route"], "projects/create/" + response.data["id"])
        return response.data["id"]

    def resolve(self, token, workspace=None):
        request = Request("GET", "/api/projects/creation-handoffs/" + token,
                          query={"workspace": [str(self.workspace) if workspace is None else workspace]})
        return self.app.router.dispatch(request).data

    def save_observation(self, update):
        snapshot = deepcopy(self.snapshot)
        snapshot["id"] = uuid.uuid4().hex
        update(snapshot)
        self.environments.save(snapshot)
        return snapshot

    def test_catalogue_and_handoff_are_read_only_and_use_the_saved_exact_target_catalogue(self):
        before = {str(path.relative_to(self.workspace)) for path in self.workspace.rglob("*")}
        catalogue = self.catalogue()
        self.assertEqual(catalogue["schema_version"], 1)
        self.assertEqual(catalogue["workspace"], str(self.workspace))
        self.assertEqual(catalogue["snapshot"]["boards"][0]["targets"], ["demo/chip", "demo/other"])
        self.assertEqual(catalogue["environment_fingerprint"], "e" * 64)
        self.assertEqual(catalogue["demos"][0]["framework"], "TensorFlow Lite Micro")
        self.assertTrue(catalogue["demos"][0]["requirements"])
        token = self.create()
        resolved = self.resolve(token)
        self.assertEqual(resolved["template_id"], "local-sample")
        self.assertEqual(resolved["sample_id"], "tinyml/mnist-shell")
        self.assertEqual(resolved["selection"], {"board_id": "board-one", "target": "demo/chip", "sdk_id": "sdk-one"})
        self.assertEqual({str(path.relative_to(self.workspace)) for path in self.workspace.rglob("*")}, before)
        self.assertEqual(self.app.context.jobs.list("projects"), [])
        self.assertFalse(self.creator.store.root.exists())
        self.analyze.assert_not_called()

    def test_no_analysis_stale_or_future_snapshot_is_unavailable_without_reading_source(self):
        for snapshot in (None, {**self.snapshot, "status": "stale"}, {**self.snapshot, "schema_version": 9},
                         {**self.snapshot, "fingerprint": "invalid"}):
            with self.subTest(snapshot=snapshot and snapshot.get("status")), \
                    patch.object(self.environments, "latest", return_value=snapshot), \
                    patch.object(self.demos, "_source") as source:
                result = self.catalogue()
                self.assertIsNone(result["snapshot"])
                self.assertIsNone(result["environment_fingerprint"])
                self.assertEqual(result["demos"][0]["status"], "missing")
                self.assertIn("Analyze", result["demos"][0]["diagnostics"][0])
                source.assert_not_called()

    def test_original_and_generated_apps_cannot_supply_the_demo_template(self):
        before = self.catalogue()["demos"][0]["source_fingerprint"]
        for name in ("mnist_shell", "ai_demo"):
            application = self.workspace / "applications" / name
            application.mkdir(parents=True)
            (application / "CMakeLists.txt").write_text('message(FATAL_ERROR "Unrelated app")\n')
            (application / "src").mkdir()
            (application / "src/main.cpp").write_text("Unrelated application source.\n")
        self.assertEqual(self.catalogue()["demos"][0]["source_fingerprint"], before)
        self.assertEqual(self.resolve(self.create())["source_fingerprint"], before)

    def test_missing_dependency_and_unrecognized_source_remain_explainable_and_cannot_issue_tokens(self):
        body = self.selection()
        header = self.module / "tensorflow/lite/micro/micro_interpreter.h"
        original = header.read_bytes()
        for changed, expected in ((self.module / "zephyr/module.yml", "missing"),
                                  (self.source / "src/main.cpp", "unsupported")):
            with self.subTest(changed=changed):
                prior = changed.read_bytes()
                if expected == "missing":
                    changed.unlink()
                else:
                    changed.write_text('#include "outside.hpp"\n')
                try:
                    demo = self.catalogue()["demos"][0]
                    self.assertEqual(demo["status"], expected)
                    self.assertTrue(demo["diagnostics"])
                    self.assertIsNone(demo["source_fingerprint"])
                    with self.assertRaises((OSError, ValueError)):
                        self.create(body)
                finally:
                    changed.write_bytes(prior)
        self.assertEqual(header.read_bytes(), original)
        self.assertEqual(self.demos.handoffs, {})

    def test_client_paths_code_unknown_variants_partial_selections_and_forged_fingerprints_are_rejected(self):
        body = self.selection()
        invalid = [{**body, "path": str(self.source)}, {**body, "commands": ["sh"]},
                   {**body, "demo_id": "caller-demo"}, {**body, "variant_id": "optimized"},
                   {**body, "source_fingerprint": "x" * 64}, {**body, "source_fingerprint": "0" * 64},
                   {**body, "environment_fingerprint": "0" * 64}, {**body, "selection": None},
                   {**body, "selection": {"board_id": "board-one"}},
                   {**body, "selection": {**body["selection"], "path": "/outside"}},
                   {**body, "selection": {**body["selection"], "target": "other/chip"}},
                   {**body, "selection": {**body["selection"], "sdk_id": "missing"}}]
        invalid.append({key: value for key, value in body.items() if key != "selection"})
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.create(value)
        self.assertEqual(self.demos.handoffs, {})

    def test_handoffs_are_detached_idempotent_and_resolution_does_not_extend_expiry(self):
        with patch("zephyr_utilities.features.projects.demo_handoffs.time.monotonic", return_value=100):
            token = self.create()
        with patch("zephyr_utilities.features.projects.demo_handoffs.time.monotonic", return_value=1000):
            first = self.resolve(token)
            first["selection"]["target"] = "caller mutation"
            second = self.resolve(token)
            self.assertEqual(second["selection"]["target"], "demo/chip")
        with patch("zephyr_utilities.features.projects.demo_handoffs.time.monotonic", return_value=1900), \
                self.assertRaisesRegex(ValueError, "expired"):
            self.resolve(token)

    def test_retention_is_bounded_and_restart_or_forged_tokens_have_no_authority(self):
        body = self.selection()
        tokens = [self.create(body) for _ in range(33)]
        self.assertEqual(len(self.demos.handoffs), 32)
        with self.assertRaisesRegex(ValueError, "expired|unavailable"):
            self.resolve(tokens[0])
        self.assertEqual(self.resolve(tokens[-1])["id"], tokens[-1])
        restarted = DemoHandoffService(self.app.context, self.creator)
        for token in (tokens[-1], "0" * 32):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "expired|unavailable"):
                restarted.resolve(token, str(self.workspace))
        for token in ("invalid", "../escape"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                restarted.resolve(token, str(self.workspace))

    def test_workspace_switch_and_foreign_workspace_requests_preserve_the_original_token(self):
        token = self.create()
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(str(other))
        with self.assertRaisesRegex(ValueError, "another workspace"):
            self.resolve(token)
        with self.assertRaisesRegex(ValueError, "another workspace"):
            self.resolve(token, str(other))
        self.app.context.workspace.set(str(self.workspace))
        self.assertEqual(self.resolve(token)["id"], token)

    def test_source_model_dependency_and_static_hardware_changes_invalidate_resolution(self):
        changes = (self.source / "src/main.cpp", self.creator.mnist_assets.path / "model/model_metadata.hpp",
                   self.module / "tensorflow/lite/micro/micro_interpreter.h",
                   self.workspace / "zephyr/boards/new.dts", self.workspace / "module/zephyr/Kconfig")
        for path in changes:
            with self.subTest(path=path):
                token = self.create()
                original = path.read_bytes() if path.exists() else None
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((original or b"") + b"\n/* changed local input */\n")
                try:
                    with self.assertRaisesRegex(ValueError, "stale|changed"):
                        self.resolve(token)
                finally:
                    if original is None:
                        path.unlink()
                    else:
                        path.write_bytes(original)

    def test_environment_sdk_and_root_identity_changes_invalidate_even_with_reused_environment_fingerprint(self):
        updates = (lambda snapshot: snapshot.update(fingerprint="f" * 64),
                   lambda snapshot: snapshot["sdks"][0].update(fingerprint="changed-sdk"),
                   lambda snapshot: snapshot.update(roots={"boards": [{"path": str(self.workspace / "custom-root")}]}))
        for update in updates:
            with self.subTest(update=update):
                token = self.create()
                self.save_observation(update)
                try:
                    with self.assertRaisesRegex(ValueError, "stale|changed"):
                        self.resolve(token)
                finally:
                    self.environments.save(self.snapshot)

    def test_source_changed_after_catalogue_and_workspace_changed_during_capture_cannot_issue_token(self):
        body = self.selection()
        path = self.source / "src/main.cpp"
        path.write_text(path.read_text() + "\n// changed after catalogue\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.create(body)
        current = self.selection()
        other = self.root / "other"
        other.mkdir()
        original = self.demos._source

        def switch(snapshot):
            result = original(snapshot)
            self.app.context.workspace.set(str(other))
            return result

        with patch.object(self.demos, "_source", side_effect=switch), self.assertRaisesRegex(ValueError, "another workspace"):
            self.create(current)
        self.assertEqual(self.demos.handoffs, {})

    def test_source_or_dependency_changed_while_binding_static_hardware_cannot_issue_token(self):
        body = self.selection()

        def change(profile):
            result = sample_input_guard(profile)
            header = self.module / "tensorflow/lite/micro/micro_interpreter.h"
            header.write_text(header.read_text() + "\n// changed during selection capture\n")
            return result

        with patch("zephyr_utilities.features.projects.demo_handoffs.sample_input_guard", side_effect=change), \
                self.assertRaisesRegex(ValueError, "changed during selection"):
            self.create(body)
        self.assertEqual(self.demos.handoffs, {})

    def test_resolved_preselection_can_use_the_ordinary_creator_preview(self):
        resolved = self.resolve(self.create())
        body = self.body(name="mnist_copy", **resolved["selection"], template_id=resolved["template_id"],
                         sample_id=resolved["sample_id"], sample_fingerprint=resolved["source_fingerprint"])
        plan = self.call("POST", "preview", body).data["plan"]
        self.assertEqual(plan["template"], {"id": "local-sample", "version": 1})
        self.assertEqual(plan["sample"]["id"], "tinyml/mnist-shell")
        self.assertEqual(plan["profile"]["target"], resolved["selection"]["target"])
        self.assertFalse(Path(plan["destination"]).exists())
        self.assertEqual(self.app.context.jobs.list("projects"), [])
        self.assertEqual(json.loads(next(item["content"] for item in plan["files"] if item["path"] == "zephyr-sample.json"))["id"],
                         "tinyml/mnist-shell")


if __name__ == "__main__":
    unittest.main()
