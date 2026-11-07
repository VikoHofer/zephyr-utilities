"""Chip activation keeps source authority, conflicts and canonical history aligned."""

from copy import deepcopy
from threading import Thread
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch
import uuid

from zephyr_utilities.features.devicetree.chip_activation import prepare_activation
from zephyr_utilities.features.devicetree.chip_catalog import observe_chip
from zephyr_utilities.features.devicetree.source import SourceDocument

import test_devicetree_board_topology as board_fixtures
from test_devicetree_chip_nordic import UART, UART_STATES, fixture as nordic_fixture


# Minimal resolved excerpt of boards/nordic/nrf52840dk/nrf52840dk_nrf52840.dts:
# Arduino D10 (connector index 16) is P1.12, with GPIO flags passed through.
NRF_CONNECTOR = '''arduino_header: connector {
    compatible = "arduino-header-r3";
    #gpio-cells = <2>;
    gpio-map-mask = <0xffffffff 0xffffffc0>;
    gpio-map-pass-thru = <0 0x3f>;
    gpio-map = <16 0 &gpio1 12 0>;
};
spi3: spi@4002f000 {
    compatible = "nordic,nrf-spim";
    reg = <0x4002f000 0x1000>;
    status = "okay";
    cs-gpios = <&arduino_header 16 1>;
    pinctrl-0 = <&spi3_default>;
    pinctrl-1 = <&spi3_sleep>;
    pinctrl-names = "default", "sleep";
};'''
NRF_SPI_STATES = '''
spi3_default: spi3_default {
    group1 { psels = <67108911 100663342 83886125>; nordic,drive-mode = <3>; };
};
spi3_sleep: spi3_sleep {
    group1 { psels = <67108911 100663342 83886125>; low-power-enable; };
};'''


def observation(*args, **kwargs):
    observed, chip = nordic_fixture(*args, **kwargs)
    observed["payload"].update(observation_id=uuid.uuid4().hex, coverage="board+selected-overlay")
    document = observed["document"]
    for row in observed["payload"]["nodes"]:
        node = document.find_node(row["path"])
        row["parent_id"] = "hardware:" + document._path(document.node(node.parent_id)) if node.parent_id else None
    for provider in observed["payload"]["gpio_providers"]:
        provider.update(cells=["pin", "flags"], pin_count=32 if "gpio0" in provider["labels"] else 16, supported=True)
    observed["sources"].revalidate = Mock()
    observed["catalog"] = SimpleNamespace(revalidate=Mock())
    return observed, chip


def connector_observation(source=NRF_CONNECTOR):
    observed, _ = observation(source, NRF_SPI_STATES)
    connector = next(row for row in observed["payload"]["nodes"] if row["path"] == "/connector")
    connector["properties"] = {"#gpio-cells": 2}
    connector["binding"].update(compatible="arduino-header-r3")
    observed["payload"]["connector_maps"] = [{"id": "hardware:/connector", "path": "/connector",
        "mask": [0xffffffff, 0xffffffc0], "pass_thru": [0, 0x3f],
        "entries": [{"child_cells": [16, 0], "provider_id": "hardware:/gpio1", "provider_path": "/gpio1", "parent_cells": [12, 0]}]}]
    chip = observe_chip(observed["sources"], observed["payload"], observed["document"])
    return observed, chip


