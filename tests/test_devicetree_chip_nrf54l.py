"""nRF54L native domains, clock pins and dedicated serial routes."""

from types import SimpleNamespace
import unittest

from zephyr_utilities.features.devicetree.binding_service import _literal
from zephyr_utilities.features.devicetree.chip_catalog import observe_chip
from zephyr_utilities.features.devicetree.chip_nordic import pin_notes, pin_restrictions, profile, route
from zephyr_utilities.features.devicetree.source import SourceDocument

from test_devicetree_chip_nordic import HEADER


CONTROLLERS = '''
    uart20: uart@c6000 { compatible = "nordic,nrf-uarte"; reg = <0xc6000 0x1000>; status = "disabled"; };
    i2c21: i2c@c7000 { compatible = "nordic,nrf-twim"; reg = <0xc7000 0x1000>; status = "disabled"; };
    spi00: spi@4a000 { compatible = "nordic,nrf-spim"; reg = <0x4a000 0x1000>; status = "disabled"; };
'''


def fixture(controllers=CONTROLLERS, states="", *, extra="", gpio="", uicr="", soc="nrf54l15", core="cpuapp", source=None):
    """Small resolved architecture observation shared with transaction tests."""
    text = source or ('/ { #address-cells = <1>; #size-cells = <1>; '
        'pinctrl: pinctrl { compatible = "nordic,nrf-pinctrl"; ' + states + ' }; '
        'uicr: uicr@ffd000 { compatible = "nordic,nrf-uicr"; reg = <0xffd000 0x1000>; ' + uicr + ' }; '
        'nfct: nfct { compatible = "nordic,nrf-nfct"; status = "okay"; }; '
        'peripheral@50000000 { reg = <0x50000000 0x10000000>; ranges = <0 0x50000000 0x10000000>; '
        '#address-cells = <1>; #size-cells = <1>; '
        + ''.join(f'gpio{port}: gpio@{reg:x} {{ compatible = "nordic,nrf-gpio"; reg = <{reg} 0x300>; '
                  f'gpio-controller; #gpio-cells = <2>; port = <{port}>; ngpios = <{count}>; {gpio} }}; '
                  for port, reg, count in ((0, 0x10a000, 7), (1, 0xd8200, 17), (2, 0x50400, 11)))
        + controllers + ' }; ' + extra + ' };')
    document = SourceDocument.parse(text.encode() if isinstance(text, str) else text)
    nodes = []
    for node in document.syntax.nodes:
        compatible = _literal(document, node, "compatible")
        compatible = [compatible.value] if compatible and compatible.kind == "string" else []
        status = _literal(document, node, "status")
        nodes.append({"id": "hardware:" + document._path(node), "path": document._path(node),
                      "reference": "&" + node.labels[0] if node.labels else "&{" + document._path(node) + "}",
                      "labels": list(node.labels), "compatible": compatible,
                      "status": status.value if status else "okay", "binding": {"state": "matched"},
                      "origin": "board", "source_locations": [], "properties": {}})
    topology = {"supported": True, "nodes": nodes,
                "gpio_providers": [node for node in nodes if "nordic,nrf-gpio" in node["compatible"]],
                "pinctrl_states": []}
    for provider in topology["gpio_providers"]:
        node = document.find_node(provider["path"])
        count = _literal(document, node, "ngpios").value[0]
        provider.update(cells=["pin", "flags"], pin_count=count, supported=True, pin_count_origin="explicit")
        provider["properties"].update({"#gpio-cells": 2, "port": _literal(document, node, "port").value[0]})
    for row in nodes:
        node = document.find_node(row["path"])
        for index, name in enumerate(("default", "sleep")):
            refs = _literal(document, node, f"pinctrl-{index}")
            if refs and refs.kind == "phandles":
                topology["pinctrl_states"].append({"owner_id": row["id"], "name": name,
                    "targets": ["hardware:" + document._path(document.find_node(ref)) for ref in refs.value]})
    sources = SimpleNamespace(files={f"/workspace/{soc}.dtsi": {"text": "/ {};"},
        f"/workspace/{soc}_{core.replace('/', '_')}.dtsi": {"text": f"#include <nordic/{soc}.dtsi>"},
        "/workspace/nrf54l_05_10_15.dtsi": {"text": "/* common architecture */"},
        "/workspace/nrf-pinctrl.h": {"text": HEADER}},
        context={"target": {"requested": f"board/{soc}/{core}", "qualifiers": [soc, *core.split('/')]}})
    observed = {"sources": sources, "payload": topology, "document": document}
    return observed, observe_chip(sources, topology, document)


