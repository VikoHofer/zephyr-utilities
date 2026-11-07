"""Complete Nordic routes preserve source-derived states and refuse unknown inputs."""

from copy import deepcopy
from types import SimpleNamespace
import unittest

from zephyr_utilities.features.devicetree.binding_service import _literal
from zephyr_utilities.features.devicetree.chip_catalog import observe_chip
from zephyr_utilities.features.devicetree.chip_nordic import pin_notes, pin_restrictions, profile, route
from zephyr_utilities.features.devicetree.chip_source import apply_activation
from zephyr_utilities.features.devicetree.source import SourceDocument

from test_devicetree_chip_catalog import NRF_HEADER


HEADER = NRF_HEADER + """
#define NRF_FUN_UART_RTS 2U
#define NRF_FUN_UART_CTS 3U
#define NRF_FUN_SPIM_MOSI 5U
#define NRF_FUN_SPIM_MISO 6U
#define NRF_PIN_DISCONNECTED NRF_PIN_MSK
#define NRF_PSEL_DISCONNECTED(fun) (NRF_PIN_DISCONNECTED | ((NRF_FUN_ ## fun & NRF_FUN_MSK) << NRF_FUN_POS))
"""
UART_STATES = """
    uart_default: uart_default {
        output { psels = <6 33554437>; };
        input { psels = <16777224 50331655>; bias-pull-up; };
    };
    uart_sleep: uart_sleep {
        input { psels = <16777224 33554437 50331655>; low-power-enable; };
        output { psels = <6>; low-power-enable; bias-pull-up; nordic,drive-mode = <3>; };
    };
"""
UART = """
    uart0: uart@40002000 {
        compatible = "nordic,nrf-uarte";
        reg = <0x40002000 0x1000>;
        status = "okay";
        current-speed = <115200>;
        pinctrl-0 = <&uart_default>;
        pinctrl-1 = <&uart_sleep>;
        pinctrl-names = "default", "sleep";
    };
"""


def fixture(controllers=UART, states=UART_STATES, *, gpio="gpio-reserved-ranges = <6 1>, <8 1>;", uicr="gpio-as-nreset;", header=HEADER,
            soc="nrf52840", package="qiaa", ports=((0, 32), (1, 16)), extra="", core=None):
    gpio_text = "".join(f'gpio{port}: gpio{port} {{ compatible = "nordic,nrf-gpio"; #gpio-cells = <2>; gpio-controller; port = <{port}>; '
                        + (gpio + (f'ngpios = <{count}>;' if count != 32 and 'ngpios' not in gpio else '')
                           if port == 0 else f'ngpios = <{count}>;') + ' }; ' for port, count in ports)
    nfc = 'nfct: nfct { compatible = "nordic,nrf-nfct"; status = "disabled"; };' if soc in {"nrf52832", "nrf52833", "nrf52840", "nrf5340"} else ""
    text = ('/ { #address-cells = <1>; #size-cells = <1>; pinctrl: pinctrl { compatible = "nordic,nrf-pinctrl"; ' + states + ' }; '
            + gpio_text + nfc + 'uicr: uicr { compatible = "nordic,nrf-uicr"; ' + uicr + ' }; ' + controllers + extra + ' };')
    document = SourceDocument.parse(text.encode())
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
        count = _literal(document, document.find_node(provider["path"]), "ngpios")
        count = count.value[0] if count and count.kind == "cells" and len(count.value) == 1 else 32 if count is None else None
        provider.update(cells=["pin", "flags"], pin_count=count, supported=True,
                        pin_count_origin="explicit" if _literal(document, document.find_node(provider["path"]), "ngpios") else "binding-default")
        provider["properties"]["#gpio-cells"] = 2
        provider["properties"]["port"] = _literal(document, document.find_node(provider["path"]), "port").value[0]
    for row in nodes:
        node = document.find_node(row["path"])
        for index, name in enumerate(("default", "sleep")):
            refs = _literal(document, node, f"pinctrl-{index}")
            if refs and refs.kind == "phandles":
                topology["pinctrl_states"].append({"owner_id": row["id"], "name": name,
                    "targets": ["hardware:" + document._path(document.find_node(ref)) for ref in refs.value]})
    sources = SimpleNamespace(files={
        f"/workspace/{soc}.dtsi": {"text": "/ {};"},
        f"/workspace/{soc}_{core + '_' if core else ''}{package}.dtsi": {"text": f"#include <nordic/{soc}.dtsi>"},
        "/workspace/nrf-pinctrl.h": {"text": header},
    }, context={"target": {"requested": f"board/{soc}" + (f"/{core}" if core else ""), "qualifiers": [soc, *([core] if core else [])]}})
    observed = {"sources": sources, "payload": topology, "document": document}
    return observed, observe_chip(sources, topology, document)


