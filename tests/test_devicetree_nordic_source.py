"""Synthetic Nordic source fixtures; no board files or hardware are required."""

from copy import deepcopy
import unittest

from zephyr_utilities.features.devicetree.nordic_source import NordicSource
from zephyr_utilities.features.devicetree.source import SourceDocument, SourceError


STATES = {
    "serial_default": b"""serial_default: serial_default {
    group1 { psels = <NRF_PSEL(UART_TX, 0, 6)>; /* retain transmit settings */ };
    group2 { psels = <NRF_PSEL(UART_RX, 0, 8)>; bias-pull-up; };
};""",
    "serial_sleep": b"""serial_sleep: serial_sleep {
    group1 { psels = <NRF_PSEL(UART_TX, 0, 6) NRF_PSEL(UART_RX, 0, 8)>; low-power-enable; };
};""",
    "other_default": b"""other_default: other_default {
    group1 { psels = <NRF_PSEL(UART_TX, 1, 2)>; };
};""",
    "other_sleep": b"""other_sleep: other_sleep {
    group1 { psels = <NRF_PSEL(UART_TX, 1, 2)>; low-power-enable; };
};""",
}
CONTROLLERS = [
    {"id": "uart0", "kind": "uart", "status": "okay", "instance": "instance0", "editable": True,
     "roles": ["UART_TX", "UART_RX", "UART_RTS", "UART_CTS"],
     "states": {"default": ["serial_default"], "sleep": ["serial_sleep"]}, "aliases": ["console_uart"]},
    {"id": "uart1", "kind": "uart", "status": "disabled", "instance": "instance1", "editable": True,
     "roles": ["UART_TX", "UART_RX", "UART_RTS", "UART_CTS"],
     "states": {"default": ["other_default"], "sleep": ["other_sleep"]}},
]


def adapter(source=b"// User overlay\n", *, states=None, controllers=None):
    document = source if isinstance(source, SourceDocument) else SourceDocument.parse(source, document_id="pins")
    return NordicSource(document, STATES if states is None else states, CONTROLLERS if controllers is None else controllers)


def selected(model, controller="uart0", state="default"):
    return next(item for row in model.project()["controllers"] if row["id"] == controller
                for item in row["states"] if item["name"] == state)


def change(model, role, pin, *, state="default", controller="uart0", split=False):
    planned = model.plan(controller, state, role, pin, split=split)
    return adapter(planned.apply(model.document), states=model.inherited_states, controllers=model.controllers)


LOCAL = b"""/* unrelated source */
&pinctrl {
    custom: custom /* keep header */ {
        group1 {
            psels = <NRF_PSEL(UART_TX, /* keep port */ 0x0, 0x6)>, <NRF_PSEL(UART_RTS, 0, 5)>;
            nordic,drive-mode = <KEEP_DRIVE>;
        };
        group2 { psels = <NRF_PSEL(UART_RX, 0, 8)>; bias-pull-up; };
    };
};
&uart0 { pinctrl-0 = <&custom>; };
"""


