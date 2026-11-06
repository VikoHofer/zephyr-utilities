"""PWM diagnostics retain unknown capabilities and guard provider dependencies."""

import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.bindings import BindingCatalog
from zephyr_utilities.features.devicetree.peripherals import PeripheralService
from zephyr_utilities.features.devicetree.pwm_resources import pwm_diagnostics

import test_devicetree_peripherals as fixtures


BINDINGS = {
    "pwm-three.yaml": '''compatible: test,pwm-three
include: base.yaml
pwm-cells: [channel, period, flags]
properties:
  "#pwm-cells": {type: int, const: 3}
  channels: {type: int}
''',
    "pwm-two.yaml": '''compatible: test,pwm-two
include: base.yaml
pwm-cells: [channel, period]
properties:
  "#pwm-cells": {type: int, const: 2}
  channels: {type: int}
''',
    "pwm-consumer.yaml": '''compatible: test,pwm-consumer
include: base.yaml
properties:
  pwms: {type: phandle-array}
''',
    "pwm-leds.yaml": '''compatible: pwm-leds
include: base.yaml
child-binding:
  properties:
    pwms: {type: phandle-array, required: true}
''',
}

SOURCE = '''/* Keep unrelated source and provider declarations. */
/ {
    pwm0: alternate: pwm { compatible = "test,pwm-three"; #pwm-cells = <3>; channels = <4>; };
    pwm1: pwm_second { compatible = "test,pwm-two"; #pwm-cells = <2>; };
    leds { compatible = "pwm-leds";
        first: red { pwms = <&pwm0 1 1000000 0>; };
        green { pwms = <&pwm0 2 1000000 0>; };
        blue { pwms = <&pwm1 1 1000000>; };
    };
};
'''


