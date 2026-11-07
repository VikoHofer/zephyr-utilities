"""Complete STM32 routes use observed package alternatives and preserve pin states."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from zephyr_utilities.features.devicetree.chip_catalog import observe_chip
from zephyr_utilities.features.devicetree.chip_source import apply_activation, unique_prefix
from zephyr_utilities.features.devicetree.chip_stm32 import pin_notes, pin_restrictions, profile, route
from zephyr_utilities.features.devicetree.source import SourceDocument

from test_devicetree_chip_catalog import STM32_HEADER


HEADER = STM32_HEADER + "\n#define STM32_AF5 5\n#define STM32_AF0 0\n#define STM32_GPIO 17\n"
ALTERNATIVES = {
    "usart1_tx_pa9": ("A", 9, "AF7", "bias-pull-up;"),
    "usart1_tx_pb6": ("B", 6, "AF7", "bias-pull-up;"),
    "usart1_rx_pa10": ("A", 10, "AF7", ""),
    "usart1_rx_pb7": ("B", 7, "AF7", ""),
    "usart1_rts_pa12": ("A", 12, "AF7", ""),
    "usart1_cts_pa11": ("A", 11, "AF7", ""),
    "i2c1_scl_pb6": ("B", 6, "AF4", "bias-pull-up; drive-open-drain;"),
    "i2c1_scl_pb8": ("B", 8, "AF4", "bias-pull-up; drive-open-drain;"),
    "i2c1_sda_pb7": ("B", 7, "AF4", "bias-pull-up; drive-open-drain;"),
    "i2c1_sda_pb9": ("B", 9, "AF4", "bias-pull-up; drive-open-drain;"),
    "spi1_sck_pa5": ("A", 5, "AF5", 'bias-pull-down; slew-rate = "very-high-speed";'),
    "spi1_sck_pb3": ("B", 3, "AF5", 'bias-pull-down; slew-rate = "very-high-speed";'),
    "spi1_mosi_pa7": ("A", 7, "AF5", 'bias-pull-down; slew-rate = "very-high-speed";'),
    "spi1_mosi_pb5": ("B", 5, "AF5", 'bias-pull-down; slew-rate = "very-high-speed";'),
    "spi1_miso_pa6": ("A", 6, "AF5", 'bias-pull-down; slew-rate = "very-high-speed";'),
    "spi1_miso_pb4": ("B", 4, "AF5", 'bias-pull-down; slew-rate = "very-high-speed";'),
    "spi1_nss_pa4": ("A", 4, "AF5", 'slew-rate = "very-high-speed";'),
    "spi1_nss_pa15": ("A", 15, "AF5", 'slew-rate = "very-high-speed";'),
    "sys_jtms_swdio_pa13": ("A", 13, "AF0", ""),
    "sys_jtck_swclk_pa14": ("A", 14, "AF0", ""),
    "analog_ph0": ("H", 0, "ANALOG", ""),
    "analog_ph1": ("H", 1, "ANALOG", ""),
    "analog_pc14": ("C", 14, "ANALOG", ""),
    "analog_pc15": ("C", 15, "ANALOG", ""),
}


def fixture(*, kind="uart", body="", states=True, custom_pin="", sleep=False, table_change=None,
            table_name="stm32f446r(c-e)tx-pinctrl.dtsi", compatible_override=None, pinctrl_properties=""):
    alternatives = deepcopy(ALTERNATIVES)
    if table_change:
        table_change(alternatives)
    raw = "&pinctrl {\n" + "\n".join(
        f"/omit-if-no-ref/ {label}: {label} {{ pinmux = <STM32_PINMUX('{port}', {pin}, {mode})>; {extra} }};"
        for label, (port, pin, mode, extra) in alternatives.items()) + "\n};"
    source = SimpleNamespace(files={"/workspace/" + table_name: {"text": raw},
                                   "/workspace/stm32-pinctrl.h": {"text": HEADER}}, context={})
    pins = []
    for label, (port, pin, mode, extra) in alternatives.items():
        value = (ord(port) - ord("A")) << 9 | pin << 5 | (16 if mode == "ANALOG" else int(mode[2:]))
        if label == "usart1_tx_pa9" and custom_pin:
            extra = custom_pin
        pins.append(f"{label}: {label} {{ pinmux = <{value}>; {extra} }};")
    controller, compatible, default = {
        "uart": ("usart1", "st,stm32-uart", ["usart1_tx_pa9", "usart1_rx_pa10"]),
        "i2c": ("i2c1", "st,stm32-i2c-v1", ["i2c1_scl_pb8", "i2c1_sda_pb9"]),
        "spi": ("spi1", "st,stm32-spi", ["spi1_sck_pa5", "spi1_mosi_pa7", "spi1_miso_pa6", "spi1_nss_pa4"]),
    }[kind]
    compatible = compatible_override or compatible
    states_text = 'pinctrl-0 = <' + ' '.join('&' + label for label in default) + '>; pinctrl-names = "default";' if states else ""
    if sleep:
        for label in default:
            port, pin, _, _ = alternatives[label]
            value = (ord(port) - ord("A")) << 9 | pin << 5 | 16
            pins.append(f"{label}_sleep: {label}_sleep {{ pinmux = <{value}>; bias-disable; }};")
        states_text = states_text.replace('pinctrl-names = "default";', 'pinctrl-names = "default", "sleep";')
        states_text += 'pinctrl-1 = <' + ' '.join('&' + label + '_sleep' for label in default) + '>;'
    tree = '/ { pinctrl: pinctrl { compatible = "st,stm32-pinctrl"; ' + pinctrl_properties + ' '.join(pins) + ' }; '
    tree += f'{controller}: controller {{ compatible = "{compatible}"; status = "disabled"; {states_text} {body} }};'
    tree += 'gpioa: gpio-a {}; gpiob: gpio-b {}; clk_hse: hse { status = "okay"; compatible = "st,stm32-hse-clock"; }; };'
    document = SourceDocument.parse(tree.encode())
    rows = []
    for node in document.syntax.nodes:
        ref = '&' + node.labels[0] if node.labels else '&{' + document._path(node) + '}'
        rows.append({"id": "hardware:" + document._path(node), "path": document._path(node), "reference": ref,
                     "labels": list(node.labels), "status": "disabled" if node.name == "controller" else "okay",
                     "compatible": [compatible] if node.name == "controller" else ["st,stm32-hse-clock"] if node.name == "hse" else [],
                     "origin": "board", "source_locations": [], "binding": {"state": "matched"}})
    owner = next(row for row in rows if row["path"] == "/controller")
    pin_states = [{"owner_id": owner["id"], "index": 0, "name": "default",
                   "targets": ["hardware:/pinctrl/" + label for label in default]}] if states else []
    if sleep:
        pin_states.append({"owner_id": owner["id"], "index": 1, "name": "sleep",
                           "targets": ["hardware:/pinctrl/" + label + "_sleep" for label in default]})
    providers = [dict(row, cells=["pin", "flags"], supported=True) for row in rows if row["reference"] in {"&gpioa", "&gpiob"}]
    topology = {"nodes": rows, "pinctrl_states": pin_states, "gpio_providers": providers}
    chip = observe_chip(source, topology, document)
    return {"sources": source, "payload": topology, "document": document}, chip


class STM32ChipRouteTests(unittest.TestCase):
    def test_controller_profile_preserves_unused_package_candidates(self):
        observed, chip = fixture()
        item = profile(observed, chip)[0]
        self.assertTrue(item["available"], item["reason"])
        self.assertEqual(item["id"], "&usart1")
        self.assertEqual(item["current"], {"tx": "PA9", "rx": "PA10"})
        self.assertEqual(item["signals"][0]["candidates"], ["PA9", "PB6"])
        self.assertEqual(item["signals"][1]["candidates"], ["PA10", "PB7"])

    def test_route_emits_complete_controller_and_cloned_pins(self):
        observed, chip = fixture()
        before = observed["document"].source
        planned = route(observed, chip, "&usart1", {"tx": "PB6", "rx": "PB7"}, label_prefix="zu_uart")
        props = planned["updates"][0]["properties"]
        self.assertEqual(props["status"], '"okay"')
        self.assertEqual(props["pinctrl-0"], "<&zu_uart_default_tx &zu_uart_default_rx>")
        self.assertEqual(props["current-speed"], "<115200>")
        self.assertEqual(planned["pins"], ["PB6", "PB7"])
        self.assertIn("STM32_PINMUX('B', 6, AF7)", planned["pinctrl_nodes"][0]["source"])
        self.assertIn("bias-pull-up;", planned["pinctrl_nodes"][0]["source"])
        self.assertEqual(observed["document"].source, before)

    def test_uart_keeps_existing_baudrate_and_custom_pin_electrical_values(self):
        observed, chip = fixture(body="current-speed = <57600>;", custom_pin='bias-pull-down; slew-rate = "high-speed";')
        planned = route(observed, chip, "&usart1", {"tx": "PB6", "rx": "PB7"})
        self.assertNotIn("current-speed", planned["updates"][0]["properties"])
        self.assertIn("bias-pull-down;", planned["pinctrl_nodes"][0]["source"])
        self.assertIn('slew-rate = "high-speed";', planned["pinctrl_nodes"][0]["source"])
        self.assertNotIn("bias-pull-up;", planned["pinctrl_nodes"][0]["source"])

    def test_missing_companion_illegal_pin_and_unknown_controller_are_rejected(self):
        observed, chip = fixture()
        for controller, selected in (("&usart1", {"tx": "PB6"}),
                                     ("&usart1", {"tx": "PB11", "rx": "PB7"}),
                                     ("&usart1", {"tx": "PB7", "rx": "PB6"}),
                                     ("&usart2", {"tx": "PB6", "rx": "PB7"})):
            with self.subTest(controller=controller, selected=selected), self.assertRaises(ValueError):
                route(observed, chip, controller, selected)

    def test_fresh_controller_uses_package_electrical_defaults(self):
        observed, chip = fixture(kind="i2c", states=False)
        planned = route(observed, chip, "&i2c1", {"scl": "PB6", "sda": "PB7"})
        self.assertEqual(planned["updates"][0]["properties"]["clock-frequency"], "<100000>")
        for node in planned["pinctrl_nodes"]:
            self.assertIn("drive-open-drain;", node["source"])
            self.assertIn("bias-pull-up;", node["source"])

    def test_i2c_recovery_follows_new_pins_and_preserves_flags_and_speed(self):
        observed, chip = fixture(kind="i2c", body="clock-frequency = <400000>; scl-gpios = <&gpiob 8 6>; sda-gpios = <&gpiob 9 7>;")
        planned = route(observed, chip, "&i2c1", {"scl": "PB6", "sda": "PB7"})
        props = planned["updates"][0]["properties"]
        self.assertEqual(props["scl-gpios"], "<&gpiob 6 6>")
        self.assertEqual(props["sda-gpios"], "<&gpiob 7 7>")
        self.assertNotIn("clock-frequency", props)

    def test_i2c_does_not_copy_incompatible_push_pull_electrical_settings(self):
        observed, chip = fixture(kind="i2c")
        observed["document"] = SourceDocument.parse(observed["document"].source.replace(b"drive-open-drain", b"drive-push-pull"))
        item = profile(observed, chip)[0]
        self.assertFalse(item["available"])
        self.assertIn("open-drain", item["reason"])
        with self.assertRaisesRegex(ValueError, "open-drain"):
            route(observed, chip, "&i2c1", {"scl": "PB6", "sda": "PB7"})

    def test_hardware_spi_requires_and_preserves_nss(self):
        observed, chip = fixture(kind="spi")
        item = profile(observed, chip)[0]
        self.assertEqual([signal["id"] for signal in item["signals"]], ["sck", "mosi", "miso", "nss"])
        with self.assertRaisesRegex(ValueError, "companion"):
            route(observed, chip, "&spi1", {"sck": "PB3", "mosi": "PB5", "miso": "PB4"})
        planned = route(observed, chip, "&spi1", {"sck": "PB3", "mosi": "PB5", "miso": "PB4", "nss": "PA4"})
        self.assertEqual(len(planned["pinctrl_nodes"]), 4)
        self.assertIn("Hardware NSS", planned["notes"][1])

    def test_spi_gpio_chip_selects_are_preserved_without_manufacturing_consumer(self):
        observed, chip = fixture(kind="spi", states=False, body="cs-gpios = <&gpioa 4 1>;")
        item = profile(observed, chip)[0]
        self.assertEqual([signal["id"] for signal in item["signals"]], ["sck", "mosi", "miso"])
        planned = route(observed, chip, "&spi1", {"sck": "PB3", "mosi": "PB5", "miso": "PB4"})
        self.assertNotIn("cs-gpios", planned["updates"][0]["properties"])
        self.assertEqual(len(planned["updates"]), 1)
        self.assertEqual(len(planned["pinctrl_nodes"]), 3)

    def test_spi_route_cannot_reuse_its_preserved_gpio_chip_select(self):
        observed, chip = fixture(kind="spi", states=False, body="cs-gpios = <&gpiob 3 1>;")
        sck = next(signal for signal in profile(observed, chip)[0]["signals"] if signal["id"] == "sck")
        self.assertIn("PB3", sck["unavailable"])
        with self.assertRaisesRegex(ValueError, "preserved GPIO chip select"):
            route(observed, chip, "&spi1", {"sck": "PB3", "mosi": "PB5", "miso": "PB4"})

    def test_retained_active_route_does_not_increase_an_existing_cs_overlap(self):
        observed, chip = fixture(kind="spi", body="cs-gpios = <&gpioa 5 1>;")
        owner = next(row for row in observed["payload"]["nodes"] if row["reference"] == "&spi1")
        owner["status"] = "okay"
        sck = next(signal for signal in profile(observed, chip)[0]["signals"] if signal["id"] == "sck")
        self.assertNotIn("PA5", sck["unavailable"])
        planned = route(observed, chip, "&spi1", {"sck": "PA5", "mosi": "PB5", "miso": "PB4", "nss": "PA4"})
        self.assertIn("PA5", planned["pins"])

    def test_uart_flow_control_requires_rts_cts_companions(self):
        observed, chip = fixture(body="hw-flow-control;")
        item = profile(observed, chip)[0]
        self.assertEqual([signal["id"] for signal in item["signals"]], ["tx", "rx", "rts", "cts"])
        with self.assertRaisesRegex(ValueError, "companion"):
            route(observed, chip, "&usart1", {"tx": "PB6", "rx": "PB7"})
        planned = route(observed, chip, "&usart1", {"tx": "PB6", "rx": "PB7", "rts": "PA12", "cts": "PA11"})
        self.assertEqual(len(planned["pinctrl_nodes"]), 4)
        self.assertNotIn("hw-flow-control", planned["updates"][0]["properties"])

    def test_sleep_route_moves_companions_and_preserves_analogue_settings(self):
        observed, chip = fixture(sleep=True)
        planned = route(observed, chip, "&usart1", {"tx": "PB6", "rx": "PB7"}, label_prefix="zu_uart")
        self.assertEqual(planned["updates"][0]["properties"]["pinctrl-names"], '"default", "sleep"')
        self.assertEqual(planned["updates"][0]["properties"]["pinctrl-1"], "<&zu_uart_sleep_tx &zu_uart_sleep_rx>")
        self.assertIn("STM32_PINMUX('B', 6, ANALOG)", planned["pinctrl_nodes"][2]["source"])
        self.assertIn("bias-disable;", planned["pinctrl_nodes"][2]["source"])

    def test_unknown_electrical_or_mode_properties_do_not_gain_edit_authority(self):
        for changes in ({"custom_pin": "unverified-drive = <7>;"}, {"body": "single-wire;"}, {"body": "tx-rx-swap;"}):
            with self.subTest(changes=changes):
                observed, chip = fixture(**changes)
                item = profile(observed, chip)[0]
                self.assertFalse(item["available"])
                self.assertTrue(item["reason"])
                with self.assertRaises(ValueError):
                    route(observed, chip, "&usart1", {"tx": "PB6", "rx": "PB7"})

    def test_source_encoding_and_binding_changes_invalidate_the_profile(self):
        observed, chip = fixture()
        observed["payload"]["nodes"][0]["binding"] = {"state": "unknown"}
        owner = next(row for row in observed["payload"]["nodes"] if row["reference"] == "&usart1")
        owner["binding"] = {"state": "unknown"}
        self.assertFalse(profile(observed, chip)[0]["available"])
        observed["sources"].files["/workspace/stm32-pinctrl.h"]["text"] = HEADER.replace("STM32_LINE_SHIFT 5U", "STM32_LINE_SHIFT 6U")
        with self.assertRaisesRegex(ValueError, "fields changed"):
            profile(observed, chip)

    def test_debug_and_active_clock_pin_pairs_have_explicit_restrictions(self):
        observed, chip = fixture()
        restrictions = pin_restrictions(observed, chip)
        self.assertEqual(set(restrictions), {"PA13", "PA14", "PH0", "PH1"})
        self.assertIn("SWD", restrictions["PA13"][0])
        self.assertIn("HSE", restrictions["PH0"][0])

    def test_package_names_do_not_select_the_route_algorithm(self):
        for table in ("stm32f401r(d-e)tx-pinctrl.dtsi", "stm32l476rgtx-pinctrl.dtsi",
                      "stm32g071r(b-8)tx-pinctrl.dtsi", "stm32h743zitx-pinctrl.dtsi",
                      "stm32u575zitxq-pinctrl.dtsi", "stm32future123-pinctrl.dtsi"):
            with self.subTest(table=table):
                observed, chip = fixture(table_name=table)
                self.assertTrue(chip["supported"], chip["reason"])
                planned = route(observed, chip, "&usart1", {"tx": "PB6", "rx": "PB7"})
                self.assertIn("STM32_PINMUX('B', 6, AF7)", planned["pinctrl_nodes"][0]["source"])
                self.assertEqual(planned["updates"][0]["properties"]["status"], '"okay"')

    def test_shared_modern_controller_bindings_keep_complete_routes(self):
        for kind, compatible, controller, pins in (
            ("uart", "st,stm32-lpuart", "&usart1", {"tx": "PB6", "rx": "PB7"}),
            ("i2c", "st,stm32-i2c-v2", "&i2c1", {"scl": "PB6", "sda": "PB7"}),
            ("spi", "st,stm32-spi-fifo", "&spi1", {"sck": "PB3", "mosi": "PB5", "miso": "PB4", "nss": "PA4"}),
            ("spi", "st,stm32h7-spi", "&spi1", {"sck": "PB3", "mosi": "PB5", "miso": "PB4", "nss": "PA4"}),
        ):
            with self.subTest(compatible=compatible):
                observed, chip = fixture(kind=kind, compatible_override=compatible,
                                         table_name="stm32u575zitxq-pinctrl.dtsi")
                item = profile(observed, chip)[0]
                self.assertTrue(item["available"], item["reason"])
                self.assertEqual(item["kind"], kind)
                planned = route(observed, chip, controller, pins)
                self.assertEqual(set(planned["pins"]), set(pins.values()))
                self.assertEqual(len(planned["pinctrl_nodes"]), len(pins))

    def test_generic_packages_do_not_borrow_f446_oscillator_pad_locations(self):
        observed, chip = fixture(table_name="stm32g071r(b-8)tx-pinctrl.dtsi")
        self.assertEqual(set(pin_restrictions(observed, chip)), {"PA13", "PA14"})
        notes = pin_notes(observed, chip)
        self.assertIn("active HSE oscillator pad", notes["PH0"][0])
        self.assertIn("board's clock wiring", notes["PB6"][0])
        self.assertTrue(any("oscillator pad" in note for note in profile(observed, chip)[0]["notes"]))
        observed["payload"]["nodes"] = [dict(row, status="disabled") if "st,stm32-hse-clock" in row["compatible"] else row
                                        for row in observed["payload"]["nodes"]]
        self.assertEqual(pin_notes(observed, chip), {})

    def test_explicit_clock_roles_are_protected_from_the_selected_table(self):
        def clock_roles(table):
            table.update(rcc_osc_in_pf0=("F", 0, "ANALOG", ""), rcc_osc_out_pf1=("F", 1, "ANALOG", ""))
        observed, chip = fixture(table_name="stm32g071r(b-8)tx-pinctrl.dtsi", table_change=clock_roles)
        restrictions = pin_restrictions(observed, chip)
        self.assertEqual(set(restrictions), {"PA13", "PA14", "PF0", "PF1"})
        self.assertEqual(pin_notes(observed, chip), {})

    def test_pinctrl_remapping_restricts_only_the_affected_pins(self):
        observed, chip = fixture(table_name="stm32g071r(b-8)tx-pinctrl.dtsi", pinctrl_properties="remap-pa11; ")
        restrictions = pin_restrictions(observed, chip)
        self.assertIn("remap-pa11", restrictions["PA9"][0])
        self.assertIn("PA11", restrictions)
        self.assertNotIn("PB6", restrictions)
        route(observed, chip, "&usart1", {"tx": "PB6", "rx": "PB7"})
        with self.assertRaisesRegex(ValueError, "protected"):
            route(observed, chip, "&usart1", {"tx": "PA9", "rx": "PB7"})
        observed, chip = fixture(pinctrl_properties="remap-unknown; ")
        with self.assertRaisesRegex(ValueError, "unrecognized pin remap"):
            pin_restrictions(observed, chip)

    def test_specialized_pin_electrical_modes_remain_unavailable(self):
        for property_value in ("st,hslv-enable;", 'st,io-retime-edge = "both";'):
            with self.subTest(property_value=property_value):
                observed, chip = fixture(table_name="stm32h563zitx-pinctrl.dtsi", custom_pin=property_value)
                item = profile(observed, chip)[0]
                self.assertFalse(item["available"])
                self.assertIn("outside the supported STM32 route format", item["reason"])

    def test_stale_package_identity_cannot_reuse_a_previous_chip_observation(self):
        observed, chip = fixture()
        original = observed["sources"].files.pop("/workspace/stm32f446r(c-e)tx-pinctrl.dtsi")
        observed["sources"].files["/workspace/stm32l476rgtx-pinctrl.dtsi"] = original
        with self.assertRaisesRegex(ValueError, "package changed"):
            profile(observed, chip)

    def test_source_helper_keeps_existing_consumers_and_state_comments(self):
        observed, chip = fixture(sleep=True, body="current-speed = <57600>;")
        before = b'''// The connected sensor remains application data.\r
&usart1 {\r
    status = "disabled"; // Board console choice\r
    current-speed = <57600>;\r
};\r
&i2c1 {\r
    sensor@40 { compatible = "vendor,sensor"; reg = <0x40>; };\r
};\r
&pinctrl {\r
    custom: custom { pinmux = <7>; }; // Keep this state\r
};\r
'''
        draft = SourceDocument.parse(before, document_id="draft", revision=8)
        prefix = unique_prefix(draft, observed, "&usart1")
        plan = route(observed, chip, "&usart1", {"tx": "PB6", "rx": "PB7"}, label_prefix=prefix)
        updated = apply_activation(draft, observed, plan)
        self.assertEqual(updated.id, "draft")
        self.assertEqual(updated.revision, 9)
        self.assertFalse(updated.syntax.blocked)
        self.assertIn(b'status = "okay"; // Board console choice', updated.source)
        self.assertIn(b'current-speed = <57600>;', updated.source)
        self.assertIn(b'sensor@40 { compatible = "vendor,sensor"; reg = <0x40>; };', updated.source)
        self.assertIn(b'custom: custom { pinmux = <7>; }; // Keep this state', updated.source)
        self.assertIn(b'STM32_PINMUX(\'B\', 6, ANALOG)', updated.source)
        self.assertEqual(updated.source.count(b"#include <zephyr/dt-bindings/pinctrl/stm32-pinctrl.h>"), 1)
        self.assertNotIn(b"\n", updated.source.replace(b"\r\n", b""))
        self.assertEqual(draft.source, before)

    def test_source_helper_uses_new_pin_identity_and_one_controller_override(self):
        observed, chip = fixture()
        draft = SourceDocument.parse(b"// Existing overlay\n")
        selection = {"tx": "PB6", "rx": "PB7"}
        first_prefix = unique_prefix(draft, observed, "&usart1")
        first = apply_activation(draft, observed, route(observed, chip, "&usart1", selection, label_prefix=first_prefix))
        second_prefix = unique_prefix(first, observed, "&usart1")
        self.assertNotEqual(first_prefix, second_prefix)
        second = apply_activation(first, observed, route(observed, chip, "&usart1", selection, label_prefix=second_prefix))
        self.assertEqual(second.source.count(b"&usart1 {"), 1)
        self.assertEqual(second.source.count(b"#include <zephyr/dt-bindings/pinctrl/stm32-pinctrl.h>"), 1)
        self.assertIn((first_prefix + "_default_tx:").encode(), second.source)
        self.assertIn((second_prefix + "_default_tx:").encode(), second.source)
        owner = second.find_node("&usart1")
        value = second.property_value(second.find_property(owner.id, "pinctrl-0").id)
        self.assertEqual(list(value.value), ["&" + second_prefix + "_default_tx", "&" + second_prefix + "_default_rx"])


if __name__ == "__main__":
    unittest.main()
