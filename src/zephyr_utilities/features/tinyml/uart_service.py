"""Explicit UART Open/Close and bounded MNIST transfers in one local application."""

import base64
import binascii
from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
import uuid


_HASH = re.compile(r"[a-f0-9]{64}\Z")
_SESSION = re.compile(r"[a-f0-9]{32}\Z")
_DRAWING_SIZE = 336
_PIXELS = 28 * 28
_MAX_PORTS = 128


def _protocol():
    try:
        from . import mnist_protocol
    except ImportError as exc:
        raise ValueError("Drawing over UART requires Pillow and pyserial in the selected Zephyr environment. Reinstall Zephyr Utilities with its runtime dependencies.") from exc
    return mnist_protocol


def _identity(info):
    return {"device": info.st_dev, "inode": info.st_ino, "rdev": info.st_rdev, "ctime_ns": info.st_ctime_ns}


def _device_identity(device):
    path = Path(device)
    if (not path.is_absolute() or ".." in path.parts or not path.is_relative_to("/dev") or
            str(path) != device or str(path.resolve(strict=True)) != device):
        raise ValueError("UART selection needs an advertised, canonical local device path.")
    info = path.lstat()
    if not stat.S_ISCHR(info.st_mode):
        raise ValueError("An advertised UART port is not a character device. Refresh ports.")
    return _identity(info)


def _label(value):
    return "".join(character if character.isprintable() else " " for character in str(value or ""))[:500]


