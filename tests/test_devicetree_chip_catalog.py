"""Chip catalogues preserve evidenced pins without granting mutation authority."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from zephyr_utilities.features.devicetree.chip_catalog import observe_chip
from zephyr_utilities.features.devicetree.source import SourceDocument


STM32_HEADER = """
#define STM32_AF4 4
#define STM32_AF7 7
#define STM32_ANALOG 16
#define STM32_MODE_SHIFT 0U
#define STM32_MODE_MASK 0x1FU
#define STM32_LINE_SHIFT 5U
#define STM32_LINE_MASK 0xFU
#define STM32_PORT_SHIFT 9U
#define STM32_PORT_MASK 0x1FU
#define STM32_PINMUX(port, line, mode) \\
 (((((port) - 'A') & STM32_PORT_MASK) << STM32_PORT_SHIFT) | \\
 (((line) & STM32_LINE_MASK) << STM32_LINE_SHIFT) | \\
 (((STM32_ ## mode) & STM32_MODE_MASK) << STM32_MODE_SHIFT))
"""
STM32_TABLE = """
&pinctrl {
 /omit-if-no-ref/ usart2_tx_pa2: usart2_tx_pa2 { pinmux = <STM32_PINMUX('A', 2, AF7)>; };
 /omit-if-no-ref/ adc1_in2_pa2: adc1_in2_pa2 { pinmux = <STM32_PINMUX('A', 2, ANALOG)>; };
 /omit-if-no-ref/ i2c2_scl_pb10: i2c2_scl_pb10 { pinmux = <STM32_PINMUX('B', 10, AF4)>; };
 /omit-if-no-ref/ adc1_in12_pc2: adc1_in12_pc2 { pinmux = <STM32_PINMUX('C', 2, ANALOG)>; };
 /* ignored_pb11: ignored_pb11 { pinmux = <STM32_PINMUX('B', 11, AF4)>; }; */
};
"""
NRF_HEADER = """
#define NRF_PIN_POS 0U
#define NRF_PIN_MSK 0x1FFU
#define NRF_FUN_POS 24U
#define NRF_FUN_MSK 0xFFU
#define NRF_FUN_UART_TX 0U
#define NRF_FUN_UART_RX 1U
#define NRF_FUN_SPIM_SCK 4U
#define NRF_FUN_TWIM_SCL 11U
#define NRF_FUN_TWIM_SDA 12U
#define NRF_PSEL(fun, port, pin) \\
 ((((((port) * 32U) + (pin)) & NRF_PIN_MSK) << NRF_PIN_POS) | \\
 ((NRF_FUN_ ## fun & NRF_FUN_MSK) << NRF_FUN_POS))
"""


def sources(family="stm32", **changes):
    files = ({"stm32f446r(c-e)tx-pinctrl.dtsi": STM32_TABLE, "stm32-pinctrl.h": STM32_HEADER}
             if family == "stm32" else {"nrf52840_qiaa.dtsi": "#include <nordic/nrf52840.dtsi>",
                                        "nrf52840.dtsi": "/ {};", "nrf-pinctrl.h": NRF_HEADER})
    files.update(changes)
    return SimpleNamespace(files={"/workspace/" + name: {"text": text} for name, text in files.items()}, context={})


def row(path, labels=(), **extra):
    return {"id": "hardware:" + path, "path": path, "labels": list(labels),
            "reference": "&" + labels[0] if labels else "&{" + path + "}",
            "origin": "board", "status": "okay", "source_locations": [{"path": "/workspace/board.dts", "line": 4}], **extra}


def nordic_topology(topology=None, ports=((0, 32), (1, 16))):
    result = deepcopy(topology or {})
    result.setdefault("nodes", [])
    result.setdefault("gpio_providers", [])
    for port, count in ports:
        provider = next((item for item in result["gpio_providers"] if f"gpio{port}" in item["labels"]), None)
        if provider is None:
            provider = row(f"/gpio{port}", [f"gpio{port}"], properties={})
            result["gpio_providers"].append(provider)
        provider.update(compatible=["nordic,nrf-gpio"], cells=["pin", "flags"], supported=True,
                        binding={"state": "matched"}, pin_count=count, pin_count_origin="binding-default")
        provider.setdefault("properties", {})["port"] = port
    return result


class ChipCatalogTests(unittest.TestCase):
    def observe(self, source=None, tree="/ {};", topology=None):
        if source is not None and "/workspace/nrf-pinctrl.h" in source.files:
            topology = nordic_topology(topology)
            document = SourceDocument.parse(tree.encode())
            paths = {document._path(node) for node in document.syntax.nodes}
            additions = "".join(f'{provider["labels"][0]}: {provider["path"].lstrip("/")} {{ port = <{provider["properties"]["port"]}>; }};'
                                for provider in topology["gpio_providers"] if provider["path"] not in paths)
            tree = tree[:tree.rfind("};")] + additions + tree[tree.rfind("};"):]
        return observe_chip(source or sources(), topology or {}, SourceDocument.parse(tree.encode()))

    def test_raw_package_table_keeps_unused_alternatives_and_holes(self):
        payload = self.observe()
        self.assertTrue(payload["supported"], payload["reason"])
        self.assertEqual([pin["name"] for pin in payload["pins"]], ["PA2", "PB10", "PC2"])
        self.assertNotIn("PB11", [pin["name"] for pin in payload["pins"]])
        self.assertEqual({item["signal"] for item in payload["pins"][0]["functions"]}, {"GPIO", "TX", "IN2"})
        self.assertFalse(payload["editing_available"])
        self.assertTrue(all(not choice["available"] and choice["reason"]
                            for pin in payload["pins"] for choice in pin["functions"]))

    def test_unknown_profile_keeps_target_without_inventing_gpio_slots(self):
        payload = observe_chip(SimpleNamespace(files={}, context={}),
                               {"gpio_providers": [{"pin_count": 100}]}, SourceDocument.parse(b"/ {};"),
                               target={"requested": "custom/soc/cpu"})
        self.assertFalse(payload["supported"])
        self.assertEqual(payload["chip"]["name"], "custom/soc/cpu")
        self.assertEqual(payload["pins"], [])
        self.assertTrue(payload["reason"])

    def test_changed_encoding_is_not_decoded_with_stale_assumptions(self):
        payload = self.observe(sources(**{"stm32-pinctrl.h": STM32_HEADER.replace("STM32_LINE_SHIFT 5U", "STM32_LINE_SHIFT 6U")}))
        self.assertFalse(payload["supported"])
        self.assertIn("fields changed", payload["reason"])
        changed = NRF_HEADER.replace("* 32U", "* 64U")
        payload = self.observe(sources("nordic", **{"nrf-pinctrl.h": changed}))
        self.assertFalse(payload["supported"])
        self.assertIn("encoding", payload["reason"])

    def test_ambiguous_package_sources_are_not_merged(self):
        selected = sources()
        selected.files["/another/stm32f446r(c-e)tx-pinctrl.dtsi"] = {"text": STM32_TABLE}
        payload = self.observe(selected)
        self.assertFalse(payload["supported"])
        self.assertIn("unambiguous", payload["reason"])

    def test_stm32_assignments_decode_controller_signal_and_origin(self):
        tree = "/ { pins: pins { pinmux = <71>; }; usart2: serial { pinctrl-0 = <&pins>; }; };"
        owner = row("/serial", ["usart2"], origin="overlay")
        topology = {"nodes": [owner, row("/pins", ["pins"])],
                    "pinctrl_states": [{"owner_id": owner["id"], "name": "default", "targets": ["hardware:/pins"]}]}
        payload = self.observe(tree=tree, topology=topology)
        assigned = payload["pins"][0]["assignments"]
        self.assertEqual(len(assigned), 1)
        self.assertEqual((assigned[0]["controller"], assigned[0]["signal"], assigned[0]["origin"]), ("&usart2", "TX", "overlay"))
        self.assertEqual(payload["pins"][0]["status"], "configured")

    def test_existing_gpio_consumers_reservations_and_board_names_survive(self):
        tree = "/ { gpioa: gpio {}; led { gpios = <&gpioa 2 1>; }; };"
        provider = row("/gpio", ["gpioa"], properties={"gpio-line-names": ["", "", "BOARD LED"]}, reserved_ranges=[[2, 1]],
                       cells=["pin", "flags"], pin_count=16, supported=True, binding={"state": "matched"})
        topology = {"gpio_providers": [provider], "nodes": [provider, row("/led", ["led0"])],
                    "connector_maps": [{"path": "/connector", "entries": [{"provider_id": provider["id"], "parent_cells": [2, 0], "child_cells": [5, 0]}]}]}
        payload = self.observe(tree=tree, topology=topology)
        pin = payload["pins"][0]
        self.assertEqual(pin["board_names"], ["BOARD LED"])
        self.assertTrue(pin["restrictions"])
        self.assertEqual(pin["status"], "reserved")
        self.assertEqual(pin["assignments"][0]["signal"], "gpios")
        self.assertEqual(pin["connectors"][0]["cells"], [5, 0])

    def test_nordic_package_inventory_does_not_create_port_one_upper_slots(self):
        payload = self.observe(sources("nordic"))
        self.assertTrue(payload["supported"], payload["reason"])
        self.assertEqual(len(payload["pins"]), 48)
        self.assertEqual(payload["pins"][-1]["name"], "P1.15")
        self.assertEqual(payload["chip"]["package"], "QIAA")
        self.assertTrue(all(pin["package_pad"] is None for pin in payload["pins"]))

    def test_nordic_default_sleep_and_disabled_owner_are_distinct(self):
        tree = """/ { pinctrl {
            uart_default: uart-default { group { psels = <6>, <16777224>; }; };
            uart_sleep: uart-sleep { group { psels = <6 16777224>; }; };
        }; uart0: uart { status = "disabled"; }; };"""
        owner = row("/uart", ["uart0"], status="disabled", compatible=["nordic,nrf-uarte"])
        topology = {"nodes": [owner], "pinctrl_states": [
            {"owner_id": owner["id"], "name": name, "targets": ["hardware:/pinctrl/uart-" + name]}
            for name in ("default", "sleep")]}
        payload = self.observe(sources("nordic"), tree, topology)
        pin = next(pin for pin in payload["pins"] if pin["name"] == "P0.06")
        self.assertEqual({item["state"] for item in pin["assignments"]}, {"default", "sleep"})
        self.assertTrue(all(item["status"] == "disabled" and item["signal"] == "UART_TX" for item in pin["assignments"]))
        self.assertIn("UART_RX", {item["signal"] for item in pin["functions"]})

    def test_separate_reserved_cell_groups_are_read_from_resolved_source(self):
        tree = "/ { gpio0: gpio { gpio-reserved-ranges = <0 2>, <6 1>, <9 2>; }; };"
        provider = row("/gpio", ["gpio0"], properties={}, reserved_ranges=None)
        payload = self.observe(sources("nordic"), tree, {"gpio_providers": [provider], "nodes": [provider]})
        self.assertEqual([pin["name"] for pin in payload["pins"] if pin["restrictions"]],
                         ["P0.00", "P0.01", "P0.06", "P0.09", "P0.10"])

    def test_unknown_state_entries_do_not_become_guessed_assignments(self):
        tree = "/ { pin { pinmux = <999999>; }; bus {}; };"
        owner = row("/bus", ["usart2"])
        topology = {"nodes": [owner], "pinctrl_states": [{"owner_id": owner["id"], "name": "default", "targets": ["hardware:/pin"]}]}
        payload = self.observe(tree=tree, topology=topology)
        self.assertTrue(payload["supported"])
        self.assertEqual(payload["coverage"], "partial-assignments")
        self.assertTrue(payload["unknown_assignments"])
        self.assertFalse(any(pin["assignments"] for pin in payload["pins"]))

    def test_nordic_missing_package_uses_gpio_slots_without_inventing_package(self):
        selected = sources("nordic")
        del selected.files["/workspace/nrf52840_qiaa.dtsi"]
        payload = self.observe(selected)
        self.assertTrue(payload["supported"], payload["reason"])
        self.assertIsNone(payload["chip"]["package"])
        self.assertEqual(payload["chip"]["inventory"], "gpio-slots")
        self.assertTrue(all(pin["package_pad"] is None for pin in payload["pins"]))


if __name__ == "__main__":
    unittest.main()
