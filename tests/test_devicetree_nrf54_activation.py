"""nRF54L assignments use canonical source history, guarded Save and recovery."""

from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from zephyr_utilities.app import Application

import test_devicetree_board_topology as boards
import test_devicetree_chip_activation as activation_fixtures
from test_devicetree_chip_nrf54l import fixture


INITIAL_UART = {"tx": "P1.04", "rx": "P1.05", "rts": "P1.06", "cts": "P1.07"}
ROUTES = {
    "&uart20": {"tx": "P1.10", "rx": "P1.11", "rts": "P1.06", "cts": "P1.07"},
    "&i2c21": {"scl": "P1.12", "sda": "P1.13"},
    "&spi00": {"sck": "P2.01", "mosi": "P2.02", "miso": "P2.04"},
}
FUNCTIONS = {"tx": 0, "rx": 1, "rts": 2, "cts": 3, "sck": 4, "mosi": 5, "miso": 6, "scl": 11, "sda": 12}


def observation(*, changed=None, gpio=False, reserved=False):
    """Small resolved CPUAPP bus excerpt; post-edit expectations are independent."""
    assignments = {"&uart20": INITIAL_UART, **(changed or {})}
    controllers, states = [], []
    for label, kind, offset in (("uart20", "uarte", 0xc6000), ("i2c21", "twim", 0xc7000), ("spi00", "spim", 0x4a000)):
        route = assignments.get("&" + label)
        pins = ""
        if route:
            values = []
            for signal, pin in route.items():
                port, index = (int(part) for part in pin[1:].split("."))
                values.append(str((FUNCTIONS[signal] << 24) | (port * 32 + index)))
            for state in ("default", "sleep"):
                properties = "low-power-enable; bias-pull-up;" if state == "sleep" else ""
                states.append(f'{label}_{state}: {label}_{state} {{ group {{ psels = <{" ".join(values)}>; {properties} }}; }};')
            pins = (f'pinctrl-0 = <&{label}_default>; pinctrl-1 = <&{label}_sleep>; '
                    'pinctrl-names = "default", "sleep";')
        status = "okay" if route else "disabled"
        speed = "current-speed = <57600>;" if kind == "uarte" else ""
        controllers.append(f'{label}: {label}@{offset:x} {{ compatible = "nordic,nrf-{kind}"; '
                           f'reg = <0x{offset:x} 0x1000>; status = "{status}"; {speed} {pins} }};')
    consumer = "zephyr,user { app-p2-06-gpios = <&gpio2 6 0>; };" if gpio else ""
    observed, chip = fixture(" ".join(controllers), " ".join(states), extra=consumer)
    if reserved:
        source = observed["document"].source.decode().replace("ngpios = <17>;", "ngpios = <17>; gpio-reserved-ranges = <12 1>;")
        observed, chip = fixture(source=source)
    observed["payload"].update(coverage="board+selected-overlay", observation_id=uuid.uuid4().hex)
    document = observed["document"]
    for row in observed["payload"]["nodes"]:
        node = document.find_node(row["path"])
        row["parent_id"] = "hardware:" + document._path(document.node(node.parent_id)) if node.parent_id else None
    observed["sources"].revalidate = Mock()
    observed["catalog"] = SimpleNamespace(revalidate=Mock())
    return observed, chip


