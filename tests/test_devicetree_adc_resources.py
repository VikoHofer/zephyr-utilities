"""ADC diagnostics and removal reviews preserve implicit channel dependencies."""

import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.adc import adc_providers
from zephyr_utilities.features.devicetree.adc_resources import adc_diagnostics
from zephyr_utilities.features.devicetree.bindings import BindingCatalog
from zephyr_utilities.features.devicetree.peripherals import PeripheralService

import test_devicetree_peripherals as fixtures


BINDINGS = {
    "adc.yaml": '''compatible: test,adc
include: base.yaml
io-channel-cells: [input]
properties:
  "#io-channel-cells": {type: int, const: 1, required: true}
  "#address-cells": {type: int, const: 1}
  "#size-cells": {type: int, const: 0}
child-binding:
  properties:
    reg: {type: array, required: true}
    zephyr,gain: {type: string, enum: [ADC_GAIN_1, ADC_GAIN_1_2], required: true}
    zephyr,reference: {type: string, enum: [ADC_REF_INTERNAL, ADC_REF_VDD_1], required: true}
    zephyr,acquisition-time: {type: int, required: true}
''',
    "adc-consumer.yaml": '''compatible: test,adc-consumer
include: base.yaml
properties:
  io-channels: {type: phandle-array}
  io-channel-names: {type: string-array}
''',
    "dac.yaml": '''compatible: test,dac
include: base.yaml
io-channel-cells: [input]
properties:
  "#io-channel-cells": {type: int, const: 1, required: true}
''',
}

SOURCE = '''/* Preserve shared providers and source comments. */
/ {
    adc0: alternate: adc {
        compatible = "test,adc"; #io-channel-cells = <1>; #address-cells = <1>; #size-cells = <0>;
        first: channel@1 { reg = <1>; zephyr,gain = "ADC_GAIN_1"; zephyr,reference = "ADC_REF_INTERNAL"; zephyr,acquisition-time = <0>; };
        second: channel@2 { reg = <2>; zephyr,gain = "ADC_GAIN_1"; zephyr,reference = "ADC_REF_INTERNAL"; zephyr,acquisition-time = <0>; };
    };
    reader: consumer { compatible = "test,adc-consumer"; io-channels = <&adc0 1>; };
};
'''


