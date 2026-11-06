"""Removal reviews expose dependencies and apply only selected exact source repairs."""

from unittest.mock import patch
import unittest

import test_devicetree_peripherals as fixtures
import test_devicetree_pins as pin_fixtures


SOURCE = fixtures.SOURCE


CONSUMER_BINDING = '''compatible: test,consumer
properties:
  link: {type: phandle}
  links: {type: phandles}
  required-link: {type: phandle, required: true}
  path-link: {type: path}
'''


class RemovalTests(unittest.TestCase):
    setUp = fixtures.PeripheralTests.setUp
    binding = fixtures.PeripheralTests.binding
    guard = fixtures.PeripheralTests.guard

    def load(self, extra=""):
        if extra:
            self.draft = self.drafts.source(self.guard(text=SOURCE.replace("/ {", "/ {\n" + extra, 1)))
        self.resources = self.app.context.services["devicetree_resources"]
        self.removal = self.app.context.services["devicetree_removal"]
        self.schema = self.resources.inspect(self.guard())
        self.document = self.drafts.drafts[self.draft["id"]].document
        return self.schema

    def consumer(self, declaration):
        (self.bindings_dir / "consumer.yaml").write_text(CONSUMER_BINDING)
        self.load('consumer { compatible = "test,consumer"; ' + declaration + " };\n")

    def review(self, selector="&first", repairs=None):
        return self.removal.review(self.guard(schema_id=self.schema["schema_id"],
            node_id=self.document.find_node(selector).id, repairs=[] if repairs is None else repairs))

    def apply(self, review):
        self.draft = self.drafts.command(self.guard(command={"kind": "remove_reviewed", "review_id": review["review_id"]}))
        return self.draft

    def test_preview_is_read_only_and_leaf_removal_is_one_undoable_draft_revision(self):
        self.load()
        before = self.draft["text"]
        with patch("subprocess.Popen", side_effect=AssertionError("No project tools")):
            review = self.review()
        self.assertTrue(review["can_apply"])
        self.assertEqual(review["dependants"], [])
        self.assertTrue(review["unknowns"])
        self.assertIn('-            handwritten = <SOME_MACRO(2)>;', review["diff"]["text"])
        self.assertEqual(self.draft["text"], before)
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.apply(review)
        self.assertEqual(self.draft["revision"], 1)
        self.assertNotIn("sensor@48", self.draft["text"])
        self.assertIn('cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;', self.draft["text"])
        self.assertIn('i2c0: i2c', self.draft["text"])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], before)
        self.draft = self.drafts.redo(self.guard())
        self.assertNotIn("sensor@48", self.draft["text"])
        self.assertEqual(self.overlay.read_text(), SOURCE)

    def test_alias_and_chosen_repairs_are_individually_selected_and_reviewed(self):
        self.load('aliases { sensor = &first; unrelated = &uart0; };\nchosen { device = "/i2c/sensor@48"; };')
        before = self.draft["text"]
        review = self.review()
        self.assertFalse(review["can_apply"])
        self.assertEqual(len(review["dependants"]), 2)
        self.assertEqual(len(review["repair_choices"]), 2)
        self.assertTrue(all(not choice["selected"] for choice in review["repair_choices"]))
        one = self.review(repairs=[review["repair_choices"][0]["id"]])
        self.assertFalse(one["can_apply"])
        both = self.review(repairs=[choice["id"] for choice in review["repair_choices"]])
        self.assertTrue(both["can_apply"])
        self.assertIn("unrelated = &uart0;", both["candidate_text"])
        self.assertNotIn("sensor = &first;", both["candidate_text"])
        self.assertEqual(self.draft["text"], before)
        revision = self.draft["revision"]
        self.apply(both)
        self.assertEqual(self.draft["revision"], revision + 1)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], before)

    def test_optional_exclusive_reference_can_be_removed_without_deleting_consumer(self):
        self.consumer("link = <&first>;")
        initial = self.review()
        self.assertEqual(len(initial["repair_choices"]), 1)
        reviewed = self.review(repairs=[initial["repair_choices"][0]["id"]])
        self.assertTrue(reviewed["can_apply"])
        self.apply(reviewed)
        self.assertIn('consumer { compatible = "test,consumer";', self.draft["text"])
        self.assertNotIn("link =", self.draft["text"])

    def test_required_reference_is_a_blocker_without_an_automatic_repair(self):
        self.consumer("required-link = <&first>;")
        review = self.review()
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertTrue(any("required reference" in blocker["message"] for blocker in review["blockers"]))
        with self.assertRaisesRegex(ValueError, "unresolved dependants"):
            self.apply(review)

    def test_mixed_reference_list_preserves_unrelated_provider(self):
        self.consumer("links = <&first &gpio0>;")
        review = self.review()
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertIn("links = <&first &gpio0>;", self.draft["text"])

    def test_opaque_reference_does_not_offer_whole_property_repair(self):
        self.consumer("link = LINK_TO(&first);")
        review = self.review()
        self.assertFalse(review["can_apply"])
        self.assertTrue(review["dependants"])
        self.assertEqual(review["repair_choices"], [])

    def test_provider_lists_required_gpio_and_shared_chip_select_consumers(self):
        self.load('extra_leds { compatible = "gpio-leds"; indicator { gpios = <&gpio0 3 0>; }; };')
        review = self.review("&gpio0")
        self.assertFalse(review["can_apply"])
        properties = {self.document._property(item["property_id"])[1].name for item in review["dependants"]}
        self.assertEqual(properties, {"gpios", "cs-gpios"})
        self.assertEqual(len(review["dependants"]), 3)
        self.assertTrue(any("required reference" in blocker["message"] for blocker in review["blockers"]))
        self.assertIn('gpio0: gpio', self.draft["text"])

    def test_subtree_is_fully_listed_and_only_its_source_is_removed(self):
        self.load()
        review = self.review("&i2c0")
        self.assertTrue(review["can_apply"])
        self.assertEqual([node["name"] for node in review["descendants"]], ["sensor@48"])
        self.apply(review)
        self.assertNotIn("i2c0:", self.draft["text"])
        self.assertIn("spi0:", self.draft["text"])
        self.assertIn("gpio0:", self.draft["text"])

    def test_nested_target_consumers_are_not_hidden_by_parent_removal(self):
        self.load('aliases { sensor = &first; };')
        review = self.review("&i2c0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["dependants"][0]["reference"], "&first")
        repaired = self.review("&i2c0", [review["repair_choices"][0]["id"]])
        self.assertTrue(repaired["can_apply"])

    def test_structural_override_and_root_removal_are_blocked(self):
        self.load()
        review = self.review("/")
        self.assertFalse(review["can_apply"])
        self.assertTrue(any("Root" in blocker["message"] for blocker in review["blockers"]))
        self.draft = self.drafts.source(self.guard(text=SOURCE + '\n&first { status = "okay"; };\n'))
        self.load()
        original = next(node for node in self.document.syntax.nodes if "first" in node.labels)
        review = self.removal.review(self.guard(schema_id=self.schema["schema_id"], node_id=original.id, repairs=[]))
        self.assertFalse(review["can_apply"])
        self.assertTrue(any(item["kind"] == "declaration-reference" for item in review["dependants"]))
        override = next(node for node in self.document.syntax.nodes if node.name == "&first")
        review = self.removal.review(self.guard(schema_id=self.schema["schema_id"], node_id=override.id, repairs=[]))
        self.assertFalse(review["can_apply"])

    def test_binding_path_and_untyped_path_consumers_do_not_disappear(self):
        self.consumer('path-link = "/i2c/sensor@48";')
        review = self.review()
        self.assertFalse(review["can_apply"])
        self.assertEqual(len(review["repair_choices"]), 1)
        self.load('untyped { unknown-path = "/i2c/sensor@48"; };')
        review = self.review()
        self.assertFalse(review["can_apply"])
        self.assertTrue(any("untyped path" in blocker["message"] for blocker in review["blockers"]))

    def test_unresolved_path_inside_subtree_blocks_ancestor_removal(self):
        self.load('chosen { nested = "/i2c/not-declared"; };')
        review = self.review("&i2c0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["dependants"][0]["reference"], "&{/i2c/not-declared}")

    def test_property_label_consumers_prevent_removing_a_repair_property(self):
        self.load('aliases { link_name: sensor = &first; };\nchosen { dependency = &link_name; };')
        review = self.review()
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])

    def test_changed_source_context_or_binding_invalidates_review(self):
        self.load()
        review = self.review()
        self.draft = self.drafts.source(self.guard(text=self.draft["text"] + "\n/* New draft */\n"))
        with self.assertRaisesRegex(ValueError, "earlier draft/context"):
            self.apply(review)
        self.load()
        review = self.review()
        (self.bindings_dir / "temp-i2c.yaml").write_text('compatible: changed,binding\n')
        with self.assertRaises(ValueError):
            self.apply(review)

    def test_expired_review_cannot_mutate_and_pool_is_bounded(self):
        self.load()
        first = self.review()
        self.removal.reviews[first["review_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.apply(first)
        for _ in range(20):
            self.review()
        self.assertEqual(len(self.removal.reviews), 16)
        self.assertEqual(self.draft["text"], SOURCE)

    def test_unknown_duplicate_or_stale_repairs_are_rejected(self):
        self.load('aliases { sensor = &first; };')
        choice = self.review()["repair_choices"][0]["id"]
        for selected in (["not-a-repair"], [choice, choice], [True], {}):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.review(repairs=selected)

    def test_dependency_truncation_prevents_application(self):
        self.load()
        self.resources.observations[self.schema["schema_id"]]["payload"]["graph"]["truncated"] = True
        review = self.review()
        self.assertFalse(review["can_apply"])
        self.assertTrue(any("truncated" in blocker["message"] for blocker in review["blockers"]))

    def test_local_pin_state_and_groups_are_reviewed_as_one_subtree(self):
        text = '''&pinctrl {
    test_state: test_state {
        group1 { psels = <NRF_PSEL(UART_TX, 1, 2)>; };
    };
};
'''
        self.draft = self.drafts.source(self.guard(text=text))
        self.load()
        review = self.review("&test_state")
        self.assertTrue(review["can_apply"])
        self.assertEqual([node["name"] for node in review["descendants"]], ["group1"])
        self.apply(review)
        self.assertIn("&pinctrl", self.draft["text"])
        self.assertNotIn("test_state:", self.draft["text"])

    def test_nested_pin_group_reports_ancestor_state_consumers(self):
        text = '''&pinctrl {
    test_state: test_state {
        group1 { psels = <NRF_PSEL(UART_TX, 1, 2)>; };
    };
};
&uart0 { pinctrl-0 = <&test_state>; };
'''
        self.draft = self.drafts.source(self.guard(text=text))
        self.load()
        group = next(node for node in self.document.syntax.nodes if node.name == "group1")
        review = self.removal.review(self.guard(schema_id=self.schema["schema_id"], node_id=group.id, repairs=[]))
        self.assertFalse(review["can_apply"])
        self.assertTrue(any(item["kind"] == "ancestor-pinctrl-reference" for item in review["dependants"]))
        self.assertTrue(any("nested pin group" in item["message"] for item in review["blockers"]))

    def test_chip_select_routing_is_not_deleted_as_an_optional_property_repair(self):
        text = SOURCE.replace('        cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;', '''        cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;
        first_spi: sensor@0 { compatible = "test,temperature"; reg = <0>; spi-max-frequency = <1000000>; };
        second_spi: sensor@1 { compatible = "test,temperature"; reg = <1>; spi-max-frequency = <1000000>; };''')
        self.draft = self.drafts.source(self.guard(text=text))
        self.load()
        review = self.review("&gpio0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        indirect = [item for item in review["dependants"] if item["kind"] == "spi-chip-select-consumer"]
        self.assertEqual(len(indirect), 2)
        self.assertEqual({item["location"]["name"] for item in indirect}, {"reg"})
        self.assertTrue(any("Indexed chip-select" in item["message"] for item in review["blockers"]))
        self.assertEqual(self.draft["text"], text)

    def test_optional_pin_state_property_preserves_companion_state_semantics(self):
        (self.bindings_dir / "consumer.yaml").write_text('''compatible: test,consumer
properties:
  pinctrl-0: {type: phandles}
  pinctrl-names: {type: string-array}
''')
        self.load('consumer { compatible = "test,consumer"; pinctrl-0 = <&first>; pinctrl-names = "default"; };')
        review = self.review()
        self.assertFalse(review["can_apply"])
        self.assertEqual(review["repair_choices"], [])
        self.assertTrue(any("companion-state" in item["message"] for item in review["blockers"]))
        prop = self.document.find_property(self.document.find_node("/consumer").id, "pinctrl-0")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.review(repairs=["remove-property:" + prop.id])


class PinEvidenceRemovalTests(unittest.TestCase):
    setUp = pin_fixtures.PinServiceTests.setUp
    binding = pin_fixtures.PinServiceTests.binding
    guard = pin_fixtures.PinServiceTests.guard
    review = RemovalTests.review
    apply = RemovalTests.apply

    def load(self, label="uart_default", extra=""):
        text = f'''&pinctrl {{
    {label}: {label} {{
        group1 {{ psels = <NRF_PSEL(UART_TX, 1, 2)>; }};
    }};
}};
''' + extra
        self.draft = self.drafts.source(self.guard(text=text))
        self.resources = self.app.context.services["devicetree_resources"]
        self.removal = self.app.context.services["devicetree_removal"]
        self.schema = self.resources.inspect(self.guard())
        self.document = self.drafts.drafts[self.draft["id"]].document
        self.assertTrue(self.schema["pins"]["supported"])

    def test_inherited_controller_reference_blocks_local_state_and_group_removal(self):
        self.load()
        target = self.document.find_node("&uart_default")
        group = next(node for node in self.document.syntax.nodes if node.name == "group1")
        for node in (target, group):
            with self.subTest(node=node.name):
                review = self.removal.review(self.guard(schema_id=self.schema["schema_id"], node_id=node.id, repairs=[]))
                self.assertFalse(review["can_apply"])
                consumer = next(item for item in review["dependants"] if item["kind"] == "known-pin-state-consumer")
                self.assertEqual(consumer["reference"], "&uart_default")
                self.assertIn("uart0 default", consumer["message"])
                self.assertIsNone(consumer["location"])
                self.assertIsNone(consumer["source_node_id"])
                self.assertEqual(review["repair_choices"], [])
                self.assertTrue(any("Use Pins or source" in item["message"] for item in review["blockers"]))

    def test_unreferenced_local_state_remains_removable_with_pin_evidence(self):
        self.load("unused_local_state")
        review = self.review("&unused_local_state")
        self.assertTrue(review["can_apply"])
        self.assertEqual(review["dependants"], [])
        before = self.draft["text"]
        self.apply(review)
        self.assertNotIn("unused_local_state", self.draft["text"])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], before)

    def test_optional_local_repair_cannot_clear_known_inherited_consumer(self):
        self.load(extra="/ { aliases { selected = &uart_default; }; };\n")
        initial = self.review("&uart_default")
        self.assertEqual(len(initial["repair_choices"]), 1)
        reviewed = self.review("&uart_default", [initial["repair_choices"][0]["id"]])
        self.assertFalse(reviewed["can_apply"])
        self.assertTrue(any(item["kind"] == "known-pin-state-consumer" for item in reviewed["dependants"]))
        with self.assertRaisesRegex(ValueError, "unresolved dependants"):
            self.apply(reviewed)