class NordicActivationTests(unittest.TestCase):
    def test_uart_preserves_companion_signals_and_reserved_current_pin_choices(self):
        observed, chip = fixture()
        controller = profile(observed, chip)[0]
        self.assertTrue(controller["available"], controller["reason"])
        self.assertEqual(controller["id"], "&uart0")
        self.assertEqual(controller["current"], {"tx": "P0.06", "rx": "P0.08", "rts": "P0.05", "cts": "P0.07"})
        signals = {item["id"]: item for item in controller["signals"]}
        self.assertEqual(set(signals), {"tx", "rx", "rts", "cts"})
        self.assertIn("P0.06", signals["tx"]["candidates"])
        self.assertNotIn("P0.06", signals["rx"]["candidates"])
        self.assertNotIn("P0.09", signals["tx"]["candidates"])
        self.assertNotIn("P0.18", signals["tx"]["candidates"])
        self.assertNotIn("P1.16", signals["tx"]["candidates"])

    def test_route_clones_default_sleep_and_keeps_per_signal_electrical_settings(self):
        observed, chip = fixture()
        before = observed["document"].source
        selected = {"tx": "P1.00", "rx": "P1.08", "rts": "P0.05", "cts": "P0.07"}
        result = route(observed, chip, "&uart0", selected, label_prefix="zu_uart0_1")
        self.assertEqual(observed["document"].source, before)
        self.assertEqual(result["pins"], list(selected.values()))
        self.assertEqual(result["includes"], ["zephyr/dt-bindings/pinctrl/nrf-pinctrl.h"])
        self.assertEqual(result["updates"], [{"reference": "&uart0", "properties": {
            "status": '"okay"', "pinctrl-0": "<&zu_uart0_1_default>", "pinctrl-1": "<&zu_uart0_1_sleep>",
            "pinctrl-names": '"default", "sleep"'}}])
        default, sleep = [node["source"] for node in result["pinctrl_nodes"]]
        self.assertIn("NRF_PSEL(UART_TX, 1, 0)", default)
        self.assertIn("NRF_PSEL(UART_RX, 1, 8)", default)
        parsed = SourceDocument.parse(("&pinctrl {" + default + sleep + "};").encode())
        tx_default = parsed.find_node("&zu_uart0_1_default")
        tx_default = parsed.node(tx_default.children[0])
        self.assertFalse(any(prop.name == "bias-pull-up" for prop in tx_default.properties))
        tx_sleep = parsed.find_node("&zu_uart0_1_sleep")
        tx_sleep = parsed.node(tx_sleep.children[0])
        self.assertEqual({prop.name for prop in tx_sleep.properties}, {"psels", "bias-pull-up", "low-power-enable", "nordic,drive-mode"})
        self.assertEqual(_literal(parsed, tx_sleep, "nordic,drive-mode").value, (3,))

    def test_all_master_compatible_variants_are_offered_even_when_disabled(self):
        controllers = "".join(f'{name}: dev@{address:x} {{ compatible = "{compatible}"; reg = <0x{address:x} 0x1000>; status = "disabled"; }};'
                              for name, compatible, address in (
                                  ("uart0", "nordic,nrf-uart", 0x40002000), ("uart1", "nordic,nrf-uarte", 0x40028000),
                                  ("i2c0", "nordic,nrf-twi", 0x40003000), ("i2c1", "nordic,nrf-twim", 0x40004000),
                                  ("spi2", "nordic,nrf-spi", 0x40023000), ("spi3", "nordic,nrf-spim", 0x4002f000)))
        observed, chip = fixture(controllers, "")
        result = profile(observed, chip)
        self.assertEqual({item["id"] for item in result}, {"&uart0", "&uart1", "&i2c0", "&i2c1", "&spi2", "&spi3"})
        self.assertTrue(all(item["available"] for item in result))
        for controller in result:
            signals = {item["id"]: "P1." + str(index).zfill(2) for index, item in enumerate(controller["signals"])}
            result = route(observed, chip, controller, signals, label_prefix="zu_test")
            self.assertIn("low-power-enable;", result["pinctrl_nodes"][1]["source"])
            if controller["kind"] == "uart":
                self.assertEqual(result["updates"][0]["properties"]["current-speed"], "<115200>")

    def test_shared_hardware_instance_cannot_be_activated_silently(self):
        controllers = '''i2c0: i2c@40003000 { compatible = "nordic,nrf-twi"; reg = <0x40003000 0x1000>; status = "okay"; };
            spi0: spi@40003000 { compatible = "nordic,nrf-spi"; reg = <0x40003000 0x1000>; status = "disabled"; };'''
        observed, chip = fixture(controllers, "")
        spi = next(item for item in profile(observed, chip) if item["id"] == "&spi0")
        self.assertFalse(spi["available"])
        self.assertEqual(spi["blocked_by"], ["&i2c0"])
        self.assertEqual(spi["shared_with"], ["&i2c0"])
        with self.assertRaisesRegex(ValueError, "hardware instance"):
            route(observed, chip, "&spi0", {"sck": "P1.00", "mosi": "P1.08", "miso": "P1.09"})

    def test_slave_mode_peer_blocks_the_master_instance(self):
        controllers = '''i2c0: i2c@40003000 { compatible = "nordic,nrf-twis"; reg = <0x40003000 0x1000>; status = "okay"; };
            spi0: spi@40003000 { compatible = "nordic,nrf-spi"; reg = <0x40003000 0x1000>; status = "disabled"; };'''
        observed, chip = fixture(controllers, "")
        selected = profile(observed, chip)
        self.assertEqual(len(selected), 1)
        self.assertFalse(selected[0]["available"])
        self.assertEqual(selected[0]["blocked_by"], ["&i2c0"])

    def test_missing_duplicate_unknown_and_reserved_pin_selections_fail(self):
        observed, chip = fixture()
        valid = {"tx": "P1.00", "rx": "P1.08", "rts": "P0.05", "cts": "P0.07"}
        for changes in ({"tx": "P1.16"}, {"rx": "P1.00"}, {"tx": "P0.09"}, {"tx": [0, 6]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                route(observed, chip, "&uart0", {**valid, **changes})
        with self.assertRaisesRegex(ValueError, "every signal"):
            route(observed, chip, "&uart0", {"tx": "P1.00", "rx": "P1.08"})
        with self.assertRaises(ValueError):
            route(observed, chip, "&other", valid)

    def test_disabled_controller_does_not_inherit_permission_to_use_reserved_pins(self):
        observed, chip = fixture(UART.replace('status = "okay"', 'status = "disabled"'))
        selected = profile(observed, chip)[0]
        self.assertNotIn("P0.06", next(signal for signal in selected["signals"] if signal["id"] == "tx")["candidates"])
        with self.assertRaisesRegex(ValueError, "available package pin"):
            route(observed, chip, selected, selected["current"])

    def test_ambiguous_extra_and_unknown_electrical_states_block_route_generation(self):
        for changed_states, changed_controller in (
            (UART_STATES.replace("bias-pull-up;", "input-enable;", 1), UART),
            (UART_STATES.replace("<6 33554437>", "<6 6 33554437>"), UART),
            (UART_STATES, UART.replace('"default", "sleep"', '"default", "idle"')),
            (UART_STATES.replace("nordic,drive-mode = <3>", "nordic,drive-mode = <8>"), UART),
            (UART_STATES.replace("bias-pull-up;", "bias-pull-up; bias-pull-down;", 1), UART),
        ):
            with self.subTest(states=changed_states, controller=changed_controller):
                observed, chip = fixture(changed_controller, changed_states)
                selected = profile(observed, chip)[0]
                self.assertFalse(selected["available"])
                self.assertTrue(selected["reason"])

    def test_flow_control_and_disabled_reception_are_handled_explicitly(self):
        observed, chip = fixture(UART.replace('current-speed = <115200>;', 'current-speed = <115200>; hw-flow-control; disable-rx;'))
        selected = profile(observed, chip)[0]
        self.assertEqual({signal["id"] for signal in selected["signals"]}, {"tx", "rx", "rts", "cts"})
        self.assertFalse(selected["route_complete"])
        result = route(observed, chip, selected, selected["current"])
        properties = result["updates"][0]["properties"]
        self.assertIsNone(properties["disable-rx"])
        self.assertNotIn("hw-flow-control", properties)
        self.assertNotIn("current-speed", properties)

    def test_source_configuration_controls_nfc_reset_and_gpio_restrictions(self):
        observed, chip = fixture()
        blocked = pin_restrictions(observed, chip)
        self.assertTrue(blocked["P0.09"])
        self.assertTrue(blocked["P0.18"])
        self.assertTrue(blocked["P0.06"])
        changed, changed_chip = fixture(gpio="", uicr="nfct-pins-as-gpios;")
        allowed = pin_restrictions(changed, changed_chip)
        self.assertEqual(allowed["P0.09"], [])
        self.assertEqual(allowed["P0.18"], [])
        self.assertEqual(allowed["P0.06"], [])
        notes = pin_notes(changed, changed_chip)
        self.assertIn("10 kHz", notes["P0.09"][0])
        self.assertNotIn("P1.00", notes)

    def test_unknown_gpio_reservations_and_ranges_block_new_routes(self):
        for gpio in ("gpio-reserved-ranges = <1 2 3>;", "gpio-reserved-ranges = <31 2>;",
                     "gpio-reserved-ranges = <2 0>;", 'gpio-reserved-ranges = "unknown";',
                     "ngpios = <33>;", 'ngpios = "unknown";'):
            with self.subTest(gpio=gpio), self.assertRaises(ValueError):
                observed, chip = fixture(gpio=gpio)
                profile(observed, chip)
        observed, chip = fixture(gpio="ngpios = <31>;")
        self.assertNotIn("P0.31", pin_restrictions(observed, chip))

    def test_changed_encoding_or_package_is_not_authorized_by_an_old_chip_payload(self):
        observed, chip = fixture()
        observed["sources"].files["/workspace/nrf-pinctrl.h"]["text"] = HEADER.replace("NRF_FUN_SPIM_MISO 6U", "NRF_FUN_SPIM_MISO 66U")
        with self.assertRaisesRegex(ValueError, "encodings changed"):
            profile(observed, chip)
        observed, chip = fixture()
        chip["chip"]["package"] = "QFAA"
        with self.assertRaisesRegex(ValueError, "source inventory changed"):
            profile(observed, chip)

    def test_existing_state_labels_and_shared_states_are_never_overwritten(self):
        observed, chip = fixture()
        selected = profile(observed, chip)[0]
        before = deepcopy(observed["payload"]), observed["document"].source
        with self.assertRaisesRegex(ValueError, "already exists"):
            route(observed, chip, selected, selected["current"], label_prefix="uart")
        plan = route(observed, chip, selected, selected["current"], label_prefix="fresh")
        self.assertEqual((observed["payload"], observed["document"].source), before)
        self.assertEqual([item["reference"] for item in plan["updates"]], ["&uart0"])
        self.assertTrue(all(node["label"].startswith("fresh_") for node in plan["pinctrl_nodes"]))

    def test_spi_keeps_chip_select_and_speed_source_properties(self):
        controllers = '''spi0: spi@40003000 { compatible = "nordic,nrf-spi"; reg = <0x40003000 0x1000>;
            status = "disabled"; cs-gpios = <&gpio1 9 1>; max-frequency = <8000000>; };'''
        observed, chip = fixture(controllers, "")
        result = route(observed, chip, "&spi0", {"sck": "P1.00", "mosi": "P1.08", "miso": "P0.27"})
        properties = result["updates"][0]["properties"]
        self.assertNotIn("cs-gpios", properties)
        self.assertNotIn("max-frequency", properties)
        self.assertIn("NRF_PSEL(SPIM_MISO, 0, 27)", result["pinctrl_nodes"][0]["source"])

    def test_explicit_disconnection_in_sleep_is_preserved(self):
        observed, chip = fixture(states=UART_STATES.replace("psels = <16777224 33554437 50331655>", "psels = <16777727 33554437 50331655>"))
        selected = profile(observed, chip)[0]
        result = route(observed, chip, selected, selected["current"])
        self.assertIn("NRF_PSEL_DISCONNECTED(UART_RX)", result["pinctrl_nodes"][1]["source"])

    def test_generated_states_roundtrip_through_source_helper_without_replacing_saved_data(self):
        observed, chip = fixture()
        selected = profile(observed, chip)[0]
        original = SourceDocument.parse(b'/* retain imported declarations */\n/ { sensor-note { label = "existing sensor"; }; };\n')
        plan = route(observed, chip, selected, {**selected["current"], "tx": "P1.00"}, label_prefix="roundtrip_uart0")
        result = apply_activation(original, observed, plan)
        self.assertEqual(result.revision, original.revision + 1)
        self.assertFalse(result.syntax.blocked)
        self.assertIn(b'/* retain imported declarations */', result.source)
        self.assertEqual(_literal(result, result.find_node("/sensor-note"), "label").value, "existing sensor")
        uart = result.find_node("&uart0")
        self.assertEqual(_literal(result, uart, "pinctrl-0").value, ("&roundtrip_uart0_default",))
        self.assertEqual(_literal(result, uart, "pinctrl-1").value, ("&roundtrip_uart0_sleep",))
        self.assertIn(b"NRF_PSEL(UART_TX, 1, 0)", result.source)
        self.assertEqual(len(result.find_node("&roundtrip_uart0_default").children), 4)
        self.assertNotIn(b"roundtrip_uart0", original.source)

    def test_multiple_nordic_families_derive_complete_routes_from_their_gpio_sources(self):
        controllers = '''uart0: uart@2000 { compatible = "nordic,nrf-uarte"; reg = <0x2000 0x1000>; status = "disabled"; };
            i2c0: i2c@3000 { compatible = "nordic,nrf-twim"; reg = <0x3000 0x1000>; status = "disabled"; };
            spi0: spi@4000 { compatible = "nordic,nrf-spim"; reg = <0x4000 0x1000>; status = "disabled"; };'''
        for soc, ports, core in (("nrf51822", ((0, 32),), None), ("nrf52810", ((0, 32),), None),
                                 ("nrf52832", ((0, 32),), None), ("nrf52833", ((0, 32), (1, 10)), None),
                                 ("nrf5340", ((0, 32), (1, 16)), "cpuapp"), ("nrf9160", ((0, 32),), None),
                                 ("nrf9161", ((0, 32),), None)):
            with self.subTest(soc=soc):
                observed, chip = fixture(controllers, "", soc=soc, package="", ports=ports, core=core, uicr="", gpio="")
                self.assertTrue(chip["supported"], chip.get("reason"))
                inventory = {pin["id"] for pin in chip["pins"]}
                self.assertEqual(len(inventory), sum(count for _, count in ports))
                found = profile(observed, chip)
                self.assertEqual({row["kind"] for row in found}, {"uart", "i2c", "spi"})
                for controller in found:
                    self.assertTrue(controller["available"], controller["reason"])
                    self.assertTrue(all(set(signal["candidates"]) <= inventory for signal in controller["signals"]))
                    selection = {signal["id"]: f"P0.{20 + index:02d}" for index, signal in enumerate(controller["signals"])}
                    generated = route(observed, chip, controller, selection)
                    self.assertIn("NRF_PSEL(", generated["pinctrl_nodes"][0]["source"])
                    self.assertEqual(generated["pins"], list(selection.values()))

    def test_dedicated_nfc_reset_and_package_advice_are_not_copied_between_models(self):
        observed, chip = fixture(soc="nrf52832", ports=((0, 32),))
        restrictions = pin_restrictions(observed, chip)
        self.assertTrue(restrictions["P0.21"])
        self.assertEqual(restrictions["P0.18"], [])
        self.assertTrue(restrictions["P0.09"])
        self.assertNotIn("P0.09", pin_notes(observed, chip))
        observed, chip = fixture(soc="nrf5340", core="cpuapp", uicr="")
        restrictions = pin_restrictions(observed, chip)
        self.assertTrue(restrictions["P0.02"])
        self.assertTrue(restrictions["P0.03"])
        self.assertEqual(restrictions["P0.09"], [])
        self.assertEqual(restrictions["P0.18"], [])
        observed, chip = fixture(soc="nrf52810", ports=((0, 32),), gpio="", uicr="")
        self.assertEqual(pin_restrictions(observed, chip)["P0.09"], [])
        observed, chip = fixture(soc="nrf9160", ports=((0, 32),), gpio="", uicr="")
        self.assertEqual(pin_notes(observed, chip), {})
        self.assertEqual(pin_restrictions(observed, chip)["P0.00"], [])

    def test_clock_sharing_is_visible_advice_without_invented_kconfig_reservations(self):
        observed, chip = fixture(gpio="", uicr="")
        self.assertEqual(pin_restrictions(observed, chip)["P0.00"], [])
        self.assertIn("Kconfig", pin_notes(observed, chip)["P0.00"][0])
        controller = profile(observed, chip)[0]
        result = route(observed, chip, controller, {**controller["current"], "tx": "P0.00"})
        self.assertTrue(any("P0.00" in note and "Kconfig" in note for note in result["notes"]))
        self.assertFalse(any("10 kHz" in note and "P0.00" in note for note in result["notes"]))

    def test_other_core_and_nonsecure_images_need_controlling_image_ownership(self):
        for core, expected in (("cpunet", "controlling core"), ("cpuapp/ns", "secure image")):
            with self.subTest(core=core):
                observed, chip = fixture(soc="nrf5340", core=core, uicr="")
                self.assertTrue(chip["supported"])
                with self.assertRaisesRegex(ValueError, expected):
                    profile(observed, chip)

    def test_unselected_board_metadata_does_not_change_core_or_security_ownership(self):
        observed, chip = fixture(soc="nrf5340", core="cpuapp", uicr="")
        observed["sources"].files.update({
            "/workspace/nrf5340dk_nrf5340_cpuapp_ns.yaml": {"text": "identifier: board/nrf5340/cpuapp/ns"},
            "/workspace/nrf5340dk_nrf5340_cpunet.yaml": {"text": "identifier: board/nrf5340/cpunet"},
            "/workspace/nrf54l15.h": {"text": "/* unrelated metadata */"},
        })
        controller = profile(observed, chip)[0]
        self.assertTrue(controller["available"], controller["reason"])
        observed["sources"].files["/workspace/nrf5340_cpuapp_ns.dtsi"] = {"text": "/* selected non-secure include */"}
        with self.assertRaisesRegex(ValueError, "secure image"):
            profile(observed, chip)

    def test_dedicated_domain_and_explicit_security_rules_remain_visible_unavailable(self):
        for soc, extra, expected in (
                ("nrf54l15", "", "cross-domain"),
                ("nrf54h20", 'domain_uicr { compatible = "nordic,nrf-uicr-v2"; domain = <1>; };', "domain ownership"),
                ("nrf5340", 'secure { secure-gpios = <&gpio0 12 0>; };', "security"),
                ("nrf99999", "", "ownership model")):
            with self.subTest(soc=soc, extra=extra):
                observed, chip = fixture(soc=soc, uicr="", extra=extra)
                self.assertTrue(chip["supported"], chip.get("reason"))
                with self.assertRaisesRegex(ValueError, expected):
                    pin_restrictions(observed, chip)

    def test_app_core_gpio_forwarding_is_a_hard_pin_reservation(self):
        forwarder = '''forwarder { compatible = "nordic,nrf-gpio-forwarder";
            network_uart { gpios = <&gpio0 12 0>, <&gpio1 0 0>; }; };'''
        observed, chip = fixture(soc="nrf5340", core="cpuapp", uicr="", gpio="", extra=forwarder)
        restrictions = pin_restrictions(observed, chip)
        for name in ("P0.12", "P1.00"):
            self.assertIn("forwarded", restrictions[name][0])
            self.assertTrue(all(name not in signal["candidates"] for signal in profile(observed, chip)[0]["signals"]))
        disabled, disabled_chip = fixture(soc="nrf5340", core="cpuapp", uicr="", gpio="",
                                          extra=forwarder.replace('compatible = "nordic,nrf-gpio-forwarder";',
                                                                  'compatible = "nordic,nrf-gpio-forwarder"; status = "disabled";'))
        self.assertEqual(pin_restrictions(disabled, disabled_chip)["P0.12"], [])

    def test_undecoded_forwarding_never_claims_free_gpio_pins(self):
        for gpios in ('"unknown"', '<&gpio1 16 0>', '<&gpio0 12>'):
            with self.subTest(gpios=gpios):
                observed, chip = fixture(soc="nrf5340", core="cpuapp", uicr="", extra='''
                    forwarder { compatible = "nordic,nrf-gpio-forwarder"; network { gpios = ''' + gpios + '; }; };')
                with self.assertRaises(ValueError):
                    pin_restrictions(observed, chip)

    def test_state_pin_range_is_taken_from_current_gpio_inventory(self):
        observed, chip = fixture(ports=((0, 32), (1, 4)), states=UART_STATES.replace('<6 33554437>', '<40 33554437>'))
        controller = profile(observed, chip)[0]
        self.assertFalse(controller["available"])
        self.assertIn("observed GPIO inventory", controller["reason"])

    def test_explicit_controller_port_limits_alternatives(self):
        observed, chip = fixture(UART.replace('current-speed = <115200>;', 'current-speed = <115200>; default-gpio-port = <&gpio1>;'))
        controller = profile(observed, chip)[0]
        self.assertTrue(controller["available"], controller["reason"])
        tx = next(signal for signal in controller["signals"] if signal["id"] == "tx")
        self.assertIn("P0.06", tx["candidates"])
        self.assertNotIn("P0.12", tx["candidates"])
        self.assertIn("P1.00", tx["candidates"])
        observed, chip = fixture(UART.replace('current-speed = <115200>;', 'current-speed = <115200>; cross-domain-pins-supported;'))
        controller = profile(observed, chip)[0]
        self.assertFalse(controller["available"])
        self.assertIn("Cross-domain", controller["reason"])

    def test_shared_instances_decode_parent_bus_cell_widths_and_address_spaces(self):
        controllers = '''bus0 { #address-cells = <2>; #size-cells = <2>;
            i2c0: i2c@100003000 { compatible = "nordic,nrf-twim"; reg = <1 0x3000 0 0x1000>; };
            spi0: spi@100003000 { compatible = "nordic,nrf-spim"; reg = <1 0x3000 0 0x1000>; status = "disabled"; };
            spi1: spi@3000 { compatible = "nordic,nrf-spim"; reg = <0 0x3000 0 0x1000>; status = "disabled"; };
        }; bus1 { #address-cells = <2>; #size-cells = <2>;
            spi2: spi@100003000 { compatible = "nordic,nrf-spim"; reg = <1 0x3000 0 0x1000>; status = "disabled"; };
        };'''
        observed, chip = fixture(controllers, "")
        controllers = {row["id"]: row for row in profile(observed, chip)}
        self.assertEqual(controllers["&spi0"]["blocked_by"], ["&i2c0"])
        self.assertFalse(controllers["&spi0"]["available"])
        self.assertTrue(controllers["&spi1"]["available"])
        self.assertTrue(controllers["&spi2"]["available"])

    def test_unresolved_active_instance_blocks_unchecked_shared_hardware(self):
        controllers = '''i2c0: i2c@3000 { compatible = "nordic,nrf-twis"; reg = <0 0x3000 0x1000>; };
            spi0: spi@3000 { compatible = "nordic,nrf-spi"; reg = <0x3000 0x1000>; status = "disabled"; };'''
        observed, chip = fixture(controllers, "")
        controller = profile(observed, chip)[0]
        self.assertFalse(controller["available"])
        self.assertIn("cannot be checked", controller["reason"])


if __name__ == "__main__":
    unittest.main()
