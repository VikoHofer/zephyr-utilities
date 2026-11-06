"""D1 read-only contexts preserve source identity and Inspector compatibility."""

import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from zephyr_utilities.app import Application, create_server
from zephyr_utilities.core.http import Request
from zephyr_utilities.core.source_files import WorkspaceSourceReader
from zephyr_utilities.features.devicetree.preview import PREVIEW_FEATURE
from zephyr_utilities.features.inspector.feature import FEATURE as INSPECTOR
from zephyr_utilities.features.projects.feature import FEATURE as PROJECTS
from zephyr_utilities.features.projects.fragments import automatic_fragments

import test_project_context as project_fixtures
import test_projects_api as http_fixtures


PREFIX = "/api/devicetree/editor/"


def application_sources(path):
    """Synthetic application markers are data and must never be executed."""
    path.mkdir(parents=True)
    (path / "CMakeLists.txt").write_text('message(FATAL_ERROR "Never execute this fixture")\n')
    (path / "app.overlay").write_bytes(b'/* caf\xc3\xa9 */\r\n&uart0 { status = "okay"; };\r\n')
    (path / "overlays").mkdir()
    (path / "overlays/extra.overlay").write_text('&i2c0 { status = "disabled"; };\n')
    return path


def source_bytes(path):
    return {item.relative_to(path).as_posix(): item.read_bytes()
            for item in path.rglob("*") if item.is_file() and not item.is_symlink()}


class EditorContextApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.source = application_sources(self.workspace / "weather-station")
        self.app = self.make_app((PREVIEW_FEATURE, INSPECTOR))

    def make_app(self, features=None):
        app = Application(self.workspace, self.root / "data", features=features)
        self.addCleanup(app.close)
        return app

    def call(self, operation, body=None, *, app=None):
        return (app or self.app).router.dispatch(Request(
            "POST", PREFIX + operation, body={} if body is None else body)).data

    def open(self, path=None, *, app=None):
        return self.call("open", {"workspace": str(self.workspace),
                                 "application_path": str(path or self.source)}, app=app)

    def selection(self, context, document=None):
        result = {"workspace": str(self.workspace), "context_id": context["id"],
                  "expected_revision": context["revision"]}
        if document is not None:
            result["document_id"] = document["id"]
        return result

    def test_default_catalogue_exposes_the_limited_editor_and_its_assets(self):
        default = self.make_app()
        features = default.router.dispatch(Request("GET", "/api/features")).data["features"]
        editor = next(item for item in features if item["id"] == "devicetree")
        self.assertEqual(editor["status"], "available")
        self.assertEqual(editor["entry_path"], "/#/devicetree/editor")
        self.assertEqual(editor["frontend"]["module"], "/assets/devicetree/index.js")
        self.assertEqual(editor["navigation"], [])
        self.assertEqual(self.call("catalog", {"workspace": str(self.workspace)}, app=default)["workspace"], str(self.workspace))
        self.assertTrue(default.asset("/assets/devicetree/index.js"))

    def test_preview_and_inspector_work_in_both_registration_orders(self):
        for features in ((PREVIEW_FEATURE, INSPECTOR), (INSPECTOR, PREVIEW_FEATURE)):
            with self.subTest(order=[item.id for item in features]):
                app = self.make_app(features)
                context = self.open(app=app)
                self.assertEqual(context["application"]["path"], str(self.source))
                self.assertIs(app.context.services["devicetree"], app.context.services["inspector"])
                self.assertIsNot(app.context.services["devicetree_editor"], app.context.services["inspector"])
                request = {"workspace": str(self.workspace), "application_path": str(self.source)}
                catalog = app.router.dispatch(Request(
                    "POST", "/api/devicetree/application-sources", body=request)).data
                document = {**request, "catalog_id": catalog["catalog_id"],
                            "file_id": catalog["files"][0]["id"]}
                legacy = app.router.dispatch(Request(
                    "POST", "/api/devicetree/application-source-file", body=document)).data
                canonical = app.router.dispatch(Request(
                    "POST", "/api/inspector/application-source-file", body=document)).data
                self.assertEqual(legacy, canonical)

    def test_directory_open_has_unknown_build_context_and_does_not_execute_or_write(self):
        before = source_bytes(self.source)
        with patch("subprocess.run", side_effect=AssertionError("Opening must not run tools")), \
                patch("subprocess.Popen", side_effect=AssertionError("Opening must not spawn tools")), \
                patch.object(self.app.context.jobs, "submit", side_effect=AssertionError("Opening must not submit jobs")):
            context = self.open()
            self.assertEqual(context["revision"], 1)
            self.assertEqual(context["workspace"], str(self.workspace))
            self.assertIsNone(context["project"])
            self.assertIsNone(context["configuration"])
            self.assertIsNone(context["target"])
            self.assertIsNone(context["environment"])
            self.assertIsNone(context["editing_document"])
            self.assertFalse(context["capabilities"]["edit"])
            self.assertFalse(context["capabilities"]["save"])
            self.assertEqual({item["relative_path"] for item in context["documents"]},
                             {"app.overlay", "overlays/extra.overlay"})
            self.assertTrue(all(item["usage"] == "unknown" for item in context["documents"]))
            selected = self.call("select", self.selection(context, context["documents"][0]))
            result = self.call("document", self.selection(selected, selected["editing_document"]))
            self.assertEqual(result["document"]["text"].encode(),
                             Path(result["document"]["path"]).read_bytes())
        self.assertEqual(before, source_bytes(self.source))
        self.assertEqual(self.app.context.jobs.list(), [])
        self.assertFalse(self.app.context.feature_data("devicetree").exists())

    def test_missing_provider_is_explained_without_disabling_directory_sources(self):
        catalog = self.call("catalog", {"workspace": str(self.workspace)})
        self.assertEqual(catalog["projects"], [])
        self.assertTrue(catalog["diagnostics"])
        with self.assertRaises(ValueError):
            self.call("open", {"workspace": str(self.workspace), "project_id": "a" * 32,
                               "configuration_id": None})
        self.assertEqual(self.open()["application"]["path"], str(self.source))

    def test_selection_advances_revision_and_rejects_out_of_order_requests(self):
        opened = self.open()
        selected = self.call("select", self.selection(opened, opened["documents"][1]))
        self.assertEqual(selected["id"], opened["id"])
        self.assertEqual(selected["revision"], opened["revision"] + 1)
        self.assertEqual(selected["editing_document"]["id"], opened["documents"][1]["id"])
        self.assertEqual(self.call("context", self.selection(selected))["editing_document"],
                         selected["editing_document"])
        for operation in ("context", "select", "document"):
            stale = self.selection(opened, opened["documents"][0] if operation != "context" else None)
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.call(operation, stale)

    def test_document_ids_do_not_authorize_another_context_or_an_arbitrary_path(self):
        first = self.open()
        second = self.open(application_sources(self.workspace / "other-app"))
        for invalid in (first["documents"][0]["id"], str(self.source / "app.overlay"), "../app.overlay"):
            request = {**self.selection(second), "document_id": invalid}
            for operation in ("select", "document"):
                with self.subTest(operation=operation, value=invalid), self.assertRaises(ValueError):
                    self.call(operation, request)

    def test_source_changes_invalidate_read_and_context_until_explicit_reopen(self):
        opened = self.open()
        document = next(item for item in opened["documents"] if item["relative_path"] == "app.overlay")
        (self.source / "app.overlay").write_text('&uart0 { status = "disabled"; };\n')
        for operation in ("context", "select", "document"):
            request = self.selection(opened, document if operation != "context" else None)
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.call(operation, request)
        reopened = self.open()
        self.assertNotEqual(reopened["id"], opened["id"])
        current = next(item for item in reopened["documents"] if item["relative_path"] == "app.overlay")
        self.assertNotEqual(current["sha256"], document["sha256"])

    def test_deleted_source_and_changed_application_marker_are_not_silently_refreshed(self):
        opened = self.open()
        document = opened["documents"][0]
        Path(document["path"]).unlink()
        with self.assertRaises((ValueError, OSError)):
            self.call("document", self.selection(opened, document))
        reopened = self.open()
        (self.source / "CMakeLists.txt").write_text("# changed application recipe\n")
        with self.assertRaises(ValueError):
            self.call("context", self.selection(reopened))

    def test_replaced_application_directory_is_rejected_even_with_identical_source_bytes(self):
        opened = self.open()
        original = source_bytes(self.source)
        self.source.rename(self.workspace / "old-application")
        self.source.mkdir()
        for name, content in original.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        with self.assertRaises(ValueError):
            self.call("context", self.selection(opened))

    def test_application_replaced_during_overlay_scan_is_rejected_even_with_identical_bytes(self):
        opened = self.open()
        list_files = WorkspaceSourceReader.list_files

        def replace_after_scan(reader, path, *args, **kwargs):
            files = list_files(reader, path, *args, **kwargs)
            original = self.workspace / "moved-original"
            self.source.rename(original)
            shutil.copytree(original, self.source)
            return files

        with patch.object(WorkspaceSourceReader, "list_files", replace_after_scan), self.assertRaises(ValueError):
            self.call("context", self.selection(opened))

    def test_workspace_switch_during_overlay_scan_rejects_the_pending_context(self):
        opened = self.open()
        elsewhere = self.root / "new-workspace"
        elsewhere.mkdir()
        list_files = WorkspaceSourceReader.list_files

        def switch_after_scan(reader, path, *args, **kwargs):
            files = list_files(reader, path, *args, **kwargs)
            self.app.context.workspace.set(elsewhere)
            return files

        with patch.object(WorkspaceSourceReader, "list_files", switch_after_scan), self.assertRaises(ValueError):
            self.call("context", self.selection(opened))

    def test_application_replaced_during_final_document_read_rejects_identical_content(self):
        opened = self.open()
        selected = next(item for item in opened["documents"] if item["relative_path"] == "app.overlay")
        read_text = WorkspaceSourceReader.read_text
        selected_reads = 0

        def replace_on_document_read(reader, path, *args, **kwargs):
            nonlocal selected_reads
            if Path(path) == Path(selected["path"]):
                selected_reads += 1
                if selected_reads == 2:
                    original = self.workspace / "moved-original"
                    self.source.rename(original)
                    shutil.copytree(original, self.source)
            return read_text(reader, path, *args, **kwargs)

        with patch.object(WorkspaceSourceReader, "read_text", replace_on_document_read), self.assertRaises(ValueError):
            self.call("document", self.selection(opened, selected))
        self.assertEqual(selected_reads, 2)

    def test_replacing_an_observed_overlay_with_a_symlink_never_follows_its_target(self):
        opened = self.open()
        document = next(item for item in opened["documents"] if item["relative_path"] == "app.overlay")
        outside = self.root / "outside.overlay"
        outside.write_text("// private external source\n")
        overlay = self.source / "app.overlay"
        overlay.unlink()
        overlay.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.call("document", self.selection(opened, document))
        self.assertEqual(outside.read_text(), "// private external source\n")

    def test_workspace_switch_cannot_retarget_an_existing_context(self):
        opened = self.open()
        second = self.root / "other-workspace"
        second.mkdir()
        self.app.context.workspace.set(second)
        old_request = self.selection(opened)
        for workspace in (str(self.workspace), str(second)):
            with self.subTest(workspace=workspace), self.assertRaises(ValueError):
                self.call("context", {**old_request, "workspace": workspace})
        with self.assertRaises(ValueError):
            self.open()

    def test_source_paths_are_confined_and_application_markers_are_required(self):
        outside = application_sources(self.root / "outside")
        linked = self.workspace / "linked-app"
        linked.symlink_to(self.source, target_is_directory=True)
        empty = self.workspace / "empty"
        empty.mkdir()
        build = application_sources(self.workspace / "configured-output")
        (build / "CMakeCache.txt").write_text("# synthetic build marker\n")
        for path in (outside, linked, empty, self.workspace, self.workspace / "missing",
                     self.workspace / "weather-station" / ".." / "weather-station", build):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.open(path)

    def test_discovery_ignores_build_hidden_and_symlinked_sources(self):
        (self.source / ".hidden.overlay").write_text("// hidden file is not a source candidate\n")
        for name in ("build", "build-release", ".git"):
            directory = self.source / name
            directory.mkdir()
            (directory / "unrelated.overlay").write_text("// never a source candidate\n")
        configured = self.source / "generated"
        configured.mkdir()
        (configured / "CMakeCache.txt").write_text("# generated\n")
        (configured / "output.overlay").write_text("// generated\n")
        (self.source / "linked.overlay").symlink_to(self.source / "app.overlay")
        context = self.open()
        self.assertEqual({item["relative_path"] for item in context["documents"]},
                         {"app.overlay", "overlays/extra.overlay"})

    def test_invalid_bodies_fields_and_revision_types_never_open_or_retarget_sources(self):
        for body in ([], "source", 1, {}, {"workspace": str(self.workspace)},
                     {"workspace": str(self.workspace), "application_path": str(self.source), "execute": True},
                     {"workspace": str(self.workspace), "application_path": str(self.source),
                      "project_id": "a" * 32, "configuration_id": None},
                     {"workspace": str(self.workspace), "application_path": []}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.call("open", body)
        opened = self.open()
        for revision in (True, "1", 0, -1, None):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.call("context", {**self.selection(opened), "expected_revision": revision})
        with self.assertRaises(ValueError):
            self.call("catalog", {"workspace": str(self.workspace), "scan": True})
        with self.assertRaises(ValueError):
            self.call("context", {**self.selection(opened), "application_path": str(self.source)})
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_context_payload_is_detached_and_expired_context_requires_reopen(self):
        opened = self.open()
        selection = self.selection(opened)
        opened["documents"].clear()
        opened["application"]["path"] = "/not-authoritative"
        current = self.call("context", selection)
        self.assertEqual(current["application"]["path"], str(self.source))
        self.assertEqual(len(current["documents"]), 2)
        with patch("zephyr_utilities.features.devicetree.service.time.monotonic",
                   return_value=time.monotonic() + 3600):
            with self.assertRaisesRegex(ValueError, "expired"):
                self.call("context", selection)

    def test_context_cache_evicts_old_observations_without_reusing_identity(self):
        self.app.context.services["devicetree_editor"].max_contexts = 1
        first = self.open()
        second = self.open()
        self.assertNotEqual(first["id"], second["id"])
        with self.assertRaisesRegex(ValueError, "expired"):
            self.call("context", self.selection(first))
        self.assertEqual(self.call("context", self.selection(second))["id"], second["id"])

    def test_replaced_workspace_changes_fingerprint_even_when_application_inode_is_retained(self):
        original = self.open()
        moved = self.root / "previous-workspace"
        self.workspace.rename(moved)
        self.workspace.mkdir()
        (moved / self.source.name).rename(self.source)
        reopened = self.open()
        self.assertEqual(original["application"]["path"], reopened["application"]["path"])
        self.assertNotEqual(original["workspace_identity"], reopened["workspace_identity"])
        self.assertNotEqual(original["fingerprint"], reopened["fingerprint"])


class RegisteredEditorContextTests(unittest.TestCase):
    def setUp(self):
        self.fixture = project_fixtures.ProjectContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.workspace = self.fixture.workspace
        self.app = Application(self.workspace, self.fixture.root / "data", features=(PREVIEW_FEATURE, PROJECTS))
        self.addCleanup(self.app.close)

    def call(self, operation, body):
        return self.app.router.dispatch(Request("POST", PREFIX + operation, body=body)).data

    def open(self, configuration_id=None):
        return self.call("open", {"workspace": str(self.workspace), "project_id": self.fixture.project["id"],
                                  "configuration_id": configuration_id})

    def test_composed_provider_preserves_original_and_named_identity_and_selects_applicable_default(self):
        named = self.fixture.configuration(revision=3, profile={**self.fixture.profile, "target": "demo@2.0/chip/core"})
        before = self.fixture.bytes_in_tree()
        with patch("subprocess.run", side_effect=AssertionError("Opening must not run tools")), \
                patch("subprocess.Popen", side_effect=AssertionError("Opening must not spawn tools")), \
                patch.object(self.app.context.jobs, "submit", side_effect=AssertionError("Opening must not submit jobs")):
            catalog = self.call("catalog", {"workspace": str(self.workspace)})
            original = self.open()
            selected = self.open(named["id"])
        self.assertEqual(before, self.fixture.bytes_in_tree())
        self.assertIsNotNone(self.app.context.project_context)
        self.assertEqual({item["id"] for item in catalog["projects"][0]["configurations"]}, {None, named["id"]})
        self.assertEqual(original["project"]["id"], self.fixture.project["id"])
        self.assertEqual(original["configuration"]["name"], "Original")
        self.assertEqual(original["configuration"]["revision"], 0)
        self.assertEqual(original["target"]["requested"], "demo@1.0/chip")
        self.assertEqual(original["editing_document"]["relative_path"], original["default_overlay"]["path"])
        self.assertEqual(original["editing_document"]["usage"], "predicted")
        self.assertEqual(selected["configuration"]["id"], named["id"])
        self.assertEqual(selected["configuration"]["revision"], 3)
        self.assertEqual(selected["target"]["requested"], "demo@2.0/chip/core")
        self.assertIsNone(selected["default_overlay"])
        self.assertIsNone(selected["editing_document"])
        self.assertEqual(original["environment"]["id"], self.fixture.observation["id"])
        self.assertEqual(selected["environment"]["id"], original["environment"]["id"])
        self.assertNotEqual(selected["fingerprint"], original["fingerprint"])

    def test_replacement_recipe_requires_explicit_destination_when_default_is_not_selected(self):
        fragments = automatic_fragments()
        fragments["overlays"] = {"mode": "replace", "files": ["app.overlay"]}
        named = self.fixture.configuration(revision=4, profile={**self.fixture.profile, "fragments": fragments})
        opened = self.open(named["id"])
        self.assertEqual(opened["fragments"]["overlays"], fragments["overlays"])
        self.assertEqual(opened["configuration"]["revision"], 4)
        default = next(item for item in opened["documents"] if item["relative_path"] == opened["default_overlay"]["path"])
        self.assertEqual(default["availability"], "available")
        self.assertEqual(default["usage"], "unknown")
        self.assertIsNone(opened["editing_document"])
        document = next(item for item in opened["documents"] if item["relative_path"] == "app.overlay")
        self.assertEqual(document["usage"], "predicted")
        request = {"workspace": str(self.workspace), "context_id": opened["id"],
                   "expected_revision": opened["revision"], "document_id": document["id"]}
        selected = self.call("select", request)
        self.assertEqual(selected["editing_document"]["id"], document["id"])
        content = self.call("document", {**request, "expected_revision": selected["revision"]})
        self.assertEqual(content["document"]["text"], (self.fixture.source / "app.overlay").read_text())
        original = self.open()
        self.assertEqual(original["configuration"]["revision"], 0)
        self.assertEqual(original["editing_document"]["relative_path"], original["default_overlay"]["path"])

    def test_hidden_default_metadata_cannot_authorize_reading_or_listing_a_hidden_overlay(self):
        hidden = self.fixture.source / ".hidden.overlay"
        hidden.write_text("// hidden source must remain excluded\n")
        project = self.fixture.project
        updated = {**project, "revision": 1,
                   "default_overlay": {**project["default_overlay"], "path": hidden.name}}
        self.fixture.creator.store.update_project(updated, project)
        read_text = WorkspaceSourceReader.read_text

        def read_visible(reader, path, *args, **kwargs):
            self.assertNotEqual(Path(path), hidden)
            return read_text(reader, path, *args, **kwargs)

        with patch.object(WorkspaceSourceReader, "read_text", read_visible):
            opened = self.open()
        self.assertNotIn(hidden.name, {item["relative_path"] for item in opened["documents"]})
        self.assertIsNone(opened["editing_document"])
        self.assertIn("overlay-unavailable", {item["code"] for item in opened["diagnostics"]})


class EditorContextHttpTests(unittest.TestCase):
    requires_loopback = True
    request = http_fixtures.ProjectsHttpTests.request
    close = http_fixtures.ProjectsHttpTests.close

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = application_sources(self.root / "app")
        self.server = create_server(self.root, self.root / "data", 0,
                                    features=(PREVIEW_FEATURE, INSPECTOR))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def test_preview_uses_shared_authentication_origin_and_validation_with_read_only_round_trip(self):
        for operation in ("catalog", "open", "context", "select", "document"):
            for authorized, expected in ((False, 403), (True, 400)):
                with self.subTest(operation=operation, authorized=authorized), self.assertRaises(HTTPError) as error:
                    self.request(PREFIX + operation, method="POST", body={}, authorized=authorized)
                self.assertEqual(error.exception.code, expected)
                error.exception.close()
        body = {"workspace": str(self.root), "application_path": str(self.source)}
        with self.assertRaises(HTTPError) as error:
            self.request(PREFIX + "open", method="POST", body=body, origin="https://outside.invalid")
        self.assertEqual(error.exception.code, 403)
        error.exception.close()
        before = source_bytes(self.source)
        with self.request(PREFIX + "open", method="POST", body=body) as response:
            opened = json.load(response)
        selection = {"workspace": str(self.root), "context_id": opened["id"],
                     "expected_revision": opened["revision"], "document_id": opened["documents"][0]["id"]}
        with self.request(PREFIX + "document", method="POST", body=selection) as response:
            document = json.load(response)["document"]
        self.assertEqual(document["text"].encode(), Path(document["path"]).read_bytes())
        self.assertEqual(source_bytes(self.source), before)
        self.assertEqual(self.server.app.context.jobs.list(), [])

    def test_draft_routes_share_authentication_and_synchronize_without_writing(self):
        for operation in ("open", "get", "source", "command", "undo", "redo", "review", "save", "compare", "reload",
                          "recovery/list", "recovery/read", "recovery/restore", "recovery/discard",
                          "validation/review", "validation/submit", "validation/status", "validation/cancel", "validation/handoff", "bindings", "peripherals",
                          "resources", "removal/review"):
            for authorized, expected in ((False, 403), (True, 400)):
                with self.subTest(operation=operation, authorized=authorized), self.assertRaises(HTTPError) as error:
                    self.request(PREFIX + "drafts/" + operation, method="POST", body={}, authorized=authorized)
                self.assertEqual(error.exception.code, expected)
                error.exception.close()

        def post(operation, body):
            with self.request(PREFIX + operation, method="POST", body=body) as response:
                return json.load(response)

        before = source_bytes(self.source)
        context = post("open", {"workspace": str(self.root), "application_path": str(self.source)})
        selection = {"workspace": str(self.root), "context_id": context["id"], "expected_revision": context["revision"]}
        draft = post("drafts/open", {**selection, "document_id": context["documents"][0]["id"]})

        def mutation(value):
            return {**selection, "draft_id": value["id"], "expected_draft_revision": value["revision"],
                    "expected_draft_sha256": value["sha256"]}

        edited = post("drafts/command", {**mutation(draft), "command": {
            "kind": "set_status", "node_id": draft["nodes"][0]["id"], "status": "disabled"}})
        self.assertIn('status = "disabled"', edited["text"])
        self.assertTrue(edited["dirty"])
        self.assertTrue(edited["capabilities"]["save"])
        invalid = post("drafts/source", {**mutation(edited), "text": '&uart0 { status = "'})
        self.assertEqual(invalid["nodes"], [])
        self.assertEqual(invalid["text"], '&uart0 { status = "')
        restored = post("drafts/undo", mutation(invalid))
        self.assertEqual(restored["text"], edited["text"])
        original = post("drafts/undo", mutation(restored))
        self.assertEqual(original["text"], draft["text"])
        self.assertFalse(original["dirty"])
        self.assertEqual(source_bytes(self.source), before)
        self.assertEqual(self.server.app.context.jobs.list(), [])

    def test_resource_removal_review_repairs_are_explicit_atomic_and_revision_bound(self):
        def post(operation, body):
            with self.request(PREFIX + operation, method="POST", body=body) as response:
                return json.load(response)

        overlay = self.source / "app.overlay"
        original = '/ { thing: sensor { status = "okay"; }; aliases { selected-device = &thing; }; };\n'
        overlay.write_text(original)
        before = source_bytes(self.source)
        context = post("open", {"workspace": str(self.root), "application_path": str(self.source)})
        binding = {"workspace": str(self.root), "context_id": context["id"], "expected_revision": context["revision"]}
        document = next(item for item in context["documents"] if item["relative_path"] == "app.overlay")
        draft = post("drafts/open", {**binding, "document_id": document["id"]})

        def guard(value):
            return {**binding, "draft_id": value["id"], "expected_draft_revision": value["revision"],
                    "expected_draft_sha256": value["sha256"]}

        observed = post("drafts/resources", guard(draft))
        target = next(node for node in observed["nodes"] if node["name"] == "sensor")
        request = {**guard(draft), "schema_id": observed["schema_id"], "node_id": target["node_id"], "repairs": []}
        blocked = post("drafts/removal/review", request)
        self.assertFalse(blocked["can_apply"])
        self.assertEqual(len(blocked["dependants"]), 1)
        self.assertEqual(len(blocked["repair_choices"]), 1)
        self.assertEqual(source_bytes(self.source), before)
        plan = post("drafts/removal/review", {**request, "repairs": [blocked["repair_choices"][0]["id"]]})
        self.assertTrue(plan["can_apply"], plan)
        self.assertIn("sensor", plan["diff"]["text"])
        with self.assertRaises(HTTPError) as error:
            post("drafts/command", {**guard(draft), "command": {"kind": "remove_reviewed", "review_id": blocked["review_id"]}})
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        removed = post("drafts/command", {**guard(draft), "command": {"kind": "remove_reviewed", "review_id": plan["review_id"]}})
        self.assertEqual(removed["revision"], draft["revision"] + 1)
        self.assertEqual(removed["text"], plan["candidate_text"])
        self.assertNotIn("selected-device", removed["text"])
        self.assertNotIn("sensor", removed["text"])
        self.assertEqual(source_bytes(self.source), before)
        with self.assertRaises(HTTPError) as stale:
            post("drafts/command", {**guard(removed), "command": {"kind": "remove_reviewed", "review_id": plan["review_id"]}})
        self.assertEqual(stale.exception.code, 400)
        stale.exception.close()
        restored = post("drafts/undo", guard(removed))
        self.assertEqual(restored["text"], original)
        self.assertEqual(self.server.app.context.jobs.list(), [])

    def test_reviewed_save_reopen_undo_and_recovery_use_the_shared_http_server(self):
        def post(operation, body):
            with self.request(PREFIX + operation, method="POST", body=body) as response:
                return json.load(response)

        workspace = str(self.root)
        overlay = self.source / "app.overlay"
        original = overlay.read_bytes()
        cmake = (self.source / "CMakeLists.txt").read_bytes()
        context = post("open", {"workspace": workspace, "application_path": str(self.source)})
        binding = {"workspace": workspace, "context_id": context["id"], "expected_revision": context["revision"]}
        draft = post("drafts/open", {**binding, "document_id": context["documents"][0]["id"]})

        def guard(value):
            return {**binding, "draft_id": value["id"], "expected_draft_revision": value["revision"],
                    "expected_draft_sha256": value["sha256"]}

        text = draft["text"].replace('"okay"', '"disabled"')
        edited = post("drafts/source", {**guard(draft), "text": text})
        self.assertEqual(overlay.read_bytes(), original)
        review = post("drafts/review", guard(edited))
        self.assertEqual(review["status"], "ready")
        self.assertEqual(review["plan"]["candidate_text"], text)
        self.assertEqual(review["plan"]["validation"]["state"], "unvalidated")
        request = {**guard(edited), "plan_id": review["plan"]["id"]}
        saved = post("drafts/save", request)
        self.assertEqual(saved["status"], "saved")
        self.assertFalse(saved["draft"]["dirty"])
        self.assertEqual(overlay.read_bytes(), text.encode())
        self.assertEqual(post("drafts/save", request), saved)
        binding = {"workspace": workspace, "context_id": saved["context"]["id"],
                   "expected_revision": saved["context"]["revision"]}
        undone = post("drafts/undo", guard(saved["draft"]))
        self.assertEqual(undone["text"].encode(), original)
        self.assertTrue(undone["dirty"])
        self.assertEqual(overlay.read_bytes(), text.encode(), "Undo only changes the draft")
        records = post("drafts/recovery/list", {"workspace": workspace})
        self.assertTrue(any(record["id"] == undone["id"] for record in records["records"]))
        comparison = post("drafts/compare", guard(undone))
        self.assertEqual(comparison["current_text"], text)
        reset = post("drafts/reload", {**guard(undone), "comparison_id": comparison["comparison_id"], "mode": "discard"})
        self.assertEqual(reset["draft"]["text"], text)
        self.assertFalse(reset["draft"]["dirty"])
        self.assertEqual((self.source / "CMakeLists.txt").read_bytes(), cmake)
        self.assertEqual(self.server.app.context.jobs.list(), [])