class ADCResourceTests(unittest.TestCase):
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
        catalog = BindingCatalog(str(self.workspace), [str(self.bindings_dir)])
        self.projection = PeripheralService._projection(self.document, catalog)
        self.projection["adc_providers"] = adc_providers(self.document, self.projection, catalog)
        self.diagnostics = adc_diagnostics(self.document, self.projection, catalog)
        return self.diagnostics

    def findings(self, code):
        return [item for item in self.diagnostics if item["code"] == code]

    def review(self, selector, repairs=None):
        observation = self.resources.inspect(self.guard())
        return self.removal.review(self.guard(schema_id=observation["schema_id"],
            node_id=self.document.find_node(selector).id, repairs=[] if repairs is None else repairs))

    def test_supported_observation_has_truthful_limits_and_no_mutation(self):
        original = self.overlay.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("No project tools")):
            self.inspect()
        self.assertFalse([item for item in self.diagnostics if item["severity"] == "error"])
        self.assertEqual(len(self.findings("adc-capability-unknown")), 1)
        self.assertEqual(self.overlay.read_bytes(), original)

    def test_input_above_api_bound_is_source_linked_without_claiming_physical_range(self):
        self.inspect(SOURCE.replace("<&adc0 1>", "<&adc0 32>"))
        finding = self.findings("adc-input-range")[0]
        self.assertEqual(finding["severity"], "error")
        loc = finding["locations"][0]
        self.assertEqual(self.document.source[loc["start"]:loc["end"]], b"io-channels = <&adc0 32>;")
        self.assertIn("not an inferred physical pin", finding["message"])

    def test_resource_observation_includes_adc_evidence(self):
        self.inspect(SOURCE.replace("<&adc0 1>", "<&adc0 32>"))
        with patch("subprocess.Popen", side_effect=AssertionError("No project tools")):
            observation = self.resources.inspect(self.guard())
        self.assertEqual([item for item in observation["diagnostics"] if item["code"] == "adc-input-range"], self.findings("adc-input-range"))
        self.assertTrue(observation["bindings"]["adc_providers"][0]["supported"])

    def test_disabled_or_opaque_activation_downgrades_input_range(self):
        base = SOURCE.replace("<&adc0 1>", "<&adc0 32>")
        for status, expected in [('"disabled"', "warning"), ('UNKNOWN_STATUS', "unknown")]:
            with self.subTest(status=status):
                self.inspect(base.replace('compatible = "test,adc";', f'compatible = "test,adc"; status = {status};'))
                self.assertEqual(self.findings("adc-input-range")[0]["severity"], expected)
        self.inspect(base.replace('compatible = "test,adc";', 'compatible = "test,adc"; status = "disabled";'))
        self.assertEqual(len(self.findings("adc-provider-disabled")[0]["locations"]), 2)

    def test_channel_unit_and_reg_mismatch_is_reported(self):
        self.inspect(SOURCE.replace("first: channel@1", "first: channel@3"))
        self.assertEqual(self.findings("adc-channel-unit-mismatch")[0]["severity"], "error")
        self.assertTrue(self.findings("adc-channel-config-unknown"))

    def test_nonchannel_child_name_is_outside_the_common_binding_profile(self):
        self.inspect(SOURCE.replace("first: channel@1", "first: sample@1"))
        self.assertTrue(self.findings("adc-channel-unit-mismatch"))
        self.assertTrue(self.findings("adc-channel-config-unknown"))

    def test_duplicate_regs_are_reported_even_when_unit_addresses_differ(self):
        self.inspect(SOURCE.replace("reg = <2>", "reg = <1>"))
        finding = self.findings("adc-channel-duplicate")[0]
        self.assertEqual(len(finding["locations"]), 2)
        self.assertEqual(finding["severity"], "error")

    def test_channel_id_and_acquisition_encoding_bounds(self):
        self.inspect(SOURCE.replace("channel@1", "channel@20").replace("reg = <1>", "reg = <32>")
                     .replace("zephyr,acquisition-time = <0>", "zephyr,acquisition-time = <65536>", 1))
        self.assertEqual(len(self.findings("adc-channel-id-range")), 1)
        self.assertEqual(len(self.findings("adc-acquisition-range")), 1)

    def test_opaque_channel_reg_retains_unknown_identity(self):
        self.inspect(SOURCE.replace("reg = <1>", "reg = <CHANNEL_ID>"))
        self.assertTrue(self.findings("adc-channel-identity-unknown"))
        self.assertFalse(self.findings("adc-channel-id-range"))

    def test_explicit_child_binding_does_not_inherit_adc_range_semantics(self):
        self.inspect(SOURCE.replace("first: channel@1 {", 'first: channel@1 { compatible = "test,dac";')
                     .replace("reg = <1>", "reg = <32>"))
        self.assertTrue(self.findings("adc-channel-identity-unknown"))
        self.assertFalse(self.findings("adc-channel-id-range"))

    def test_unknown_inherited_and_invalid_count_provider_has_no_adc_range_claim(self):
        for source in [SOURCE.replace("<&adc0 1>", "<&external 32>"),
                       SOURCE.replace("#io-channel-cells = <1>", "#io-channel-cells = <2>").replace("<&adc0 1>", "<&adc0 32>"),
                       SOURCE.replace('compatible = "test,adc"', 'compatible = "test,dac"').replace("<&adc0 1>", "<&adc0 32>")]:
            with self.subTest(source=source):
                self.inspect(source)
                self.assertTrue(self.findings("adc-provider-unknown"))
                self.assertFalse(self.findings("adc-input-range"))

    def test_shared_adc_input_is_not_claimed_an_exclusive_pin_conflict(self):
        extra = 'another { compatible = "test,adc-consumer"; io-channels = <&alternate 1>; };'
        self.inspect(SOURCE.replace("reader: consumer", extra + "\n reader: consumer"))
        self.assertFalse([item for item in self.diagnostics if item["severity"] == "error"])

    def test_multiple_opaque_and_untyped_consumers_are_visible_as_unknown(self):
        cases = [SOURCE.replace("<&adc0 1>", "<&adc0 1>, <&adc0 2>"),
                 SOURCE.replace("<&adc0 1>", "<&adc0 CHANNEL_ID>"),
                 SOURCE.replace('compatible = "test,adc-consumer";', "")]
        for source in cases:
            with self.subTest(source=source):
                self.inspect(source)
                self.assertTrue(self.findings("adc-consumer-unknown"))

    def test_diagnostics_have_a_visible_bound(self):
        source = "/ { " + "".join(f"node_{index} {{ io-channels = <&external 1>; }};" for index in range(400)) + " };"
        self.inspect(source)
        self.assertEqual(len(self.diagnostics), 256)
        self.assertEqual(self.diagnostics[-1]["code"], "adc-diagnostics-truncated")

    def test_consumed_channel_removal_is_blocked_by_numeric_dependency(self):
        self.inspect()
        before = self.draft["text"]
        review = self.review("&first")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertEqual(review["dependants"][0]["kind"], "adc-channel-consumer")
        self.assertEqual(review["dependants"][0]["location"]["name"], "io-channels")
        self.assertTrue(any("channel configuration" in item["message"] for item in review["blockers"]))
        self.assertEqual(self.draft["text"], before)

    def test_other_channel_removal_preserves_provider_and_consumer_with_one_undo(self):
        self.inspect()
        before = self.draft["text"]
        review = self.review("&second")
        self.assertTrue(review["can_apply"])
        self.draft = self.drafts.command(self.guard(command={"kind": "remove_reviewed", "review_id": review["review_id"]}))
        self.assertNotIn("second: channel@2", self.draft["text"])
        self.assertIn("io-channels = <&adc0 1>;", self.draft["text"])
        self.assertIn("adc0: alternate: adc", self.draft["text"])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], before)

    def test_multiple_specifier_and_path_alias_dependencies_block_channel_removal(self):
        cases = [SOURCE.replace("<&adc0 1>", "<&adc0 2>, <&alternate 1>"),
                 SOURCE.replace("<&adc0 1>", "<&{/adc} 1>")]
        for source in cases:
            with self.subTest(source=source):
                self.inspect(source)
                self.assertFalse(self.review("&first")["can_apply"])

    def test_opaque_consumer_and_malformed_channel_identity_cannot_authorize_removal(self):
        cases = [SOURCE.replace("<&adc0 1>", "<&adc0 CHANNEL_ID>"),
                 SOURCE.replace("<&adc0 1>", "<&adc0 1 2>"),
                 SOURCE.replace("<&adc0 1>", "CHANNEL_REFERENCE(1)"),
                 SOURCE.replace("channel@1", "channel@3"),
                 SOURCE.replace("reg = <1>", "reg = <CHANNEL_ID>"),
                 SOURCE.replace("io-channels = <&adc0 1>;", "io-channels = <&adc0 2>; io-channels = <&adc0 3>;")]
        for source in cases:
            with self.subTest(source=source):
                self.inspect(source)
                review = self.review("&first")
                self.assertFalse(review["can_apply"])
                self.assertEqual(review["repair_choices"], [])

    def test_named_provider_dependency_requires_explicit_companion_repair(self):
        self.inspect(SOURCE.replace("io-channels = <&adc0 1>;", 'io-channels = <&adc0 1>; io-channel-names = "sense";'))
        review = self.review("&adc0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertTrue(any("companion names" in item["message"] for item in review["blockers"]))

    def test_opaque_mixed_consumer_cannot_exclude_a_channel_using_unrelated_visible_reference(self):
        source = SOURCE.replace("reader: consumer", '''other_adc: adc_second {
            compatible = "test,adc"; #io-channel-cells = <1>; #address-cells = <1>; #size-cells = <0>;
        };
        reader: consumer''').replace("<&adc0 1>", "<&other_adc 2>, ADC_INPUT(1)")
        self.inspect(source)
        review = self.review("&first")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertTrue(any(item["kind"] == "adc-channel-consumer" for item in review["dependants"]))
        self.assertTrue(any("may select" in item["message"] for item in review["dependants"]))

    def test_numeric_child_dependency_survives_arbitrary_name_and_explicit_binding(self):
        source = SOURCE.replace('first: channel@1 { reg = <1>; zephyr,gain = "ADC_GAIN_1"; zephyr,reference = "ADC_REF_INTERNAL"; zephyr,acquisition-time = <0>; };',
                                'first: sample@1 { compatible = "test,dac"; reg = <1>; };')
        self.inspect(source)
        review = self.review("&first")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertTrue(any(item["kind"] == "adc-channel-consumer" for item in review["dependants"]))
        self.assertTrue(any("may select" in item["message"] for item in review["dependants"]))

    def test_unsupported_provider_evidence_does_not_exclude_implicit_child_dependency(self):
        source = SOURCE.replace('first: channel@1 { reg = <1>; zephyr,gain = "ADC_GAIN_1"; zephyr,reference = "ADC_REF_INTERNAL"; zephyr,acquisition-time = <0>; };',
                                'first: sample@1 { compatible = "test,dac"; reg = <1>; };')
        for declaration in ("#io-channel-cells = <2>;", ""):
            with self.subTest(declaration=declaration):
                self.inspect(source.replace("#io-channel-cells = <1>;", declaration))
                review = self.review("&first")
                self.assertFalse(review["can_apply"])
                self.assertEqual(review["repair_choices"], [])
                self.assertTrue(any("may select" in item["message"] for item in review["dependants"]))

    def test_optional_unnamed_provider_dependency_still_has_explicit_property_repair(self):
        self.inspect()
        review = self.review("&adc0")
        self.assertEqual(len(review["repair_choices"]), 1)
        approved = self.review("&adc0", repairs=[review["repair_choices"][0]["id"]])
        self.assertTrue(approved["can_apply"])
        self.assertNotIn("io-channels", approved["candidate_text"])

    def test_consumer_removal_keeps_provider_and_channel_configuration(self):
        self.inspect()
        review = self.review("&reader")
        self.assertTrue(review["can_apply"])
        self.assertIn("first: channel@1", review["candidate_text"])
        self.assertIn("adc0: alternate: adc", review["candidate_text"])
        self.assertNotIn("reader: consumer", review["candidate_text"])
