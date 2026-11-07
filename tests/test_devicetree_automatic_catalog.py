"""Source formats, not chip allowlists, determine logical pin discovery."""

from types import SimpleNamespace
import unittest

from zephyr_utilities.features.devicetree.chip_catalog import observe_chip
from zephyr_utilities.features.devicetree.source import SourceDocument

from test_devicetree_chip_catalog import NRF_HEADER, STM32_HEADER, STM32_TABLE, nordic_topology, row


def selected_files(files, soc=None):
    return SimpleNamespace(files={"/workspace/" + path: {"text": text} for path, text in files.items()},
                           context={"target": {"qualifiers": [soc]}} if soc else {})


def nordic_fixture(soc="nrf52832", ports=((0, 32),), *, extra="", source_files=None):
    topology = nordic_topology(ports=ports)
    tree = "/ { " + "".join(f"gpio{port}: gpio{port} {{ port = <{port}>; ngpios = <{count}>; }};"
                             for port, count in ports) + extra + " };"
    document = SourceDocument.parse(tree.encode())
    sources = selected_files(source_files or {soc + ".dtsi": "/ {};", "nrf-pinctrl.h": NRF_HEADER}, soc)
    return sources, topology, document


class AutomaticCatalogTests(unittest.TestCase):
    def test_stm32_selected_tables_across_families_need_no_registered_chip_profile(self):
        for profile, soc in (("stm32f401r(b-c)tx", "stm32f401xc"), ("stm32g071rbtx", "stm32g071xx"),
                             ("stm32l476r(g-i)tx", "stm32l476xx"), ("stm32h743zitx", "stm32h743xx"),
                             ("stm32u575zitx", "stm32u575xx")):
            with self.subTest(profile=profile):
                sources = selected_files({profile + "-pinctrl.dtsi": STM32_TABLE, "stm32-pinctrl.h": STM32_HEADER}, soc)
                result = observe_chip(sources, {}, SourceDocument.parse(b"/ {};"))
                self.assertTrue(result["supported"], result["reason"])
                self.assertEqual(result["chip"]["family"], "stm32")
                self.assertEqual(result["chip"]["soc"], soc)
                self.assertEqual(result["chip"]["inventory"], "pin-table")
                self.assertIn(profile + "-pinctrl.dtsi", result["chip"]["profile_source"])
                self.assertEqual([pin["id"] for pin in result["pins"]], ["PA2", "PB10", "PC2"])
                self.assertTrue(all(pin["sources"] for pin in result["pins"]))

    def test_two_selected_stm32_tables_do_not_merge_package_pin_inventories(self):
        sources = selected_files({"stm32f401rctx-pinctrl.dtsi": STM32_TABLE,
                                  "stm32f401vctx-pinctrl.dtsi": STM32_TABLE,
                                  "stm32-pinctrl.h": STM32_HEADER})
        result = observe_chip(sources, {}, SourceDocument.parse(b"/ {};"))
        self.assertFalse(result["supported"])
        self.assertIn("unambiguous", result["reason"])
        self.assertEqual(result["pins"], [])

    def test_partial_or_changed_stm32_encoding_never_authorizes_discovered_subset(self):
        for table, header in ((STM32_TABLE.replace("STM32_PINMUX('C', 2, ANALOG)", "UNKNOWN_MUX(2)"), STM32_HEADER),
                              (STM32_TABLE, STM32_HEADER.replace("STM32_AF7 7", "STM32_AF7 8")),
                              (STM32_TABLE.replace("STM32_PINMUX('C', 2, ANALOG)", "STM32_PINMUX('C', 16, ANALOG)"), STM32_HEADER)):
            with self.subTest(table=table, header=header):
                result = observe_chip(selected_files({"stm32l476rgtx-pinctrl.dtsi": table, "stm32-pinctrl.h": header}),
                                      {}, SourceDocument.parse(b"/ {};"))
                self.assertFalse(result["supported"])
                self.assertEqual(result["pins"], [])
                self.assertTrue(result["reason"])

    def test_stm32_f1_remapping_has_specific_visible_unavailable_reason(self):
        sources = selected_files({"stm32f103r8tx-pinctrl.dtsi":
            "&pinctrl { tx: tx { pinmux = <STM32F1_PINMUX('A', 9, ALTERNATE, 0)>; }; };"})
        result = observe_chip(sources, {}, SourceDocument.parse(b"/ {};"))
        self.assertFalse(result["supported"])
        self.assertIn("STM32F1 remap", result["reason"])
        self.assertEqual(result["pins"], [])

    def test_stm32_declared_invalid_reservations_never_leave_free_pin_alternatives(self):
        sources = selected_files({"stm32g071rbtx-pinctrl.dtsi": STM32_TABLE, "stm32-pinctrl.h": STM32_HEADER})
        for value, count in (("<1>", 16), ("<15 2>", 16), ("<2 0>", 16), ("<-1 1>", 16),
                             ('"unknown"', 16), ("<3 2>", 4), ("<15 2>", None)):
            with self.subTest(value=value, count=count):
                provider = row("/gpio", ["gpioa"], compatible=["st,stm32-gpio"], pin_count=count)
                document = SourceDocument.parse(f"/ {{ gpioa: gpio {{ gpio-reserved-ranges = {value}; }}; }};".encode())
                result = observe_chip(sources, {"gpio_providers": [provider]}, document)
                self.assertFalse(result["supported"])
                self.assertIn("reserved range", result["reason"])
                self.assertEqual(result["pins"], [])

    def test_stm32_valid_source_reservations_use_provider_count_or_standard_port_bound(self):
        sources = selected_files({"stm32g071rbtx-pinctrl.dtsi": STM32_TABLE, "stm32-pinctrl.h": STM32_HEADER})
        for count in (4, None):
            with self.subTest(count=count):
                provider = row("/gpio", ["gpioa"], compatible=["st,stm32-gpio"], pin_count=count)
                document = SourceDocument.parse(b"/ { gpioa: gpio { gpio-reserved-ranges = <0 1>, <2 1>; }; };")
                result = observe_chip(sources, {"gpio_providers": [provider]}, document)
                self.assertTrue(result["supported"], result["reason"])
                self.assertEqual([pin["id"] for pin in result["pins"] if pin["restrictions"]], ["PA2"])

    def test_unmapped_and_foreign_gpio_reservations_do_not_poison_stm32_inventory(self):
        sources = selected_files({"stm32g071rbtx-pinctrl.dtsi": STM32_TABLE, "stm32-pinctrl.h": STM32_HEADER})
        for labels, compatible in ((["expander"], ["vnd,gpio-expander"]),
                                   (["gpioa"], ["vnd,gpio-expander"]),
                                   (["gpioz"], ["st,stm32-gpio"])):
            with self.subTest(labels=labels, compatible=compatible):
                provider = row("/gpio", labels, compatible=compatible, pin_count=8)
                document = SourceDocument.parse(b'/ { gpio { gpio-reserved-ranges = "unknown"; }; };')
                result = observe_chip(sources, {"gpio_providers": [provider]}, document)
                self.assertTrue(result["supported"], result["reason"])
                self.assertEqual([pin["id"] for pin in result["pins"]], ["PA2", "PB10", "PC2"])
                self.assertTrue(all(not pin["restrictions"] for pin in result["pins"]))

    def test_nordic_families_use_only_observed_ports_and_counts(self):
        for soc, ports in (("nrf52832", ((0, 32),)), ("nrf52833", ((0, 32), (1, 10))),
                           ("nrf5340", ((0, 32), (1, 16))), ("nrf9160", ((0, 32),)),
                           ("nrf54l15", ((0, 7), (1, 17), (2, 11)))):
            with self.subTest(soc=soc):
                sources, topology, document = nordic_fixture(soc, ports)
                result = observe_chip(sources, topology, document)
                self.assertTrue(result["supported"], result["reason"])
                self.assertEqual(result["chip"]["soc"], soc)
                self.assertEqual(result["chip"]["family"], "nordic")
                self.assertEqual(result["chip"]["inventory"], "gpio-slots")
                self.assertIsNone(result["chip"]["package"])
                expected = [f"P{port}.{index:02d}" for port, count in ports for index in range(count)]
                self.assertEqual([pin["id"] for pin in result["pins"]], expected)
                self.assertTrue(all(pin["package_pad"] is None for pin in result["pins"]))
                self.assertTrue(any("Physical package pads" in text for text in result["diagnostics"]))

    def test_nordic_included_package_metadata_remains_optional_and_unambiguous(self):
        files = {"nrf5340_cpuapp.dtsi": "/ {};", "nrf5340_cpuapp_qkaa.dtsi": "/ {};", "nrf-pinctrl.h": NRF_HEADER}
        sources, topology, document = nordic_fixture("nrf5340", source_files=files)
        result = observe_chip(sources, topology, document)
        self.assertTrue(result["supported"], result["reason"])
        self.assertEqual(result["chip"]["package"], "QKAA")
        sources.files["/workspace/nrf5340_cpuapp_qfaa.dtsi"] = {"text": "/ {};"}
        result = observe_chip(sources, topology, document)
        self.assertFalse(result["supported"])
        self.assertIn("ambiguous Nordic package", result["reason"])

    def test_nordic_declared_reserved_holes_remain_visible_but_restricted(self):
        sources, topology, document = nordic_fixture("nrf54l15", ((2, 11),))
        document = SourceDocument.parse(document.source.replace(b"ngpios = <11>;", b"ngpios = <11>; gpio-reserved-ranges = <1 2>, <9 1>;"))
        result = observe_chip(sources, topology, document)
        self.assertTrue(result["supported"], result["reason"])
        reserved = [pin for pin in result["pins"] if pin["status"] == "reserved"]
        self.assertEqual([pin["id"] for pin in reserved], ["P2.01", "P2.02", "P2.09"])
        self.assertTrue(all(len(pin["restrictions"]) == 1 for pin in reserved))

    def test_nordic_missing_or_conflicting_port_count_evidence_stays_unavailable(self):
        for change in ("count", "port", "label", "binding", "provenance"):
            with self.subTest(change=change):
                sources, topology, document = nordic_fixture()
                provider = topology["gpio_providers"][0]
                if change == "count":
                    provider["pin_count"] = 31
                elif change == "port":
                    document = SourceDocument.parse(document.source.replace(b"port = <0>", b"port = <16>"))
                elif change == "label":
                    provider["labels"] = ["gpio1"]
                elif change == "binding":
                    provider["binding"] = {"state": "unknown"}
                else:
                    document = SourceDocument.parse(document.source.replace(b"ngpios = <32>;", b""))
                    provider.pop("pin_count_origin")
                result = observe_chip(sources, topology, document)
                self.assertFalse(result["supported"])
                self.assertEqual(result["pins"], [])
                self.assertTrue(result["reason"])

    def test_nordic_malformed_reserved_ranges_do_not_leave_unrestricted_pins(self):
        for value in ("<1>", "<31 2>", '"unknown"'):
            sources, topology, document = nordic_fixture()
            document = SourceDocument.parse(document.source.replace(b"ngpios = <32>;", f"ngpios = <32>; gpio-reserved-ranges = {value};".encode()))
            result = observe_chip(sources, topology, document)
            self.assertFalse(result["supported"])
            self.assertIn("reserved range", result["reason"])

    def test_nordic_port_two_assignments_decode_without_port_zero_assumptions(self):
        sources, topology, document = nordic_fixture("nrf54l15", ((2, 11),),
            extra='pins: pins { psels = <65>; }; uart0: uart {};')
        owner = {"id": "hardware:/uart", "path": "/uart", "labels": ["uart0"], "compatible": ["nordic,nrf-uarte"]}
        topology["nodes"].append(owner)
        topology["pinctrl_states"] = [{"owner_id": owner["id"], "name": "default", "targets": ["hardware:/pins"]}]
        result = observe_chip(sources, topology, document)
        self.assertTrue(result["supported"], result["reason"])
        pin = next(pin for pin in result["pins"] if pin["id"] == "P2.01")
        self.assertEqual(pin["assignments"][0]["signal"], "UART_TX")

    def test_nordic_name_without_verified_gpio_evidence_does_not_invent_slots(self):
        sources = selected_files({"nrf52832.dtsi": "/ {};", "nrf-pinctrl.h": NRF_HEADER}, "nrf52832")
        result = observe_chip(sources, {}, SourceDocument.parse(b"/ {};"))
        self.assertFalse(result["supported"])
        self.assertEqual(result["pins"], [])
        self.assertIn("GPIO port inventory", result["reason"])


if __name__ == "__main__":
    unittest.main()