class ChipActivationConflictTests(unittest.TestCase):
    def prepared(self, *args, **kwargs):
        observed, chip = observation(*args, **kwargs)
        result = prepare_activation(observed, chip, SimpleNamespace(document=SourceDocument.parse(b"")))
        return observed, chip, result

    def test_existing_gpio_owner_blocks_gpio_and_new_uart_pin_but_retained_pins_remain_visible(self):
        _, _, result = self.prepared(UART + 'led: led { gpios = <&gpio1 0 0>; };')
        self.assertTrue(result["available"], result["reason"])
        self.assertIn("P1.00", result["gpio"]["unavailable"])
        uart = result["controllers"][0]
        signals = {signal["id"]: signal for signal in uart["signals"]}
        self.assertIn("&led", signals["tx"]["unavailable"]["P1.00"])
        self.assertNotIn("P0.06", signals["tx"]["unavailable"])
        self.assertTrue(any("P0.06 retains" in note for note in uart["notes"]))

    def test_same_controller_chip_select_stays_reserved_when_spi_is_activated_or_rerouted(self):
        for status in ("okay", "disabled"):
            controllers = ('spi0: spi@40003000 { compatible = "nordic,nrf-spi"; reg = <0x40003000 0x1000>; '
                           f'status = "{status}"; cs-gpios = <&gpio1 0 1>; }};')
            with self.subTest(status=status):
                _, _, result = self.prepared(controllers, "")
                self.assertTrue(result["available"], result["reason"])
                for signal in result["controllers"][0]["signals"]:
                    self.assertIn("cs-gpios", signal["unavailable"].get("P1.00", ""))

    def test_disabled_other_owner_does_not_block_free_pin(self):
        _, _, result = self.prepared(UART + 'led: led { status = "disabled"; gpios = <&gpio1 0 0>; };')
        self.assertIn("P1.00", result["gpio"]["pins"])
        self.assertNotIn("P1.00", result["controllers"][0]["signals"][0]["unavailable"])

    def test_controller_under_disabled_parent_remains_unavailable(self):
        _, _, result = self.prepared('bus: bus { status = "disabled"; ' + UART + " };")
        self.assertTrue(result["available"], result["reason"])
        controller = result["controllers"][0]
        self.assertFalse(controller["available"])
        self.assertIn("parent", controller["reason"])

    def test_nrf_dk_arduino_chip_select_resolves_and_protects_the_package_pin(self):
        observed, chip = connector_observation()
        result = prepare_activation(observed, chip, SimpleNamespace(document=SourceDocument.parse(b"")))
        self.assertTrue(result["available"], result["reason"])
        cs_pin = next(pin for pin in chip["pins"] if pin["id"] == "P1.12")
        self.assertTrue(any(use["controller"] == "&spi3" and use["signal"] == "cs-gpios" for use in cs_pin["assignments"]))
        self.assertIn("cs-gpios", result["gpio"]["unavailable"]["P1.12"])
        self.assertTrue(result["controllers"][0]["available"])
        for signal in result["controllers"][0]["signals"]:
            self.assertIn("cs-gpios", signal["unavailable"].get("P1.12", ""))

    def test_unmapped_connector_chip_select_cannot_authorize_new_assignments(self):
        observed, chip = connector_observation(NRF_CONNECTOR.replace("cs-gpios = <&arduino_header 16 1>", "cs-gpios = <&arduino_header 17 1>"))
        result = prepare_activation(observed, chip, SimpleNamespace(document=SourceDocument.parse(b"")))
        self.assertFalse(result["available"])
        self.assertIn("GPIO assignment", result["reason"])

    def test_unknown_active_gpio_specifiers_and_hogs_disable_activation(self):
        for specifier in ("<&gpio1 0>", "<&gpio1 0 0 1>", "<&gpio1 16 0>", '"unknown"'):
            with self.subTest(specifier=specifier):
                _, _, result = self.prepared(UART + f'led: led {{ gpios = {specifier}; }};')
                self.assertFalse(result["available"])
                self.assertIn("GPIO assignment", result["reason"])
        for cells in ("<2>", "<2 0 3>", "<32 0>"):
            with self.subTest(hog=cells):
                _, _, result = self.prepared(gpio=f'hog {{ gpio-hog; gpios = {cells}; input; }};')
                self.assertFalse(result["available"])
                self.assertIn("GPIO assignment", result["reason"])

    def test_board_only_and_unknown_pin_states_cannot_authorize_mutation(self):
        observed, chip = observation()
        draft = SimpleNamespace(document=SourceDocument.parse(b""))
        observed["payload"]["coverage"] = "board-only"
        self.assertIn("single-overlay", prepare_activation(observed, chip, draft)["reason"])
        observed["payload"]["coverage"] = "board+selected-overlay"
        chip["unknown_assignments"] = 1
        self.assertIn("could not be decoded", prepare_activation(observed, chip, draft)["reason"])
        self.assertIn("existing application overlay", prepare_activation(observed, chip, None)["reason"])

    def test_automatic_nordic_inventory_dispatches_without_a_chip_specific_adapter(self):
        for soc, ports, core in (("nrf52832", ((0, 32),), None),
                                  ("nrf5340", ((0, 32), (1, 16)), "cpuapp"),
                                  ("nrf9160", ((0, 32),), None)):
            with self.subTest(soc=soc):
                observed, chip = nordic_fixture(soc=soc, ports=ports, core=core, uicr="")
                observed["payload"].update(observation_id=uuid.uuid4().hex, coverage="board+unsaved-overlay")
                result = prepare_activation(observed, chip, SimpleNamespace(document=SourceDocument.parse(b"")))
                self.assertTrue(result["available"], result["reason"])
                self.assertTrue(result["gpio"]["pins"])
                self.assertTrue(any(controller["kind"] == "uart" and controller["available"]
                                    for controller in result["controllers"]))
                self.assertTrue(all(int(pin.split(".")[0][1:]) in {port for port, _ in ports}
                                    for pin in result["gpio"]["pins"]))

    def test_unknown_routing_family_never_falls_back_to_vendor_or_chip_name(self):
        observed, chip = observation()
        chip["chip"]["family"] = "unknown"
        result = prepare_activation(observed, chip, SimpleNamespace(document=SourceDocument.parse(b"")))
        self.assertFalse(result["available"])
        self.assertTrue(result["reason"])
        self.assertEqual(result["controllers"], [])


