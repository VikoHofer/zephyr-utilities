"""Explicit Open, Send and Close actions retain one shared UART connection."""

import base64
from concurrent.futures import CancelledError
from contextlib import suppress
import hashlib
import os
from pathlib import Path
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
import zlib

from zephyr_utilities.core.context import ApplicationContext
from zephyr_utilities.core.jobs import JobManager
from zephyr_utilities.core.workspace import WorkspaceService
from zephyr_utilities.features.tinyml.uart_service import UARTService


def encoded(data):
    return base64.b64encode(data).decode("ascii")


def wait_job(manager, identifier):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = manager.get(identifier)
        if job["status"] not in {"queued", "running"}:
            return job
        Event().wait(0.005)
    raise AssertionError("UART job did not settle within three seconds.")


class TinyMLUARTTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tinyml-uart-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.jobs = JobManager(max_workers=1)
        self.addCleanup(self.jobs.close)
        self.context = ApplicationContext(WorkspaceService(self.workspace), self.jobs, self.root / "data")
        self.master, self.slave = os.openpty()
        self.addCleanup(self.close_pty)
        self.device = os.ttyname(self.slave)
        self.advertised = [SimpleNamespace(device=self.device, description="Synthetic UART", hwid="TEST-PTY")]
        self.clients = []
        self.open_error = None
        self.synchronize_error = None
        self.upload_error = None
        self.upload_behavior = None
        self.pixels = bytes(255 if 10 <= index % 28 <= 17 and 4 <= index // 28 <= 23 else 0 for index in range(784))
        self.connection_id = None
        owner = self

        class FakeClient:
            def __init__(self, path, *, cancel, expected_identity):
                self.path = path
                self.cancel = cancel
                self.expected_identity = expected_identity
                self.closed = False
                self.opens = self.synchronizations = self.connects = 0
                self.uploaded = []
                owner.clients.append(self)

            def open(self):
                self.opens += 1
                if owner.open_error:
                    raise owner.open_error

            def connect(self):
                self.connects += 1
                raise AssertionError("Open and Send must not query firmware, board or model metadata.")

            def synchronize(self):
                self.synchronizations += 1
                if owner.synchronize_error:
                    raise owner.synchronize_error

            def upload(self, pixels, progress=None):
                self.uploaded.append(pixels)
                if owner.upload_error:
                    raise owner.upload_error
                if owner.upload_behavior:
                    return owner.upload_behavior(self, pixels, progress)
                if progress:
                    progress(28, 28)
                return SimpleNamespace(prediction=7, inference_us=1234,
                                       scores=tuple(1.0 if digit == 7 else 0.0 for digit in range(10)),
                                       crc32=f"{zlib.crc32(pixels) & 0xffffffff:08x}")

            def close(self):
                self.closed = True

        self.client_factory = FakeClient
        self.service = UARTService(self.context, port_provider=lambda: list(self.advertised), client_factory=FakeClient)
        self.addCleanup(self.service.close)

    def close_pty(self):
        for descriptor in (self.master, self.slave):
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
        self.master = self.slave = None

    def ports(self, workspace=None):
        return self.service.ports(workspace or str(self.workspace))

    def open_body(self, **changes):
        return {"workspace": self.context.workspace.get()["path"], "port_id": self.ports()["ports"][0]["id"], **changes}

    def open_connection(self):
        job = wait_job(self.jobs, self.service.open(self.open_body())["id"])
        self.assertEqual(job["status"], "completed", job.get("error"))
        connection = job["result"]["connection"]
        self.connection_id = connection["id"]
        return connection

    def body(self, **changes):
        return {"workspace": self.context.workspace.get()["path"], "connection_id": self.connection_id,
                "image": {"gray8": encoded(self.pixels)}, **changes}

    def close_body(self, **changes):
        return {"workspace": self.context.workspace.get()["path"], "connection_id": self.connection_id, **changes}

    def block_worker(self):
        entered, release = Event(), Event()
        self.addCleanup(release.set)
        self.jobs.submit("test", lambda emit, cancel: (entered.set(), release.wait(3), {"done": True})[-1])
        self.assertTrue(entered.wait(1))
        return release

    def test_port_catalogue_is_read_only_and_advertises_only_local_character_devices(self):
        self.advertised.extend([
            SimpleNamespace(device=str(self.root / "regular-file"), description="Invalid", hwid="FILE"),
            SimpleNamespace(device="/tmp/arbitrary-uart", description="Outside dev", hwid="OUTSIDE"),
        ])
        (self.root / "regular-file").write_text("Not a character device.")
        catalogue = self.ports()
        self.assertEqual(catalogue["schema_version"], 1)
        self.assertEqual(catalogue["workspace"], str(self.workspace))
        self.assertTrue(catalogue["available"])
        self.assertEqual([port["device"] for port in catalogue["ports"]], [self.device])
        self.assertRegex(catalogue["ports"][0]["id"], r"^[a-f0-9]{64}$")
        self.assertIsNone(catalogue["connection"])
        self.assertEqual(catalogue["jobs"], [])
        self.assertEqual(self.clients, [])
        self.assertFalse(self.context.feature_data("tinyml").exists())

    def test_open_opens_only_uart_and_requires_no_firmware_board_or_demo_selection(self):
        connection = self.open_connection()
        self.assertRegex(connection["id"], r"^[a-f0-9]{32}$")
        self.assertEqual(connection["device"], self.device)
        self.assertEqual(connection["state"], "open")
        self.assertEqual(self.ports()["connection"]["id"], connection["id"])
        self.assertEqual((self.clients[0].opens, self.clients[0].synchronizations, self.clients[0].connects), (1, 0, 0))
        self.assertEqual(self.clients[0].uploaded, [])
        self.assertFalse(self.clients[0].closed)
        info = os.stat(self.device)
        self.assertEqual(self.clients[0].expected_identity,
                         {"device": info.st_dev, "inode": info.st_ino, "rdev": info.st_rdev, "ctime_ns": info.st_ctime_ns})
        self.assertEqual(self.ports()["jobs"][0]["metadata"]["operation"], "uart-open")
        with self.assertRaises(ValueError):
            self.service.open(self.open_body())

    def test_send_without_chooser_metadata_reuses_the_open_connection(self):
        connection = self.open_connection()
        for _ in range(2):
            settled = wait_job(self.jobs, self.service.infer(self.body())["id"])
            self.assertEqual(settled["status"], "completed", settled.get("error"))
            result = settled["result"]
            self.assertEqual(result["schema_version"], 1)
            self.assertEqual(result["workspace"], str(self.workspace))
            self.assertEqual(result["connection_id"], connection["id"])
            self.assertIsNone(result["board"])
            self.assertEqual(result["port"]["device"], self.device)
            self.assertEqual(result["image_sha256"], hashlib.sha256(self.pixels).hexdigest())
            self.assertEqual(result["crc32"], f"{zlib.crc32(self.pixels) & 0xffffffff:08x}")
            self.assertEqual(result["prediction"], 7)
            self.assertEqual(result["inference_us"], 1234)
            self.assertEqual(result["scores"], [1.0 if digit == 7 else 0.0 for digit in range(10)])
            self.assertEqual(result["timing"], "invoke-only")
            self.assertNotIn("expected_target", settled["metadata"])
            self.assertEqual(self.ports()["connection"]["state"], "open")
        self.assertEqual(len(self.clients), 1)
        self.assertEqual((self.clients[0].opens, self.clients[0].synchronizations, self.clients[0].connects), (1, 2, 0))
        self.assertEqual(self.clients[0].uploaded, [self.pixels, self.pixels])
        self.assertFalse(self.clients[0].closed)

    def test_close_releases_idle_connection_and_stale_connection_ids_cannot_send(self):
        self.open_connection()
        result = wait_job(self.jobs, self.service.close_connection(self.close_body())["id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertIsNone(result["result"]["connection"])
        self.assertTrue(self.clients[0].closed)
        self.assertIsNone(self.ports()["connection"])
        with self.assertRaises(ValueError):
            self.service.infer(self.body())

    def test_shutdown_releases_idle_connection_without_a_firmware_request(self):
        self.open_connection()
        client = self.clients[0]
        self.service.close()
        self.assertTrue(client.closed)
        self.assertEqual((client.synchronizations, client.connects, client.uploaded), (0, 0, []))

    def test_prepare_normalizes_a_drawing_without_opening_uart(self):
        drawing = bytes(255 if 140 <= index % 336 <= 195 and 60 <= index // 336 <= 275 else 0 for index in range(336 * 336))
        prepared = self.service.prepare({"workspace": str(self.workspace),
                                         "drawing": {"width": 336, "height": 336, "gray8": encoded(drawing)}})
        self.assertEqual(prepared["schema_version"], 1)
        self.assertEqual(prepared["workspace"], str(self.workspace))
        pixels = bytes(prepared["pixels"])
        self.assertEqual(len(pixels), 784)
        self.assertTrue(any(pixels))
        self.assertEqual(prepared["image_sha256"], hashlib.sha256(pixels).hexdigest())
        self.assertEqual(prepared["crc32"], f"{zlib.crc32(pixels) & 0xffffffff:08x}")
        self.assertEqual(self.clients, [])
        self.assertEqual(self.jobs.list(), [])

    def test_invalid_or_blank_drawings_are_rejected_before_any_job_or_port_open(self):
        drawing = {"width": 336, "height": 336, "gray8": encoded(bytes(336 * 336))}
        cases = [drawing, {**drawing, "gray8": "not base64"}, {**drawing, "gray8": encoded(bytes(336 * 336 + 1))},
                 {**drawing, "width": 100000}, {**drawing, "height": True}, {**drawing, "path": "/dev/ttyS0"}]
        for value in cases:
            with self.subTest(value={key: item for key, item in value.items() if key != "gray8"}), self.assertRaises(ValueError):
                self.service.prepare({"workspace": str(self.workspace), "drawing": value})
        self.assertEqual(self.clients, [])
        self.assertEqual(self.jobs.list(), [])

    def test_open_accepts_only_current_advertised_port_ids_and_no_paths(self):
        valid = self.open_body()
        cases = [{**valid, "port_id": "0" * 64}, {**valid, "device": self.device}, {**valid, "path": self.device},
                 {**valid, "port_id": self.device}, {**valid, "expected_target": "board/chip"}]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.service.open(body)
        self.advertised = []
        with self.assertRaises(ValueError):
            self.service.open(valid)
        self.assertEqual(self.clients, [])
        self.assertEqual(self.jobs.list(), [])

    def test_infer_requires_open_connection_and_valid_image_but_no_target_fields(self):
        with self.assertRaises(ValueError):
            self.service.infer(self.body(connection_id="f" * 32))
        self.open_connection()
        valid = self.body()
        cases = [{**valid, "connection_id": "f" * 32}, {**valid, "device": self.device},
                 {**valid, "port_id": self.ports()["ports"][0]["id"]}, {**valid, "expected_target": "board/chip"},
                 {**valid, "image": {"gray8": "!bad!"}}, {**valid, "image": {"gray8": encoded(bytes(784))}},
                 {**valid, "image": {"gray8": encoded(bytes(785))}},
                 {**valid, "image": {"gray8": encoded(self.pixels), "path": "/tmp/image.bin"}}]
        count = len(self.jobs.list())
        for body in cases:
            with self.subTest(body={key: value for key, value in body.items() if key != "image"}), self.assertRaises(ValueError):
                self.service.infer(body)
        self.assertEqual(len(self.jobs.list()), count)
        self.assertEqual(self.clients[0].uploaded, [])
        self.assertFalse(self.clients[0].closed)

    def test_open_permission_error_is_visible_and_does_not_leave_a_connection(self):
        self.open_error = PermissionError("Synthetic UART permission denied.")
        job = wait_job(self.jobs, self.service.open(self.open_body())["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("Synthetic UART permission denied", job["error"])
        self.assertTrue(self.clients[0].closed)
        self.assertIsNone(self.ports()["connection"])

    def test_send_errors_and_invalid_results_close_the_connection(self):
        correct = {"prediction": 7, "inference_us": 1234, "scores": tuple(1.0 if digit == 7 else 0.0 for digit in range(10)),
                   "crc32": f"{zlib.crc32(self.pixels) & 0xffffffff:08x}"}
        cases = [({"crc32": "00000000"}, None), ({"scores": (float("nan"),) * 10}, None),
                 ({"scores": (0.1,) * 9}, None), ({"prediction": 3}, None), ({"inference_us": True}, None),
                 (None, RuntimeError("Synthetic upload failure."))]
        for changes, error in cases:
            with self.subTest(changes=changes, error=error):
                self.open_connection()
                self.upload_error = error
                self.upload_behavior = (lambda *args: SimpleNamespace(**{**correct, **changes})) if changes else None
                settled = wait_job(self.jobs, self.service.infer(self.body())["id"])
                self.assertEqual(settled["status"], "failed")
                self.assertIsNone(settled["result"])
                self.assertTrue(self.clients[-1].closed)
                self.assertIsNone(self.ports()["connection"])

    def test_cancellation_closes_running_transfer_and_reopen_allows_retry(self):
        self.open_connection()
        entered = Event()

        def upload(client, pixels, progress):
            entered.set()
            self.assertTrue(client.cancel.wait(2))
            raise CancelledError()

        self.upload_behavior = upload
        job = self.service.infer(self.body())
        self.assertTrue(entered.wait(1))
        self.jobs.cancel(job["id"])
        self.assertEqual(wait_job(self.jobs, job["id"])["status"], "cancelled")
        self.assertTrue(self.clients[-1].closed)
        self.assertIsNone(self.ports()["connection"])
        self.upload_behavior = None
        self.open_connection()
        retry = wait_job(self.jobs, self.service.infer(self.body())["id"])
        self.assertEqual(retry["status"], "completed", retry.get("error"))

    def test_busy_connection_rejects_another_send_and_explicit_close_cancels_then_releases(self):
        self.open_connection()
        entered = Event()

        def upload(client, pixels, progress):
            entered.set()
            self.assertTrue(client.cancel.wait(2))
            raise CancelledError()

        self.upload_behavior = upload
        inference = self.service.infer(self.body())
        self.assertTrue(entered.wait(1))
        self.assertEqual(self.ports()["connection"]["state"], "busy")
        with self.assertRaises(ValueError):
            self.service.infer(self.body())
        closing = self.service.close_connection(self.close_body())
        self.assertEqual(wait_job(self.jobs, inference["id"])["status"], "cancelled")
        closed = wait_job(self.jobs, closing["id"])
        self.assertEqual(closed["status"], "completed", closed.get("error"))
        self.assertTrue(self.clients[0].closed)
        self.assertIsNone(self.ports()["connection"])

    def test_queued_send_keeps_workspace_and_global_connection_survives_workspace_switch(self):
        connection = self.open_connection()
        release = self.block_worker()
        original_workspace = str(self.workspace)
        job = self.service.infer(self.body())
        other = self.root / "other-workspace"
        other.mkdir()
        self.context.workspace.set(other)
        current = self.ports(str(other))
        self.assertEqual(current["jobs"], [])
        self.assertEqual(current["connection"]["id"], connection["id"])
        release.set()
        settled = wait_job(self.jobs, job["id"])
        self.assertEqual(settled["status"], "completed", settled.get("error"))
        self.assertEqual(settled["result"]["workspace"], original_workspace)
        self.assertEqual(self.clients[0].uploaded, [self.pixels])
        self.assertFalse(self.clients[0].closed)
        sent_again = wait_job(self.jobs, self.service.infer(self.body())["id"])
        self.assertEqual(sent_again["status"], "completed", sent_again.get("error"))
        self.assertEqual(sent_again["result"]["workspace"], str(other))
        self.assertEqual(len(self.clients), 1)

    def test_a_second_open_and_disappeared_queued_device_never_open_another_port(self):
        release = self.block_worker()
        body = self.open_body()
        job = self.service.open(body)
        with self.assertRaises(ValueError):
            self.service.open(body)
        self.close_pty()
        release.set()
        settled = wait_job(self.jobs, job["id"])
        self.assertEqual(settled["status"], "failed")
        self.assertTrue(settled.get("error"))
        self.assertEqual(self.clients, [])

    def test_cancelled_queued_open_releases_its_reservation_without_opening_uart(self):
        release = self.block_worker()
        opening = self.service.open(self.open_body())
        self.jobs.cancel(opening["id"])
        release.set()
        self.assertEqual(wait_job(self.jobs, opening["id"])["status"], "cancelled")
        self.assertIsNone(self.ports()["connection"])
        self.assertEqual(self.clients, [])
        self.open_connection()
        self.assertEqual(len(self.clients), 1)

    def test_cancelled_queued_send_keeps_idle_uart_and_releases_busy_reservation(self):
        connection = self.open_connection()
        release = self.block_worker()
        inference = self.service.infer(self.body())
        self.jobs.cancel(inference["id"])
        release.set()
        self.assertEqual(wait_job(self.jobs, inference["id"])["status"], "cancelled")
        current = self.ports()["connection"]
        self.assertEqual((current["id"], current["state"]), (connection["id"], "open"))
        self.assertEqual(self.clients[0].synchronizations, 0)
        self.assertEqual(self.clients[0].uploaded, [])
        self.assertFalse(self.clients[0].closed)
        retry = wait_job(self.jobs, self.service.infer(self.body())["id"])
        self.assertEqual(retry["status"], "completed", retry.get("error"))

    def test_missing_serial_dependency_is_visible_and_workspace_guards_precede_submission(self):
        def missing():
            raise ImportError("Synthetic pyserial dependency unavailable.")

        unavailable = UARTService(self.context, port_provider=missing, client_factory=self.client_factory)
        catalogue = unavailable.ports(str(self.workspace))
        self.assertFalse(catalogue["available"])
        self.assertTrue(catalogue["reason"])
        self.assertEqual(catalogue["ports"], [])
        other = self.root / "other"
        other.mkdir()
        with self.assertRaises(ValueError):
            self.service.ports(str(other))
        with self.assertRaises(ValueError):
            self.service.open(self.open_body(workspace=str(other)))
        self.assertEqual(self.jobs.list(), [])