def _decode(value, size):
    encoded_size = ((size + 2) // 3) * 4
    if not isinstance(value, str) or len(value) != encoded_size:
        raise ValueError(f"Expected exactly {size} grayscale bytes encoded as base64.")
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Image data must be canonical base64 grayscale bytes.") from exc
    if len(data) != size or base64.b64encode(data).decode("ascii") != value:
        raise ValueError("Image data must contain the exact grayscale dimensions.")
    return data


class UARTService:
    """One explicitly opened UART, independent of Projects and firmware metadata."""

    def __init__(self, context, *, port_provider=None, client_factory=None):
        self.context = context
        self.port_provider = port_provider
        self.client_factory = client_factory
        self.lock = RLock()
        self.io_lock = RLock()
        self.catalogue_workspace = None
        self.catalogue = {}
        self.connection = None
        self.active = None
        self.closing = None
        self.closed = False

    def _reconcile(self):
        # The shared manager can cancel a queued task before invoking its body.
        # Its reservation must therefore also be released from observed status.
        for name in ("active", "closing"):
            operation = getattr(self, name)
            if operation and operation.get("job_id"):
                try:
                    running = self.context.jobs.get(operation["job_id"])["status"] in {"queued", "running"}
                except FileNotFoundError:
                    running = False
                if not running:
                    setattr(self, name, None)

    def _public_connection(self):
        if self.connection is None:
            return None
        value = {key: self.connection[key] for key in ("id", "port_id", "device", "description")}
        operation = self.closing or self.active
        value["state"] = "busy" if operation else "open"
        if operation and operation.get("job_id"):
            value["job_id"] = operation["job_id"]
        return value

    def _ready(self):
        self._reconcile()
        if self.closed:
            raise ValueError("The UART viewer is shutting down.")
        if self.active or self.closing:
            raise ValueError("A UART operation is in progress. Wait for it or close the port.")

    def _submit(self, operation, task, metadata, *, closing=False, resource_key=None):
        name = "closing" if closing else "active"
        reservation = {"nonce": uuid.uuid4().hex, "operation": operation}
        setattr(self, name, reservation)

        def run(emit, cancel):
            try:
                with self.io_lock:
                    if cancel.is_set() or self.closed:
                        raise CancelledError()
                    return task(emit, cancel)
            finally:
                with self.lock:
                    if getattr(self, name) is reservation:
                        setattr(self, name, None)
        try:
            job = self.context.jobs.submit("tinyml", run, resource_key=resource_key,
                metadata={"operation": operation, "view": "draw", **metadata})
            reservation["job_id"] = job["id"]
            return job
        except BaseException:
            if getattr(self, name) is reservation:
                setattr(self, name, None)
            raise

    def _workspace(self, body, fields):
        if not isinstance(body, dict) or set(body) != {"workspace", *fields}:
            raise ValueError("Unknown or missing TinyML UART request fields.")
        workspace = body.get("workspace")
        if not isinstance(workspace, str) or workspace != self.context.workspace.get()["path"]:
            raise ValueError("The workspace changed. Refresh this TinyML view before sending.")
        return workspace

    def _discover(self):
        if os.name != "posix":
            raise ValueError("UART drawing currently requires a POSIX host with advertised /dev character devices.")
        if self.port_provider is None:
            try:
                from serial.tools import list_ports
            except ImportError as exc:
                raise ValueError("UART discovery requires pyserial in the selected Zephyr environment.") from exc
            provider = list_ports.comports
        else:
            provider = self.port_provider
        records = {}
        for index, port in enumerate(provider()):
            if index >= _MAX_PORTS:
                raise ValueError("UART discovery exceeded 128 ports; no partial selection was used.")
            device = getattr(port, "device", None)
            if not isinstance(device, str) or len(device) > 4096 or any(ord(c) < 32 for c in device):
                continue
            try:
                identity = _device_identity(device)
            except (OSError, ValueError):
                continue
            record = {"device": device, "description": _label(getattr(port, "description", "")),
                      "hwid": _label(getattr(port, "hwid", "")), "identity": identity}
            token = hashlib.sha256(json.dumps([device, identity, record["hwid"]], sort_keys=True).encode()).hexdigest()
            records[token] = {"id": token, **record}
        return records

    def ports(self, workspace):
        workspace = self._workspace({"workspace": workspace}, set())
        available, reason = True, ""
        try:
            _protocol()
            records = self._discover()
        except (ImportError, OSError, ValueError) as exc:
            available, reason, records = False, str(exc), {}
        self._workspace({"workspace": workspace}, set())
        with self.lock:
            self.catalogue_workspace = workspace
            self.catalogue = records
        jobs = [job for job in self.context.jobs.list("tinyml")
                if job["metadata"].get("workspace") == workspace and
                job["metadata"].get("operation") in {"mnist-infer", "uart-open", "uart-close"}]
        with self.lock:
            self._reconcile()
            connection = self._public_connection()
        return {"schema_version": 1, "workspace": workspace, "available": available, "reason": reason,
                "ports": [{key: value for key, value in record.items() if key != "identity"}
                          for record in sorted(records.values(), key=lambda value: value["device"])],
                "connection": connection, "jobs": jobs}

    def prepare(self, body):
        workspace = self._workspace(body, {"drawing"})
        drawing = body["drawing"]
        if (not isinstance(drawing, dict) or set(drawing) != {"width", "height", "gray8"} or
                any(type(drawing[key]) is not int or drawing[key] != _DRAWING_SIZE for key in ("width", "height"))):
            raise ValueError("A drawing must be a 336 by 336 grayscale raster.")
        raw = _decode(drawing["gray8"], _DRAWING_SIZE * _DRAWING_SIZE)
        protocol = _protocol()
        from PIL import Image
        pixels = protocol.prepare_image(Image.frombytes("L", (_DRAWING_SIZE, _DRAWING_SIZE), raw)).tobytes()
        self._workspace({"workspace": workspace}, set())
        return {"schema_version": 1, "workspace": workspace, "pixels": list(pixels),
                "image_sha256": hashlib.sha256(pixels).hexdigest(), "crc32": protocol.image_crc(pixels)}

    def open(self, body):
        workspace = self._workspace(body, {"port_id"})
        port_id = body["port_id"]
        if not isinstance(port_id, str) or not _HASH.fullmatch(port_id):
            raise ValueError("Select a UART port before opening it.")
        protocol = _protocol()
        with self.lock:
            self._ready()
            if self.connection is not None:
                raise ValueError("Close the current UART port before opening another one.")
            selected = deepcopy(self.catalogue.get(port_id)) if self.catalogue_workspace == workspace else None
        if selected is None:
            raise ValueError("This UART selection is unavailable. Refresh ports and choose again.")
        if self._discover().get(port_id) != selected:
            raise ValueError("The selected UART device changed or disappeared. Refresh ports and choose again.")
        self._workspace({"workspace": workspace}, set())
        factory = self.client_factory or protocol.SerialClient

        def task(emit, cancel):
            if _device_identity(selected["device"]) != selected["identity"]:
                raise ValueError("The UART device changed. Refresh ports and choose again.")
            client = factory(selected["device"], cancel=cancel, expected_identity=deepcopy(selected["identity"]))
            try:
                emit(f"Opening {selected['device']} at 115200 baud…")
                client.open()
                if cancel.is_set():
                    raise CancelledError()
                with self.lock:
                    if self.closed:
                        raise CancelledError()
                    self.connection = {"id": uuid.uuid4().hex, "port_id": port_id,
                                       "device": selected["device"], "description": selected["description"], "client": client}
                    value = {**self._public_connection(), "state": "open"}
                    value.pop("job_id", None)
                emit("UART open.")
                return {"schema_version": 1, "workspace": workspace, "connection": value}
            except BaseException:
                client.close()
                raise

        with self.lock:
            self._ready()
            if self.connection is not None:
                raise ValueError("Close the current UART port before opening another one.")
            return self._submit("uart-open", task, {"workspace": workspace, "port_id": port_id,
                "port_device": selected["device"]}, resource_key=f"uart-device:{selected['identity']['rdev']}")

    def infer(self, body):
        workspace = self._workspace(body, {"connection_id", "image"})
        connection_id = body["connection_id"]
        if not isinstance(connection_id, str) or not _SESSION.fullmatch(connection_id):
            raise ValueError("Open a UART port before sending.")
        image = body["image"]
        if not isinstance(image, dict) or set(image) != {"gray8"}:
            raise ValueError("The UART image needs only its prepared grayscale pixels.")
        pixels = _decode(image["gray8"], _PIXELS)
        if not any(pixels):
            raise ValueError("Draw a digit before sending.")
        protocol = _protocol()
        image_hash, crc = hashlib.sha256(pixels).hexdigest(), protocol.image_crc(pixels)
        with self.lock:
            self._ready()
            connection = self.connection
            if connection is None or connection["id"] != connection_id:
                raise ValueError("The UART port is closed. Open a port before sending.")
            client = connection["client"]

            def task(emit, cancel):
                client.cancel = cancel
                try:
                    emit("Sending the prepared image…")
                    client.synchronize()
                    if cancel.is_set():
                        raise CancelledError()
                    result = client.upload(pixels, progress=lambda row, total: emit(f"Sent row {row}/{total}."))
                    if cancel.is_set():
                        raise CancelledError()
                    checked = protocol.parse_result("MNIST_RESULT " + json.dumps({
                        "prediction": result.prediction, "inference_us": result.inference_us,
                        "crc32": result.crc32, "scores": result.scores}), crc)
                    emit("Prediction received.")
                    return {"schema_version": 1, "workspace": workspace,
                            "port": {"id": connection["port_id"], "device": connection["device"], "description": connection["description"]},
                            "connection_id": connection_id, "board": None, "image_sha256": image_hash,
                            "crc32": crc, "prediction": checked.prediction, "inference_us": checked.inference_us,
                            "scores": list(checked.scores), "timing": "invoke-only"}
                except BaseException:
                    client.close()
                    with self.lock:
                        if self.connection is connection:
                            self.connection = None
                    raise

            return self._submit("mnist-infer", task, {"workspace": workspace, "connection_id": connection_id,
                "port_id": connection["port_id"], "port_device": connection["device"], "image_sha256": image_hash, "crc32": crc},
                resource_key=f"uart-transfer:{connection_id}")

    def close_connection(self, body):
        workspace = self._workspace(body, {"connection_id"})
        connection_id = body["connection_id"]
        if not isinstance(connection_id, str) or not _SESSION.fullmatch(connection_id):
            raise ValueError("Choose the open UART connection to close.")
        with self.lock:
            self._reconcile()
            connection = self.connection
            if connection is None or connection["id"] != connection_id:
                raise ValueError("This UART port is already closed. Refresh ports.")
            if self.closing:
                raise ValueError("The UART port is already closing.")
            if self.active and self.active.get("job_id"):
                self.context.jobs.cancel(self.active["job_id"])

            def task(emit, cancel):
                connection["client"].close()
                with self.lock:
                    if self.connection is connection:
                        self.connection = None
                emit("UART closed.")
                return {"schema_version": 1, "workspace": workspace, "connection": None}

            return self._submit("uart-close", task, {"workspace": workspace, "connection_id": connection_id,
                "port_id": connection["port_id"], "port_device": connection["device"]}, closing=True,
                resource_key=f"uart-close:{connection_id}")

    def close(self):
        with self.lock:
            self.closed = True
            for operation in (self.active, self.closing):
                if operation and operation.get("job_id"):
                    try:
                        self.context.jobs.cancel(operation["job_id"])
                    except FileNotFoundError:
                        pass
        with self.io_lock:
            with self.lock:
                connection, self.connection = self.connection, None
            if connection:
                connection["client"].close()
