"""Observable contracts for the Environment feature, jobs and shared HTTP transport."""

from concurrent.futures import CancelledError
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request as HTTPRequest, urlopen
import uuid

from zephyr_utilities.app import Application, create_server
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.projects.service import validate_options


def observation(workspace, options, **kwargs):
    return {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": {"path": str(workspace)},
            "request": options, "status": "complete", "cache": {"hit": False}, "boards": [], "socs": [],
            "sdks": [], "diagnostics": [], "capabilities": {"analysis_only": True, "create_project": False}}


def wait_job(manager, identifier):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = manager.get(identifier)
        if job["status"] not in {"queued", "running"}:
            return job
        threading.Event().wait(0.005)
    raise AssertionError("Analysis job did not reach a terminal state.")


class ProjectsServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)

    def call(self, method, path, body=None):
        return self.app.router.dispatch(Request(method, path, body=body or {}))

    def test_opening_environment_does_not_scan_or_write(self):
        with patch("zephyr_utilities.features.projects.service.analyze_environment") as analyze:
            data = self.call("GET", "/api/projects/environment").data
            analyze.assert_not_called()
        self.assertEqual(data, {"workspace": str(self.workspace), "snapshot": None, "job": None})
        self.assertFalse((self.root / "data/.features/projects").exists())

    def test_analysis_available_but_creation_stays_unimplemented(self):
        feature = next(item for item in self.call("GET", "/api/features").data["features"] if item["id"] == "projects")
        self.assertEqual(feature["status"], "available")
        self.assertEqual(feature["entry_path"], "/#/projects/environment")
        self.assertIn("planned", feature["description"])
        self.assertIn(b"mount", self.app.asset("/assets/projects/index.js").data)
        with self.assertRaises(FileNotFoundError):
            self.call("POST", "/api/projects/create")

    def test_completed_observation_survives_application_restart(self):
        with patch("zephyr_utilities.features.projects.service.analyze_environment", side_effect=observation):
            response = self.call("POST", "/api/projects/analyze", {"board_roots": ["custom"]})
            self.assertEqual(response.status, 202)
            job = wait_job(self.app.context.jobs, response.data["id"])
        self.assertEqual(job["status"], "completed", job.get("error"))
        reopened = Application(self.workspace, self.root / "data")
        self.addCleanup(reopened.close)
        data = reopened.router.dispatch(Request("GET", "/api/projects/environment")).data
        self.assertEqual(data["snapshot"]["id"], job["result"]["snapshot"]["id"])
        self.assertEqual(data["snapshot"]["freshness"], "saved-observation")
        self.assertEqual(data["snapshot"]["request"]["board_roots"], [str(self.workspace / "custom")])
        self.assertIsNone(data["job"])
        self.assertEqual(self.app.store.root, self.root / "data")

    def test_workspace_and_options_are_captured_at_submission(self):
        other = self.root / "second"
        other.mkdir()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        received = {}

        def analyze(workspace, options, **kwargs):
            received.update(workspace=workspace, options=options, environ=kwargs["environ"])
            started.set()
            release.wait(2)
            return observation(workspace, options)

        with patch("zephyr_utilities.features.projects.service.analyze_environment", side_effect=analyze):
            response = self.call("POST", "/api/projects/analyze", {"sdk_paths": ["sdk"]})
            self.assertTrue(started.wait(1))
            self.call("PATCH", "/api/workspace", {"path": str(other)})
            data = self.call("GET", "/api/projects/environment").data
            self.assertIsNone(data["job"])
            self.assertIsNone(data["snapshot"])
            release.set()
            job = wait_job(self.app.context.jobs, response.data["id"])
        self.assertEqual(received["workspace"], str(self.workspace))
        self.assertEqual(received["options"]["sdk_paths"], [str(self.workspace / "sdk")])
        self.assertEqual(job["result"]["snapshot"]["workspace"]["path"], str(self.workspace))

    def test_cancelled_scan_keeps_previous_observation(self):
        previous = observation(self.workspace, {})
        self.app.context.services["projects"].store.save(previous)
        started = threading.Event()

        def analyze(workspace, options, *, cancel, **kwargs):
            started.set()
            if cancel.wait(2):
                raise CancelledError()
            raise AssertionError("Scan was not cancelled")

        with patch("zephyr_utilities.features.projects.service.analyze_environment", side_effect=analyze):
            job = self.call("POST", "/api/projects/analyze").data
            self.assertTrue(started.wait(1))
            with self.assertRaisesRegex(ValueError, "already using"):
                self.call("POST", "/api/projects/analyze")
            self.call("POST", f"/api/jobs/{job['id']}/cancel")
            self.assertEqual(wait_job(self.app.context.jobs, job["id"])["status"], "cancelled")
        self.assertEqual(self.call("GET", "/api/projects/environment").data["snapshot"]["id"], previous["id"])

    def test_invalid_options_never_submit_a_job(self):
        for body in ({"force": "yes"}, {"board_roots": "path"}, {"sdk_paths": [None]},
                     {"soc_roots": ["bad\0path"]}, {"sdk_paths": ["x"] * 17},
                     {"zephyr_base": []}, {"workspace": "/unexpected"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.call("POST", "/api/projects/analyze", body)
        self.assertEqual(self.app.context.jobs.list("projects"), [])

    def test_paths_with_spaces_and_argument_like_names_remain_data(self):
        options = validate_options({"board_roots": ["a board", "--help", "a board"]}, self.workspace)
        self.assertEqual(options["board_roots"], [str(self.workspace / "a board"), str(self.workspace / "--help")])


class ProjectsHttpTests(unittest.TestCase):
    requires_loopback = True

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = create_server(self.root, self.root / "data", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()
        self.server.app.close()

    def request(self, path, *, method="GET", body=None, authorized=True, origin=None):
        headers = {}
        if authorized:
            headers["X-Session-Token"] = self.server.app.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        if origin:
            headers["Origin"] = origin
        return urlopen(HTTPRequest(self.url + path, data=json.dumps(body).encode() if body is not None else None,
                                   headers=headers, method=method), timeout=3)

    def test_analysis_uses_shared_auth_origin_and_method_guards(self):
        for kwargs, expected in [({"authorized": False}, 403), ({"origin": "https://outside.invalid"}, 403),
                                 ({"method": "DELETE", "body": {}}, 405)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPError) as error:
                self.request("/api/projects/environment", **kwargs)
            self.assertEqual(error.exception.code, expected)
        with self.assertRaises(HTTPError) as error:
            self.request("/api/projects/analyze", method="POST", body={"force": "wrong"})
        self.assertEqual(error.exception.code, 400)

    def test_scan_and_read_share_the_application_server(self):
        with patch("zephyr_utilities.features.projects.service.analyze_environment", side_effect=observation):
            with self.request("/api/projects/analyze", method="POST", body={}) as response:
                self.assertEqual(response.status, 202)
                job = json.load(response)
            result = wait_job(self.server.app.context.jobs, job["id"])
        self.assertEqual(result["status"], "completed")
        with self.request("/api/projects/environment") as response:
            data = json.load(response)
        self.assertEqual(data["snapshot"]["id"], result["result"]["snapshot"]["id"])
        with self.request("/assets/projects/environment-state.js") as response:
            self.assertIn(b"createEnvironmentSession", response.read())
