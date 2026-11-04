"""Application source navigation uses the shared authenticated, read-only server."""

import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import test_projects_api as existing_api


class ApplicationSourcesHttpTests(unittest.TestCase):
    requires_loopback = True
    setUp = existing_api.ProjectsHttpTests.setUp
    close = existing_api.ProjectsHttpTests.close
    request = existing_api.ProjectsHttpTests.request

    def post(self, path, body):
        with self.request(path, method="POST", body=body) as response:
            return json.load(response)

    def test_new_routes_retain_auth_origin_method_and_input_guards(self):
        for path in ("/api/devicetree/application-sources", "/api/devicetree/application-source-file"):
            for options, expected in [({"authorized": False}, 403),
                                      ({"origin": "https://outside.invalid"}, 403), ({}, 400)]:
                with self.subTest(path=path, options=options), self.assertRaises(HTTPError) as error:
                    self.request(path, method="POST", body={}, **options)
                self.assertEqual(error.exception.code, expected)
                error.exception.close()
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.request(path)
            self.assertEqual(error.exception.code, 405)
            error.exception.close()

    def test_unconfigured_application_reads_and_refresh_preserve_input_files(self):
        app = self.root / "application"
        app.mkdir()
        (app / "CMakeLists.txt").write_text("message(FATAL_ERROR \"Never execute this fixture\")\n")
        (app / "prj.conf").write_text("CONFIG_GPIO=y\n")
        (app / "app.overlay").write_text('#include "pins.dtsi"\n&uart0 { status = "okay"; };\n')
        (app / "pins.dtsi").write_text('/ { model = "Current source"; };\n')
        before = {path: path.read_bytes() for path in app.iterdir()}
        selected = {"workspace": str(self.root), "application_path": "application"}
        with patch("subprocess.Popen", side_effect=AssertionError("Source browsing must not execute tools")):
            catalogue = self.post("/api/devicetree/application-sources", selected)
            self.assertEqual(catalogue["mode"], "application")
            self.assertEqual(catalogue["application_path"], str(app))
            self.assertNotIn("inspection_id", catalogue)
            self.assertNotIn("domain", catalogue)
            overlay = next(item for item in catalogue["files"] if item["name"] == "app.overlay")
            body = {"workspace": str(self.root), "application_path": str(app),
                    "catalog_id": catalogue["catalog_id"], "file_id": overlay["id"]}
            document = self.post("/api/devicetree/application-source-file", body)
            include = document["includes"][0]["files"][0]
            included = self.post("/api/devicetree/application-source-file", {**body, "file_id": include["id"]})
        self.assertEqual(included["document"]["text"], before[app / "pins.dtsi"].decode())
        self.assertEqual({path: path.read_bytes() for path in app.iterdir()}, before)
        self.assertEqual(self.server.app.context.jobs.list(), [])

        (app / "app.overlay").write_text('&uart0 { status = "disabled"; };\n')
        with self.assertRaises(HTTPError) as error:
            self.post("/api/devicetree/application-source-file", body)
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        refreshed = self.post("/api/devicetree/application-sources", selected)
        self.assertNotEqual(refreshed["catalog_id"], catalogue["catalog_id"])
        self.assertNotEqual(next(item["sha256"] for item in refreshed["files"] if item["name"] == "app.overlay"), overlay["sha256"])

        other = self.root / "other"
        other.mkdir()
        self.server.app.context.workspace.set(other)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/devicetree/application-source-file", body)
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
