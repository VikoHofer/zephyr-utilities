"""Current diagnostics distinguish established conflicts from incomplete evidence."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.resources import ResourceService
from zephyr_utilities.features.devicetree.resource_graph import add_pin_state_consumers, build_resource_graph, pin_diagnostics
from zephyr_utilities.features.devicetree.pins import _conflicts
from zephyr_utilities.features.devicetree.source import SourceDocument

import test_devicetree_peripherals as peripheral_fixtures
import test_devicetree_pins as pin_fixtures


class ResourceTests(unittest.TestCase):
    setUp = peripheral_fixtures.PeripheralTests.setUp
    binding = peripheral_fixtures.PeripheralTests.binding
    guard = peripheral_fixtures.PeripheralTests.guard

    def load(self):
        if not hasattr(self, "resources"):
            self.resources = ResourceService(self.drafts)
        self.schema = self.resources.inspect(self.guard())
        return self.schema

    def source(self, text):
        self.draft = self.drafts.source(self.guard(text=text))
        return self.load()

    def findings(self, code):
        return [item for item in self.schema["diagnostics"] if item["code"] == code]

    def validate(self, body=None):
        return self.resources.validate_observation(body or self.guard(schema_id=self.schema["schema_id"]),
                                                   self.context, self.drafts.drafts[self.draft["id"]])

    def test_observation_is_read_only_and_shared_bus_children_are_not_pin_owners(self):
        original = self.overlay.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            self.load()
        self.assertFalse([item for item in self.schema["diagnostics"] if item["severity"] == "error"])
        self.assertEqual(self.overlay.read_bytes(), original)
        self.assertEqual(self.draft["revision"], 0)
        self.assertEqual(len(self.findings("pin-coverage-unknown")), 1)
        self.assertTrue(all(item["severity"] in {"error", "warning", "unknown", "info"} for item in self.schema["diagnostics"]))
        self.assertEqual(self.schema["nodes"], self.schema["graph"]["nodes"])

    def duplicate_address(self, status):
        source = peripheral_fixtures.SOURCE.replace('status = "disabled";', f'status = {status};', 1)
        return source.replace('        first: sensor@48 {', '''        duplicate@48 { compatible = "test,temperature"; reg = <0x48>; };
        first: sensor@48 {''')

    def test_enabled_address_conflict_has_both_consumers_and_source_locations(self):
        self.source(self.duplicate_address('"okay"'))
        item = self.findings("bus-address-conflict")[0]
        self.assertEqual(item["severity"], "error")
        self.assertEqual(len(item["locations"]), 2)
        for loc in item["locations"]:
            self.assertIn("reg", self.draft["text"].encode()[loc["start"]:loc["end"]].decode())
        self.assertEqual(item["repair_choices"][0]["kind"], "peripherals")

    def test_disabled_or_opaque_status_is_not_claimed_as_simultaneous_conflict(self):
        for status, severity in [('"disabled"', "warning"), ("UNKNOWN_STATUS", "unknown")]:
            with self.subTest(status=status):
                self.source(self.duplicate_address(status))
                self.assertEqual(self.findings("bus-address-conflict")[0]["severity"], severity)

    def test_chip_select_range_and_unit_address_use_only_declared_limits(self):
        source = peripheral_fixtures.SOURCE.replace('        cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;', '''        cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;
        device@3 { compatible = "test,temperature"; reg = <2>; spi-max-frequency = <1000000>; };''')
        self.source(source)
        self.assertTrue(self.findings("spi-chip-select-range"))
        self.assertTrue(self.findings("unit-address-mismatch"))
        self.source(source.replace('        cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;', ''))
        self.assertFalse(self.findings("spi-chip-select-range"))
        self.assertTrue(self.findings("spi-capacity-unknown"))

    def test_gpio_pin_conflicts_ranges_and_reservations_are_source_linked(self):
        source = peripheral_fixtures.SOURCE.replace('gpio-controller;', 'gpio-controller; ngpios = <4>; gpio-reserved-ranges = <1 1>;')
        source = source.replace('leds { compatible = "gpio-leds"; };', '''leds { compatible = "gpio-leds";
            first_led { gpios = <&gpio0 3 0>; };
            second_led { gpios = <&gpio0 3 1>; };
            invalid_led { gpios = <&gpio0 4 0>; };
        };''')
        self.source(source)
        self.assertEqual(len(self.findings("gpio-pin-conflict")), 1)
        self.assertEqual(self.findings("gpio-pin-conflict")[0]["severity"], "error")
        self.assertTrue(self.findings("gpio-pin-range"))
        self.assertTrue(self.findings("gpio-reserved"))
        self.source(source.replace('second_led {', 'second_led { status = "disabled";'))
        self.assertEqual(self.findings("gpio-pin-conflict")[0]["severity"], "warning")

    def test_alias_chosen_binding_paths_and_property_label_dependants_are_indexed(self):
        (self.bindings_dir / "consumer.yaml").write_text('''compatible: test,consumer
include: base.yaml
properties:
  provider-path: {type: path}
''')
        source = peripheral_fixtures.SOURCE.replace('/ {', '''/ {
    aliases { indicator = &gpio0; };
    chosen { test,provider = "/gpio"; };
    consumer { compatible = "test,consumer"; provider-path = "/gpio"; link = <&property_label>; };
    data { property_label: number = <1>; };
''', 1)
        self.source(source)
        refs = self.schema["graph"]["references"]
        self.assertTrue(any(item["kind"] == "binding-path" and item["reference"] == "&{/gpio}" for item in refs))
        property_ref = next(item for item in refs if item["reference"] == "&property_label")
        self.assertEqual(property_ref["resolution"], "resolved")
        self.assertEqual(len(property_ref["target_property_ids"]), 1)
        self.assertTrue(all(item["property_id"] for item in refs if item["reference"] in {"&gpio0", "&{/gpio}"}))

    def test_missing_and_ambiguous_local_references_are_distinct(self):
        self.source('/ { a: one {}; a: two {}; consumer { refs = <&a &inherited>; }; };\n')
        self.assertEqual(self.findings("ambiguous-reference")[0]["severity"], "error")
        self.assertEqual(self.findings("unresolved-reference")[0]["severity"], "unknown")
        self.assertEqual(len(next(ref for ref in self.schema["graph"]["references"] if ref["reference"] == "&a")["target_node_ids"]), 2)

    def test_opaque_dependencies_and_incomplete_source_stay_inspectable_unknown(self):
        self.load()
        self.assertTrue(self.schema["graph"]["unknown_properties"])
        self.assertTrue(self.findings("external-coverage-unknown"))
        self.source('/ { unfinished = "')
        self.assertEqual(self.schema["coverage"], "incomplete-source")
        self.assertEqual(self.schema["nodes"], [])
        self.assertEqual(self.schema["diagnostics"][0]["severity"], "unknown")
        self.assertEqual(self.validate()["sha256"], self.draft["sha256"])

    def test_stale_draft_context_expiry_and_changed_bindings_invalidate_observation(self):
        self.load()
        body = self.guard(schema_id=self.schema["schema_id"])
        self.assertEqual(self.validate()["schema_id"], self.schema["schema_id"])
        altered = deepcopy(body)
        altered["context_id"] = "other"
        with self.assertRaises(ValueError):
            self.validate(altered)
        self.resources.observations[self.schema["schema_id"]]["expires"] = 0
        with self.assertRaises(ValueError):
            self.validate()
        self.load()
        (self.bindings_dir / "base.yaml").write_text("properties: {}\n")
        with self.assertRaises(ValueError):
            self.validate()
        self.load()
        old = self.guard(schema_id=self.schema["schema_id"])
        self.draft = self.drafts.source(self.guard(text=peripheral_fixtures.SOURCE + "\n/* New source */\n"))
        with self.assertRaises(ValueError):
            self.validate(old)

    def test_diagnostics_and_reference_graph_have_explicit_bounds(self):
        self.source('/ { consumer { refs = <' + ' '.join(f'&missing_{i}' for i in range(4200)) + '>; }; };')
        self.assertEqual(len(self.schema["diagnostics"]), 256)
        self.assertEqual(len(self.schema["graph"]["references"]), 4096)
        self.assertTrue(self.schema["graph"]["truncated"])
        self.assertEqual(self.schema["diagnostics"][-1]["code"], "diagnostics-truncated")

    def test_projection_detaches_data_from_validated_observation(self):
        self.load()
        self.schema["nodes"].clear()
        self.schema["graph"]["references"].clear()
        self.assertTrue(self.validate()["nodes"])
        self.assertTrue(self.validate()["graph"]["references"])

    def test_source_header_reference_and_unknown_path_string_remain_visible(self):
        document = SourceDocument.parse(b'/ { provider: provider {}; consumer { path = "/provider"; }; }; &provider { status = "okay"; };')
        graph = build_resource_graph(document, {"nodes": [], "targets": []})
        self.assertEqual(graph["references"][0]["kind"], "declaration-reference")
        self.assertEqual(graph["unknown_properties"][0]["name"], "path")

    def test_node_and_property_with_same_label_remain_ambiguous_on_same_owner(self):
        self.source('/ { same: node { same: property = <1>; }; consumer { ref = <&same>; }; };')
        reference = next(ref for ref in self.schema["graph"]["references"] if ref["reference"] == "&same")
        self.assertEqual(reference["resolution"], "ambiguous")
        self.assertEqual(len(reference["target_node_ids"]), 1)
        self.assertEqual(len(reference["target_property_ids"]), 1)

    def test_alternate_provider_reference_and_unknown_capacity_remain_distinct(self):
        source = peripheral_fixtures.SOURCE.replace('gpio0: gpio {', 'gpio0: other_gpio: gpio {')
        source = source.replace('leds { compatible = "gpio-leds"; };', '''leds { compatible = "gpio-leds";
            one { gpios = <&other_gpio 3 0>; };
            two { gpios = <&{/gpio} 3 0>; };
        };''')
        self.source(source)
        self.assertTrue(self.findings("gpio-pin-conflict"))
        self.assertTrue(self.findings("gpio-range-unknown"))
        self.assertFalse(self.findings("gpio-pin-range"))
        self.assertFalse(self.findings("gpio-provider-unknown"))

    def test_local_pinctrl_descendants_have_unique_source_identity_without_claiming_binding(self):
        source = '&pinctrl { local_state: local_state { group1 { psels = <NRF_PSEL(UART_TX, 0, 6)>; }; }; };'
        self.source(source)
        self.assertEqual([node["unique"] for node in self.schema["nodes"]], [False, True, True])
        for suffix in (' &pinctrl {};', ' &local_state {};', ' &{/pin-controller} {};'):
            with self.subTest(suffix=suffix):
                self.source(source + suffix)
                self.assertFalse(next(node for node in self.schema["nodes"] if node["name"] == "local_state")["unique"])

    def test_malformed_reserved_ranges_are_unknown_even_with_known_gpio_count(self):
        self.source(peripheral_fixtures.SOURCE.replace('gpio-controller;', 'gpio-controller; ngpios = <32>; gpio-reserved-ranges = <1 2 3>;'))
        self.assertTrue(self.findings("gpio-reservations-unknown"))
        self.assertFalse(self.findings("gpio-range-unknown"))

    def test_pin_conflict_construction_is_bounded_before_response_truncation(self):
        document = SourceDocument.parse(b'/ {};')
        allocations = [{"owner": f"owner_{index}", "kind": "gpio", "state": None, "role": "gpios", "port": 0, "pin": 1} for index in range(1000)]
        diagnostics = pin_diagnostics(document, {"supported": True, "allocations": allocations, "controllers": []})
        self.assertEqual(len(diagnostics), 256)
        self.assertEqual(diagnostics[-1]["code"], "pin-conflicts-truncated")
        conflicts = _conflicts({"controllers": []}, allocations)
        self.assertEqual(len(conflicts), 256)
        self.assertIn("limit", conflicts[-1])


class ResourcePinTests(unittest.TestCase):
    setUp = pin_fixtures.PinServiceTests.setUp
    binding = pin_fixtures.PinServiceTests.binding
    guard = pin_fixtures.PinServiceTests.guard

    def load(self):
        if not hasattr(self, "resources"):
            self.resources = ResourceService(self.drafts, self.pins)
        self.schema = self.resources.inspect(self.guard())
        return self.schema

    def test_verified_default_sleep_sharing_remains_valid_and_is_detached(self):
        self.load()
        self.assertTrue(self.schema["pins"]["supported"])
        self.assertFalse([item for item in self.schema["diagnostics"] if item["code"] == "pin-conflict"])
        observation = next(reversed(self.pins.observations.values()))
        self.assertTrue(observation["payload"]["allocations"])
        self.assertTrue(observation["payload"]["reservations"])

    def test_known_gpio_pinctrl_conflicts_link_both_consumers(self):
        self.draft = self.drafts.source(self.guard(text='/ { own: own { gpios = <&gpio0 6 0>; }; };'))
        self.load()
        conflict = next(item for item in self.schema["diagnostics"] if item["code"] == "pin-conflict")
        self.assertEqual(conflict["severity"], "error")
        self.assertEqual(len(conflict["locations"]), 2)
        self.assertTrue(any(item["node_id"] for item in conflict["locations"]))
        self.assertTrue(any(item["code"] == "pin-reserved" for item in self.schema["diagnostics"]))

    def test_enabled_hardware_instance_conflict_is_explicit(self):
        self.draft = self.drafts.source(self.guard(text='&spi0 { status = "okay"; };'))
        self.load()
        conflict = next(item for item in self.schema["diagnostics"] if item["code"] == "controller-instance-conflict")
        self.assertEqual(conflict["severity"], "error")
        self.assertIn("i2c0", conflict["message"])
        self.assertIn("spi0", conflict["message"])

    def test_changed_pin_evidence_invalidates_removal_guard(self):
        self.load()
        observed = self.resources.observations[self.schema["schema_id"]]
        observed["pin_evidence"].changed = True
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.resources.validate_observation(self.guard(schema_id=self.schema["schema_id"]), self.context,
                                                self.drafts.drafts[self.draft["id"]])

    def test_pin_observation_failure_preserves_local_graph(self):
        with patch.object(self.pins, "inspect", side_effect=ValueError("Pin evidence unavailable")):
            self.load()
        self.assertTrue(self.schema["nodes"])
        self.assertTrue(any(item["code"] == "pin-coverage-unknown" and "unavailable" in item["message"] for item in self.schema["diagnostics"]))

    def test_generic_gpio_sharing_is_unknown_without_established_output_ownership(self):
        self.draft = self.drafts.source(self.guard(text='/ { one { gpios = <&gpio1 3 0>; }; two { gpios = <&gpio1 3 0>; }; };'))
        self.load()
        conflicts = [item for item in self.schema["diagnostics"] if item["code"] == "pin-conflict"]
        self.assertTrue(conflicts)
        self.assertTrue(all(item["severity"] == "unknown" for item in conflicts))

    def test_inherited_pin_references_survive_unknown_state_allocations_and_disabled_status(self):
        self.draft = self.drafts.source(self.guard(text='''
&pinctrl { uart_default: uart_default { group1 { psels = <NRF_PSEL(UART_TX, 0, 6)>; }; }; };
&uart0 { status = "disabled"; };
'''))
        self.load()
        consumers = self.schema["graph"]["pin_state_consumers"]
        self.assertEqual(len(consumers), 1)
        consumer = consumers[0]
        self.assertEqual((consumer["controller"], consumer["state"], consumer["reference"]), ("uart0", "default", "&uart_default"))
        self.assertEqual(consumer["origin"], "verified-board")
        self.assertIsNone(consumer["location"])
        self.assertIsNone(consumer["source_node_id"])
        self.assertIsNone(consumer["property_id"])
        target = next(node for node in self.schema["nodes"] if node["node_id"] == consumer["target_node_id"])
        self.assertIn("uart_default", target["labels"])
        observed = next(reversed(self.pins.observations.values()))["payload"]
        controller = next(row for row in observed["controllers"] if row["id"] == "uart0")
        self.assertFalse(controller["states"][0]["known"])

    def test_local_pin_reference_has_exact_source_and_unreferenced_state_has_no_consumer(self):
        text = '''&pinctrl {
 own_state: own_state { group1 { psels = <NRF_PSEL(UART_TX, 1, 3)>; }; };
 spare_state: spare_state { group1 { psels = <NRF_PSEL(UART_RX, 1, 4)>; }; };
};
&uart0 { pinctrl-0 = <&own_state>; };
'''
        self.draft = self.drafts.source(self.guard(text=text))
        self.load()
        consumers = self.schema["graph"]["pin_state_consumers"]
        self.assertEqual(len(consumers), 1)
        consumer = consumers[0]
        self.assertEqual(consumer["reference"], "&own_state")
        self.assertEqual(consumer["origin"], "overlay")
        self.assertEqual(consumer["location"]["property_id"], consumer["property_id"])
        self.assertEqual(consumer["location"]["node_id"], consumer["source_node_id"])
        self.assertEqual(text[consumer["location"]["start"]:consumer["location"]["end"]], "pinctrl-0 = <&own_state>;")

    def test_pin_reference_consumers_are_bounded_and_preserve_ambiguous_targets(self):
        document = SourceDocument.parse(b'/ { shared: one {}; shared: two {}; };')
        graph = build_resource_graph(document, {"nodes": [], "targets": []})
        pins = {"supported": True, "controllers": [{"id": "controller", "states": [
            {"name": "default", "index": 0, "refs": ["shared"]}]}]}
        self.assertFalse(add_pin_state_consumers(document, graph, pins))
        self.assertEqual(len(graph["pin_state_consumers"]), 2)
        self.assertEqual(len({row["target_node_id"] for row in graph["pin_state_consumers"]}), 2)
        graph["pin_state_consumers"] = []
        pins["controllers"] *= 200
        self.assertTrue(add_pin_state_consumers(document, graph, pins))
        self.assertEqual(len(graph["pin_state_consumers"]), 256)
        self.assertTrue(graph["truncated"])
