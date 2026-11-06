"""CAN diagnostics and removals retain explicit source limits and dependencies."""

import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.can_resources import can_diagnostics

import test_devicetree_peripherals as fixtures


CAN_BINDING = '''compatible: zephyr,can-loopback
include: base.yaml
properties:
  bitrate: {type: int}
  bus-speed: {type: int, deprecated: true}
  sample-point: {type: int}
  bitrate-data: {type: int}
  sample-point-data: {type: int}
  phys: {type: phandle}
  clocks: {type: phandle-array, required: true}
  interrupt-parent: {type: phandle}
  interrupts: {type: array}
child-binding:
  properties:
    min-bitrate: {type: int}
    max-bitrate: {type: int, required: true}
'''

BINDINGS = {
    "can.yaml": CAN_BINDING,
    "can-phy.yaml": '''compatible: can-transceiver-gpio
include: base.yaml
properties:
  "#phy-cells": {type: int, required: true, const: 0}
  min-bitrate: {type: int}
  max-bitrate: {type: int, required: true}
  enable-gpios: {type: phandle-array}
  standby-gpios: {type: phandle-array}
''',
    "clock.yaml": '''compatible: test,clock
include: base.yaml
clock-cells: [id]
properties:
  "#clock-cells": {type: int, const: 1}
''',
    "interrupt.yaml": '''compatible: test,interrupt
include: base.yaml
properties:
  interrupt-controller: {type: boolean}
  "#interrupt-cells": {type: int, const: 1}
''',
}

SOURCE = '''/* Keep CAN wiring and unrelated source intact. */
/ {
    gpio0: gpio { compatible = "test,gpio"; gpio-controller; #gpio-cells = <2>; };
    gpio1: other-gpio { compatible = "test,gpio"; gpio-controller; #gpio-cells = <2>; };
    phy0: transceiver {
        compatible = "can-transceiver-gpio";
        #phy-cells = <0>;
        min-bitrate = <10000>;
        max-bitrate = <1000000>;
        enable-gpios = <&gpio0 1 0>;
    };
    can0: can {
        compatible = "zephyr,can-loopback";
        bitrate = <500000>;
        sample-point = <875>;
        phys = <&phy0>;
    };
    chosen { zephyr,canbus = <&can0>; };
};
'''

PASSIVE = 'passive: can-transceiver { min-bitrate = <10000>; max-bitrate = <125000>; };'