class ChipActivationTransactionTests(unittest.TestCase):
    binding = board_fixtures.BoardTopologyTests.binding
    guard = board_fixtures.BoardTopologyTests.guard

    def setUp(self):
        board_fixtures.BoardTopologyTests.setUp(self)
        self.chip_workspace = self.app.context.services["devicetree_chip"]
        self.activation = self.app.context.services["devicetree_chip_activation"]
        self.observed, self.chip = observation()
        self.install_observation()

    def install_observation(self):
        self.observed["payload"]["observation_id"] = uuid.uuid4().hex
        self.chip["activation"] = prepare_activation(self.observed, self.chip, self.drafts.drafts[self.draft["id"]])
        payload = deepcopy(self.chip)
        job = self.app.context.jobs.submit("devicetree", lambda _emit, _cancel: payload)
        deadline = time.monotonic() + 3
        while self.app.context.jobs.get(job["id"])["status"] in {"queued", "running"}:
            self.assertLess(time.monotonic(), deadline, "Fixture observation job did not finish")
            time.sleep(0.005)
        self.record = {"guard": self.guard(), "observation": self.observed, "expires": time.monotonic() + 60}
        self.chip_workspace.records[job["id"]] = self.record

    def command(self, *, signals=None, pin=None):
        command = {"kind": "chip_gpio" if pin else "chip_assign", "observation_id": self.observed["payload"]["observation_id"]}
        if pin:
            command["pin"] = pin
        else:
            command.update(controller="&uart0", signals=signals or {"tx": "P1.00", "rx": "P0.08", "rts": "P0.05", "cts": "P0.07"})
        return self.guard(command=command)

    @staticmethod
    def moved_uart():
        return observation(states=UART_STATES.replace("<6 33554437>", "<32 33554437>").replace("<6>", "<32>"))[0]

    def test_activation_is_one_history_step_and_undo_redo_leave_firmware_file_untouched(self):
        before = self.overlay.read_bytes()
        checked = self.moved_uart()
        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe", return_value=checked) as validate:
            self.draft = self.drafts.command(self.command())
        self.assertEqual(validate.call_count, 1)
        self.assertEqual(self.draft["revision"], 1)
        self.assertIn("NRF_PSEL(UART_TX, 1, 0)", self.draft["text"])
        self.assertEqual(len(self.drafts.drafts[self.draft["id"]].undo), 1)
        generated = self.draft["text"]
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], before.decode())
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft["text"], generated)
        self.assertEqual(self.overlay.read_bytes(), before)
        checked["sources"].revalidate.assert_called_once()
        checked["catalog"].revalidate.assert_called_once()

    def test_current_complete_route_is_noop_without_preprocessing_or_history(self):
        current = {"tx": "P0.06", "rx": "P0.08", "rts": "P0.05", "cts": "P0.07"}
        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe") as validate:
            result = self.drafts.command(self.command(signals=current))
        validate.assert_not_called()
        self.assertEqual(result["revision"], 0)
        self.assertEqual(self.drafts.drafts[result["id"]].undo, ())

    def test_same_uart_pins_with_disabled_reception_still_activate_the_complete_route(self):
        self.observed, self.chip = observation(UART.replace("current-speed = <115200>;", "current-speed = <115200>; disable-rx;"))
        self.install_observation()
        checked = observation()[0]
        current = {"tx": "P0.06", "rx": "P0.08", "rts": "P0.05", "cts": "P0.07"}
        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe", return_value=checked) as validate:
            result = self.drafts.command(self.command(signals=current))
        validate.assert_called_once()
        self.assertEqual(result["revision"], 1)
        self.assertIn("/delete-property/ disable-rx;", result["text"])

    def test_failed_or_mismatched_candidate_never_changes_source_or_history(self):
        unchanged = self.drafts.drafts[self.draft["id"]]
        invalid = {"payload": {"supported": False, "reason": "Candidate include is missing"}}
        for checked in (invalid, self.observed):
            with self.subTest(checked=checked["payload"].get("supported")), patch(
                    "zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe", return_value=checked):
                with self.assertRaisesRegex(ValueError, "not applied|did not match"):
                    self.drafts.command(self.command())
            self.assertIs(self.drafts.drafts[self.draft["id"]], unchanged)

    def test_newly_enabled_controller_with_malformed_gpio_is_rejected_after_resolution(self):
        controller = ('spi0: spi@40003000 { compatible = "nordic,nrf-spi"; reg = <0x40003000 0x1000>; '
                      'status = "disabled"; cs-gpios = <&gpio1 0>; };')
        self.observed, self.chip = observation(controller, "")
        self.install_observation()
        self.assertTrue(self.chip["activation"]["controllers"][0]["available"])
        checked = observation(controller.replace('"disabled"', '"okay"'), "")[0]
        body = self.guard(command={"kind": "chip_assign", "observation_id": self.observed["payload"]["observation_id"],
                                   "controller": "&spi0", "signals": {"sck": "P1.01", "mosi": "P1.02", "miso": "P1.03"}})
        before = self.drafts.drafts[self.draft["id"]]
        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe", return_value=checked):
            with self.assertRaisesRegex(ValueError, "GPIO assignment"):
                self.drafts.command(body)
        self.assertIs(self.drafts.drafts[self.draft["id"]], before)

    def test_expired_observation_and_changed_source_dependencies_are_rejected(self):
        self.record["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.drafts.command(self.command())
        self.record["expires"] = time.monotonic() + 60
        self.observed["sources"].revalidate.side_effect = ValueError("Board input changed")
        with self.assertRaisesRegex(ValueError, "input changed"):
            self.drafts.command(self.command())
        self.assertEqual(self.drafts.drafts[self.draft["id"]].revision, 0)

    def test_candidate_dependency_change_before_commit_leaves_the_original_draft(self):
        checked = self.moved_uart()
        checked["catalog"].revalidate.side_effect = ValueError("Candidate binding changed")
        before = self.drafts.drafts[self.draft["id"]]
        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe", return_value=checked):
            with self.assertRaisesRegex(ValueError, "binding changed"):
                self.drafts.command(self.command())
        self.assertIs(self.drafts.drafts[self.draft["id"]], before)

    def test_ambiguous_overlay_contributors_are_not_rewritten(self):
        source = '&uart0 { current-speed = <115200>; };\n&uart0 { status = "okay"; };\n'
        self.draft = self.drafts.source(self.guard(text=source))
        self.install_observation()
        before = self.drafts.drafts[self.draft["id"]]
        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe") as validate:
            with self.assertRaisesRegex(ValueError, "several source contributors"):
                self.drafts.command(self.command())
        validate.assert_not_called()
        self.assertIs(self.drafts.drafts[self.draft["id"]], before)

    def test_changed_draft_rejects_both_stale_guard_and_stale_observation(self):
        stale = self.command()
        self.draft = self.drafts.source(self.guard(text="/* independently edited */\n"))
        for body in (stale, self.command()):
            with self.subTest(revision=body["expected_draft_revision"]), self.assertRaisesRegex(ValueError, "changed|revision"):
                self.drafts.command(body)
        self.assertEqual(self.drafts.drafts[self.draft["id"]].source, b"/* independently edited */\n")

    def test_edit_during_candidate_validation_is_not_blocked_or_overwritten(self):
        checked = self.moved_uart()

        def validate(_context, _draft):
            def change():
                self.draft = self.drafts.source(self.guard(text="/* edit while validating */\n"))
            thread = Thread(target=change)
            thread.start()
            thread.join(2)
            self.assertFalse(thread.is_alive(), "Preprocessing held the canonical draft locks")
            return checked

        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe", side_effect=validate):
            with self.assertRaisesRegex(ValueError, "changed|revision"):
                self.drafts.command(self.command())
        self.assertEqual(self.drafts.drafts[self.draft["id"]].source, b"/* edit while validating */\n")
        self.assertEqual(len(self.drafts.drafts[self.draft["id"]].undo), 1)

    def test_gpio_activation_adds_reference_and_repeated_application_is_noop(self):
        checked, changed_chip = observation(UART + "zephyr,user { app-p1-00-gpios = <&gpio1 0 0>; };")
        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe", return_value=checked):
            self.draft = self.drafts.command(self.command(pin="P1.00"))
        self.assertIn("app-p1-00-gpios = <&gpio1 0 GPIO_ACTIVE_HIGH>", self.draft["text"])
        self.assertEqual(self.draft["revision"], 1)
        self.observed, self.chip = checked, changed_chip
        self.install_observation()
        with patch("zephyr_utilities.features.devicetree.chip_activation.BoardTopologyService.observe") as validate:
            result = self.drafts.command(self.command(pin="P1.00"))
        validate.assert_not_called()
        self.assertEqual(result["revision"], 1)
        self.assertEqual(len(self.drafts.drafts[result["id"]].undo), 1)


if __name__ == "__main__":
    unittest.main()