class NordicSourceTests(unittest.TestCase):
    def test_inherited_observation_is_read_only_and_decodes_states(self):
        model = adapter()
        before = model.document.source
        state = selected(model)
        self.assertTrue(state["known"])
        self.assertTrue(state["editable"])
        self.assertEqual([(item["role"], item["port"], item["pin"]) for item in state["signals"]],
                         [("UART_TX", 0, 6), ("UART_RX", 0, 8)])
        self.assertTrue(all(item["origin"] == "board" for item in state["signals"]))
        self.assertEqual(model.document.source, before)

    def test_inherited_change_copies_one_state_and_preserves_other_controller(self):
        model = adapter(b"// retained overlay\n&uart0 { current-speed = <115200>; };\n")
        plan = model.plan("uart0", "default", "UART_TX", {"port": 1, "pin": 4})
        result = plan.apply(model.document)
        self.assertEqual(result.revision, 1)
        self.assertEqual(plan.inverse().apply(result).source, model.document.source)
        self.assertIn(b"#include <zephyr/dt-bindings/pinctrl/nrf-pinctrl.h>", result.source)
        self.assertIn(b"NRF_PSEL(UART_TX, 1, 4)", result.source)
        self.assertIn(b"NRF_PSEL(UART_RX, 0, 8)", result.source)
        self.assertIn(b"/* retain transmit settings */", result.source)
        self.assertIn(b"current-speed = <115200>", result.source)
        self.assertEqual(selected(adapter(result), state="sleep")["refs"], ["serial_sleep"])
        self.assertEqual(selected(adapter(result), controller="uart1")["refs"], ["other_default"])
        self.assertNotIn(b"&uart1 {", result.source)

    def test_local_reassignment_preserves_comments_symbols_and_crlf(self):
        model = adapter(LOCAL.replace(b"\n", b"\r\n"))
        result = change(model, "UART_TX", {"port": 1, "pin": 12}).document.source
        self.assertIn(b"NRF_PSEL(UART_TX, /* keep port */ 0x1, 0xc)", result)
        self.assertIn(b"nordic,drive-mode = <KEEP_DRIVE>;", result)
        self.assertIn(b"custom: custom /* keep header */", result)
        self.assertNotIn(b"\n", result.replace(b"\r\n", b""))
        self.assertEqual(result.count(b"custom: custom"), 1)

    def test_inherited_copy_uses_overlay_newline_style(self):
        model = adapter(b"// CRLF overlay\r\n")
        result = change(model, "UART_TX", {"port": 1, "pin": 4}).document.source
        self.assertNotIn(b"\n", result.replace(b"\r\n", b""))

    def test_same_assignment_is_noop_and_add_role_creates_separate_group(self):
        model = adapter()
        plan = model.plan("uart0", "default", "UART_TX", {"port": 0, "pin": 6})
        self.assertEqual(plan.edits, ())
        result = change(model, "UART_RTS", {"port": 1, "pin": 6})
        self.assertEqual(len(selected(result)["signals"]), 3)
        self.assertIn(b"zephyr_utils_group", result.document.source)
        self.assertIn(b"bias-pull-up;", result.document.source)

    def test_sleep_assignment_creates_explicit_low_power_group(self):
        result = change(adapter(), "UART_CTS", {"port": 1, "pin": 7}, state="sleep")
        self.assertEqual(result.document.source.count(b"low-power-enable;"), 2)
        self.assertEqual(selected(result)["refs"], ["serial_default"])

    def test_unassign_removes_only_signal_then_exclusive_empty_groups(self):
        model = change(adapter(), "UART_CTS", {"port": 1, "pin": 7}, state="sleep")
        model = change(model, "UART_TX", None, state="sleep")
        self.assertEqual([item["role"] for item in selected(model, state="sleep")["signals"]], ["UART_RX", "UART_CTS"])
        model = change(model, "UART_CTS", None, state="sleep")
        self.assertNotIn(b"zephyr_utils_group", model.document.source)
        self.assertIn(b"low-power-enable", model.document.source)
        model = change(model, "UART_RX", None, state="sleep")
        self.assertEqual(selected(model, state="sleep")["signals"], [])
        self.assertEqual(selected(model, state="sleep")["refs"], [])
        self.assertIn(b"pinctrl-1 = <>;", model.document.source)
        self.assertNotIn(b"zephyr_utils_uart0_sleep:", model.document.source)

    def test_final_inherited_signal_unassignment_never_reveals_inheritance(self):
        model = change(adapter(), "UART_TX", None, controller="uart1")
        self.assertIn(b"pinctrl-0 = <>;", model.document.source)
        self.assertEqual(selected(model, controller="uart1")["signals"], [])
        plan = model.plan("uart1", "default", "UART_TX", None)
        self.assertEqual(plan.edits, ())
        model = change(model, "UART_RX", {"port": 1, "pin": 0}, controller="uart1")
        self.assertEqual([item["role"] for item in selected(model, controller="uart1")["signals"]], ["UART_RX"])

    def test_shared_local_state_requires_explicit_split_and_keeps_consumers(self):
        model = adapter(LOCAL + b"&uart1 { pinctrl-0 = <&custom>; };\n")
        self.assertTrue(selected(model)["shared"])
        with self.assertRaisesRegex(SourceError, "Explicitly split"):
            change(model, "UART_TX", {"port": 1, "pin": 9})
        result = change(model, "UART_TX", {"port": 1, "pin": 9}, split=True)
        original = model.document.find_node("&custom")
        self.assertIn(model.document.source[original.start:original.end], result.document.source)
        self.assertIn(b"&uart1 { pinctrl-0 = <&custom>; };", result.document.source)
        self.assertIn(b"/* keep header */", result.document.source)
        self.assertEqual(next(item["pin"] for item in selected(result, controller="uart1")["signals"] if item["role"] == "UART_TX"), 6)
        self.assertEqual(next(item["pin"] for item in selected(result)["signals"] if item["role"] == "UART_TX"), 9)

    def test_shared_inherited_and_default_sleep_refs_are_isolated(self):
        descriptors = deepcopy(CONTROLLERS)
        descriptors[0]["states"]["sleep"] = ["serial_default"]
        model = adapter(controllers=descriptors)
        self.assertEqual(selected(model)["consumers"], ["uart0/sleep"])
        result = change(model, "UART_TX", {"port": 1, "pin": 9}, split=True)
        self.assertEqual(selected(result, state="sleep")["refs"], ["serial_default"])
        self.assertEqual(selected(result, state="sleep")["signals"][0]["pin"], 6)

    def test_shared_final_removal_leaves_original_state(self):
        local = b"&pinctrl { shared: shared { g { psels = <NRF_PSEL(UART_TX, 1, 3)>; }; }; };\n"
        model = adapter(local + b"&uart0 { pinctrl-0 = <&shared>; }; &uart1 { pinctrl-0 = <&shared>; };\n")
        result = change(model, "UART_TX", None, split=True)
        self.assertIn(local, result.document.source)
        self.assertEqual(selected(result)["signals"], [])
        self.assertEqual(selected(result, controller="uart1")["signals"][0]["pin"], 3)

    def test_other_local_consumers_also_require_split(self):
        model = adapter(LOCAL + b"/ { other { state = <&custom>; }; };\n")
        self.assertIn("Other local source", selected(model)["consumers"])
        result = change(model, "UART_RX", {"port": 1, "pin": 6}, split=True)
        self.assertIn(b"state = <&custom>;", result.document.source)

    def test_unknown_psels_and_duplicate_roles_block_without_changes(self):
        for expression in (b"NRF_PSEL(UART_TX, PORT, 6)", b"0x123", b"NRF_PSEL_DISCONNECTED(UART_TX)",
                           b"NRF_PSEL(UART_TX, 0, 1) NRF_PSEL(UART_TX, 0, 2)"):
            with self.subTest(expression=expression):
                source = b"&pinctrl { custom: custom { g { psels = <" + expression + b">; }; }; }; &uart0 { pinctrl-0 = <&custom>; };"
                model = adapter(source)
                self.assertFalse(selected(model)["editable"])
                self.assertFalse(selected(model)["known"])
                with self.assertRaises(SourceError):
                    change(model, "UART_TX", {"port": 1, "pin": 5})
                self.assertEqual(model.document.source, source)

    def test_readonly_controller_assignments_still_have_known_pin_uses(self):
        descriptors = deepcopy(CONTROLLERS)
        descriptors[0].update(editable=False, reason="Unsupported controller profile")
        state = selected(adapter(controllers=descriptors))
        self.assertFalse(state["editable"])
        self.assertTrue(state["known"])
        self.assertEqual(len(state["signals"]), 2)

    def test_duplicate_contributors_and_opaque_status_are_blocked(self):
        for source in (b"&uart0 {}; &uart0 {};", b'&uart0 { status = STATUS; };',
                       b'&uart0 { pinctrl-0 = <&serial_default>; pinctrl-0 = <&other_default>; };',
                       b'/ { uart0: fake {}; };', b'&console_uart {}; &uart0 {};',
                       b'&uart0 { pinctrl-2 = <&serial_default>; };', b'&uart0 { pinctrl-00 = <&serial_default>; };'):
            with self.subTest(source=source):
                self.assertFalse(selected(adapter(source))["editable"])

    def test_multiple_state_references_preserve_untouched_reference(self):
        local = b"""&pinctrl {
            one: one { g { psels = <NRF_PSEL(UART_TX, 1, 3)>; }; };
            two: two { g { psels = <NRF_PSEL(UART_RX, 1, 4)>; }; };
        }; &uart0 { pinctrl-0 = <&one /* keep separator */ &two>; };"""
        model = adapter(local)
        result = change(model, "UART_RX", {"port": 1, "pin": 7})
        self.assertIn(b"pinctrl-0 = <&one /* keep separator */ &two>", result.document.source)
        self.assertIn(b"one: one { g { psels = <NRF_PSEL(UART_TX, 1, 3)>; }; };", result.document.source)

    def test_split_preserves_reference_comments_and_avoids_label_collision(self):
        source = LOCAL.replace(b"<&custom>", b"</* before */ &custom /* after */>")
        source += b"&uart1 { pinctrl-0 = <&custom>; }; / { zephyr_utils_uart0_default: reserved {}; };"
        result = change(adapter(source), "UART_RX", {"port": 1, "pin": 7}, split=True)
        self.assertIn(b"</* before */ &zephyr_utils_uart0_default_1 /* after */>", result.document.source)

    def test_split_with_descendant_labels_refuses_duplicate_label_generation(self):
        source = LOCAL.replace(b"group1 {", b"tag: group1 {") + b"&uart1 { pinctrl-0 = <&custom>; };"
        with self.assertRaisesRegex(SourceError, "descendant labels"):
            change(adapter(source), "UART_TX", {"port": 1, "pin": 7}, split=True)

    def test_direct_group_consumer_blocks_an_implicit_shared_group_edit(self):
        source = LOCAL.replace(b"group1 {", b"tag: group1 {") + b"/ { consumer { state = <&tag>; }; };"
        self.assertFalse(selected(adapter(source))["editable"])

    def test_repeated_pinctrl_state_or_group_paths_are_not_edited(self):
        sources = [
            b"&pinctrl { a: same {g {psels = <NRF_PSEL(UART_TX, 1, 2)>;};}; };"
            b"&pinctrl { b: same {g {psels = <NRF_PSEL(UART_TX, 1, 3)>;};}; };"
            b"&uart0 {pinctrl-0=<&a>;};",
            b"&pinctrl { a: same {g {psels = <NRF_PSEL(UART_TX, 1, 2)>;};"
            b"g {psels = <NRF_PSEL(UART_RX, 1, 3)>;};}; }; &uart0 {pinctrl-0=<&a>;};",
        ]
        for source in sources:
            with self.subTest(source=source):
                model = adapter(source)
                self.assertFalse(selected(model)["editable"])
                self.assertFalse(selected(model)["known"])
                with self.assertRaisesRegex(SourceError, "contributors"):
                    change(model, "UART_TX", {"port": 1, "pin": 4})
                self.assertEqual(model.document.source, source)

    def test_separate_pinctrl_blocks_with_distinct_states_remain_editable(self):
        model = adapter(b"&pinctrl { a: one {g {psels = <NRF_PSEL(UART_TX, 1, 2)>;};}; };"
                        b"&pinctrl { b: two {g {psels = <NRF_PSEL(UART_RX, 1, 3)>;};}; };"
                        b"&uart0 {pinctrl-0=<&a &b>;};")
        result = change(model, "UART_TX", {"port": 1, "pin": 4})
        self.assertEqual([signal["pin"] for signal in selected(result)["signals"]], [4, 3])

    def test_comment_shape_changes_and_invalid_roles_or_pins_are_rejected(self):
        model = adapter(LOCAL)
        with self.assertRaisesRegex(SourceError, "comments"):
            change(model, "UART_TX", None)
        for role, pin in (("PWM_OUT0", {"port": 0, "pin": 1}), ("UART_TX", {"port": 1, "pin": 16}),
                          ("UART_TX", {"port": 0, "pin": -1}), ("UART_TX", {"port": True, "pin": 1})):
            with self.subTest(role=role, pin=pin), self.assertRaises(SourceError):
                change(model, role, pin)

    def test_missing_states_are_known_empty_and_created_only_on_assignment(self):
        descriptors = deepcopy(CONTROLLERS)
        descriptors[0]["states"] = {}
        model = adapter(controllers=descriptors)
        self.assertTrue(selected(model)["known"])
        self.assertTrue(selected(model)["editable"])
        model = change(model, "UART_RX", {"port": 1, "pin": 4}, state="sleep")
        self.assertIn(b'pinctrl-names = "sleep";', model.document.source)
        self.assertEqual(selected(model)["signals"], [])
        self.assertEqual(selected(model, state="sleep")["signals"][0]["pin"], 4)
        model = change(model, "UART_TX", {"port": 1, "pin": 5})
        self.assertIn(b'pinctrl-names = "sleep", "default";', model.document.source)
        self.assertEqual(selected(model)["signals"][0]["pin"], 5)
        self.assertEqual(selected(model, state="sleep")["signals"][0]["pin"], 4)

    def test_empty_group_cleanup_preserves_comments_and_unknown_settings(self):
        for extra in (b"/* Keep this note. */", b"vendor,setting = <MACRO>;", b"nordic,drive-mode = <UNKNOWN_DRIVE>;"):
            with self.subTest(extra=extra):
                model = adapter(b"&pinctrl { custom: custom { g { psels = <NRF_PSEL(UART_TX, 1, 3)>; " + extra
                                + b" }; }; }; &uart0 { pinctrl-0 = <&custom>; };")
                with self.assertRaises(SourceError):
                    change(model, "UART_TX", None)