class Nrf54ActivationTransactionTests(unittest.TestCase):
    binding = boards.BoardTopologyTests.binding
    guard = boards.BoardTopologyTests.guard
    install_observation = activation_fixtures.ChipActivationTransactionTests.install_observation

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.application.mkdir(parents=True)
        (self.application / "CMakeLists.txt").write_text('message(FATAL_ERROR "Do not execute")\n')
        self.overlay = self.application / "app.overlay"
        self.overlay.write_text("/* Preserve existing application source. */\n")
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = self.app.context.services["devicetree_drafts"]
        self.chip_workspace = self.app.context.services["devicetree_chip"]
        self.saving = self.app.context.services["devicetree_saving"]
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})
        self.observed, self.chip = observation()
        self.install_observation()

    def command(self, controller=None, signals=None, pin=None):
        command = {"kind": "chip_gpio" if pin else "chip_assign", "observation_id": self.observed["payload"]["observation_id"]}
        command.update({"pin": pin} if pin else {"controller": controller, "signals": signals or ROUTES[controller]})
        return self.guard(command=command)

    def activate(self, controller=None, *, pin=None):
        checked, _ = observation(changed={controller: ROUTES[controller]} if controller else None, gpio=bool(pin))
        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe", return_value=checked):
            self.draft = self.drafts.command(self.command(controller, pin=pin))
        return checked

    def verify_history_and_save(self, controller=None, *, pin=None):
        baseline = self.overlay.read_bytes()
        checked = self.activate(controller, pin=pin)
        candidate = self.draft["text"]
        self.assertNotEqual(candidate, baseline.decode())
        self.assertEqual(len(self.drafts.drafts[self.draft["id"]].undo), 1)
        self.assertEqual(self.overlay.read_bytes(), baseline)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], baseline.decode())
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft["text"], candidate)
        self.assertEqual(self.overlay.read_bytes(), baseline)
        review = self.saving.review(self.guard())
        self.assertEqual(review["status"], "ready", review)
        self.assertEqual(review["plan"]["candidate_text"], candidate)
        saved = self.saving.save(self.guard(plan_id=review["plan"]["id"]))
        self.assertEqual(saved["status"], "saved", saved)
        self.context, self.draft = saved["context"], saved["draft"]
        self.assertFalse(self.draft["dirty"])
        self.assertEqual(self.overlay.read_bytes(), candidate.encode())
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], baseline.decode())
        self.assertTrue(self.draft["dirty"])
        self.assertEqual(self.overlay.read_bytes(), candidate.encode())
        checked["sources"].revalidate.assert_called_once()
        checked["catalog"].revalidate.assert_called_once()
        return candidate

    def test_gpio_activation_uses_reviewed_save_and_canonical_history(self):
        text = self.verify_history_and_save(pin="P2.06")
        self.assertIn("app-p2-06-gpios = <&gpio2 6 GPIO_ACTIVE_HIGH>", text)

    def test_uart_activation_preserves_sleep_companions_and_canonical_history(self):
        text = self.verify_history_and_save("&uart20")
        for value in ("NRF_PSEL(UART_TX, 1, 10)", "NRF_PSEL(UART_RX, 1, 11)",
                      "NRF_PSEL(UART_RTS, 1, 6)", "NRF_PSEL(UART_CTS, 1, 7)", "bias-pull-up;", "low-power-enable;"):
            self.assertIn(value, text)
        self.assertNotIn("current-speed", text)

    def test_i2c_activation_uses_reviewed_save_and_canonical_history(self):
        text = self.verify_history_and_save("&i2c21")
        self.assertIn("NRF_PSEL(TWIM_SCL, 1, 12)", text)
        self.assertIn("NRF_PSEL(TWIM_SDA, 1, 13)", text)

    def test_dedicated_spi_activation_uses_reviewed_save_and_canonical_history(self):
        text = self.verify_history_and_save("&spi00")
        for signal, index in (("SCK", 1), ("MOSI", 2), ("MISO", 4)):
            self.assertIn(f"NRF_PSEL(SPIM_{signal}, 2, {index})", text)

    def test_illegal_cross_port_reserved_or_dedicated_routes_leave_draft_and_file_unchanged(self):
        self.observed, self.chip = observation(reserved=True)
        self.install_observation()
        before, disk = self.drafts.drafts[self.draft["id"]], self.overlay.read_bytes()
        for controller, signals in (("&uart20", {**ROUTES["&uart20"], "tx": "P2.06"}),
                                    ("&i2c21", ROUTES["&i2c21"]),
                                    ("&spi00", {**ROUTES["&spi00"], "sck": "P2.03"})):
            with self.subTest(controller=controller), patch(
                    "zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe") as validate:
                with self.assertRaises(ValueError):
                    self.drafts.command(self.command(controller, signals))
                validate.assert_not_called()
                self.assertIs(self.drafts.drafts[self.draft["id"]], before)
                self.assertEqual(self.overlay.read_bytes(), disk)

    def test_restart_recovery_retains_activation_and_undo_without_publishing(self):
        original = self.overlay.read_bytes()
        self.activate("&uart20")
        recovery_id, candidate = self.draft["id"], self.draft["text"]
        self.app.close()
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = self.app.context.services["devicetree_drafts"]
        self.saving = self.app.context.services["devicetree_saving"]
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        document = self.context["documents"][0]
        self.draft = self.drafts.open({**self.binding(), "document_id": document["id"]})
        self.assertEqual(self.draft["text"], original.decode())
        recovery = self.saving.recovery_read({"workspace": str(self.workspace), "recovery_id": recovery_id})
        restored = self.saving.recovery_restore({**self.binding(), "document_id": document["id"],
            "recovery_id": recovery_id, "expected_recovery_revision": recovery["record"]["revision"],
            "comparison_id": recovery["comparison"]["comparison_id"], "mode": "keep"})
        self.context, self.draft = restored["context"], restored["draft"]
        self.assertEqual(self.draft["text"], candidate)
        self.assertEqual(self.overlay.read_bytes(), original)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], original.decode())
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft["text"], candidate)
        self.assertEqual(self.overlay.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
