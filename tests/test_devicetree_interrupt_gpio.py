"""Interrupt GPIO recipes mutate one local property without inventing triggers."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.features.devicetree.interrupt_gpio import InterruptGPIOService


BINDINGS = {
    "base.yaml": '''properties:
  compatible: {type: string-array, required: true}
  status: {type: string, enum: [okay, disabled]}
''',
    "gpio.yaml": '''compatible: test,gpio
include: base.yaml
gpio-cells: [pin, flags]
properties:
  gpio-controller: {type: boolean, required: true}
  "#gpio-cells": {type: int, const: 2, required: true}
  ngpios: {type: int, default: 32}
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
    "unknown.yaml": '''compatible: test,unknown-signal
include: base.yaml
properties:
  irq-gpios: {type: phandle-array}
''',
}
SOURCE = '''/* Keep this interrupt signal source note. */
/ {
    gpio0: gpio { compatible = "test,gpio"; gpio-controller; #gpio-cells = <2>;
        ngpios = <16>; gpio-reserved-ranges = <3 2>; status = "disabled"; };
    gpio1: other-gpio { compatible = "test,gpio"; gpio-controller; #gpio-cells = <2>; };
    motion { compatible = "bosch,bmi270"; irq-gpios = <&gpio0 2 256>; keep = <CUSTOM(2)>; };
    humidity { compatible = "ti,hdc2010"; int-gpios = <&gpio1 7 17>; };
    temperature { compatible = "adi,max30210"; };
    unverified { compatible = "test,unknown-signal"; irq-gpios = <&gpio0 8 0>; };
};
'''


class InterruptGPIOTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.bindings_dir = self.application / "dts/bindings"
        self.bindings_dir.mkdir(parents=True)
        (self.application / "CMakeLists.txt").write_text('message(FATAL_ERROR "Never execute fixture")\n')
        self.overlay = self.application / "app.overlay"
        self.overlay.write_text(SOURCE)
        for name, content in BINDINGS.items():
            (self.bindings_dir / name).write_text(content)
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = self.app.context.services["devicetree_drafts"]
        self.interrupt_gpio = InterruptGPIOService(self.drafts)
        self.drafts.interrupt_gpio = self.interrupt_gpio
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def load(self):
        self.schema = self.interrupt_gpio.inspect(self.guard())
        return self.schema

    def source(self, text):
        self.draft = self.drafts.source(self.guard(text=text))
        return self.load()

    def consumer(self, path="/motion"):
        return next(item for item in self.schema["consumers"] if item["path"] == path)

    def provider(self, reference="&gpio0"):
        return next(item for item in self.schema["providers"] if item["reference"] == reference)

    def command(self, **fields):
        self.draft = self.drafts.command(self.guard(command={"kind": "interrupt_gpio_set", "schema_id": self.schema["schema_id"], **fields}))
        return self.draft

    def assign(self, *, path="/motion", reference="&gpio0", pin=5, flags=257):
        consumer = self.consumer(path)
        return self.command(node_id=consumer["node_id"], property=consumer["property"], entry={"reference": reference, "cells": [pin, flags]})

    def test_observation_uses_local_binding_evidence_without_tools_or_mutation(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            self.load()
        self.assertEqual([item["property"] for item in self.schema["consumers"]], ["irq-gpios", "int-gpios", "interrupt-gpios"])
        self.assertEqual(self.provider()["cells"], ["pin", "flags"])
        self.assertEqual(self.provider()["status"], "disabled")
        self.assertEqual(self.provider()["pin_count"], 16)
        self.assertEqual(self.provider()["reserved_ranges"], [[3, 2]])
        self.assertTrue(self.consumer()["editable"])
        self.assertEqual(self.consumer()["entry"], {"reference": "&gpio0", "cells": [2, 256]})
        self.assertEqual(self.consumer()["signal"], "INT1")
        self.assertIn("does not select an interrupt edge", self.schema["diagnostics"][0])
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.assertEqual(self.draft["revision"], 0)

    def test_suffix_alone_never_establishes_an_interrupt_signal_recipe(self):
        self.load()
        self.assertFalse(any(item["path"] == "/unverified" for item in self.schema["consumers"]))
        unknown = next(row for row in self.schema["nodes"] if row["path"] == "/unverified")
        with self.assertRaises(ValueError):
            self.command(node_id=unknown["node_id"], property="irq-gpios", entry={"reference": "&gpio0", "cells": [1, 0]})

    def test_child_binding_compatible_metadata_does_not_establish_recipe_identity(self):
        (self.bindings_dir / "holder.yaml").write_text('''compatible: test,holder
include: base.yaml
child-binding:
  compatible: bosch,bmi270
  properties:
    irq-gpios: {type: phandle-array}
''')
        original = 'motion { compatible = "bosch,bmi270"; irq-gpios = <&gpio0 2 256>; keep = <CUSTOM(2)>; };'
        replacement = 'holder { compatible = "test,holder"; child { irq-gpios = <&gpio0 2 256>; }; };'
        self.source(SOURCE.replace(original, replacement))
        consumer = self.consumer("/holder/child")
        self.assertFalse(consumer["editable"])
        self.assertIn("literal local compatible", consumer["reason"])
        with self.assertRaises(ValueError):
            self.assign(path="/holder/child")

    def test_literal_compatible_fallback_list_can_select_the_audited_recipe(self):
        self.source(SOURCE.replace('compatible = "bosch,bmi270"', 'compatible = "unknown,newer-device", "bosch,bmi270"'))
        self.assertTrue(self.consumer()["editable"])
        self.assign()
        self.assertIn('compatible = "unknown,newer-device", "bosch,bmi270";', self.draft["text"])

    def test_assignment_changes_only_selected_property_and_one_undo_restores_source(self):
        self.load()
        self.assign(pin=6, flags=257)
        self.assertEqual(self.draft["text"], SOURCE.replace('<&gpio0 2 256>', '<&gpio0 6 257>'))
        self.assertEqual(self.draft["revision"], 1)
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)
        self.draft = self.drafts.redo(self.guard())
        self.assertIn('irq-gpios = <&gpio0 6 257>;', self.draft["text"])

    def test_all_three_recipes_edit_or_add_their_exact_property(self):
        self.load()
        for path, name in (("/motion", "irq-gpios"), ("/humidity", "int-gpios"), ("/temperature", "interrupt-gpios")):
            with self.subTest(path=path):
                self.assign(path=path, reference="&gpio1", pin=31, flags=65535)
                self.assertIn(f'{name} = <&gpio1 31 65535>;', self.draft["text"])
                self.load()
                self.assertEqual(self.consumer(path)["entry"], {"reference": "&gpio1", "cells": [31, 65535]})
        self.assertNotIn("pinctrl", self.draft["text"])
        self.assertNotIn("interrupt-parent", self.draft["text"])
        self.assertIn('keep = <CUSTOM(2)>;', self.draft["text"])

    def test_absent_ngpios_and_ranges_are_unknown_without_inferred_binding_defaults(self):
        self.load()
        self.assertIsNone(self.provider("&gpio1")["pin_count"])
        self.assertIsNone(self.provider("&gpio1")["reserved_ranges"])
        self.assign(reference="&gpio1", pin=31)
        self.assertEqual(self.draft["text"].count("ngpios"), 1)
        self.assertEqual(self.draft["text"].count("gpio-reserved-ranges"), 1)

    def test_api_pin_and_flag_widths_and_numeric_vendor_bits(self):
        self.load()
        for pin, flags in ((0, 0), (31, 65535), (1, 0x8000), (1, 0x8001)):
            self.assign(reference="&gpio1", pin=pin, flags=flags)
            self.load()
            self.assertEqual(self.consumer()["entry"]["cells"], [pin, flags])
        for pin, flags in ((-1, 0), (32, 0), (True, 0), ("1", 0), (1, -1), (1, 65536), (1, True), (1, "0")):
            with self.subTest(pin=pin, flags=flags), self.assertRaises(ValueError):
                self.assign(reference="&gpio1", pin=pin, flags=flags)

    def test_explicit_ngpios_and_reserved_ranges_restrict_assignment(self):
        self.load()
        for pin in (3, 4, 16, 31):
            with self.subTest(pin=pin), self.assertRaises(ValueError):
                self.assign(pin=pin)
        for pin in (0, 2, 5, 15):
            self.assign(pin=pin)
            self.load()
            self.assertEqual(self.consumer()["entry"]["cells"][0], pin)

    def test_invalid_imported_pin_and_flag_literals_can_be_repaired(self):
        for entry in ('<&gpio0 3 0>', '<&gpio0 100 0>', '<&gpio0 1 65536>'):
            self.source(SOURCE.replace('<&gpio0 2 256>', entry))
            self.assertTrue(self.consumer()["editable"])
            self.assign(pin=5, flags=0)
            self.assertIn('irq-gpios = <&gpio0 5 0>;', self.draft["text"])

    def test_multiple_opaque_unresolved_and_repeated_specifiers_remain_source_only(self):
        for replacement in ('irq-gpios = <&gpio0 1 0>, <&gpio1 2 0>;', 'irq-gpios = <&gpio0 1 GPIO_ACTIVE_LOW>;',
                            'irq-gpios = <&inherited 1 0>;', 'irq-gpios = <&gpio0 1>;',
                            'irq-gpios = <&gpio0 1 0>; irq-gpios = <&gpio0 2 0>;', '/delete-property/ irq-gpios;'):
            with self.subTest(replacement=replacement):
                self.source(SOURCE.replace('irq-gpios = <&gpio0 2 256>;', replacement))
                self.assertFalse(self.consumer()["editable"])
                with self.assertRaises(ValueError):
                    self.assign()

    def test_provider_needs_present_supported_gpio_controller_boolean(self):
        for text in (SOURCE.replace('gpio-controller; ', '', 1),
                     SOURCE.replace('gpio-controller;', 'gpio-controller = <1>;', 1),
                     SOURCE.replace('gpio-controller;', 'gpio-controller; gpio-controller;', 1),
                     SOURCE.replace('gpio-controller;', '/delete-property/ gpio-controller;', 1)):
            self.source(text)
            self.assertFalse(self.provider()["supported"])
            self.assertFalse(self.consumer()["editable"])
        path = self.bindings_dir / "gpio.yaml"
        for value in ('{type: int, required: true}', '{type: boolean, required: true, const: true}'):
            path.write_text(BINDINGS["gpio.yaml"].replace('{type: boolean, required: true}', value))
            self.source(SOURCE)
            self.assertFalse(self.provider()["supported"])

    def test_provider_count_layout_and_compatible_constraints_are_not_bypassed(self):
        path = self.bindings_dir / "gpio.yaml"
        for content in (BINDINGS["gpio.yaml"].replace('[pin, flags]', '[flags, pin]'),
                        BINDINGS["gpio.yaml"].replace('const: 2', 'const: 3'),
                        BINDINGS["gpio.yaml"].replace('const: 2', 'enum: [3]'),
                        BINDINGS["gpio.yaml"].replace('const: 2', 'const: 2, minimum: 0'),
                        BINDINGS["gpio.yaml"] + '  compatible: {const: [other,gpio]}\n'):
            path.write_text(content)
            self.load()
            self.assertFalse(self.provider()["supported"])
            with self.assertRaises(ValueError):
                self.assign()

    def test_missing_count_and_ambiguous_provider_declarations_are_not_guessed(self):
        for text in (SOURCE.replace('#gpio-cells = <2>;', '', 1),
                     SOURCE.replace('#gpio-cells = <2>;', '#gpio-cells = <1>;', 1),
                     SOURCE + '\n&gpio0 { status = "okay"; };\n',
                     SOURCE.replace('/ {', '/ { gpio0: duplicate {};', 1)):
            self.source(text)
            self.assertFalse(any(provider["reference"] == "&gpio0" for provider in self.schema["providers"]))
            self.assertFalse(self.consumer()["editable"])

    def test_malformed_explicit_ngpios_withholds_provider_instead_of_using_default(self):
        for value in ('<COUNT>', '<0>', '<1 2>', '"16"'):
            self.source(SOURCE.replace('ngpios = <16>', f'ngpios = {value}'))
            self.assertFalse(self.provider()["supported"])
            self.assertIsNone(self.provider()["pin_count"])
        self.source(SOURCE.replace('ngpios = <16>;', 'ngpios = <16>; ngpios = <8>;'))
        self.assertFalse(self.provider()["supported"])
        path = self.bindings_dir / "gpio.yaml"
        for settings in ('{type: int, const: 8}', '{type: int, enum: [8]}', '{type: int, minimum: 1}', '{type: array}'):
            path.write_text(BINDINGS["gpio.yaml"].replace('{type: int, default: 32}', settings))
            self.source(SOURCE)
            self.assertFalse(self.provider()["supported"])

    def test_malformed_explicit_reservations_withhold_provider(self):
        for value in ('<RESERVED>', '<3>', '<3 0>', '<4294967295 2>', '"3 2"', '<3 2>, <10 1>'):
            self.source(SOURCE.replace('gpio-reserved-ranges = <3 2>', f'gpio-reserved-ranges = {value}'))
            self.assertFalse(self.provider()["supported"])
            self.assertIsNone(self.provider()["reserved_ranges"])
        path = self.bindings_dir / "gpio.yaml"
        for settings in ('{type: array, enum: [8]}', '{type: array, minimum: 1}', '{type: int}'):
            path.write_text(BINDINGS["gpio.yaml"].replace('gpio-reserved-ranges: {type: array}', f'gpio-reserved-ranges: {settings}'))
            self.source(SOURCE)
            self.assertFalse(self.provider()["supported"])

    def test_multiple_valid_reservation_pairs_are_preserved(self):
        self.source(SOURCE.replace('gpio-reserved-ranges = <3 2>', 'gpio-reserved-ranges = <3 2 10 1>'))
        self.assertEqual(self.provider()["reserved_ranges"], [[3, 2], [10, 1]])
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.assign(pin=10)
        self.assign(pin=9)
        self.assertIn('gpio-reserved-ranges = <3 2 10 1>;', self.draft["text"])

    def test_local_gpio_hogs_withhold_unmodeled_allocation_authority(self):
        for declaration in ('gpio-hog;', 'gpio-hog = <UNKNOWN>;', '/delete-property/ gpio-hog;'):
            text = SOURCE.replace('status = "disabled"; };', f'status = "disabled"; hog {{ {declaration} gpios = <2 0>; input; }}; }};')
            self.source(text)
            self.assertFalse(self.provider()["supported"])
            self.assertIn("GPIO hog", self.provider()["reason"])
            self.assertFalse(self.consumer()["editable"])
            with self.assertRaises(ValueError):
                self.assign()

    def test_consumer_compatible_and_property_constraints_are_rechecked(self):
        path = self.bindings_dir / "bmi270.yaml"
        for content in (BINDINGS["bmi270.yaml"] + '  compatible: {const: [other,sensor]}\n',
                        BINDINGS["bmi270.yaml"].replace('type: phandle-array', 'type: phandle-array, specifier-space: custom'),
                        BINDINGS["bmi270.yaml"].replace('type: phandle-array', 'type: compound'),
                        BINDINGS["bmi270.yaml"].replace('type: phandle-array', 'type: phandle-array, minimum: 1'),
                        BINDINGS["bmi270.yaml"].replace('properties:\n  irq-gpios: {type: phandle-array}\n', 'properties: {}\n')):
            path.write_text(content)
            self.load()
            self.assertFalse(self.consumer()["editable"])
            with self.assertRaises(ValueError):
                self.assign()

    def test_consumer_reference_override_withholds_mutation(self):
        self.source(SOURCE.replace('    motion {', '    sensor: motion {') + '\n&sensor { status = "okay"; };\n')
        self.assertFalse(any(item["editable"] for item in self.schema["consumers"] if item["path"] == "/motion"))

    def test_empty_source_and_no_provider_have_explanations(self):
        self.source('/ {};\n')
        self.assertEqual(self.schema["providers"], [])
        self.assertEqual(self.schema["consumers"], [])
        self.source('/ { motion { compatible = "bosch,bmi270"; }; };\n')
        self.assertFalse(self.consumer()["editable"])
        self.assertIn("No unique local GPIO", self.consumer()["reason"])

    def test_exact_command_fields_and_entry_shapes_are_required(self):
        self.load()
        for entry in ([], {}, {"reference": "&gpio0", "cells": [1]}, {"reference": "&gpio0", "cells": [1, 0, 0]},
                      {"reference": "&missing", "cells": [1, 0]}, {"reference": "&gpio0", "cells": [1, 0], "extra": True}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.command(node_id=self.consumer()["node_id"], property="irq-gpios", entry=entry)
        with self.assertRaises(ValueError):
            self.command(node_id=self.consumer()["node_id"], property="reset-gpios", entry={"reference": "&gpio0", "cells": [1, 0]})
        with self.assertRaises(ValueError):
            self.command(node_id=self.consumer()["node_id"], property="irq-gpios", entry={"reference": "&gpio0", "cells": [1, 0]}, trigger="rising")
        self.assertEqual(self.draft["text"], SOURCE)

    def test_schema_revision_expiry_workspace_and_binding_changes_reject_stale_commands(self):
        self.load()
        (self.bindings_dir / "base.yaml").write_text(BINDINGS["base.yaml"] + '  changed: {type: int}\n')
        with self.assertRaisesRegex(ValueError, "changed"):
            self.assign()
        self.load()
        self.interrupt_gpio.observations[self.schema["schema_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.assign()
        self.load()
        self.draft = self.drafts.source(self.guard(text=SOURCE + '/* changed */'))
        with self.assertRaisesRegex(ValueError, "earlier"):
            self.assign()
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaises(ValueError):
            self.load()

    def test_catalogue_is_revalidated_after_candidate_planning(self):
        self.load()
        catalog = self.interrupt_gpio.observations[self.schema["schema_id"]]["catalog"]
        with patch.object(catalog, "revalidate", side_effect=[None, ValueError("Binding changed during plan")]):
            with self.assertRaisesRegex(ValueError, "during plan"):
                self.assign()
        self.assertEqual(self.draft["text"], SOURCE)
        self.assertEqual(self.draft["revision"], 0)
