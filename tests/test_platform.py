"""Behavior checks for feature composition, shared state and isolated job lifecycles."""

from concurrent.futures import CancelledError
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request as HTTPRequest, urlopen

from zephyr_utilities.app import Application, create_server
from zephyr_utilities.core.features import FeatureDefinition, FrontendDefinition, NavigationItem
from zephyr_utilities.core.http import MethodNotAllowed, Request, Response, Router
from zephyr_utilities.core.jobs import JobManager
from zephyr_utilities.features import builtin_features


def report(size=4):
    return {"total_size": size, "symbols": {"name": "Root", "size": size, "children": [
        {"name": "buffer", "size": size, "section": ".bss"}]}}


def wait_for_job(manager, identifier):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = manager.get(identifier)
        if result["status"] not in {"queued", "running"}:
            return result
        threading.Event().wait(0.005)
    raise AssertionError("The job did not finish within three seconds.")


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def application(self, **kwargs):
        app = Application(self.root, self.root / "data", **kwargs)
        self.addCleanup(app.close)
        return app

    def test_feature_can_add_service_routes_and_assets_without_server_changes(self):
        assets = self.root / "demo-web"
        assets.mkdir()
        (assets / "index.js").write_text("export function mount() {}")

        def register(router, context):
            context.services["demo"] = {"data": context.feature_data("demo")}
            router.add("GET", "/api/demo/items/:id", lambda request: {
                "id": request.params["id"], "filter": request.query_value("filter"),
                "workspace": context.workspace.get()["path"],
            })

        feature = FeatureDefinition("demo", "Demo", "Test extension", register=register,
                                    frontend=FrontendDefinition(assets))
        app = self.application(features=(*builtin_features(), feature))
        response = app.router.dispatch(Request("GET", "/api/demo/items/one", {"filter": ["active"]}))
        self.assertEqual(response.data, {"id": "one", "filter": "active", "workspace": str(self.root)})
        self.assertEqual(app.context.services["demo"]["data"], self.root / "data/.features/demo")
        catalog = app.router.dispatch(Request("GET", "/api/features")).data["features"]
        self.assertEqual(catalog[-1]["frontend"]["module"], "/assets/demo/index.js")
        self.assertEqual(app.asset("/assets/demo/index.js").data, b"export function mount() {}")
        planned = [item for item in catalog if item["status"] == "planned"]
        self.assertEqual(planned, [])

    def test_tinyml_demo_and_uart_views_have_assets_without_training_routes_or_services(self):
        legacy = self.root / "data/.features/tinyml/datasets/retained.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(b"Retired data must remain untouched, even if unreadable by the old tool.")
        app = self.application()
        self.assertEqual(legacy.read_bytes(), b"Retired data must remain untouched, even if unreadable by the old tool.")
        catalog = app.router.dispatch(Request("GET", "/api/features")).data["features"]
        tinyml = next(item for item in catalog if item["id"] == "tinyml")
        self.assertEqual(tinyml["title"], "TinyML Demo Creator")
        self.assertEqual(tinyml["status"], "available")
        self.assertEqual(tinyml["entry_path"], "/#/tinyml/overview")
        self.assertEqual(tinyml["frontend"], {"module": "/assets/tinyml/index.js", "template": "/assets/tinyml/view.html",
                                              "stylesheet": "/assets/tinyml/style.css"})
        self.assertEqual({entry["view"] for entry in tinyml["navigation"]}, {"overview", "draw"})
        self.assertIn("tinyml_uart", app.context.services)
        self.assertNotIn("tinyml", app.context.services)
        for path in ("/api/tinyml/catalog", "/api/tinyml/capabilities"):
            with self.subTest(path=path), self.assertRaises(FileNotFoundError):
                app.router.dispatch(Request("GET", path))
        with self.assertRaises(FileNotFoundError):
            app.router.dispatch(Request("POST", "/api/tinyml/datasets/preview", body={}))
        for path in ("/assets/tinyml/index.js", "/assets/tinyml/view.html", "/assets/tinyml/style.css"):
            with self.subTest(path=path):
                self.assertTrue(app.asset(path).data)
        self.assertEqual(legacy.read_bytes(), b"Retired data must remain untouched, even if unreadable by the old tool.")

    def test_workspace_changes_future_operations_but_keeps_submitted_inputs(self):
        app = self.application(features=())
        other = self.root / "second"
        other.mkdir()
        captured_path = app.context.workspace.get()["path"]
        release = threading.Event()
        self.addCleanup(release.set)
        job = app.context.jobs.submit("demo", lambda emit, cancel: (
            release.wait(2), {"path": captured_path})[1])
        response = app.router.dispatch(Request("PATCH", "/api/workspace", body={"path": str(other)}))
        self.assertEqual(response.data, {"path": str(other)})
        self.assertEqual(app.router.dispatch(Request("GET", "/api/folders")).data["path"], str(other))
        release.set()
        self.assertEqual(wait_for_job(app.context.jobs, job["id"])["result"]["path"], captured_path)
        with self.assertRaises(FileNotFoundError):
            app.context.workspace.set(other / "missing")
        self.assertEqual(app.context.workspace.get()["path"], str(other))

    def test_namespaced_and_legacy_routes_share_existing_snapshot_layout(self):
        app = self.application()
        imported = app.router.dispatch(Request("POST", "/api/import", body={"ram": report(), "name": "Before"})).data
        identifier = imported["id"]
        self.assertEqual(app.store.root, self.root / "data")
        self.assertEqual(app.context.feature_data("memory"), app.store.root)
        self.assertEqual(app.router.dispatch(Request("GET", f"/api/memory/snapshots/{identifier}")).data, imported)
        app.router.dispatch(Request("PATCH", f"/api/memory/snapshots/{identifier}", body={"name": "Renamed"}))
        self.assertEqual(app.router.dispatch(Request("GET", f"/api/snapshots/{identifier}")).data["name"], "Renamed")
        reopened = Application(self.root, self.root / "data")
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.store.load(identifier)["name"], "Renamed")
        self.assertEqual(len(reopened.store.list()), 1)
        from zephyr_utilities.memory import snapshots as legacy
        from zephyr_utilities.features.memory import snapshots as current
        self.assertIs(legacy, current)

    def test_assets_cannot_escape_registered_directory_or_expose_python(self):
        assets = self.root / "web"
        assets.mkdir()
        (assets / "secret.py").write_text("secret")
        (self.root / "outside.js").write_text("private")
        (assets / "link.js").symlink_to(self.root / "outside.js")
        feature = FeatureDefinition("demo", "Demo", "Test extension", frontend=FrontendDefinition(assets))
        app = self.application(features=(feature,))
        for path in ("/assets/demo/../outside.js", "/assets/demo/link.js", "/assets/demo/secret.py",
                     "/assets/demo/.hidden.js", "/assets/demo/\\outside.js", "/assets/unknown/index.js"):
            with self.subTest(path=path), self.assertRaises(FileNotFoundError):
                app.asset(path)

    def test_duplicate_features_routes_and_invalid_manifests_are_rejected(self):
        feature = FeatureDefinition("demo", "Demo", "Test extension")
        with self.assertRaisesRegex(ValueError, "unique"):
            self.application(features=(feature, feature))
        with self.assertRaisesRegex(ValueError, "Planned"):
            FeatureDefinition("demo", "Demo", "Test", status="planned", register=lambda router, context: None)
        router = Router()
        router.add("GET", "/api/demo/:id", lambda request: Response({}, 202))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            router.add("GET", "/api/demo/:other", lambda request: {})
        with self.assertRaises(MethodNotAllowed):
            router.dispatch(Request("POST", "/api/demo/one"))
        with self.assertRaises(FileNotFoundError):
            router.dispatch(Request("GET", "/api/missing"))

    def test_shell_paths_assets_and_legacy_hashes_cannot_be_feature_ids(self):
        for identifier in ("home", "shell", "shared", "core", "api", "assets", "app-main",
                           "overview", "explorer", "compare", "snapshots"):
            with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "reserved"):
                FeatureDefinition(identifier, "Collision", "Conflicts with shared navigation")

    def test_feature_sidebar_contract_supports_nested_views_without_cross_feature_paths(self):
        feature = FeatureDefinition("demo", "Demo", "Test extension", navigation_group="advanced",
                                    navigation=(NavigationItem("Results", "results", ("inspect",)),
                                                NavigationItem("History", "results/history")))
        descriptor = feature.descriptor()
        self.assertEqual(descriptor["navigation_group"], "advanced")
        self.assertEqual(descriptor["navigation"], [
            {"label": "Results", "view": "results", "match_views": ["results", "inspect"]},
            {"label": "History", "view": "results/history", "match_views": ["results/history"]},
        ])
        self.assertEqual(FeatureDefinition("simple", "Simple", "No subviews").descriptor()["navigation"], [])
        for view in ("", "/memory", "../memory", "https://outside.invalid", "results?x=1", "results#anchor"):
            with self.subTest(view=view), self.assertRaises(ValueError):
                NavigationItem("Bad", view)
        for options in ({"status": "planned", "navigation": (NavigationItem("Results", "results"),)},
                        {"navigation_group": "other"},
                        {"navigation": (NavigationItem("One", "results"), NavigationItem("Two", "results"))},
                        {"navigation": (NavigationItem("One", "results", ("inspect",)), NavigationItem("Two", "inspect"))}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                FeatureDefinition("invalid", "Invalid", "Bad navigation", **options)


class JobTests(unittest.TestCase):
    def manager(self, **kwargs):
        manager = JobManager(**kwargs)
        self.addCleanup(manager.close)
        return manager

    def test_cancelled_resource_is_released_without_affecting_other_features(self):
        manager = self.manager()
        started = threading.Event()
        cleaned = threading.Event()

        def cancellable(emit, cancel):
            started.set()
            try:
                cancel.wait(2)
                if cancel.is_set():
                    raise CancelledError()
                raise AssertionError("Cancellation did not arrive.")
            finally:
                cleaned.set()

        first = manager.submit("demo", cancellable, resource_key="device:one")
        self.assertTrue(started.wait(1))
        with self.assertRaisesRegex(ValueError, "resource"):
            manager.submit("another", lambda emit, cancel: {}, resource_key="device:one")
        independent = manager.submit("another", lambda emit, cancel: {"value": 42}, resource_key="device:two")
        manager.cancel(first["id"])
        self.assertEqual(wait_for_job(manager, first["id"])["status"], "cancelled")
        self.assertTrue(cleaned.is_set())
        self.assertEqual(wait_for_job(manager, independent["id"])["result"], {"value": 42})
        reused = manager.submit("another", lambda emit, cancel: {}, resource_key="device:one")
        self.assertEqual(wait_for_job(manager, reused["id"])["status"], "completed")
        self.assertEqual({job["feature"] for job in manager.list()}, {"demo", "another"})

    def test_failure_releases_resource_and_logs_are_bounded(self):
        manager = self.manager(max_completed=2)

        def failing(emit, cancel):
            for _ in range(510):
                emit("x" * 2200)
            raise RuntimeError("Expected failure")

        failed = manager.submit("demo", failing, resource_key="build:one")
        result = wait_for_job(manager, failed["id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "Expected failure")
        self.assertEqual(len(result["logs"]), 500)
        self.assertTrue(all(len(line) <= 2000 for line in result["logs"]))
        result["logs"].clear()
        self.assertEqual(len(manager.get(failed["id"])["logs"]), 500)
        for _ in range(2):
            job = manager.submit("demo", lambda emit, cancel: {"snapshot_id": "example"},
                                 resource_key="build:one", metadata={"build_path": "/example"})
            result = wait_for_job(manager, job["id"])
        self.assertEqual(result["snapshot_id"], "example")
        self.assertEqual(result["build_path"], "/example")
        self.assertEqual(len(manager.list()), 2)

    def test_shutdown_cancels_running_and_queued_work_and_rejects_submissions(self):
        manager = self.manager(max_workers=1)
        started = threading.Event()
        queued_ran = threading.Event()

        def cancellable(emit, cancel):
            started.set()
            cancel.wait(2)
            raise CancelledError()

        running = manager.submit("demo", cancellable)
        self.assertTrue(started.wait(1))
        queued = manager.submit("other", lambda emit, cancel: queued_ran.set())
        manager.close()
        self.assertEqual(manager.get(running["id"])["status"], "cancelled")
        self.assertEqual(manager.get(queued["id"])["status"], "cancelled")
        self.assertFalse(queued_ran.is_set())
        with self.assertRaisesRegex(ValueError, "shutting down"):
            manager.submit("demo", lambda emit, cancel: {})


class PlatformHttpTests(unittest.TestCase):
    requires_loopback = True

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

        def register(router, context):
            router.add("GET", "/api/demo/echo/:id", lambda request: {"id": request.params["id"]})

        feature = FeatureDefinition("demo", "Demo", "Extension fixture", register=register)
        self.server = create_server(self.temp.name, Path(self.temp.name) / "data", 0,
                                    features=(*builtin_features(), feature))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.token = self.request("/api/session")[1]["token"]

    def close_server(self):
        self.server.shutdown()
        self.server.app.close()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, method="GET", body=None, headers=None, token=True):
        request_headers = {"Content-Type": "application/json"}
        if token and hasattr(self, "token"):
            request_headers["X-Session-Token"] = self.token
        request_headers.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        request = HTTPRequest(self.url + path, data=data, method=method, headers=request_headers)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    def test_injected_feature_uses_same_auth_origin_and_method_guards(self):
        self.assertEqual(self.request("/api/demo/echo/one", token=False)[0], 403)
        self.assertEqual(self.request("/api/demo/echo/one", headers={"Origin": "https://outside.invalid"})[0], 403)
        self.assertEqual(self.request("/api/demo/echo/one"), (200, {"id": "one"}))
        self.assertEqual(self.request("/api/demo/echo/one", "POST", {})[0], 405)
        self.assertEqual(self.request("/api/memory/snapshots", token=False)[0], 403)
        self.assertEqual(self.request("/api/features")[1]["features"][-1]["id"], "demo")

    def test_memory_aliases_and_shared_jobs_and_workspace_use_one_application(self):
        status, snapshot = self.request("/api/memory/import", "POST", {"ram": report()})
        self.assertEqual(status, 200)
        self.assertEqual(self.request(f"/api/snapshots/{snapshot['id']}")[1], snapshot)
        path = Path(self.temp.name) / "other"
        path.mkdir()
        self.assertEqual(self.request("/api/workspace", "PATCH", {"path": str(path)}), (200, {"path": str(path)}))
        self.assertEqual(self.request("/api/workspace")[1]["path"], str(path))
        job = self.server.app.context.jobs.submit("demo", lambda emit, cancel: {"value": "done"})
        wait_for_job(self.server.app.context.jobs, job["id"])
        self.assertEqual(self.request("/api/jobs")[1]["jobs"][0]["feature"], "demo")
        self.assertEqual(self.request(f"/api/jobs/{job['id']}")[1]["result"], {"value": "done"})
        self.assertEqual(self.request("/api/devicetree/editor")[0], 404)


if __name__ == "__main__":
    unittest.main()