class CANResourceTests(unittest.TestCase):
    binding = fixtures.PeripheralTests.binding
    guard = fixtures.PeripheralTests.guard

    def setUp(self):
        fixtures.PeripheralTests.setUp(self)
        for name, content in BINDINGS.items():
            (self.bindings_dir / name).write_text(content)
        self.resources = self.app.context.services["devicetree_resources"]
        self.removal = self.app.context.services["devicetree_removal"]

    def inspect(self, source=SOURCE):
        self.draft = self.drafts.source(self.guard(text=source))
        self.document = self.drafts.drafts[self.draft["id"]].document
        self.observation = self.resources.inspect(self.guard())
        self.diagnostics = can_diagnostics(self.document, self.observation["bindings"])

    def findings(self, code):
        return [item for item in self.diagnostics if item["code"] == code]

    def review(self, selector, repairs=None):
        return self.removal.review(self.guard(schema_id=self.observation["schema_id"],
            node_id=self.document.find_node(selector).id, repairs=[] if repairs is None else repairs))

    def apply(self, review):
        self.draft = self.drafts.command(self.guard(command={"kind": "remove_reviewed", "review_id": review["review_id"]}))

    def test_literal_phys_and_chosen_dependencies_are_source_linked_without_tools(self):
        before = self.overlay.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            self.inspect()
        references = self.observation["graph"]["references"]
        self.assertEqual({item["location"]["name"] for item in references}, {"phys", "enable-gpios", "zephyr,canbus"})
        self.assertTrue(all(item["resolution"] == "resolved" for item in references))
        self.assertTrue(self.findings("can-hardware-unknown"))
        self.assertFalse(self.findings("can-transceiver-bitrate-range"))
        self.assertEqual(self.overlay.read_bytes(), before)

    def test_nominal_api_bounds_and_sample_point_default_are_distinct(self):
        for value in (0, 1000001):
            with self.subTest(value=value):
                self.inspect(SOURCE.replace("bitrate = <500000>", f"bitrate = <{value}>"))
                finding = self.findings("can-bitrate-range")[0]
                self.assertEqual(finding["severity"], "error")
                self.assertEqual(finding["locations"][0]["name"], "bitrate")
        self.inspect(SOURCE.replace("sample-point = <875>", "sample-point = <1000>"))
        self.assertEqual(self.findings("can-sample-point-range")[0]["severity"], "error")
        self.inspect(SOURCE.replace("sample-point = <875>", "sample-point = <0>"))
        self.assertFalse(self.findings("can-sample-point-range"))

    def test_disabled_nominal_invalidity_is_visible_without_active_operation_claim(self):
        self.inspect(SOURCE.replace('compatible = "zephyr,can-loopback";', 'compatible = "zephyr,can-loopback"; status = "disabled";')
                     .replace("bitrate = <500000>", "bitrate = <0>"))
        self.assertEqual(self.findings("can-bitrate-range")[0]["severity"], "warning")

    def test_declared_active_phy_range_links_consumer_and_limiting_property(self):
        for rate, bound in ((5000, "min-bitrate"), (750000, "max-bitrate")):
            with self.subTest(rate=rate):
                self.inspect(SOURCE.replace("max-bitrate = <1000000>", "max-bitrate = <500000>")
                             .replace("\n        bitrate = <500000>", f"\n        bitrate = <{rate}>"))
                finding = self.findings("can-transceiver-bitrate-range")[0]
                self.assertEqual(finding["severity"], "error")
                self.assertEqual({item["name"] for item in finding["locations"]}, {"bitrate", bound})

    def test_passive_limits_apply_only_when_phys_is_absent(self):
        self.inspect(SOURCE.replace("phys = <&phy0>;", PASSIVE))
        finding = self.findings("can-transceiver-bitrate-range")[0]
        self.assertEqual(finding["locations"][1]["path"], "/can/can-transceiver")
        self.inspect(SOURCE.replace("phys = <&phy0>;", "phys = <&phy0>; " + PASSIVE))
        self.assertFalse(self.findings("can-transceiver-bitrate-range"))

    def test_unknown_and_opaque_sources_do_not_get_invented_ranges_or_defaults(self):
        for replacement in ("phys = <&external>;", "phys = PHY_MACRO(0);", ""):
            with self.subTest(replacement=replacement):
                self.inspect(SOURCE.replace("phys = <&phy0>;", replacement))
                self.assertTrue(self.findings("can-transceiver-range-unknown"))
                self.assertFalse(self.findings("can-transceiver-bitrate-range"))
        self.inspect(SOURCE.replace("bitrate = <500000>;", "").replace("sample-point = <875>", "sample-point = <SAMPLE_POINT>"))
        self.assertTrue(self.findings("can-bitrate-unknown"))
        self.assertTrue(self.findings("can-sample-point-unknown"))
        self.assertFalse(self.findings("can-bitrate-range"))

    def test_zero_or_absent_minimum_does_not_invent_a_positive_floor(self):
        for minimum in ("min-bitrate = <0>;", ""):
            with self.subTest(minimum=minimum):
                self.inspect(SOURCE.replace("min-bitrate = <10000>;", minimum).replace("bitrate = <500000>", "bitrate = <1>"))
                self.assertFalse(self.findings("can-transceiver-bitrate-range"))
                self.assertTrue(self.findings("can-hardware-unknown"))

    def test_maximum_only_still_diagnoses_nominal_and_preserved_data_rate(self):
        self.inspect(SOURCE.replace("min-bitrate = <10000>;", "").replace("max-bitrate = <1000000>", "max-bitrate = <125000>")
                     .replace("bitrate = <500000>;", "bitrate = <500000>; bitrate-data = <2000000>;"))
        self.assertTrue(self.findings("can-transceiver-minimum-unknown"))
        self.assertEqual(self.findings("can-transceiver-bitrate-range")[0]["locations"][1]["name"], "max-bitrate")
        self.assertEqual(self.findings("can-transceiver-data-bitrate-range")[0]["locations"][0]["name"], "bitrate-data")

    def test_deprecated_bitrate_is_used_only_without_current_property(self):
        self.inspect(SOURCE.replace("bitrate = <500000>", "bus-speed = <0>"))
        self.assertEqual(self.findings("can-bitrate-range")[0]["locations"][0]["name"], "bus-speed")
        self.inspect(SOURCE.replace("bitrate = <500000>;", "bitrate = <500000>; bus-speed = <0>;"))
        self.assertFalse(self.findings("can-bitrate-range"))

    def test_disabled_and_shared_phys_remain_conservative(self):
        self.inspect(SOURCE.replace('compatible = "can-transceiver-gpio";', 'compatible = "can-transceiver-gpio"; status = "disabled";'))
        self.assertEqual(len(self.findings("can-transceiver-disabled")[0]["locations"]), 2)
        extra = 'other: other-can { compatible = "zephyr,can-loopback"; bitrate = <250000>; phys = <&phy0>; };'
        self.inspect(SOURCE.replace("chosen {", extra + "\n chosen {"))
        finding = self.findings("can-transceiver-sharing-unknown")[0]
        self.assertEqual(finding["severity"], "unknown")
        self.assertEqual(len(finding["locations"]), 3)

    def test_ambiguous_profile_has_no_api_range_claim(self):
        (self.bindings_dir / "duplicate-can.yaml").write_text(CAN_BINDING)
        self.inspect(SOURCE.replace("bitrate = <500000>", "bitrate = <0>"))
        self.assertTrue(self.findings("can-controller-unknown"))
        self.assertFalse(self.findings("can-bitrate-range"))

    def test_can_diagnostics_are_bounded(self):
        self.inspect("/ { " + "".join(f'can{index} {{ compatible = "zephyr,can-loopback"; bitrate = <0>; }};' for index in range(100)) + " };")
        self.assertEqual(len(self.diagnostics), 256)
        self.assertEqual(self.diagnostics[-1]["code"], "can-diagnostics-truncated")

    def test_passive_child_removal_is_blocked_without_fabricated_property_repair(self):
        self.inspect(SOURCE.replace("phys = <&phy0>;", PASSIVE))
        before = self.draft["text"]
        review = self.review("&passive")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertEqual(review["dependants"][0]["kind"], "can-passive-transceiver")
        self.assertEqual(self.draft["text"], before)
        with self.assertRaisesRegex(ValueError, "unresolved dependants"):
            self.apply(review)

    def test_ambiguous_or_repeated_known_can_parent_still_guards_passive_child(self):
        (self.bindings_dir / "duplicate-can.yaml").write_text(CAN_BINDING)
        self.inspect(SOURCE.replace("phys = <&phy0>;", PASSIVE))
        self.assertFalse(self.review("&passive")["can_apply"])
        (self.bindings_dir / "duplicate-can.yaml").unlink()
        self.inspect(SOURCE.replace("phys = <&phy0>;", PASSIVE).replace('compatible = "zephyr,can-loopback";', 'compatible = "zephyr,can-loopback"; compatible = "zephyr,can-loopback";'))
        self.assertFalse(self.review("&passive")["can_apply"])

    def test_normalized_passive_names_cannot_bypass_implicit_guard_or_phys_repair(self):
        for name in ("can_transceiver", "CAN-TRANSCEIVER", "can.transceiver", "can,transceiver", "can+transceiver"):
            with self.subTest(name=name):
                passive = PASSIVE.replace("can-transceiver", name)
                self.inspect(SOURCE.replace("phys = <&phy0>;", "phys = <&phy0>; " + passive))
                self.assertFalse(self.review("&passive")["can_apply"])
                review = self.review("&phy0")
                self.assertFalse(review["can_apply"])
                self.assertEqual(review["repair_choices"], [])

    def test_controller_removal_includes_passive_child_and_only_selected_chosen_repair(self):
        self.inspect(SOURCE.replace("phys = <&phy0>;", PASSIVE))
        before = self.draft["text"]
        initial = self.review("&can0")
        self.assertEqual(len(initial["repair_choices"]), 1)
        review = self.review("&can0", [initial["repair_choices"][0]["id"]])
        self.assertTrue(review["can_apply"])
        self.apply(review)
        self.assertNotIn("can0: can", self.draft["text"])
        self.assertNotIn("zephyr,canbus", self.draft["text"])
        self.assertIn("phy0: transceiver", self.draft["text"])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], before)

    def test_optional_phys_repair_preserves_controller_and_other_providers(self):
        self.inspect()
        initial = self.review("&phy0")
        self.assertEqual(len(initial["repair_choices"]), 1)
        review = self.review("&phy0", [initial["repair_choices"][0]["id"]])
        self.assertTrue(review["can_apply"])
        self.apply(review)
        self.assertNotIn("phys =", self.draft["text"])
        self.assertIn("can0: can", self.draft["text"])
        self.assertIn("gpio0: gpio", self.draft["text"])

    def test_phys_repair_cannot_expose_different_passive_limits(self):
        self.inspect(SOURCE.replace("phys = <&phy0>;", "phys = <&phy0>; " + PASSIVE))
        review = self.review("&phy0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertTrue(any("expose a passive CAN" in item["message"] for item in review["blockers"]))

    def test_last_transceiver_gpio_control_cannot_be_repaired_away(self):
        for property_name in ("enable-gpios", "standby-gpios"):
            with self.subTest(property_name=property_name):
                self.inspect(SOURCE.replace("enable-gpios", property_name))
                review = self.review("&gpio0")
                self.assertFalse(review["can_apply"])
                self.assertEqual(review["repair_choices"], [])
                self.assertTrue(any("last control property" in item["message"] for item in review["blockers"]))

    def test_two_joint_control_repairs_cannot_remove_both_gpio_controls(self):
        self.inspect(SOURCE.replace("enable-gpios = <&gpio0 1 0>;", "enable-gpios = <&gpio0 1 0>; standby-gpios = <&gpio0 2 0>;"))
        initial = self.review("&gpio0")
        self.assertEqual(len(initial["repair_choices"]), 2)
        review = self.review("&gpio0", [item["id"] for item in initial["repair_choices"]])
        self.assertFalse(review["can_apply"])
        self.assertTrue(any("last control property" in item["message"] for item in review["blockers"]))

    def test_one_control_repair_retains_other_control_and_transceiver(self):
        self.inspect(SOURCE.replace("enable-gpios = <&gpio0 1 0>;", "enable-gpios = <&gpio0 1 0>; standby-gpios = <&gpio1 2 0>;"))
        initial = self.review("&gpio0")
        review = self.review("&gpio0", [item["id"] for item in initial["repair_choices"]])
        self.assertTrue(review["can_apply"])
        self.apply(review)
        self.assertIn("standby-gpios = <&gpio1 2 0>", self.draft["text"])
        self.assertIn("phys = <&phy0>", self.draft["text"])
        self.assertNotIn("enable-gpios", self.draft["text"])

    def test_required_clock_dependency_and_interrupt_parent_remain_in_generic_graph(self):
        providers = 'clock0: clock { compatible = "test,clock"; #clock-cells = <1>; }; intc: interrupt { compatible = "test,interrupt"; interrupt-controller; #interrupt-cells = <1>; };'
        self.inspect(SOURCE.replace("can0: can", providers + "\n can0: can").replace("bitrate = <500000>;", "bitrate = <500000>; clocks = <&clock0 1>; interrupt-parent = <&intc>; interrupts = <3>;"))
        names = {item["location"]["name"] for item in self.observation["graph"]["references"]}
        self.assertTrue({"clocks", "interrupt-parent"} <= names)
        review = self.review("&clock0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
