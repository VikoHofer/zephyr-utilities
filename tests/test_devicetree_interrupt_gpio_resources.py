"""Interrupt GPIO forms retain existing conservative dependency diagnostics."""

import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.interrupt_gpio import InterruptGPIOService

import test_devicetree_peripherals as fixtures


BINDINGS = {
    "gpio.yaml": '''compatible: test,gpio
include: base.yaml
gpio-cells: [pin, flags]
properties:
  gpio-controller: {type: boolean, required: true}
  "#gpio-cells": {type: int, const: 2, required: true}
  ngpios: {type: int}
  gpio-reserved-ranges: {type: array}
''',
    "bmi270.yaml": '''compatible: bosch,bmi270
include: base.yaml
properties:
  irq-gpios: {type: phandle-array}
''',
    "hdc2010.yaml": '''compatible: ti,hdc2010
include: base.yaml
properties:
  int-gpios: {type: phandle-array}
''',
    "max30210.yaml": '''compatible: adi,max30210
include: base.yaml
properties:
  interrupt-gpios: {type: phandle-array}
''',
}

SOURCE = '''/* Preserve devices and their shared GPIO provider. */
/ {
    gpio0: gpio { compatible = "test,gpio"; gpio-controller; #gpio-cells = <2>; ngpios = <8>; gpio-reserved-ranges = <6 2>; };
    first: imu { compatible = "bosch,bmi270"; irq-gpios = <&gpio0 1 256>; };
    second: humidity { compatible = "ti,hdc2010"; int-gpios = <&gpio0 2 1>; };
    third: temperature { compatible = "adi,max30210"; interrupt-gpios = <&gpio0 3 1>; };
};
'''


class InterruptGPIOResourceTests(unittest.TestCase):
    binding = fixtures.PeripheralTests.binding
    guard = fixtures.PeripheralTests.guard

    def setUp(self):
        fixtures.PeripheralTests.setUp(self)
        for name, content in BINDINGS.items():
            (self.bindings_dir / name).write_text(content)
        self.interrupt_gpio = InterruptGPIOService(self.drafts)
        self.drafts.interrupt_gpio = self.interrupt_gpio
        self.resources = self.app.context.services["devicetree_resources"]
        self.removal = self.app.context.services["devicetree_removal"]

    def inspect(self, source=SOURCE):
        self.draft = self.drafts.source(self.guard(text=source))
        self.document = self.drafts.drafts[self.draft["id"]].document
        self.schema = self.interrupt_gpio.inspect(self.guard())
        self.observation = self.resources.inspect(self.guard())

    def findings(self, code):
        return [item for item in self.observation["diagnostics"] if item["code"] == code]

    def review(self, selector, repairs=None):
        return self.removal.review(self.guard(schema_id=self.observation["schema_id"],
            node_id=self.document.find_node(selector).id, repairs=[] if repairs is None else repairs))

    def test_all_three_recipe_properties_have_literal_provider_dependencies(self):
        before = self.overlay.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            self.inspect()
        self.assertEqual({row["property"] for row in self.schema["consumers"]}, {"irq-gpios", "int-gpios", "interrupt-gpios"})
        self.assertTrue(all(row["editable"] for row in self.schema["consumers"]))
        references = self.observation["graph"]["references"]
        self.assertEqual(len(references), 3)
        self.assertTrue(all(row["resolution"] == "resolved" for row in references))
        self.assertFalse(self.findings("gpio-pin-conflict"))
        self.assertEqual(self.overlay.read_bytes(), before)

    def test_recipe_edit_updates_shared_input_diagnostics_without_claiming_exclusive_conflict(self):
        self.inspect()
        consumer = next(row for row in self.schema["consumers"] if row["property"] == "irq-gpios")
        self.draft = self.drafts.command(self.guard(command={"kind": "interrupt_gpio_set", "schema_id": self.schema["schema_id"],
            "node_id": consumer["node_id"], "property": consumer["property"], "entry": {"reference": "&gpio0", "cells": [2, 257]}}))
        self.observation = self.resources.inspect(self.guard())
        finding = self.findings("gpio-pin-conflict")[0]
        self.assertEqual(finding["severity"], "unknown")
        self.assertEqual({item["path"] for item in finding["locations"]}, {"/imu", "/humidity"})
        self.assertIn("sharing compatibility is not established", finding["message"])
        self.assertIn("irq-gpios = <&gpio0 2 257>;", self.draft["text"])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)

    def test_disabled_shared_irq_consumer_does_not_establish_simultaneous_use(self):
        self.inspect(SOURCE.replace("irq-gpios = <&gpio0 1 256>", "irq-gpios = <&gpio0 2 256>")
                     .replace("second: humidity {", 'second: humidity { status = "disabled";'))
        self.assertEqual(self.findings("gpio-pin-conflict")[0]["severity"], "warning")

    def test_provider_removal_lists_each_recipe_and_requires_individual_optional_repairs(self):
        self.inspect()
        review = self.review("&gpio0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(len(review["repair_choices"]), 3)
        self.assertEqual({item["location"]["name"] for item in review["dependants"]}, {"irq-gpios", "int-gpios", "interrupt-gpios"})
        partly = self.review("&gpio0", [review["repair_choices"][0]["id"]])
        self.assertFalse(partly["can_apply"])
        complete = self.review("&gpio0", [item["id"] for item in review["repair_choices"]])
        self.assertTrue(complete["can_apply"])
        self.assertNotIn("gpio0: gpio", complete["candidate_text"])
        self.assertTrue(all(text in complete["candidate_text"] for text in ('compatible = "bosch,bmi270"', 'compatible = "ti,hdc2010"', 'compatible = "adi,max30210"')))

    def test_required_local_recipe_binding_prevents_automatic_provider_repair(self):
        (self.bindings_dir / "bmi270.yaml").write_text(BINDINGS["bmi270.yaml"].replace("type: phandle-array", "type: phandle-array, required: true"))
        self.inspect()
        review = self.review("&gpio0")
        self.assertFalse(review["can_apply"])
        self.assertEqual(len(review["repair_choices"]), 2)
        self.assertTrue(any("required reference" in item["message"] for item in review["blockers"]))

    def test_consumer_removal_preserves_shared_gpio_provider_and_other_irq_consumers(self):
        self.inspect()
        review = self.review("&first")
        self.assertTrue(review["can_apply"])
        self.draft = self.drafts.command(self.guard(command={"kind": "remove_reviewed", "review_id": review["review_id"]}))
        self.assertIn("gpio0: gpio", self.draft["text"])
        self.assertIn("int-gpios = <&gpio0 2 1>;", self.draft["text"])
        self.assertIn("interrupt-gpios = <&gpio0 3 1>;", self.draft["text"])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)
