"""Inspector owns advanced reads while legacy routes retain the same live identities."""

import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.inspector.feature import FEATURE

import test_devicetree_bindings as binding_fixtures
import test_projects_api as http_fixtures


class InspectorApiTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.build, _, _ = binding_fixtures.prepare(self.workspace)
        self.app = Application(self.workspace, self.root / "data", features=(FEATURE,))
        self.addCleanup(self.app.close)

    def call(self, path, body=None, query=None):
        return self.app.router.dispatch(Request("GET" if query else "POST", path,
                                                query=query or {}, body=body or {})).data

    def test_canonical_build_and_tree_routes_share_observations_with_legacy_aliases(self):
        observed = self.call("/api/inspector/inspect", {"workspace": str(self.workspace), "build_dir": str(self.build)})
        query = {"workspace": [str(self.workspace)]}
        self.assertEqual(self.call(f"/api/inspector/inspections/{observed['id']}", query=query),
                         self.call(f"/api/projects/inspections/{observed['id']}", query=query))
        selection = {"workspace": str(self.workspace), "inspection_id": observed["id"], "domain": "application"}
        self.assertEqual(self.call("/api/inspector/devicetree", selection), self.call("/api/devicetree/inspect", selection))
        text = {**selection, "kind": "resolved-dts"}
        self.assertEqual(self.call("/api/inspector/inspection-text", text), self.call("/api/projects/inspection-text", text))
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_catalogues_and_binding_references_work_across_old_and_new_routes(self):
        observed = self.call("/api/projects/inspect", {"workspace": str(self.workspace), "build_dir": str(self.build)})
        selection = {"workspace": str(self.workspace), "inspection_id": observed["id"], "domain": "application"}
        catalog = self.call("/api/inspector/sources", selection)
        selected = {**selection, "catalog_id": catalog["catalog_id"], "node_path": "/soc/serial@4000"}
        candidates = self.call("/api/devicetree/bindings", selected)
        self.assertEqual(self.call("/api/inspector/bindings", selected), candidates)
        document = {**selection, "catalog_id": catalog["catalog_id"], "file_id": candidates["bindings"][0]["file"]["id"]}
        self.assertEqual(self.call("/api/inspector/source-file", document), self.call("/api/devicetree/source-file", document))
        self.assertIs(self.app.context.services["inspector"], self.app.context.services["devicetree"])

    def test_application_catalogue_aliases_share_file_ids_and_stale_checks(self):
        application = self.workspace / "app"
        application.mkdir()
        (application / "CMakeLists.txt").write_text("# Never execute\n")
        (application / "prj.conf").write_text("CONFIG_TEST=y\n")
        selection = {"workspace": str(self.workspace), "application_path": str(application)}
        catalog = self.call("/api/devicetree/application-sources", selection)
        selected = {**selection, "catalog_id": catalog["catalog_id"], "file_id": catalog["files"][0]["id"]}
        self.assertEqual(self.call("/api/inspector/application-source-file", selected),
                         self.call("/api/devicetree/application-source-file", selected))
        (application / "prj.conf").write_text("CONFIG_TEST=n\n")
        for prefix in ("/api/inspector", "/api/devicetree"):
            with self.subTest(prefix=prefix), self.assertRaisesRegex(ValueError, "changed"):
                self.call(prefix + "/application-source-file", selected)
        refreshed = self.call("/api/inspector/application-sources", selection)
        self.assertNotEqual(refreshed["catalog_id"], catalog["catalog_id"])


class InspectorHttpTests(unittest.TestCase):
    requires_loopback = True
    setUp = http_fixtures.ProjectsHttpTests.setUp
    close = http_fixtures.ProjectsHttpTests.close
    request = http_fixtures.ProjectsHttpTests.request

    def test_inspector_routes_use_shared_authentication_and_input_guards(self):
        endpoints = ("inspect", "inspection-text", "devicetree", "sources", "bindings", "source-file",
                     "application-sources", "application-source-file")
        for endpoint in endpoints:
            for authorized, expected in ((False, 403), (True, 400)):
                with self.subTest(endpoint=endpoint, authorized=authorized), self.assertRaises(HTTPError) as error:
                    self.request("/api/inspector/" + endpoint, method="POST", body={}, authorized=authorized)
                self.assertEqual(error.exception.code, expected)
                error.exception.close()
        build, _, _ = binding_fixtures.prepare(self.root)
        with self.request("/api/inspector/inspect", method="POST", body={
                "workspace": str(self.root), "build_dir": str(build)}) as response:
            observed = json.load(response)
        with self.request(f"/api/projects/inspections/{observed['id']}?workspace={self.root}") as response:
            self.assertEqual(json.load(response)["id"], observed["id"])