class NordicGpioSourceTests(unittest.TestCase):
    def test_symbolic_gpio_reassignment_and_flags_keep_unrelated_bytes(self):
        source = b'/ { led: led { gpios = <&gpio0 /* port */ 13 (GPIO_ACTIVE_LOW | GPIO_PULL_UP)>; label = "Keep"; }; };\n'
        model = adapter(source)
        row = model.gpio()[0]
        self.assertEqual(row["flags"], ["GPIO_ACTIVE_LOW", "GPIO_PULL_UP"])
        result = model.plan_gpio(row["node_id"], "gpios", 0, {"port": 1, "pin": 4}, row["flags"]).apply(model.document)
        self.assertIn(b'<&gpio1 /* port */ 4 (GPIO_ACTIVE_LOW | GPIO_PULL_UP)>', result.source)
        self.assertIn(b'label = "Keep";', result.source)
        self.assertIn(b'#include <zephyr/dt-bindings/gpio/gpio.h>', result.source)

    def test_gpio_final_unassign_suppresses_inheritance_then_reassigns(self):
        model = adapter(b'&led0 { gpios = <&gpio0 13 GPIO_ACTIVE_LOW>; label = "Keep"; };')
        row = model.gpio()[0]
        result = model.plan_gpio(row["node_id"], "gpios", 0, None, []).apply(model.document)
        self.assertIn(b"/delete-property/ gpios;", result.source)
        model = adapter(result)
        row = model.gpio()[0]
        self.assertTrue(row["editable"])
        self.assertIsNone(row["pin"])
        result = model.plan_gpio(row["node_id"], "gpios", 0, {"port": 1, "pin": 5}, ["GPIO_ACTIVE_HIGH"]).apply(model.document)
        self.assertIn(b"gpios = <&gpio1 5 GPIO_ACTIVE_HIGH>;", result.source)
        self.assertIn(b'label = "Keep";', result.source)

    def test_gpio_list_removes_only_chosen_entry(self):
        for index, retained in ((0, b"<&gpio1 5 GPIO_ACTIVE_HIGH>"), (1, b"<&gpio0 12 GPIO_ACTIVE_LOW>")):
            with self.subTest(index=index):
                model = adapter(b'/ { consumer { reset-gpios = <&gpio0 12 GPIO_ACTIVE_LOW>, <&gpio1 5 GPIO_ACTIVE_HIGH>; }; };')
                row = model.gpio()[index]
                result = model.plan_gpio(row["node_id"], "reset-gpios", index, None, []).apply(model.document)
                self.assertIn(b"reset-gpios = " + retained + b";", result.source)
                self.assertEqual(len(adapter(result).gpio()), 1)

    def test_shared_chip_select_slots_cannot_be_removed(self):
        model = adapter(b'&spi0 { cs-gpios = <&gpio0 12 GPIO_ACTIVE_LOW>, <&gpio1 5 GPIO_ACTIVE_HIGH>; };')
        row = model.gpio()[0]
        with self.assertRaisesRegex(SourceError, "renumber"):
            model.plan_gpio(row["node_id"], "cs-gpios", 0, None, [])

    def test_gpio_unknown_flags_or_provider_remain_readonly(self):
        for value in (b"<&gpio0 12 0x1234>", b"<&gpio0 12 CUSTOM_FLAG>", b"<&header 12 GPIO_ACTIVE_LOW>", b"<&gpio0 PIN GPIO_ACTIVE_LOW>"):
            with self.subTest(value=value):
                row = adapter(b"/ { led { gpios = " + value + b"; }; };").gpio()[0]
                self.assertFalse(row["editable"])

    def test_gpio_conflicting_or_unknown_flags_rejected_without_changes(self):
        model = adapter(b"/ { led { gpios = <&gpio0 12 0>; }; };")
        row = model.gpio()[0]
        self.assertEqual(row["flags"], ["GPIO_ACTIVE_HIGH"])
        for flags in ([], ["UNKNOWN"], ["GPIO_PULL_UP", "GPIO_PULL_DOWN"], ["GPIO_ACTIVE_HIGH", "GPIO_ACTIVE_LOW"]):
            with self.subTest(flags=flags), self.assertRaises(SourceError):
                model.plan_gpio(row["node_id"], "gpios", 0, {"port": 0, "pin": 1}, flags)

    def test_gpio_comment_removal_and_duplicate_consumers_block(self):
        model = adapter(b"&led0 { gpios = <&gpio0 /* keep */ 12 GPIO_ACTIVE_LOW>; };")
        with self.assertRaisesRegex(SourceError, "comments"):
            model.plan_gpio(model.gpio()[0]["node_id"], "gpios", 0, None, [])
        rows = adapter(b"&led0 { gpios = <&gpio0 12 GPIO_ACTIVE_LOW>; }; &led0 {};").gpio()
        self.assertFalse(rows[0]["editable"])

    def test_local_label_and_ancestor_reference_merges_block_both_contributors(self):
        sources = [
            b"/ { spare: spare {gpios=<&gpio1 2 GPIO_ACTIVE_LOW>;};};"
            b"&spare {gpios=<&gpio1 3 GPIO_ACTIVE_LOW>;};",
            b"/ { leds_alias: leds {led_0 {gpios=<&gpio1 2 GPIO_ACTIVE_LOW>;};};};"
            b"&leds_alias {led_0 {gpios=<&gpio1 3 GPIO_ACTIVE_LOW>;};};",
            b"/ { leds_alias: leds {led_0 {gpios=<&gpio1 2 GPIO_ACTIVE_LOW>;};};};"
            b'&leds_alias {status="disabled";};',
            b"/ { parent {leaf {gpios=<&gpio1 2 GPIO_ACTIVE_LOW>;};};};"
            b"/ { parent {leaf {gpios=<&gpio1 3 GPIO_ACTIVE_LOW>;};};};",
            b"first: &second {child {gpios=<&gpio1 2 GPIO_ACTIVE_LOW>;};}; second: &first {};",
            b"/ { repeated: one {gpios=<&gpio1 2 GPIO_ACTIVE_LOW>;}; repeated: two {gpios=<&gpio1 3 GPIO_ACTIVE_LOW>;}; };",
        ]
        for source in sources:
            with self.subTest(source=source):
                model = adapter(source)
                rows = model.gpio()
                self.assertTrue(rows)
                self.assertTrue(all(not row["editable"] for row in rows))
                for row in rows:
                    with self.assertRaisesRegex(SourceError, "contributors"):
                        model.plan_gpio(row["node_id"], row["property"], row["index"], {"port": 1, "pin": 5}, ["GPIO_ACTIVE_LOW"])
                self.assertEqual(model.document.source, source)

    def test_excessive_local_alias_chains_are_bounded(self):
        source = b"&alias0 {gpios=<&gpio1 2 GPIO_ACTIVE_LOW>;};\n" + b"".join(
            f"alias{index}: &alias{index + 1} {{}};\n".encode("ascii") for index in range(160))
        row = adapter(source).gpio()[0]
        self.assertFalse(row["editable"])
        self.assertIn("contributors", row["reason"])

    def test_unique_local_gpio_declaration_remains_editable(self):
        model = adapter(b"/ { spare: spare {gpios=<&gpio1 2 GPIO_ACTIVE_LOW>;}; other {}; };")
        row = model.gpio()[0]
        self.assertTrue(row["editable"])
        result = model.plan_gpio(row["node_id"], "gpios", 0, {"port": 1, "pin": 5}, row["flags"]).apply(model.document)
        self.assertIn(b"gpios=<&gpio1 5 GPIO_ACTIVE_LOW>", result.source)


if __name__ == "__main__":
    unittest.main()