class NRF54LActivationTests(unittest.TestCase):
    def test_source_inventory_loads_automatically_and_generates_native_routes(self):
        observed, chip = fixture()
        self.assertEqual(len(chip["pins"]), 35)
        profiles = {item["id"]: item for item in profile(observed, chip)}
        routes = {"&uart20": {"tx": "P1.05", "rx": "P1.06"},
                  "&i2c21": {"scl": "P1.11", "sda": "P1.12"},
                  "&spi00": {"sck": "P2.06", "mosi": "P2.08", "miso": "P2.09"}}
        for reference, selection in routes.items():
            with self.subTest(reference=reference):
                controller = profiles[reference]
                self.assertTrue(controller["available"], controller["reason"])
                generated = route(observed, chip, reference, selection)
                self.assertEqual(generated["pins"], list(selection.values()))
                self.assertEqual(len(generated["pinctrl_nodes"]), 2)
                self.assertIn('"default", "sleep"', generated["updates"][0]["properties"]["pinctrl-names"])
                self.assertIn("NRF_PSEL(", generated["pinctrl_nodes"][0]["source"])

    def test_architecture_is_shared_without_per_chip_pin_inventory(self):
        for soc in ("nrf54l05", "nrf54l10", "nrf54l15"):
            with self.subTest(soc=soc):
                observed, chip = fixture(soc=soc)
                self.assertEqual(len(chip["pins"]), 35)
                self.assertTrue(all(item["available"] for item in profile(observed, chip)))

    def test_native_domains_and_clock_roles_limit_alternatives(self):
        controllers = CONTROLLERS + '''
            spi22: spi@c8000 { compatible = "nordic,nrf-spim"; reg = <0xc8000 0x1000>; status = "disabled"; };
            i2c30: i2c@104000 { compatible = "nordic,nrf-twim"; reg = <0x104000 0x1000>; status = "disabled"; };
        '''
        observed, chip = fixture(controllers)
        found = {item["id"]: {signal["id"]: signal["candidates"] for signal in item["signals"]}
                 for item in profile(observed, chip)}
        self.assertNotIn("P1.02", found["&uart20"]["tx"])
        self.assertTrue(all(pin.startswith("P1.") for pin in found["&uart20"]["tx"]))
        self.assertEqual(set(found["&i2c21"]["scl"]), {"P1.04", "P1.08", "P1.11", "P1.12"})
        self.assertIn("P1.05", found["&i2c21"]["sda"])
        self.assertNotIn("P1.05", found["&spi22"]["sck"])
        self.assertEqual(set(found["&i2c30"]["scl"]), {"P0.03", "P0.04"})
        self.assertEqual(set(found["&spi00"]["sck"]), {"P2.01", "P2.06"})
        self.assertEqual(set(found["&spi00"]["mosi"]), {"P2.02", "P2.08"})
        self.assertEqual(set(found["&spi00"]["miso"]), {"P2.04", "P2.09"})

    def test_uart00_dedicated_roles_and_peripheral_instance_conflicts(self):
        uart = '''uart00: uart@4a000 { compatible = "nordic,nrf-uarte"; reg = <0x4a000 0x1000>;
                   status = "disabled"; hw-flow-control; };'''
        observed, chip = fixture(uart)
        found = profile(observed, chip)[0]
        candidates = {signal["id"]: set(signal["candidates"]) for signal in found["signals"]}
        self.assertEqual(candidates, {"tx": {"P2.02", "P2.08"}, "rx": {"P2.00", "P2.07"},
                                      "rts": {"P2.05", "P2.10"}, "cts": {"P2.04", "P2.09"}})
        generated = route(observed, chip, "&uart00", {"tx": "P2.08", "rx": "P2.07", "rts": "P2.10", "cts": "P2.09"})
        self.assertTrue(any("dedicated" in note for note in generated["notes"]))
        observed, chip = fixture(uart + CONTROLLERS.replace('status = "disabled";', 'status = "okay";'))
        found = next(row for row in profile(observed, chip) if row["id"] == "&uart00")
        self.assertFalse(found["available"])
        self.assertEqual(found["blocked_by"], ["&spi00"])

    def test_register_identity_cannot_be_overridden_by_labels_or_default_ports(self):
        observed, chip = fixture(CONTROLLERS.replace("uart20:", "my_console:"))
        row = next(row for row in profile(observed, chip) if row["id"] == "&my_console")
        self.assertTrue(row["available"], row["reason"])
        self.assertTrue(all(pin.startswith("P1.") for signal in row["signals"] for pin in signal["candidates"]))
        for change, reason in (("reg = <0xc6000 0x1000>; default-gpio-port = <&gpio0>;", "conflicts"),
                               ("reg = <0xc9000 0x1000>;", "no verified")):
            with self.subTest(change=change):
                observed, chip = fixture(CONTROLLERS.replace("reg = <0xc6000 0x1000>;", change))
                row = next(row for row in profile(observed, chip) if row["id"] == "&uart20")
                self.assertFalse(row["available"])
                self.assertIn(reason, row["reason"])

    def test_missing_architecture_and_changed_address_space_remain_unavailable(self):
        observed, chip = fixture()
        del observed["sources"].files["/workspace/nrf54l_05_10_15.dtsi"]
        with self.assertRaisesRegex(ValueError, "common architecture"):
            pin_restrictions(observed, chip)
        for before, after in (("0x50000000", "0x40000000"), ("1089536", "1089792"), ("ngpios = <7>", "ngpios = <8>")):
            with self.subTest(after=after):
                original, _ = fixture()
                changed = original["document"].source.decode().replace(before, after)
                self.assertNotEqual(changed, original["document"].source.decode())
                observed, chip = fixture(source=changed)
                with self.assertRaisesRegex(ValueError, "address space|layout"):
                    pin_restrictions(observed, chip)

    def test_ancestor_translation_cannot_spoof_the_secure_peripheral_address(self):
        observed, _ = fixture()
        text = observed["document"].source.decode()
        text = text.replace("peripheral@50000000 {", '''translated { #address-cells = <1>; #size-cells = <1>;
                            ranges = <0 0x10000000 0x60000000>; peripheral@50000000 {''') + " };"
        observed, chip = fixture(source=text)
        self.assertFalse(observed["document"].syntax.blocked)
        with self.assertRaisesRegex(ValueError, "Translated.*ancestor"):
            profile(observed, chip)
        observed, chip = fixture(source=text.replace("ranges = <0 0x10000000 0x60000000>;", "ranges;"))
        self.assertTrue(all(row["available"] for row in profile(observed, chip)))

    def test_nonsecure_secondary_and_domain_owned_architectures_remain_unavailable(self):
        for kwargs, reason in (({"core": "cpuapp/ns"}, "secure image"),
                               ({"core": "cpuflpr"}, "controlling core"),
                               ({"soc": "nrf54h20"}, "common architecture"),
                               ({"extra": 'domain { compatible = "nordic,nrf-uicr-v2"; };'}, "domain ownership"),
                               ({"extra": 'secure { secure-gpios = <&gpio1 12 0>; };'}, "security")):
            with self.subTest(kwargs=kwargs):
                observed, chip = fixture(**kwargs)
                with self.assertRaisesRegex(ValueError, reason):
                    pin_restrictions(observed, chip)

    def test_coprocessor_and_forwarded_pins_do_not_gain_ownership(self):
        for status in ("okay", "reserved"):
            observed, chip = fixture(extra='flpr { compatible = "nordic,nrf-vpr-coprocessor"; status = "' + status + '"; };')
            with self.assertRaisesRegex(ValueError, "FLPR coprocessor"):
                pin_restrictions(observed, chip)
        observed, chip = fixture(extra='''flpr { compatible = "nordic,nrf-vpr-coprocessor"; status = "disabled"; };
            forwarder { compatible = "nordic,nrf-gpio-forwarder"; peripheral { gpios = <&gpio2 8 0>; }; };''')
        restrictions = pin_restrictions(observed, chip)
        self.assertTrue(any("forwarded" in item for item in restrictions["P2.08"]))
        self.assertEqual(restrictions["P2.07"], [])

    def test_nfc_pins_require_disabled_nfc_and_explicit_gpio_configuration(self):
        observed, chip = fixture()
        restrictions = pin_restrictions(observed, chip)
        self.assertTrue(restrictions["P1.02"])
        self.assertTrue(restrictions["P1.03"])
        self.assertEqual(restrictions["P0.02"], [])
        observed, chip = fixture(uicr="nfct-pins-as-gpios;")
        self.assertTrue(pin_restrictions(observed, chip)["P1.02"])
        text = observed["document"].source.decode().replace('nfct { compatible = "nordic,nrf-nfct"; status = "okay";',
                                                          'nfct { compatible = "nordic,nrf-nfct"; status = "disabled";')
        observed, chip = fixture(source=text)
        self.assertEqual(pin_restrictions(observed, chip)["P1.02"], [])
        self.assertEqual(pin_restrictions(observed, chip)["P1.03"], [])

    def test_nfc_release_requires_the_uicr_label_consumed_by_zephyr(self):
        observed, _ = fixture(uicr="nfct-pins-as-gpios;")
        text = observed["document"].source.decode().replace('nfct { compatible = "nordic,nrf-nfct"; status = "okay";',
                                                          'nfct { compatible = "nordic,nrf-nfct"; status = "disabled";')
        for replacement in ("", "other_configuration:"):
            with self.subTest(label=replacement):
                observed, chip = fixture(source=text.replace("uicr:", replacement))
                restrictions = pin_restrictions(observed, chip)
                self.assertTrue(restrictions["P1.02"])
                self.assertTrue(restrictions["P1.03"])
                controller = next(row for row in profile(observed, chip) if row["id"] == "&i2c21")
                self.assertNotIn("P1.03", next(signal["candidates"] for signal in controller["signals"] if signal["id"] == "scl"))
        observed, chip = fixture(source=text)
        self.assertEqual(pin_restrictions(observed, chip)["P1.03"], [])

    def test_deleting_nfc_node_does_not_release_its_default_dedicated_pins(self):
        observed, _ = fixture()
        text = observed["document"].source.decode().replace('nfct: nfct { compatible = "nordic,nrf-nfct"; status = "okay"; };', "")
        observed, chip = fixture(source=text)
        self.assertTrue(pin_restrictions(observed, chip)["P1.02"])
        self.assertTrue(pin_restrictions(observed, chip)["P1.03"])
        observed, chip = fixture(source=text.replace("reg = <0xffd000 0x1000>;", "reg = <0xffd000 0x1000>; nfct-pins-as-gpios;"))
        self.assertEqual(pin_restrictions(observed, chip)["P1.02"], [])

    def test_clock_sharing_and_port2_interrupt_limit_are_visible_notes(self):
        observed, chip = fixture()
        notes = pin_notes(observed, chip)
        self.assertIn("Kconfig", notes["P1.00"][0])
        self.assertIn("Kconfig", notes["P1.01"][0])
        self.assertNotIn("P0.00", notes)
        self.assertIn("no GPIO interrupt", notes["P2.08"][0])
        self.assertEqual(pin_restrictions(observed, chip)["P2.08"], [])

    def test_invalid_clock_and_cross_domain_choices_are_rejected(self):
        observed, chip = fixture()
        for controller, selection in (("&i2c21", {"scl": "P1.05", "sda": "P1.06"}),
                                       ("&uart20", {"tx": "P0.05", "rx": "P1.06"}),
                                       ("&spi00", {"sck": "P2.01", "mosi": "P2.03", "miso": "P2.04"})):
            with self.subTest(controller=controller):
                with self.assertRaisesRegex(ValueError, "available package pin"):
                    route(observed, chip, controller, selection)

    def test_invalid_inherited_route_cannot_bypass_legality_by_retaining_current_pin(self):
        states = 'state_default: state_default { group { psels = <184549413 201326630>; }; };'
        controller = '''i2c21: i2c@c7000 { compatible = "nordic,nrf-twim"; reg = <0xc7000 0x1000>;
            status = "okay"; pinctrl-0 = <&state_default>; pinctrl-names = "default"; };'''
        observed, chip = fixture(controller, states)
        row = profile(observed, chip)[0]
        self.assertFalse(row["available"])
        self.assertIn("clock-pin route", row["reason"])
        with self.assertRaisesRegex(ValueError, "clock-pin route"):
            route(observed, chip, "&i2c21", {"scl": "P1.11", "sda": "P1.12"})

    def test_uart_clones_companion_signals_and_sleep_electrical_properties(self):
        states = '''uart_default: uart_default {
            output { psels = <36 33554470>; };
            input { psels = <16777253 50331687>; bias-pull-up; };
        }; uart_sleep: uart_sleep {
            tx { psels = <36>; low-power-enable; bias-pull-up; nordic,drive-mode = <3>; };
            other { psels = <16777253 33554470 50331687>; low-power-enable; };
        };'''
        controller = '''uart20: uart@c6000 { compatible = "nordic,nrf-uarte"; reg = <0xc6000 0x1000>;
            status = "okay"; current-speed = <57600>; pinctrl-0 = <&uart_default>; pinctrl-1 = <&uart_sleep>;
            pinctrl-names = "default", "sleep"; };'''
        observed, chip = fixture(controller, states)
        source = observed["document"].source
        row = profile(observed, chip)[0]
        self.assertTrue(row["available"], row["reason"])
        self.assertEqual(set(row["current"]), {"tx", "rx", "rts", "cts"})
        generated = route(observed, chip, "&uart20", {"tx": "P1.11", "rx": "P1.12", "rts": "P1.06", "cts": "P1.07"})
        self.assertEqual(observed["document"].source, source)
        self.assertNotIn("current-speed", generated["updates"][0]["properties"])
        self.assertIn("nordic,drive-mode = <3>", generated["pinctrl_nodes"][1]["source"])
        self.assertIn("bias-pull-up;", generated["pinctrl_nodes"][1]["source"])
        self.assertIn("NRF_PSEL(UART_RTS, 1, 6)", generated["pinctrl_nodes"][0]["source"])

    def test_spi00_high_speed_or_unknown_device_frequency_needs_drive_validation(self):
        observed, chip = fixture()
        row = next(row for row in profile(observed, chip) if row["id"] == "&spi00")
        self.assertTrue(row["available"], row["reason"])
        self.assertTrue(any("8 MHz or below" in note for note in row["notes"]))
        for frequency in ("<32000000>", '"unknown"'):
            with self.subTest(frequency=frequency):
                controller = 'spi00: spi@4a000 { compatible = "nordic,nrf-spim"; reg = <0x4a000 0x1000>; status = "okay"; '
                controller += 'flash@0 { spi-max-frequency = ' + frequency + '; }; };'
                observed, chip = fixture(controller)
                row = profile(observed, chip)[0]
                self.assertFalse(row["available"])
                self.assertIn("drive validation", row["reason"])


if __name__ == "__main__":
    unittest.main()
