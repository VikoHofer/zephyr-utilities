# SPDX-License-Identifier: Apache-2.0
"""Bundled MNIST image preparation and checked UART shell transport.

Adapted from ``applications/mnist_shell/tools/mnist_uart.py`` (Apache-2.0).
The crop, LANCZOS scaling, mass centering and mnist-gray8-v1 wire format retain
that implementation's behavior. Workspace tools are never imported or executed.
"""

from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass
import errno
import json
import math
import os
import re
import stat
import threading
import time
import zlib

from PIL import Image
import serial


WIDTH = 28
PIXELS = WIDTH * WIDTH
PROTOCOL = "mnist-gray8-v1"
PROMPT = "mnist:~$ "
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MAX_RESPONSE = 65536
_COMMAND_TIMEOUT = 5.0
_UPLOAD_TIMEOUT = 45.0
_IO_SLICE = 0.025
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_CRC = re.compile(r"[a-f0-9]{8}\Z")


def prepare_image(drawing: Image.Image) -> Image.Image:
    """Fit white ink in 20x20, then center its mass on a black 28x28 field."""
    if not isinstance(drawing, Image.Image):
        raise ValueError("The drawing must be an image.")
    gray = drawing.convert("L")
    bounds = gray.getbbox()
    if bounds is None:
        raise ValueError("Please draw a digit first.")
    cropped = gray.crop(bounds)
    scale = 20 / max(cropped.size)
    size = tuple(max(1, round(dimension * scale)) for dimension in cropped.size)
    scaled = cropped.resize(size, Image.Resampling.LANCZOS)
    field = Image.new("L", (WIDTH, WIDTH), 0)
    field.paste(scaled, ((WIDTH - size[0]) // 2, (WIDTH - size[1]) // 2))
    pixels = field.tobytes()
    mass = sum(pixels)
    if not mass:
        raise ValueError("The drawing does not contain a visible digit.")
    center_x = sum((index % WIDTH) * value for index, value in enumerate(pixels)) / mass
    center_y = sum((index // WIDTH) * value for index, value in enumerate(pixels)) / mass
    bounds = field.getbbox()
    dx = max(-bounds[0], min(round(13.5 - center_x), WIDTH - bounds[2]))
    dy = max(-bounds[1], min(round(13.5 - center_y), WIDTH - bounds[3]))
    centered = Image.new("L", (WIDTH, WIDTH), 0)
    centered.paste(field, (dx, dy))
    return centered


def _pixels(pixels):
    if not isinstance(pixels, (bytes, bytearray)) or len(pixels) != PIXELS:
        raise ValueError("Exactly 784 grayscale bytes are required.")
    return bytes(pixels)


def image_crc(pixels: bytes) -> str:
    return f"{zlib.crc32(_pixels(pixels)) & 0xFFFFFFFF:08x}"


def row_command(row: int, pixels: bytes) -> str:
    pixels = _pixels(pixels)
    if type(row) is not int or not 0 <= row < WIDTH:
        raise ValueError("The image row must be between 0 and 27.")
    return f"ai upload row {row} {pixels[row * WIDTH:(row + 1) * WIDTH].hex()}"


def _clean(response, *, complete=True):
    if not isinstance(response, str) or len(response) > _MAX_RESPONSE:
        raise RuntimeError("The UART response is invalid or exceeds the 65536-character limit.")
    value = _ANSI.sub("", response).replace("\r", "")
    if complete and any(ord(character) < 32 and character not in "\n\t" or ord(character) == 127 for character in value):
        raise RuntimeError("The UART response contains unsupported control data.")
    return value


def _lines(response):
    return [line.strip() for line in _clean(response).splitlines()]


def _unique_field(lines, prefix):
    matches = [line[len(prefix):] for line in lines if line.startswith(prefix)]
    if len(matches) != 1:
        raise RuntimeError("The UART firmware information is incomplete or ambiguous.")
    return matches[0]


def parse_info(response: str, expected_target=None, expected_model=None) -> dict:
    """Require one ready MNIST identity, upload protocol and Invoke-only timing."""
    lines = _lines(response)
    target = _unique_field(lines, "MNIST / TensorFlow Lite Micro on ")
    model = _unique_field(lines, "Model SHA256: ")
    state = _unique_field(lines, "State: ")
    protocol = _unique_field(lines, "Upload protocol: ")
    timing = _unique_field(lines, "Timing: ")
    if (not re.fullmatch(r"[A-Za-z0-9_+./@-]{1,512}", target) or not _SHA.fullmatch(model) or state != "ready" or
            protocol != f"{PROTOCOL} (28 rows, CRC32-IEEE)" or timing != "Invoke only; no preprocessing or console output"):
        raise RuntimeError("The selected UART device is not ready with the required MNIST upload firmware.")
    if expected_target is not None:
        if not isinstance(expected_target, str) or not re.fullmatch(r"[A-Za-z0-9_+./@-]{1,512}", expected_target):
            raise ValueError("The expected firmware target is invalid.")
        if target != expected_target:
            raise RuntimeError("The UART firmware target differs from the selected application.")
    if expected_model is not None:
        if not isinstance(expected_model, str) or not _SHA.fullmatch(expected_model):
            raise ValueError("The expected model SHA-256 is invalid.")
        if model != expected_model:
            raise RuntimeError("The UART firmware model differs from the selected application.")
    simulated = [line for line in lines if line.startswith("Simulator clock:")]
    if len(simulated) > 1 or simulated and not simulated[0][len("Simulator clock:"):].strip():
        raise RuntimeError("The UART firmware timing information is ambiguous.")
    return {"protocol": PROTOCOL, "target": target, "model_sha256": model, "ready": True,
            "timing": "invoke-only", "simulated": bool(simulated), "raw_info": _clean(response)[-8192:]}


@dataclass(frozen=True)
class InferenceResult:
    prediction: int
    inference_us: int
    scores: tuple[float, ...]
    crc32: str


def _json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate result field.")
        result[name] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite result number.")


def parse_result(response: str, expected_crc: str) -> InferenceResult:
    if not isinstance(expected_crc, str) or not _CRC.fullmatch(expected_crc):
        raise ValueError("The expected image CRC32 must contain eight lowercase hexadecimal digits.")
    lines = [line for line in _lines(response) if line.startswith("MNIST_RESULT")]
    if len(lines) != 1 or not re.fullmatch(r"MNIST_RESULT (\{[^\n]+\})", lines[0]):
        raise RuntimeError("The device did not return exactly one valid MNIST result.")
    try:
        payload = json.loads(lines[0][len("MNIST_RESULT "):], object_pairs_hook=_json_object, parse_constant=_invalid_constant)
        if not isinstance(payload, dict) or set(payload) != {"prediction", "inference_us", "scores", "crc32"}:
            raise ValueError("Unexpected result fields.")
        prediction, duration, scores, crc = (payload[name] for name in ("prediction", "inference_us", "scores", "crc32"))
        valid = (type(prediction) is int and 0 <= prediction < 10 and type(duration) is int and 0 <= duration <= 0xFFFFFFFF and
                 isinstance(scores, list) and len(scores) == 10 and
                 all(type(value) in {int, float} and math.isfinite(value) and 0 <= value <= 1 for value in scores) and
                 abs(math.fsum(scores) - 1) < 0.00001 and prediction == max(range(10), key=scores.__getitem__) and crc == expected_crc)
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
        raise RuntimeError("The model response is incomplete or corrupted.") from exc
    if not valid:
        raise RuntimeError("Invalid model response or image checksum.")
    return InferenceResult(prediction, duration, tuple(float(score) for score in scores), crc)


def _acknowledged(response, prefix, expected):
    matches = [line for line in _lines(response) if line.startswith(prefix)]
    return matches == [expected]


def upload_image(command, pixels: bytes, progress=None) -> InferenceResult:
    """Transfer one checked image through a command callback; never guess an ACK."""
    pixels = _pixels(pixels)
    checksum = image_crc(pixels)
    if not _acknowledged(command("ai upload begin"), "UPLOAD READY", f"UPLOAD READY {PROTOCOL} 28x28"):
        raise RuntimeError("The device did not acknowledge the MNIST upload protocol.")
    for row in range(WIDTH):
        if not _acknowledged(command(row_command(row, pixels)), "UPLOAD ROW ", f"UPLOAD ROW {row} OK"):
            raise RuntimeError(f"Image row {row + 1} was not acknowledged exactly.")
        if progress:
            progress(row + 1, WIDTH)
    return parse_result(command(f"ai upload infer {checksum}"), checksum)


class SerialClient:
    """One cancellable, bounded transaction at a time on an exclusive UART port."""

    def __init__(self, path: str, cancel=None, expected_identity=None):
        if not isinstance(path, str) or not path or len(path) > 4096 or any(ord(value) < 32 or ord(value) == 127 for value in path):
            raise ValueError("Choose a valid local UART port path.")
        if expected_identity is not None and (not isinstance(expected_identity, dict) or
                set(expected_identity) != {"device", "inode", "rdev", "ctime_ns"} or
                any(type(value) is not int or value < 0 for value in expected_identity.values())):
            raise ValueError("The expected UART device identity is invalid.")
        self.path = path
        self.cancel = cancel if cancel is not None else threading.Event()
        self.expected_identity = dict(expected_identity) if expected_identity is not None else None
        self.port = None
        self._lock = threading.Lock()
        self._upload_deadline = None

    def _check_cancel(self):
        if self.cancel.is_set():
            raise CancelledError()

    @contextmanager
    def _operation(self):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Another UART operation is already in progress.")
        try:
            self._check_cancel()
            yield
        except serial.SerialTimeoutException as exc:
            self.close()
            self._check_cancel()
            raise TimeoutError("The UART write timed out. Check the device connection.") from exc
        except TimeoutError:
            self.close()
            raise
        except (serial.SerialException, OSError) as exc:
            self.close()
            self._check_cancel()
            if getattr(exc, "errno", None) in {errno.EAGAIN, errno.EBUSY} or "exclusively lock" in str(exc).lower():
                raise RuntimeError("The UART port is busy. Close other terminal applications before connecting.") from exc
            raise RuntimeError(f"UART communication failed: {str(exc)[:1000]}") from exc
        except BaseException:
            self.close()
            raise
        finally:
            self._lock.release()

    def _open_port(self):
        self.close()
        self.port = serial.Serial(port=None, baudrate=115200, timeout=_IO_SLICE, write_timeout=_IO_SLICE, exclusive=True)
        self.port.dtr = False
        self.port.rts = False
        self.port.port = self.path
        self.port.open()
        self._check_cancel()
        if self.expected_identity is not None:
            observed = os.fstat(self.port.fileno())
            identity = {"device": observed.st_dev, "inode": observed.st_ino,
                        "rdev": observed.st_rdev, "ctime_ns": observed.st_ctime_ns}
            if not stat.S_ISCHR(observed.st_mode) or identity != self.expected_identity:
                raise RuntimeError("The UART device changed after selection. Refresh ports and choose it again.")

    def _deadline(self):
        deadline = time.monotonic() + _COMMAND_TIMEOUT
        return min(deadline, self._upload_deadline) if self._upload_deadline is not None else deadline

    def _remaining(self, deadline):
        self._check_cancel()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("No UART response received before timeout.")
        return remaining

    def _require_port(self):
        if self.port is None or not self.port.is_open:
            raise RuntimeError("The UART connection is closed.")
        return self.port

    def _write(self, payload, deadline):
        offset = 0
        while offset < len(payload):
            self._remaining(deadline)
            written = self._require_port().write(payload[offset:])
            if type(written) is not int or not 0 <= written <= len(payload) - offset:
                raise RuntimeError("The UART driver returned an invalid write result.")
            offset += written
            if not written:
                time.sleep(min(0.005, self._remaining(deadline)))
        # pyserial.flush() can block in tcdrain without a timeout. Observe its
        # output queue instead, keeping cancellation and the transaction deadline.
        while self._require_port().out_waiting:
            time.sleep(min(0.005, self._remaining(deadline)))
        self._remaining(deadline)

    def _read_prompt(self, deadline):
        output = bytearray()
        while True:
            self._remaining(deadline)
            data = self._require_port().read(4096)
            self._check_cancel()
            if not isinstance(data, bytes):
                raise RuntimeError("The UART driver returned invalid response bytes.")
            output.extend(data)
            if len(output) > _MAX_RESPONSE:
                raise RuntimeError("The UART response exceeded the 65536-byte limit.")
            try:
                response = _clean(output.decode("ascii"), complete=False)
            except UnicodeError as exc:
                raise RuntimeError("The UART device returned non-ASCII data instead of the MNIST shell.") from exc
            if response.endswith(PROMPT):
                self._remaining(deadline)
                return _clean(response)

    def _command(self, text):
        if (not isinstance(text, str) or not 1 <= len(text) < 128 or
                any(not 32 <= ord(character) <= 126 for character in text)):
            raise ValueError("The shell command must be one bounded ASCII line.")
        deadline = self._deadline()
        self._write(text.encode("ascii") + b"\n", deadline)
        response = self._read_prompt(deadline)
        if any(line.startswith("ERROR:") or "wrong parameter count" in line.lower() for line in _lines(response)):
            raise RuntimeError(response.rsplit(PROMPT, 1)[0].strip()[-1800:])
        return response

    def connect(self) -> dict:
        with self._operation():
            self._open_port()
            self._synchronize()
            return parse_info(self._command("ai info"))

    def open(self):
        """Open the guarded device without sending bytes or requiring firmware."""
        with self._operation():
            self._open_port()

    def _synchronize(self):
        self._require_port().reset_input_buffer()
        deadline = self._deadline()
        self._write(b"\x03\n", deadline)
        return self._read_prompt(deadline)

    def synchronize(self) -> str:
        """Find the upload shell prompt without querying firmware identity or state."""
        with self._operation():
            return self._synchronize()

    def command(self, text: str) -> str:
        with self._operation():
            return self._command(text)

    def upload(self, pixels: bytes, progress=None) -> InferenceResult:
        with self._operation():
            self._upload_deadline = time.monotonic() + _UPLOAD_TIMEOUT
            try:
                return upload_image(self._command, pixels, progress)
            finally:
                self._upload_deadline = None

    def close(self):
        port, self.port = self.port, None
        if port is not None:
            try:
                port.close()
            except (serial.SerialException, OSError):
                pass
