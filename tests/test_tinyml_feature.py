"""TinyML's chooser and UART drawing share the existing application and job manager."""

from pathlib import Path
import os
import tempfile
import time
from types import SimpleNamespace
import unittest

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.tinyml.feature import FEATURE


class TinyMLFeatureTests(unittest.TestCase):
    def test_chooser_and_drawing_are_registered_without_reading_retired_training_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "data/.features/tinyml/datasets/retained.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"Retired training records stay untouched.")
            app = Application(root, root / "data")
            self.addCleanup(app.close)
            descriptor = next(item for item in app.router.dispatch(Request("GET", "/api/features")).data["features"]
                              if item["id"] == "tinyml")
            self.assertEqual(descriptor["status"], "available")
            self.assertEqual(descriptor["entry_path"], "/#/tinyml/overview")
            self.assertTrue(callable(FEATURE.register))
            self.assertEqual({entry["view"] for entry in descriptor["navigation"]}, {"overview", "draw"})
            self.assertIn("tinyml_uart", app.context.services)
            self.assertNotIn("tinyml", app.context.services)
            for name in ("index.js", "state.js", "view.html", "style.css"):
                response = app.asset(f"/assets/tinyml/{name}")
                self.assertTrue(response.data)
            self.assertEqual(legacy.read_bytes(), b"Retired training records stay untouched.")
            self.assertEqual(app.context.jobs.list(feature="tinyml"), [])

    def test_uart_routes_share_workspace_guards_and_do_not_start_jobs_when_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root, root / "data")
            self.addCleanup(app.close)
            service = app.context.services["tinyml_uart"]
            service.port_provider = lambda: []
            ports = app.router.dispatch(Request("GET", "/api/tinyml/ports", {"workspace": [str(root)]})).data
            self.assertEqual(ports["workspace"], str(root))
            self.assertEqual(ports["ports"], [])
            self.assertEqual(ports["jobs"], [])
            self.assertIsNone(ports["connection"])
            for method, path, body in (("GET", "/api/tinyml/ports", None),
                                       ("POST", "/api/tinyml/prepare", {}), ("POST", "/api/tinyml/open", {}),
                                       ("POST", "/api/tinyml/close", {}), ("POST", "/api/tinyml/infer", {})):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    app.router.dispatch(Request(method, path, body=body))
            self.assertEqual(app.context.jobs.list(feature="tinyml"), [])
            self.assertFalse(app.context.feature_data("tinyml").exists())

    def test_application_shutdown_closes_explicitly_opened_idle_uart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = Application(root, root / "data")
            self.addCleanup(app.close)
            master, slave = os.openpty()
            self.addCleanup(os.close, master)
            self.addCleanup(os.close, slave)
            calls = []

            class Client:
                def __init__(self, *args, **kwargs):
                    pass

                def open(self):
                    calls.append("open")

                def close(self):
                    calls.append("close")

            service = app.context.services["tinyml_uart"]
            service.port_provider = lambda: [SimpleNamespace(device=os.ttyname(slave), description="Test UART", hwid="PTY")]
            service.client_factory = Client
            ports = app.router.dispatch(Request("GET", "/api/tinyml/ports", {"workspace": [str(root)]})).data
            opened = app.router.dispatch(Request("POST", "/api/tinyml/open",
                body={"workspace": str(root), "port_id": ports["ports"][0]["id"]}))
            self.assertEqual(opened.status, 202)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                job = app.context.jobs.get(opened.data["id"])
                if job["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.005)
            self.assertEqual(job["status"], "completed", job.get("error"))
            self.assertEqual(calls, ["open"])
            app.close()
            self.assertEqual(calls, ["open", "close"])