class PWMResourceTests(unittest.TestCase):
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
        self.diagnostics = pwm_diagnostics(self.document, self.projection)
        return self.diagnostics

    def findings(self, code):
        return [item for item in self.diagnostics if item["code"] == code]

    def review(self, selector):
        observation = self.resources.inspect(self.guard())
        return self.removal.review(self.guard(schema_id=observation["schema_id"],
            node_id=self.document.find_node(selector).id, repairs=[]))

    def test_distinct_channels_and_providers_are_not_claimed_conflicting(self):
        original = self.overlay.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("No project tools")):
            self.inspect()
        self.assertFalse([item for item in self.diagnostics if item["severity"] == "error"])
        self.assertFalse(self.findings("pwm-channel-sharing-unknown"))
        self.assertFalse(self.findings("pwm-timer-coupling-unknown"))
        self.assertEqual(len(self.findings("pwm-channel-range-unknown")), 2)
        self.assertEqual(self.overlay.read_bytes(), original)

    def test_zero_period_uses_consumer_source_location_and_enabled_state(self):
        self.inspect(SOURCE.replace("<&pwm0 1 1000000 0>", "<&pwm0 1 0 0>"))
        finding = self.findings("pwm-period-invalid")[0]
        self.assertEqual(finding["severity"], "error")
        loc = finding["locations"][0]
        self.assertEqual(self.document.source[loc["start"]:loc["end"]], b"pwms = <&pwm0 1 0 0>;")
        self.assertEqual(finding["repair_choices"][0]["location"], loc)

    def test_resource_observation_includes_pwm_findings_without_mutating_the_draft(self):
        self.inspect(SOURCE.replace("<&pwm0 1 1000000 0>", "<&pwm0 1 0 0>"))
        before = dict(self.draft)
        with patch("subprocess.Popen", side_effect=AssertionError("No project tools")):
            observation = self.resources.inspect(self.guard())
        findings = [item for item in observation["diagnostics"] if item["code"] == "pwm-period-invalid"]
        self.assertEqual(findings, self.findings("pwm-period-invalid"))
        self.assertEqual(self.draft, before)
        self.assertEqual(observation["revision"], self.draft["revision"])

    def test_disabled_provider_consumer_or_opaque_status_downgrades_invalid_period(self):
        base = SOURCE.replace("<&pwm0 1 1000000 0>", "<&pwm0 1 0 0>")
        cases = [(base.replace('channels = <4>;', 'channels = <4>; status = "disabled";'), "warning"),
                 (base.replace('first: red {', 'first: red { status = "disabled";'), "warning"),
                 (base.replace('first: red {', 'first: red { status = UNKNOWN_STATUS;'), "unknown")]
        for source, severity in cases:
            with self.subTest(severity=severity):
                self.inspect(source)
                self.assertEqual(self.findings("pwm-period-invalid")[0]["severity"], severity)
        self.inspect(cases[0][0])
        warning = self.findings("pwm-provider-disabled")[0]
        self.assertEqual(warning["severity"], "warning")
        self.assertEqual(len(warning["locations"]), 2)

    def test_shared_channel_with_complementary_flags_remains_unknown(self):
        self.inspect(SOURCE.replace("<&pwm0 2 1000000 0>", "<&pwm0 1 1000000 256>"))
        finding = self.findings("pwm-channel-sharing-unknown")[0]
        self.assertEqual(finding["severity"], "unknown")
        self.assertEqual({item["path"] for item in finding["locations"]}, {"/leds/red", "/leds/green"})
        self.assertFalse([item for item in self.diagnostics if item["severity"] == "error"])

    def test_alternate_label_and_path_references_identify_the_same_provider(self):
        source = SOURCE.replace("<&pwm0 1 1000000 0>", "<&alternate 1 1000000 0>")
        source = source.replace("<&pwm0 2 1000000 0>", "<&{/pwm} 1 1000000 0>")
        self.inspect(source)
        self.assertEqual(len(self.findings("pwm-channel-sharing-unknown")), 1)
        self.assertFalse(self.findings("pwm-provider-unknown"))

    def test_different_periods_do_not_claim_independent_channels_or_universal_conflict(self):
        self.inspect(SOURCE.replace("<&pwm0 2 1000000 0>", "<&pwm0 2 2000000 0>"))
        finding = self.findings("pwm-timer-coupling-unknown")[0]
        self.assertEqual(finding["severity"], "unknown")
        self.assertEqual(len(finding["locations"]), 2)
        self.assertFalse(self.findings("pwm-channel-sharing-unknown"))
        self.assertFalse([item for item in self.diagnostics if item["severity"] == "error"])

    def test_disabled_consumer_does_not_establish_simultaneous_sharing_or_timing(self):
        source = SOURCE.replace("green {", 'green { status = "disabled";')
        self.inspect(source.replace("<&pwm0 2 1000000 0>", "<&pwm0 1 2000000 0>"))
        self.assertEqual(self.findings("pwm-channel-sharing-unknown")[0]["severity"], "warning")
        self.inspect(source.replace("<&pwm0 2 1000000 0>", "<&pwm0 2 2000000 0>"))
        self.assertEqual(self.findings("pwm-timer-coupling-unknown")[0]["severity"], "warning")

    def test_channels_count_is_not_an_assumed_zero_based_range(self):
        self.inspect(SOURCE.replace("<&pwm0 1 1000000 0>", "<&pwm0 4 1000000 0>"))
        self.assertFalse([item for item in self.diagnostics if item["severity"] == "error"])
        messages = [item["message"] for item in self.findings("pwm-channel-range-unknown")]
        self.assertTrue(any("value is 4" in message and "numbering" in message for message in messages))
        self.assertTrue(any("No local channel count" in message for message in messages))

    def test_missing_inherited_and_wrong_count_providers_remain_unknown(self):
        cases = [SOURCE.replace("<&pwm0 1 1000000 0>", "<&inherited 1 1000000 0>"),
                 SOURCE.replace("#pwm-cells = <3>", "#pwm-cells = <2>"),
                 SOURCE.replace("#pwm-cells = <3>;", ""),
                 SOURCE.replace("<&pwm0 1 1000000 0>", "<&pwm0 1 1000000>")]
        for source in cases:
            with self.subTest(source=source):
                self.inspect(source)
                self.assertTrue(self.findings("pwm-provider-unknown"))
                self.assertFalse([item for item in self.diagnostics if item["severity"] == "error"])

    def test_nonstandard_provider_cell_names_are_not_reinterpreted(self):
        path = self.bindings_dir / "pwm-three.yaml"
        path.write_text(BINDINGS[path.name].replace("[channel, period, flags]", "[period, channel, flags]"))
        self.inspect()
        self.assertEqual(len(self.findings("pwm-provider-unknown")), 2)
        self.assertFalse(self.findings("pwm-period-invalid"))

    def test_unsupported_property_metadata_is_not_treated_as_an_editable_pwm_contract(self):
        path = self.bindings_dir / "pwm-leds.yaml"
        path.write_text(BINDINGS[path.name].replace("type: phandle-array, required: true", "type: phandle-array, required: true, unsupported: true"))
        self.inspect(SOURCE.replace("<&pwm0 1 1000000 0>", "<&pwm0 1 0 0>"))
        self.assertTrue(self.findings("pwm-consumer-unknown"))
        self.assertFalse(self.findings("pwm-period-invalid"))

    def test_invalid_provider_binding_constraints_do_not_authorize_period_evidence(self):
        path = self.bindings_dir / "pwm-three.yaml"
        cases = [BINDINGS[path.name].replace("const: 3", "const: 2"),
                 BINDINGS[path.name].replace("type: int, const: 3", "type: string"),
                 BINDINGS[path.name].replace("properties:", 'properties:\n  compatible: {type: string-array, const: [test,wrong]}')]
        for content in cases:
            with self.subTest(content=content):
                path.write_text(content)
                self.inspect(SOURCE.replace("<&pwm0 1 1000000 0>", "<&pwm0 1 0 0>"))
                self.assertTrue(self.findings("pwm-provider-unknown"))
                self.assertFalse(self.findings("pwm-period-invalid"))

    def test_opaque_and_multiple_specifiers_do_not_become_known_allocations(self):
        cases = [SOURCE.replace("<&pwm0 1 1000000 0>", "<&pwm0 1 PWM_MSEC(1) 0>"),
                 SOURCE.replace("<&pwm0 1 1000000 0>", "<&pwm0 1 1000000 0>, <&pwm0 2 1000000 0>")]
        for source in cases:
            with self.subTest(source=source):
                self.inspect(source)
                self.assertEqual(len(self.findings("pwm-allocation-unknown")), 1)
                self.assertFalse(self.findings("pwm-channel-sharing-unknown"))

    def test_untyped_repeated_deleted_and_ambiguous_consumers_remain_unknown(self):
        cases = [SOURCE.replace('compatible = "pwm-leds";', 'compatible = "test,unknown";'),
                 SOURCE.replace("pwms = <&pwm0 1 1000000 0>;", "pwms = <&pwm0 1 1000000 0>; pwms = <&pwm0 2 1000000 0>;"),
                 SOURCE.replace("pwms = <&pwm0 1 1000000 0>;", "/delete-property/ pwms;"),
                 SOURCE.replace("first: red {", "first: red {}; first: red {")]
        for source in cases:
            with self.subTest(source=source):
                self.inspect(source)
                self.assertTrue(self.findings("pwm-consumer-unknown"))

    def test_diagnostic_construction_has_a_visible_bound(self):
        source = '/ { ' + ''.join(f'node_{index} {{ pwms = <&external 1 1000 0>; }};' for index in range(400)) + ' };'
        self.inspect(source)
        self.assertEqual(len(self.diagnostics), 256)
        self.assertEqual(self.diagnostics[-1]["code"], "pwm-diagnostics-truncated")

    def test_provider_removal_lists_required_pwm_consumers_and_cannot_auto_repair(self):
        self.inspect()
        review = self.review("&pwm0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(len(review["dependants"]), 2)
        self.assertEqual(review["repair_choices"], [])
        self.assertTrue(all(item["location"]["name"] == "pwms" for item in review["dependants"]))
        self.assertTrue(any("required reference" in item["message"] for item in review["blockers"]))

    def test_pwm_consumer_removal_keeps_shared_providers_and_undo_restores_source(self):
        self.inspect()
        review = self.review("&first")
        self.assertTrue(review["can_apply"])
        before = self.draft["text"]
        self.draft = self.drafts.command(self.guard(command={"kind": "remove_reviewed", "review_id": review["review_id"]}))
        self.assertNotIn("first: red", self.draft["text"])
        self.assertIn('pwm0: alternate: pwm { compatible = "test,pwm-three";', self.draft["text"])
        self.assertIn("green { pwms", self.draft["text"])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], before)

    def test_named_optional_pwm_reference_requires_explicit_companion_repair(self):
        source = '''/ {
            pwm0: pwm { compatible = "test,pwm-three"; #pwm-cells = <3>; };
            consumer { compatible = "test,pwm-consumer"; pwms = <&pwm0 1 1000000 0>; pwm-names = "output"; };
        };'''
        self.inspect(source)
        review = self.review("&pwm0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertTrue(any("companion names" in item["message"] for item in review["blockers"]))
        self.assertEqual(self.draft["text"], source)

    def test_unnamed_optional_pwm_reference_still_offers_explicit_property_repair(self):
        source = '''/ {
            pwm0: pwm { compatible = "test,pwm-three"; #pwm-cells = <3>; };
            consumer { compatible = "test,pwm-consumer"; pwms = <&pwm0 1 1000000 0>; };
        };'''
        self.inspect(source)
        review = self.review("&pwm0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(len(review["repair_choices"]), 1)
        choice = review["repair_choices"][0]
        self.assertEqual(choice["location"]["name"], "pwms")
        approved = self.removal.review(self.guard(schema_id=review["schema_id"],
            node_id=self.document.find_node("&pwm0").id, repairs=[choice["id"]]))
        self.assertTrue(approved["can_apply"])
        self.assertNotIn("pwms =", approved["candidate_text"])
        self.assertIn('consumer { compatible = "test,pwm-consumer";', approved["candidate_text"])
