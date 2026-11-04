"""Integration checks for local access controls and persistent report workflows."""

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zephyr_utilities.memory.api import create_server


def report(size):
    return {"total_size": size, "symbols": {"name": "Root", "size": size, "children": [
        {"name": "application", "size": size, "children": [
            {"name": "sample_buffer", "identifier": "sample_buffer", "size": size,
             "section": ".bss", "address": 8192}]}]}}


class ApiTests(unittest.TestCase):
    requires_loopback = True

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = create_server(self.temp.name, Path(self.temp.name) / "snapshots", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.token = self.request("/api/session")[1]["token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.app.close()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, headers=None, token=True, raw=False):
        request_headers = {"Content-Type": "application/json"}
        if token and hasattr(self, "token"):
            request_headers["X-Session-Token"] = self.token
        request_headers.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.url + path, data=data, method=method, headers=request_headers)
        try:
            response = urlopen(req, timeout=10)
        except HTTPError as exc:
            response = exc
        with response:
            value = response.read()
            return response.status, value if raw else json.loads(value)

    def test_session_and_origin_guards(self):
        self.assertEqual(self.request("/api/snapshots", token=False)[0], 403)
        self.assertEqual(self.request("/api/session", headers={"Origin": "https://outside.invalid"})[0], 403)
        self.assertEqual(self.request("/api/session", headers={"Host": "outside.invalid"})[0], 403)
        self.assertEqual(self.request("/api/session", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request("/api/snapshots")[0], 200)

    def test_import_compare_rename_and_export_persist(self):
        status, baseline = self.request("/api/import", "POST", {"ram": report(1024), "name": "Before"})
        self.assertEqual(status, 200, baseline)
        status, candidate = self.request("/api/import", "POST", {"ram": report(1536), "name": "After"})
        self.assertEqual(status, 200, candidate)
        _, comparison = self.request(f"/api/compare?baseline={baseline['id']}&candidate={candidate['id']}")
        self.assertEqual(comparison["totals"]["ram"]["delta"], 512)
        self.assertIsNone(comparison["totals"]["rom"]["delta"])
        self.assertEqual(comparison["changes"]["ram"][0]["delta"], 512)
        status, renamed = self.request(f"/api/snapshots/{candidate['id']}", "PATCH",
                                       {"name": "After optimization", "notes": "Measured configuration"})
        self.assertEqual(status, 200, renamed)
        self.assertEqual(renamed["name"], "After optimization")
        _, listing = self.request("/api/snapshots")
        self.assertEqual(len(listing["snapshots"]), 2)
        self.assertEqual(self.server.app.store.load(baseline["id"])["reports"]["ram"]["total_size"], 1024)
        status, data = self.request(f"/api/snapshots/{candidate['id']}/export?format=csv", raw=True)
        self.assertEqual(status, 200)
        self.assertIn(b"size_bytes", data)
        self.assertIn(b"1536", data)
        _, exported = self.request(f"/api/snapshots/{baseline['id']}/export?format=json")
        self.assertEqual(exported["name"], "Before")

    def test_bad_input_does_not_create_snapshot(self):
        self.assertEqual(self.request("/api/import", "POST", {"ram": {"unexpected": 12}})[0], 400)
        self.assertEqual(self.request("/api/import", "POST", {"ram": report(1), "name": ["invalid"]})[0], 400)
        self.assertEqual(self.request("/api/jobs", "POST", {"build_path": ""})[0], 400)
        self.assertEqual(self.request("/api/snapshots")[1]["snapshots"], [])
        self.assertIn(self.request("/api/snapshots/..%2F..%2Fprivate")[0], (400, 404))

    def test_delete_snapshot_requires_auth_and_updates_both_api_namespaces(self):
        _, first = self.request("/api/memory/import", "POST", {"ram": report(1024), "name": "Delete me"})
        _, second = self.request("/api/memory/import", "POST", {"ram": report(1536), "name": "Keep me"})
        path = f"/api/memory/snapshots/{first['id']}"
        self.assertEqual(self.request(path, "DELETE", {}, token=False)[0], 403)
        self.assertEqual(self.request(path, "DELETE", {}, headers={"Origin": "https://outside.invalid"})[0], 403)
        self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.request(path, "DELETE", {}), (200, {"id": first["id"], "deleted": True}))
        self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request(path + "/export")[0], 404)
        self.assertEqual(self.request(path, "DELETE", {})[0], 404)
        self.assertEqual(self.request("/api/snapshots")[1]["snapshots"][0]["id"], second["id"])
        self.assertEqual(self.request(f"/api/snapshots/{second['id']}")[1]["name"], "Keep me")
        self.assertEqual(self.request(f"/api/snapshots/{second['id']}", "DELETE", {})[0], 200)
        self.assertEqual(self.request("/api/memory/snapshots")[1]["snapshots"], [])
        self.assertEqual(self.request("/api/memory/snapshots/not-an-id", "DELETE", {})[0], 400)


if __name__ == "__main__":
    unittest.main()
