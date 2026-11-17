"""Bundled normalization and UART transactions validate exact device evidence."""

from concurrent.futures import CancelledError
from copy import deepcopy
import errno
import json
import os
from pathlib import Path
import select
import stat
from threading import Event, Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

from PIL import Image, ImageDraw
import serial

from zephyr_utilities.features.tinyml import mnist_protocol as protocol


TARGET = "nrf52840dk/nrf52840"
MODEL = "a" * 64
PIXELS = bytes(range(256)) * 3 + bytes(range(16))


def info(*, target=TARGET, model=MODEL, simulated=False):
    return (f"MNIST / TensorFlow Lite Micro on {target}\r\nState: ready\r\n"
            f"Model SHA256: {model}\r\n"
            "Timing: Invoke only; no preprocessing or console output\r\n"
            "Upload protocol: mnist-gray8-v1 (28 rows, CRC32-IEEE)\r\n" +
            ("Simulator clock: synthetic timing is not a hardware measurement\r\n" if simulated else ""))


def payload(pixels=PIXELS):
    return {"prediction": 3, "inference_us": 123, "scores": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "crc32": f"{zlib.crc32(pixels):08x}"}


def result_line(value=None):
    return "MNIST_RESULT " + json.dumps(payload() if value is None else value, separators=(",", ":")) + "\r\n"


class FakePort:
    """A byte-stream shell, including fragmented writes, reads and ANSI prompts."""

    def __init__(self, *, read_size=4096, write_size=None):
        self.is_open = False
        self.dtr = True
        self.rts = True
        self.port = None
        self.read_size = read_size
        self.write_size = write_size
        self.input = bytearray()
        self.output = bytearray()
        self.written = bytearray()
        self.commands = []
        self.overrides = {}
        self.on_read = None
        self.read_error = None
        self.write_error = None
        self.closed = False
        self.reset_count = 0
        self.open_state = None
        self.rows = {}
        self.waiting = 0

    @property
    def out_waiting(self):
        return self.waiting

    def open(self):
        self.open_state = (self.port, self.dtr, self.rts)
        self.is_open = True

    def close(self):
        self.closed = True
        self.is_open = False

    def reset_input_buffer(self):
        self.reset_count += 1
        self.input.clear()

    def fileno(self):
        return 42

    def flush(self):
        raise AssertionError("Blocking driver flush must not be used.")

    def write(self, data):
        if self.write_error:
            raise self.write_error
        count = len(data) if self.write_size is None else min(self.write_size, len(data))
        self.written.extend(data[:count])
        self.output.extend(data[:count])
        while b"\n" in self.output:
            raw, _, remaining = self.output.partition(b"\n")
            self.output[:] = remaining
            command = raw.decode("ascii")
            self.commands.append(command)
            response = self.respond(command)
            if response is not None:
                self.input.extend(response.encode("ascii"))
                self.input.extend(b"\x1b[32mmnist:~$ \x1b[0m")
        return count

    def respond(self, command):
        if command in self.overrides:
            return self.overrides[command]
        if command == "\x03":
            return "\r\n"
        if command == "ai info":
            return info()
        if command == "ai upload begin":
            self.rows = {}
            return "UPLOAD READY mnist-gray8-v1 28x28\r\n"
        if command.startswith("ai upload row "):
            _, _, _, row, data = command.split()
            self.rows[int(row)] = bytes.fromhex(data)
            return f"UPLOAD ROW {row} OK\r\n"
        if command.startswith("ai upload infer "):
            pixels = b"".join(self.rows[row] for row in range(28))
            return result_line(payload(pixels))
        return "command response\r\n"

    def read(self, count):
        if self.on_read:
            self.on_read()
        if self.read_error:
            raise self.read_error
        count = min(count, self.read_size, len(self.input))
        result = bytes(self.input[:count])
        del self.input[:count]
        return result


