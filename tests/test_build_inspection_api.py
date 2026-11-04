"""Existing-build inspection and the Device Tree handoff share one read-only context."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request

import test_projects_api as existing_api


def make_build(root, name="build"):
    build = root / name
    (build / "zephyr").mkdir(parents=True)
    (build / "CMakeCache.txt").write_text(
        f"ZEPHYR_BASE:PATH={root}/zephyr-source\nAPPLICATION_SOURCE_DIR:PATH={root}/app\n"
        "CMAKE_C_COMPILER:FILEPATH=/example/gcc\nBOARD:STRING=demo@2/chip\n")
    (build / "build_info.yml").write_text(
        f"cmake:\n  application:\n    source-dir: '{root}/app'\n"
        "  board:\n    name: demo\n    revision: '2'\n    qualifiers: chip\n"
        f"  zephyr:\n    zephyr-base: '{root}/zephyr-source'\n"
        "  devicetree:\n    files: []\n")
    (build / "zephyr" / "zephyr.dts").write_text(
        '/dts-v1/;\n/ { model = "Fixture"; compatible = "test,fixture";\n'
        '  soc { uart0: serial@4000 { compatible = "test,uart"; status = "okay"; }; };\n};\n')
    return build


class InspectionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.build = make_build(self.workspace)
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)

    def call(self, method, path, body=None, query=None):
        return self.app.router.dispatch(Request(method, path, query=query or {}, body=body or {})).data

    def body(self, **values):
        return {"workspace": str(self.workspace), **values}

    def inspect(self, build=None):
        return self.call("POST", "/api/projects/inspect", self.body(build_dir=str(build or self.build)))

    def test_projects_and_tree_share_target_domain_and_output_without_execution(self):
        files = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("subprocess.Popen", side_effect=AssertionError("Inspection must not execute tools")):
            inspection = self.inspect()
            domain = inspection["domains"][0]
            body = self.body(inspection_id=inspection["id"], domain=domain["name"])
            tree = self.call("POST", "/api/devicetree/inspect", body)
            raw = self.call("POST", "/api/projects/inspection-text", {**body, "kind": "resolved-dts"})
        self.assertEqual(domain["context"]["effective_target"], "demo@2/chip")
        self.assertEqual(tree["domain"]["context"], domain["context"])
        self.assertEqual(tree["domain"]["name"], domain["name"])
        self.assertEqual(tree["document"]["sha256"], raw["sha256"])
        self.assertIn("/soc/serial@4000", [node["path"] for node in tree["tree"]["nodes"]])
        self.assertEqual(self.app.context.jobs.list(), [])
        self.assertEqual({path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}, files)

    def test_changed_output_needs_new_observation_and_separate_builds_remain_distinct(self):
        before = self.inspect()
        other = self.inspect(make_build(self.workspace, "second-build"))
        self.assertNotEqual(before["id"], other["id"])
        self.assertNotEqual(before["domains"][0]["build_dir"], other["domains"][0]["build_dir"])
        dts = self.build / "zephyr" / "zephyr.dts"
        dts.write_text('/dts-v1/; / { model = "Changed"; };')
        observed = self.call("GET", f"/api/projects/inspections/{before['id']}",
                             query={"workspace": [str(self.workspace)]})
        self.assertEqual(observed["domains"][0]["freshness"], "changed")
        with self.assertRaises(ValueError):
            self.call("POST", "/api/devicetree/inspect", self.body(
                inspection_id=before["id"], domain=before["domains"][0]["name"]))
        after = self.inspect()
        self.assertNotEqual(after["id"], before["id"])
        self.assertEqual(after["domains"][0]["freshness"], "unchanged")

    def test_workspace_switch_and_unknown_input_cannot_redirect_inspection(self):
        inspection = self.inspect()
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaises(ValueError):
            self.call("POST", "/api/projects/inspect", self.body(build_dir=str(self.build)))
        with self.assertRaises(ValueError):
            self.call("GET", f"/api/projects/inspections/{inspection['id']}",
                      query={"workspace": [str(other)]})
        with self.assertRaises(ValueError):
            self.call("POST", "/api/devicetree/inspect", {
                "workspace": str(other), "inspection_id": inspection["id"], "domain": "application"})
        self.app.context.workspace.set(self.workspace)
        for path, body in (("inspect", self.body(build_dir=str(self.build), run=True)),
                           ("inspection-text", self.body(inspection_id=inspection["id"],
                                                          domain="application", kind="resolved-dts", path="/etc/passwd"))):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call("POST", f"/api/projects/{path}", body)


class InspectionHttpTests(unittest.TestCase):
    requires_loopback = True
    setUp = existing_api.ProjectsHttpTests.setUp
    close = existing_api.ProjectsHttpTests.close
    request = existing_api.ProjectsHttpTests.request

    def test_inspection_routes_use_shared_authentication_and_workspace_guards(self):
        for path in ("/api/projects/inspect", "/api/projects/inspection-text", "/api/devicetree/inspect"):
            with self.subTest(path=path):
                with self.assertRaises(HTTPError) as error:
                    self.request(path, method="POST", body={}, authorized=False)
                self.assertEqual(error.exception.code, 403)
                with self.assertRaises(HTTPError) as error:
                    self.request(path, method="POST", body={})
                self.assertEqual(error.exception.code, 400)
        build = make_build(self.root)
        with self.request("/api/projects/inspect", method="POST", body={
                "workspace": str(self.root), "build_dir": str(build)}) as response:
            observed = json.load(response)
        self.assertEqual(observed["workspace"], str(self.root))
        self.assertEqual(observed["domains"][0]["context"]["effective_target"], "demo@2/chip")
