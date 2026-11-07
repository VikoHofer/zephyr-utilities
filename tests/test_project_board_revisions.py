"""Board revision evidence selects overlays without rewriting saved project intent."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from zephyr_utilities.features.projects.builds import command_recipe
from zephyr_utilities.features.projects.editor_overlays import ProjectsOverlayProvider
from zephyr_utilities.features.projects.overlay_usage import overlay_usage
from zephyr_utilities.features.projects.revision_evidence import snapshot_revision

import test_project_context as fixtures
import test_project_creator as creator_fixtures
from test_project_samples import sample_fixture
from test_projects_api import wait_job


class ProjectBoardRevisionTests(unittest.TestCase):
    capture = fixtures.ProjectContextTests.capture
    configuration = fixtures.ProjectContextTests.configuration

    def setUp(self):
        fixtures.ProjectContextTests.setUp(self)
        for path in self.source.rglob("*.overlay"):
            path.unlink()
        (self.source / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20.0)\n"
                                                  "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\nproject(sample)\n")
        before = deepcopy(self.project)
        self.profile["target"] = "demo/chip"
        self.project.update(revision=1, profile_revision=1, profile=deepcopy(self.profile))
        self.project.pop("default_overlay")
        self.creator.store.update_project(self.project, before)
        self.board = {"id": "custom-board", "name": "demo", "status": "discovered", "socs": ["chip"],
                      "targets": ["demo/chip", "demo@0.7.0/chip", "demo@0.14.0/chip"],
                      "directories": [str(self.workspace / "custom/boards/demo")], "source_files": [],
                      "architectures": ["arm"], "platforms": [], "qualifiers": ["chip"],
                      "revisions": ["0.7.0", "0.14.0"], "default_revision": "0.14.0",
                      "revision_format": "major.minor.patch", "revision_exact": False}
        self.save_board()

    def save_board(self):
        path = self.creator.environments.root / "snapshots" / f"{self.observation['id']}.json"
        path.write_text(json.dumps({**self.observation, "boards": [self.board]}))

    def overlay(self, relative):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// selected application overlay\n")
        return path

    @staticmethod
    def selected_files(selection):
        return [item["path"] for item in selection["input_evidence"]["predicted"][0]["files"]
                if item["state"] == "expected"]

    def recipe(self, profile):
        profile = deepcopy(profile)
        profile["tools"] = [{"name": name, "path": str(self.workspace / name), "status": "discovered"}
                            for name in ("Python", "cmake", "ninja")]
        profile["modules"] = [{"path": str(self.workspace / "module"), "is_module": True,
                               "status": "discovered", "active": True}]
        return command_recipe(profile, self.source, self.workspace / "build", environ={})

    def test_default_revision_selects_its_application_overlay_without_changing_intent(self):
        relative = "boards/demo_chip_0_14_0.overlay"
        self.overlay(relative)
        self.overlay("app.overlay")
        before = fixtures.ProjectContextTests.bytes_in_tree(self)
        with patch("subprocess.Popen", side_effect=AssertionError("Opening does not run tools")):
            selected = self.capture()
            self.assertEqual(self.provider.revalidate(selected), selected)
        self.assertEqual(selected["target"]["requested"], "demo/chip")
        self.assertIsNone(selected["target"]["revision"])
        resolution = selected["target"]["revision_resolution"]
        self.assertEqual((resolution["effective"], resolution["match"]), ("0.14.0", "default"))
        self.assertTrue(resolution["supported"])
        self.assertEqual(self.selected_files(selected), [relative])
        self.assertIsNone(selected["overlay_creation"])
        self.assertEqual(fixtures.ProjectContextTests.bytes_in_tree(self), before)
        self.assertIn("-DBOARD=demo/chip", self.recipe(self.profile)["configure"])

    def test_explicit_revision_keeps_a_separate_target_and_overlay_selection(self):
        old, new = "boards/demo_chip_0_7_0.overlay", "boards/demo_chip_0_14_0.overlay"
        self.overlay(old)
        self.overlay(new)
        named_profile = {**self.profile, "target": "demo@0.7.0/chip"}
        named = self.configuration(profile=named_profile)
        selected = self.capture(named["id"])
        self.assertEqual(selected["target"]["revision"], "0.7.0")
        self.assertEqual(selected["target"]["revision_resolution"]["match"], "exact")
        self.assertEqual(self.selected_files(selected), [old])
        self.assertEqual(self.selected_files(self.capture()), [new])
        self.assertIn("-DBOARD=demo@0.7.0/chip", self.recipe(named_profile)["configure"])
        self.assertEqual(self.creator.store.configuration(named["id"], self.project["id"], str(self.workspace)), named)

    def test_capture_does_not_predict_auto_selection_without_its_board_observation(self):
        self.overlay("app.overlay")
        self.overlay("boards/demo_chip_0_14_0.overlay")
        path = self.creator.environments.root / "snapshots" / f"{self.observation['id']}.json"
        for failure in ("missing", "mismatched", "missing-board"):
            with self.subTest(failure=failure):
                if failure == "missing":
                    path.unlink()
                else:
                    snapshot = {**self.observation, "boards": [self.board]}
                    if failure == "mismatched":
                        snapshot["fingerprint"] = "different-observation"
                    else:
                        snapshot["boards"] = []
                    path.write_text(json.dumps(snapshot))
                selected = self.capture()
                resolution = selected["target"]["revision_resolution"]
                self.assertFalse(resolution["supported"])
                self.assertIn("unavailable", resolution["reason"])
                self.assertEqual(selected["input_evidence"]["predicted"][0]["status"], "unknown")
                self.assertEqual(self.selected_files(selected), [])
                self.assertFalse(selected["overlay_creation"]["supported"])
                self.assertIn("board-revision-unavailable", {item["code"] for item in selected["diagnostics"]})
                self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)
        for key in ("revisions", "revision_format", "revision_exact", "default_revision"):
            del self.board[key]
        self.save_board()
        unrevisioned = self.capture()
        self.assertNotIn("revision_resolution", unrevisioned["target"])
        self.assertEqual(self.selected_files(unrevisioned), ["app.overlay"])

    def test_no_overlay_save_and_registration_recovery_preserve_the_default_target(self):
        selected = self.capture()
        proposal = selected["overlay_creation"]
        self.assertEqual(proposal["path"], "overlays/demo_chip.overlay")
        self.assertTrue(proposal["supported"])
        self.assertEqual(proposal["input_evidence"]["revision_resolution"]["effective"], "0.14.0")
        provider = ProjectsOverlayProvider(self.app.context, self.creator)
        review = provider.review(selected, proposal["path"], creating=True)
        path = self.source / proposal["path"]
        self.assertFalse(path.exists())
        path.parent.mkdir(parents=True, exist_ok=True)
        text = '&uart0 { status = "okay"; };\n'
        path.write_text(text)
        provider = ProjectsOverlayProvider(self.app.context, self.creator)
        receipt = provider.register(review, str(self.workspace), str(path), hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(receipt["state"], "included")
        changed = self.creator.store.project(self.project["id"], str(self.workspace))
        self.assertEqual(changed["profile"]["target"], "demo/chip")
        self.assertEqual(changed["profile"]["fragments"]["overlays"],
                         {"mode": "append", "files": [proposal["path"]]})
        self.assertEqual(receipt["selection"]["target"]["revision_resolution"]["effective"], "0.14.0")
        self.assertEqual(self.selected_files(receipt["selection"]), [proposal["path"]])

    def test_unsupported_custom_or_invalid_metadata_keeps_revision_selection_unknown(self):
        self.overlay("app.overlay")
        for changes, reason in (({"revision_format": "custom"}, "revision.cmake"),
                                ({"default_revision": "2.0.0"}, "declared default")):
            with self.subTest(changes=changes):
                previous = deepcopy(self.board)
                self.board.update(changes)
                self.save_board()
                selected = self.capture()
                resolution = selected["target"]["revision_resolution"]
                self.assertFalse(resolution["supported"])
                self.assertIn(reason, resolution["reason"])
                self.assertEqual(selected["input_evidence"]["predicted"][0]["status"], "unknown")
                self.assertEqual(self.selected_files(selected), [])
                self.assertFalse(selected["overlay_creation"]["supported"])
                self.assertIn("board-revision-unavailable", {item["code"] for item in selected["diagnostics"]})
                self.assertEqual(self.creator.store.project(self.project["id"], str(self.workspace)), self.project)
                self.board = previous

    def test_prediction_uses_effective_revision_but_default_overlay_identity_stays_requested(self):
        profile = {"target": "demo@0.8.0/chip"}
        resolution = {"requested": "0.8.0", "effective": "0.7.0", "supported": True}
        files = [{"path": "CMakeLists.txt", "content": (self.source / "CMakeLists.txt").read_text()},
                 {"path": "boards/demo_chip_0_7_0.overlay"}, {"path": "boards/demo_chip_0_8_0.overlay"}]
        report = overlay_usage(profile, self.source, files=files, revision_resolution=resolution)
        self.assertEqual(report["target"], profile["target"])
        self.assertEqual([item["path"] for item in report["files"] if item["state"] == "expected"],
                         ["boards/demo_chip_0_7_0.overlay"])
        report["revision_resolution"]["effective"] = "changed"
        self.assertEqual(resolution["effective"], "0.7.0")
        wrong = overlay_usage(profile, self.source, files=files, revision_resolution={**resolution, "requested": "other"})
        self.assertEqual(wrong["status"], "unknown")


class ProjectBoardRevisionPreviewTests(unittest.TestCase):
    call = creator_fixtures.CreatorServiceTests.call
    body = creator_fixtures.CreatorServiceTests.body
    preview = creator_fixtures.CreatorServiceTests.preview
    sample_selection = creator_fixtures.CreatorServiceTests.sample_selection

    def setUp(self):
        creator_fixtures.CreatorServiceTests.setUp(self)
        self.snapshot.update(id=uuid.uuid4().hex, fingerprint="environment-revisions")
        self.snapshot["boards"][0].update(
            targets=["demo/chip", "demo@0.7.0/chip", "demo@0.14.0/chip"],
            revisions=["0.7.0", "0.14.0"], default_revision="0.7.0",
            revision_format="major.minor.patch", revision_exact=False)
        self.creator.environments.save(self.snapshot)
        sample = sample_fixture(self.workspace / "zephyr")
        (sample / "boards").mkdir()
        for name in ("boards/demo_chip_0_7_0.overlay", "boards/demo_chip_0_14_0.overlay", "app.overlay"):
            (sample / name).write_text("/* SPDX-License-Identifier: Apache-2.0 */\n")

    @staticmethod
    def selected_files(plan):
        return {item["path"] for item in plan["overlay_usage"]["files"] if item["state"] == "expected"}

    def create_sample(self):
        plan = self.preview(**self.sample_selection())
        job = self.call("POST", "create", self.body(plan_id=plan["id"])).data
        result = wait_job(self.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        return result["result"]["project"]

    def test_creation_preview_uses_captured_default_and_preserves_requested_commands(self):
        for target, revision, match in (("demo/chip", "0.7.0", "default"),
                                        ("demo@0.14.0/chip", "0.14.0", "exact")):
            with self.subTest(target=target):
                plan = self.preview(target=target, **self.sample_selection())
                self.assertEqual(plan["profile"]["target"], target)
                self.assertIn(f"-DBOARD={target}", plan["recipe"]["configure"])
                evidence = plan["overlay_usage"]["revision_resolution"]
                self.assertEqual((evidence["effective"], evidence["match"]), (revision, match))
                self.assertEqual(self.selected_files(plan),
                                 {f"boards/demo_chip_{revision.replace('.', '_')}.overlay",
                                  plan["default_overlay"]["path"]})
                self.assertFalse(Path(plan["destination"]).exists())
        self.analyze.assert_not_called()

    def test_build_preview_uses_original_observation_and_leaves_captured_attempts_unchanged(self):
        project = self.create_sample()
        source = Path(project["path"])
        attempt = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": str(self.workspace),
                   "project_id": project["id"], "status": "built", "profile": deepcopy(project["profile"]),
                   "configuration": {"id": None, "name": "Original"},
                   "context": {"configured": True, "source_dir": str(source), "requested_target": "demo/chip",
                               "devicetree": {"files": [str(source / "app.overlay")], "files_observed": True}}}
        self.creator.store.save_attempt(attempt)
        newer = deepcopy(self.snapshot)
        newer.update(id=uuid.uuid4().hex, fingerprint="environment-newer-default")
        newer["boards"][0]["default_revision"] = "0.14.0"
        self.creator.environments.save(newer)
        records = {path: path.read_bytes() for path in self.creator.store.root.rglob("*.json")}
        self.analyze.reset_mock()
        with patch("subprocess.Popen", side_effect=AssertionError("A preview does not run Configure")):
            plan = self.call("POST", "build-preview", self.body(project_id=project["id"], operation="configure")).data["plan"]
            current = self.call("GET", "creator").data["projects"][0]
        self.assertEqual(plan["overlay_usage"]["revision_resolution"]["effective"], "0.7.0")
        self.assertEqual(self.selected_files(plan), {"boards/demo_chip_0_7_0.overlay", project["default_overlay"]["path"]})
        self.assertEqual(plan["profile"], project["profile"])
        self.assertIn("-DBOARD=demo/chip", plan["recipe"]["configure"])
        historical = current["overlay_usage_by_attempt"][attempt["id"]]
        self.assertEqual(historical["status"], "confirmed")
        self.assertEqual([item["path"] for item in historical["files"] if item["state"] == "active"], ["app.overlay"])
        self.assertNotIn("revision_resolution", historical)
        self.assertEqual(records, {path: path.read_bytes() for path in records})
        self.assertFalse((source / "build").exists())
        self.analyze.assert_not_called()

    def test_snapshot_revision_requires_matching_identity_workspace_and_target(self):
        profile = self.preview(**self.sample_selection())["profile"]
        self.assertEqual(snapshot_revision(profile, self.snapshot)["effective"], "0.7.0")
        for changes in ({"environment_id": uuid.uuid4().hex}, {"environment_fingerprint": "changed"},
                        {"workspace": str(self.root)}, {"target": "demo@0.8.0/chip"}):
            with self.subTest(changes=changes):
                result = snapshot_revision({**profile, **changes}, self.snapshot)
                self.assertFalse(result["supported"])
                self.assertTrue(result["reason"])
        malformed = deepcopy(self.snapshot)
        malformed["boards"][0]["targets"] = None
        self.assertFalse(snapshot_revision(profile, malformed)["supported"])
        unrevisioned = deepcopy(self.snapshot)
        for key in ("revisions", "revision_format", "revision_exact", "default_revision"):
            del unrevisioned["boards"][0][key]
        self.assertIsNone(snapshot_revision(profile, unrevisioned))

    def test_unavailable_snapshot_does_not_predict_default_board_auto_selection(self):
        project = self.create_sample()
        fragments = {"schema_version": 1, "kconfig": {"mode": "auto", "files": []},
                     "overlays": {"mode": "replace", "files": ["app.overlay"]}}
        configuration = self.call("POST", "configurations", self.body(project_id=project["id"], name="Replace",
            board_id="board-one", target="demo/chip", sdk_id="sdk-one", fragments=fragments)).data["configuration"]
        path = self.creator.environments.root / "snapshots" / f"{self.snapshot['id']}.json"
        for failure in ("missing", "mismatched"):
            with self.subTest(failure=failure):
                if failure == "missing":
                    path.unlink()
                else:
                    path.write_text(json.dumps({**self.snapshot, "fingerprint": "different-observation"}))
                plan = self.call("POST", "build-preview", self.body(project_id=project["id"], operation="configure")).data["plan"]
                report = plan["overlay_usage"]
                self.assertEqual(report["status"], "unknown")
                self.assertFalse(report["revision_resolution"]["supported"])
                self.assertTrue(report["revision_resolution"]["reason"])
                self.assertNotIn("app.overlay", self.selected_files(plan))
                replacement = self.call("POST", "build-preview", self.body(project_id=project["id"],
                    configuration_id=configuration["id"], operation="configure")).data["plan"]
                self.assertEqual(replacement["overlay_usage"]["status"], "expected")
                self.assertEqual(self.selected_files(replacement), {"app.overlay"})
        self.assertEqual(self.creator.store.project(project["id"], str(self.workspace)), project)


if __name__ == "__main__":
    unittest.main()