class NormalizationTests(unittest.TestCase):
    def test_one_pixel_becomes_the_expected_centered_twenty_pixel_square(self):
        drawing = Image.new("L", (336, 336))
        drawing.putpixel((100, 200), 255)
        prepared = protocol.prepare_image(drawing)
        self.assertEqual(prepared.mode, "L")
        self.assertEqual(prepared.size, (28, 28))
        self.assertEqual(prepared.getbbox(), (4, 4, 24, 24))
        self.assertEqual(prepared.tobytes().count(255), 400)
        self.assertEqual(prepared.tobytes().count(0), 384)

    def test_crop_preserves_aspect_and_translations_produce_identical_normalized_bytes(self):
        first = Image.new("L", (336, 336))
        ImageDraw.Draw(first).rectangle((10, 20, 89, 59), fill=255)
        second = Image.new("RGB", (336, 336))
        ImageDraw.Draw(second).rectangle((120, 210, 199, 249), fill="white")
        prepared = protocol.prepare_image(first)
        self.assertEqual(prepared.getbbox(), (4, 9, 24, 19))
        self.assertEqual(protocol.prepare_image(second).tobytes(), prepared.tobytes())

    def test_asymmetric_ink_is_mass_centered_and_scaled_with_antialiasing(self):
        drawing = Image.new("L", (336, 336))
        ImageDraw.Draw(drawing).line((90, 20, 30, 250, 150, 250), fill=255, width=17)
        prepared = protocol.prepare_image(drawing)
        pixels = prepared.tobytes()
        mass = sum(pixels)
        center = (sum(index % 28 * value for index, value in enumerate(pixels)) / mass,
                  sum(index // 28 * value for index, value in enumerate(pixels)) / mass)
        self.assertLessEqual(abs(center[0] - 13.5), 0.5)
        self.assertLessEqual(abs(center[1] - 13.5), 0.5)
        self.assertTrue(any(0 < value < 255 for value in pixels))

    def test_empty_and_invalid_drawings_are_rejected(self):
        for drawing in (Image.new("L", (336, 336)), None, b"image"):
            with self.subTest(drawing=type(drawing).__name__), self.assertRaises(ValueError):
                protocol.prepare_image(drawing)


class ProtocolEvidenceTests(unittest.TestCase):
    def test_crc_and_each_row_describe_exact_grayscale_bytes(self):
        self.assertEqual(protocol.image_crc(PIXELS), f"{zlib.crc32(PIXELS):08x}")
        rows = [protocol.row_command(row, PIXELS).split() for row in range(28)]
        self.assertEqual([row[:4] for row in rows], [["ai", "upload", "row", str(row)] for row in range(28)])
        self.assertEqual(b"".join(bytes.fromhex(row[4]) for row in rows), PIXELS)
        for pixels in (b"", b"x" * 783, "x" * 784, [0] * 784):
            with self.subTest(pixels=type(pixels).__name__), self.assertRaises(ValueError):
                protocol.image_crc(pixels)
        for row in (-1, 28, True, 1.0):
            with self.subTest(row=row), self.assertRaises(ValueError):
                protocol.row_command(row, PIXELS)

    def test_firmware_info_is_exact_and_retains_target_model_and_simulation_evidence(self):
        target = "custom_board@0.14.0/chip/cpuapp"
        parsed = protocol.parse_info("\x1b[34m" + info(target=target, simulated=True) + "\x1b[0m" + protocol.PROMPT,
                                     expected_target=target, expected_model=MODEL)
        self.assertEqual(parsed["target"], target)
        self.assertEqual(parsed["model_sha256"], MODEL)
        self.assertEqual(parsed["protocol"], "mnist-gray8-v1")
        self.assertEqual(parsed["timing"], "invoke-only")
        self.assertTrue(parsed["simulated"])
        self.assertTrue(parsed["ready"])
        self.assertFalse(protocol.parse_info(info())["simulated"])

    def test_wrong_ambiguous_missing_or_failed_firmware_info_is_unavailable(self):
        for response in ("Hello World\n", info().replace("State: ready", "State: FAILED"),
                         info().replace("mnist-gray8-v1", "unknown-v2"),
                         info().replace(MODEL, "not-a-model-hash"), info() + "State: ready\n",
                         info() + f"MNIST / TensorFlow Lite Micro on {TARGET}\n",
                         info().replace("Timing: Invoke only; no preprocessing or console output\r\n", "")):
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                protocol.parse_info(response)
        with self.assertRaisesRegex(RuntimeError, "target differs"):
            protocol.parse_info(info(), expected_target="different/chip")
        with self.assertRaisesRegex(RuntimeError, "model differs"):
            protocol.parse_info(info(), expected_model="b" * 64)

    def test_exact_result_retains_scores_crc_and_unsigned_invoke_time(self):
        parsed = protocol.parse_result("\x1b[33m" + result_line() + "\x1b[0m" + protocol.PROMPT, protocol.image_crc(PIXELS))
        self.assertEqual(parsed.prediction, 3)
        self.assertEqual(parsed.inference_us, 123)
        self.assertEqual(parsed.crc32, protocol.image_crc(PIXELS))
        self.assertEqual(parsed.scores[3], 1.0)
        final = payload()
        final["inference_us"] = 0xFFFFFFFF
        self.assertEqual(protocol.parse_result(result_line(final), final["crc32"]).inference_us, 0xFFFFFFFF)

    def test_corrupt_spoofed_or_ambiguous_results_are_rejected(self):
        changes = [{"prediction": True}, {"prediction": 9}, {"inference_us": True}, {"inference_us": -1},
                   {"inference_us": 0x100000000}, {"inference_us": 12.5}, {"crc32": "00000000"},
                   {"scores": [0.1] * 9}, {"scores": [True] + [0.0] * 9}, {"scores": ["0.1"] * 10},
                   {"scores": [float("nan")] * 10}, {"scores": [float("inf")] * 10},
                   {"scores": [0.0] * 10}, {"scores": [-0.1] + [0.0] * 8 + [1.1]}, {"extra": "ignored"}]
        responses = [result_line({**payload(), **change}) for change in changes]
        responses.extend([result_line() + result_line(), "echo " + result_line(),
                          result_line().replace('"prediction":3', '"prediction":3,"prediction":3'),
                          "MNIST_RESULT {}\n", result_line() + "MNIST_RESULT malformed\n",
                          result_line() + "\x00", "x" * 65537])
        for response in responses:
            with self.subTest(response=response[:100]), self.assertRaises(RuntimeError):
                protocol.parse_result(response, protocol.image_crc(PIXELS))

    def test_upload_requires_exact_unique_begin_and_row_acknowledgments(self):
        commands = []
        progress = []

        def command(text):
            commands.append(text)
            if text == "ai upload begin":
                return "UPLOAD READY mnist-gray8-v1 28x28\n"
            if text.startswith("ai upload row "):
                return f"UPLOAD ROW {text.split()[3]} OK\n"
            return result_line()

        result = protocol.upload_image(command, PIXELS, lambda row, total: progress.append((row, total)))
        self.assertEqual(result.prediction, 3)
        self.assertEqual(progress, [(row, 28) for row in range(1, 29)])
        self.assertEqual(commands[-1], "ai upload infer " + protocol.image_crc(PIXELS))
        for incorrect in ("UPLOAD ROW 11 OK\n", "echo UPLOAD ROW 1 OK\n", "UPLOAD ROW 1 OK extra\n",
                          "UPLOAD ROW 1 OK\nUPLOAD ROW 1 OK\n"):
            def spoof(text):
                return incorrect if text.startswith("ai upload row 1 ") else command(text)
            with self.subTest(incorrect=incorrect), self.assertRaisesRegex(RuntimeError, "not acknowledged exactly"):
                protocol.upload_image(spoof, PIXELS)
        for response in ("noise UPLOAD READY mnist-gray8-v1 28x28\n", "UPLOAD READY mnist-gray8-v1 28x28 extra\n"):
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                protocol.upload_image(lambda text: response, PIXELS)


class SerialTransportTests(unittest.TestCase):
    def setUp(self):
        self.port = FakePort(read_size=7, write_size=2)
        serial_patch = patch.object(protocol.serial, "Serial", return_value=self.port)
        self.factory = serial_patch.start()
        self.addCleanup(serial_patch.stop)
        self.cancel = Event()
        self.client = protocol.SerialClient("/dev/ttyUSB0", cancel=self.cancel)
        self.addCleanup(self.client.close)

    def connect(self):
        return self.client.connect()

    def test_open_leaves_dtr_rts_deasserted_and_handles_ansi_partial_reads_and_writes(self):
        parsed = self.connect()
        self.factory.assert_called_once_with(port=None, baudrate=115200, timeout=0.025, write_timeout=0.025, exclusive=True)
        self.assertEqual(self.port.open_state, ("/dev/ttyUSB0", False, False))
        self.assertEqual(self.port.commands, ["\x03", "ai info"])
        self.assertEqual(parsed["target"], TARGET)
        self.assertEqual(parsed["model_sha256"], MODEL)
        self.assertEqual(self.client.upload(PIXELS).prediction, 3)
        self.assertEqual(b"".join(self.port.rows[row] for row in range(28)), PIXELS)
        self.assertTrue(self.port.is_open)

    def test_open_needs_no_firmware_response_and_synchronization_does_not_query_identity(self):
        self.port.overrides["ai info"] = None
        self.assertIsNone(self.client.open())
        self.assertTrue(self.port.is_open)
        self.assertEqual(self.port.commands, [])
        self.assertEqual(self.port.written, b"")
        self.assertEqual(self.port.reset_count, 0)
        self.assertTrue(self.client.synchronize().endswith(protocol.PROMPT))
        self.assertEqual(self.port.commands, ["\x03"])
        self.assertEqual(self.client.upload(PIXELS).prediction, 3)
        self.assertNotIn("ai info", self.port.commands)
        self.assertTrue(self.port.is_open)

    def test_opened_character_device_identity_is_checked_before_any_port_reset_or_command(self):
        expected = {"device": 1, "inode": 2, "rdev": 3, "ctime_ns": 4}
        observed = SimpleNamespace(st_mode=stat.S_IFCHR, st_dev=1, st_ino=2, st_rdev=3, st_ctime_ns=4)
        client = protocol.SerialClient("/dev/ttyUSB0", expected_identity=expected)
        self.addCleanup(client.close)
        with patch.object(protocol.os, "fstat", return_value=observed) as fstat:
            self.assertEqual(client.connect()["target"], TARGET)
        fstat.assert_called_once_with(42)
        self.assertEqual(self.port.reset_count, 1)

    def test_replaced_or_noncharacter_opened_device_is_closed_before_io(self):
        expected = {"device": 1, "inode": 2, "rdev": 3, "ctime_ns": 4}
        for changes in ({"st_ino": 99}, {"st_rdev": 99}, {"st_ctime_ns": 99}, {"st_mode": stat.S_IFREG}):
            self.port.reset_count = 0
            self.port.written.clear()
            observed = SimpleNamespace(st_mode=stat.S_IFCHR, st_dev=1, st_ino=2, st_rdev=3, st_ctime_ns=4)
            for name, value in changes.items():
                setattr(observed, name, value)
            client = protocol.SerialClient("/dev/ttyUSB0", expected_identity=deepcopy(expected))
            with self.subTest(changes=changes), patch.object(protocol.os, "fstat", return_value=observed), \
                    self.assertRaisesRegex(RuntimeError, "changed after selection"):
                client.connect()
            self.assertIsNone(client.port)
            self.assertFalse(self.port.is_open)
            self.assertEqual(self.port.reset_count, 0)
            self.assertEqual(self.port.written, b"")

    def test_failed_firmware_handshake_closes_the_port(self):
        self.port.overrides["ai info"] = info().replace("State: ready", "State: FAILED")
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            self.connect()
        self.assertIsNone(self.client.port)
        self.assertFalse(self.port.is_open)

    def test_driver_disconnect_write_timeout_shell_error_and_invalid_commands_close(self):
        cases = ("disconnect", "write-timeout", "shell-error", "command-newline", "command-control")
        for case in cases:
            with self.subTest(case=case):
                self.port.read_error = None
                self.port.write_error = None
                self.connect()
                text = "ai info"
                if case == "disconnect":
                    self.port.read_error = serial.SerialException("device disconnected")
                elif case == "write-timeout":
                    self.port.write_error = serial.SerialTimeoutException("write stalled")
                elif case == "shell-error":
                    self.port.overrides["ai info"] = "ERROR: model unavailable\r\n"
                elif case == "command-newline":
                    text = "ai info\nreset"
                else:
                    text = "ai info\x00"
                with self.assertRaises((RuntimeError, TimeoutError, ValueError)):
                    self.client.command(text)
                self.assertIsNone(self.client.port)
                self.assertFalse(self.port.is_open)
                self.port.overrides.clear()

    def test_busy_port_error_is_actionable_and_cancellation_never_opens_a_port(self):
        self.factory.side_effect = serial.SerialException(errno.EBUSY, "Device or resource busy")
        with self.assertRaisesRegex(RuntimeError, "port is busy"):
            self.connect()
        self.assertIsNone(self.client.port)
        self.factory.reset_mock()
        self.cancel.set()
        with self.assertRaises(CancelledError):
            self.connect()
        self.factory.assert_not_called()

    def test_cancellation_during_read_or_upload_closes_without_a_result(self):
        self.connect()
        self.port.on_read = self.cancel.set
        with self.assertRaises(CancelledError):
            self.client.command("ai info")
        self.assertIsNone(self.client.port)
        self.cancel.clear()
        self.port.on_read = None
        self.connect()

        def progress(row, total):
            if row == 3:
                self.cancel.set()

        with self.assertRaises(CancelledError):
            self.client.upload(PIXELS, progress)
        self.assertIsNone(self.client.port)
        self.assertNotIn("ai upload infer " + protocol.image_crc(PIXELS), self.port.commands)

    def test_timeout_and_oversized_reply_are_bounded_and_close_the_connection(self):
        self.connect()
        self.port.overrides["ai info"] = None
        clock = iter(index * 0.01 for index in range(100))
        with patch.object(protocol, "_COMMAND_TIMEOUT", 0.05), \
                patch.object(protocol.time, "monotonic", side_effect=lambda: next(clock)), \
                self.assertRaisesRegex(TimeoutError, "No UART response received before timeout"):
            self.client.command("ai info")
        self.assertIsNone(self.client.port)
        self.port.overrides.clear()
        self.port.read_size = 4096
        self.connect()
        self.port.overrides["ai info"] = "x" * 65537
        with self.assertRaisesRegex(RuntimeError, "65536-byte limit"):
            self.client.command("ai info")
        self.assertIsNone(self.client.port)

    def test_full_upload_has_a_transaction_deadline_and_output_drain_is_cancellable(self):
        self.connect()
        clock = iter(index * 0.01 for index in range(100))
        with patch.object(protocol, "_UPLOAD_TIMEOUT", 0.1), \
                patch.object(protocol.time, "monotonic", side_effect=lambda: next(clock)), \
                self.assertRaises(TimeoutError):
            self.client.upload(PIXELS)
        self.assertIsNone(self.client.port)
        self.connect()
        self.port.waiting = 1
        with patch.object(protocol.time, "sleep", side_effect=lambda delay: self.cancel.set()), \
                self.assertRaises(CancelledError):
            self.client.command("ai info")
        self.assertIsNone(self.client.port)

    def test_a_competing_operation_does_not_interrupt_the_active_connection(self):
        self.connect()
        self.client._lock.acquire()
        try:
            with self.assertRaisesRegex(RuntimeError, "already in progress"):
                self.client.command("ai info")
        finally:
            self.client._lock.release()
        self.assertTrue(self.port.is_open)

    def test_module_is_bundled_instead_of_loading_a_workspace_script(self):
        self.assertEqual(Path(protocol.__file__).name, "mnist_protocol.py")
        self.assertIn("zephyr_utilities/features/tinyml", Path(protocol.__file__).as_posix())


@unittest.skipUnless(hasattr(os, "openpty"), "A POSIX pseudoterminal is required.")
class PseudoterminalTransportTests(unittest.TestCase):
    """Exercise the installed pyserial driver against an in-process firmware peer."""

    def setUp(self):
        try:
            self.master, self.slave = os.openpty()
        except OSError as exc:
            self.skipTest(f"Pseudoterminal creation is unavailable: {exc}")
        self.path = os.ttyname(self.slave)
        observed = os.fstat(self.slave)
        self.identity = {"device": observed.st_dev, "inode": observed.st_ino,
                         "rdev": observed.st_rdev, "ctime_ns": observed.st_ctime_ns}
        self.stop = Event()
        self.peer = FakePort()
        self.peer_errors = []

        def serve():
            pending = bytearray()
            try:
                while not self.stop.is_set():
                    if not select.select([self.master], [], [], 0.05)[0]:
                        continue
                    chunk = os.read(self.master, 4096)
                    if not chunk:
                        return
                    pending.extend(chunk)
                    while b"\n" in pending:
                        line, _, remaining = pending.partition(b"\n")
                        pending[:] = remaining
                        command = line.decode("ascii").rstrip("\r")
                        self.peer.commands.append(command)
                        response = self.peer.respond(command)
                        if response is not None:
                            output = (response + "\x1b[32m" + protocol.PROMPT + "\x1b[0m").encode("ascii")
                            while output:
                                output = output[os.write(self.master, output):]
            except BaseException as exc:
                if not self.stop.is_set():
                    self.peer_errors.append(exc)

        self.thread = Thread(target=serve, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.stop.set()
        self.thread.join(timeout=1)
        os.close(self.master)
        os.close(self.slave)

    def client(self, cancel=None):
        client = protocol.SerialClient(self.path, cancel=cancel, expected_identity=self.identity)
        self.addCleanup(client.close)
        return client

    def test_real_pyserial_connects_transfers_all_rows_and_releases_fd_and_exclusive_lock(self):
        client = self.client()
        connected = client.connect()
        self.assertEqual(connected["target"], TARGET)
        self.assertEqual(protocol.parse_info(connected["raw_info"], expected_target=TARGET, expected_model=MODEL)["model_sha256"], MODEL)
        port = client.port
        descriptor = port.fileno()
        competing = self.client()
        with self.assertRaisesRegex(RuntimeError, "port is busy"):
            competing.connect()
        self.assertTrue(port.is_open)
        progress = []
        result = client.upload(PIXELS, lambda row, total: progress.append((row, total)))
        self.assertEqual(result.crc32, protocol.image_crc(PIXELS))
        self.assertEqual(result.prediction, 3)
        self.assertEqual(progress[-1], (28, 28))
        self.assertEqual(b"".join(self.peer.rows[row] for row in range(28)), PIXELS)
        client.close()
        self.assertFalse(port.is_open)
        with self.assertRaises(OSError):
            os.fstat(descriptor)
        replacement = self.client()
        self.assertEqual(replacement.connect()["target"], TARGET)
        self.assertEqual(self.peer_errors, [])

    def test_real_pyserial_cancel_closes_transaction_and_allows_reconnection(self):
        cancel = Event()
        client = self.client(cancel)
        client.connect()
        port = client.port

        def progress(row, total):
            if row == 3:
                cancel.set()

        with self.assertRaises(CancelledError):
            client.upload(PIXELS, progress)
        self.assertIsNone(client.port)
        self.assertFalse(port.is_open)
        self.assertNotIn("ai upload infer " + protocol.image_crc(PIXELS), self.peer.commands)
        replacement = self.client()
        self.assertEqual(replacement.connect()["protocol"], "mnist-gray8-v1")
        self.assertEqual(self.peer_errors, [])

    def test_real_pyserial_opens_a_silent_device_and_sends_successive_images_without_identity_queries(self):
        self.peer.overrides["ai info"] = None
        client = self.client()
        self.assertIsNone(client.open())
        descriptor = client.port.fileno()
        self.assertTrue(client.port.is_open)
        self.assertEqual(self.peer.commands, [], "Open must not send CtrlC, ai info or any other bytes.")
        self.assertEqual(self.peer.rows, {})
        for pixels in (PIXELS, bytes(reversed(PIXELS))):
            client.synchronize()
            result = client.upload(pixels)
            self.assertEqual(result.crc32, protocol.image_crc(pixels))
            self.assertEqual(client.port.fileno(), descriptor)
            self.assertTrue(client.port.is_open)
        self.assertEqual(self.peer.commands.count("\x03"), 2)
        self.assertNotIn("ai info", self.peer.commands)
        self.assertEqual(self.peer_errors, [])


if __name__ == "__main__":
    unittest.main()
