"""PWM forms preserve shared providers and unknown hardware semantics."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.features.devicetree.pwm import PWMService


BINDINGS = {
    "base.yaml": '''properties:
  compatible: {type: string-array, required: true}
  status: {type: string, enum: [okay, disabled]}
''',
    "pwm-three.yaml": '''compatible: test,pwm-three
include: base.yaml
pwm-cells: [channel, period, flags]
properties:
  "#pwm-cells": {type: int, const: 3, required: true}
  channels: {type: int}
''',
    "pwm-two.yaml": '''compatible: test,pwm-two
include: base.yaml
pwm-cells: [channel, period]
properties:
  "#pwm-cells": {type: int, const: 2, required: true}
''',
    "consumer.yaml": '''compatible: test,pwm-consumer
include: base.yaml
properties:
  pwms: {type: phandle-array, required: true}
  pwm-names: {type: string-array}
''',
    "pwm-leds.yaml": '''compatible: pwm-leds
include: base.yaml
child-binding:
  properties:
    pwms: {type: phandle-array, required: true}
    label: {type: string}
''',
}
SOURCE = '''/* Keep this PWM source note. */
/ {
    pwm0: pwm { compatible = "test,pwm-three"; #pwm-cells = <3>; channels = <4>; status = "disabled"; };
    pwm1: timer { compatible = "test,pwm-two"; #pwm-cells = <2>; };
    consumer { compatible = "test,pwm-consumer"; pwms = <&pwm0 1 20000000 256>; pwm-names = "output"; keep = <CUSTOM(2)>; };
    leds { compatible = "pwm-leds";
        indicator: led { pwms = <&pwm1 1 10000000>; label = "Existing"; };
    };
};
'''


class PWMTests(unittest.TestCase):
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
        self.pwm = PWMService(self.drafts)
        self.drafts.pwm = self.pwm
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def load(self):
        self.schema = self.pwm.inspect(self.guard())
        return self.schema

    def source(self, text):
        self.draft = self.drafts.source(self.guard(text=text))
        return self.load()

    def consumer(self, path="/consumer"):
        return next(item for item in self.schema["consumers"] if item["path"] == path)

    def command(self, kind, **fields):
        self.draft = self.drafts.command(self.guard(command={"kind": kind, "schema_id": self.schema["schema_id"], **fields}))
        return self.draft

    def assign(self, reference="&pwm0", cells=None, path="/consumer"):
        return self.command("pwm_set", node_id=self.consumer(path)["node_id"],
                            entry={"reference": reference, "cells": [2, 1000000, 0] if cells is None else cells})

    def test_observation_reads_local_bindings_without_tools_or_mutation(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            self.load()
        self.assertEqual([item["cells"] for item in self.schema["providers"]], [["channel", "period", "flags"], ["channel", "period"]])
        self.assertEqual(self.schema["providers"][0]["channel_count"], 4)
        self.assertEqual(self.schema["providers"][0]["status"], "disabled")
        self.assertTrue(self.consumer()["editable"])
        self.assertEqual(self.consumer()["entry"], {"reference": "&pwm0", "cells": [1, 20000000, 256]})
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.assertEqual(self.draft["revision"], 0)

    def test_assignment_changes_only_pwms_and_one_undo_restores_source(self):
        self.load()
        self.assign(cells=[3, 10000000, 0xffffffff])
        self.assertEqual(self.draft["text"], SOURCE.replace('<&pwm0 1 20000000 256>', '<&pwm0 3 10000000 4294967295>'))
        self.assertEqual(self.draft["revision"], 1)
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)
        self.draft = self.drafts.redo(self.guard())
        self.assertIn('<&pwm0 3 10000000 4294967295>', self.draft["text"])

    def test_two_and_three_cell_provider_changes_preserve_single_pwm_name(self):
        self.load()
        self.assign("&pwm1", [1, 1234])
        self.assertIn('pwms = <&pwm1 1 1234>; pwm-names = "output";', self.draft["text"])
        self.load()
        self.assign("&pwm0", [0, 1234, 257])
        self.assertIn('pwms = <&pwm0 0 1234 257>; pwm-names = "output";', self.draft["text"])

    def test_channel_count_does_not_invent_a_zero_based_range(self):
        self.load()
        for channel in (0, 4, 0xffffffff):
            self.assign(cells=[channel, 1, 0])
            self.load()
            self.assertEqual(self.consumer()["entry"]["cells"][0], channel)
        self.assertIn("Channel numbering", self.schema["diagnostics"][0])

    def test_nonpositive_period_and_malformed_cells_are_rejected(self):
        self.load()
        for cells in ([0, 0, 0], [0, -1, 0], [0, True, 0], [0, 2 ** 32, 0], [True, 1, 0],
                      [0, 1, -1], [0, 1, 2 ** 32], [0, 1], [0, 1, 0, 0], "0 1 0"):
            with self.subTest(cells=cells), self.assertRaises(ValueError):
                self.assign(cells=cells)
        self.assertEqual(self.draft["text"], SOURCE)

    def test_invalid_imported_period_can_be_repaired(self):
        self.source(SOURCE.replace('<&pwm0 1 20000000 256>', '<&pwm0 1 0 256>'))
        self.assertTrue(self.consumer()["editable"])
        self.assign(cells=[1, 1, 256])
        self.assertEqual(self.consumer()["entry"]["cells"][1], 0)
        self.load()
        self.assertEqual(self.consumer()["entry"]["cells"][1], 1)

    def test_missing_pwms_can_be_added_without_filling_other_defaults(self):
        self.source(SOURCE.replace('pwms = <&pwm0 1 20000000 256>; ', ''))
        self.assertTrue(self.consumer()["editable"])
        self.assertIsNone(self.consumer()["entry"])
        self.assign()
        self.assertIn('pwms = <&pwm0 2 1000000 0>;', self.draft["text"])
        self.assertIn('keep = <CUSTOM(2)>;', self.draft["text"])

    def test_multi_specifier_macro_and_named_array_inputs_remain_source_only(self):
        replacements = ['pwms = <&pwm0 1 20000000 256>, <&pwm1 1 10>;',
                        'pwms = <&pwm0 1 PWM_MSEC(20) 0>;', 'pwms = <&missing 1 20 0>;',
                        'pwms = <&pwm0 1 20 0>; pwms = <&pwm0 2 20 0>;', '/delete-property/ pwms;']
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                self.source(SOURCE.replace('pwms = <&pwm0 1 20000000 256>;', replacement))
                self.assertFalse(self.consumer()["editable"])
                with self.assertRaises(ValueError):
                    self.assign()
        for names in ('"first", "second"', 'NAME_MACRO'):
            self.source(SOURCE.replace('pwm-names = "output";', f'pwm-names = {names};'))
            self.assertFalse(self.consumer()["editable"])

    def test_ambiguous_declarations_and_provider_counts_are_not_guessed(self):
        for text in (SOURCE.replace('#pwm-cells = <3>;', '#pwm-cells = <2>;'),
                     SOURCE.replace('#pwm-cells = <3>;', ''),
                     SOURCE + '\n&pwm0 { status = "okay"; };\n',
                     SOURCE.replace('/ {', '/ { pwm0: duplicate {};', 1)):
            self.source(text)
            self.assertFalse(any(item["reference"] == "&pwm0" for item in self.schema["providers"]))
            self.assertFalse(self.consumer()["editable"])
            with self.assertRaises(ValueError):
                self.assign()

    def test_nonstandard_provider_layout_remains_available_only_to_generic_forms(self):
        path = self.bindings_dir / "pwm-three.yaml"
        path.write_text(BINDINGS["pwm-three.yaml"].replace('[channel, period, flags]', '[timer, channel, period]'))
        self.load()
        provider = self.schema["providers"][0]
        self.assertFalse(provider["supported"])
        self.assertIn("cell layout", provider["reason"])
        self.assertFalse(self.consumer()["editable"])
        with self.assertRaises(ValueError):
            self.assign()

    def test_provider_cell_count_must_satisfy_its_binding_constraints(self):
        path = self.bindings_dir / "pwm-three.yaml"
        for field in ('{type: int, const: 2, required: true}', '{type: int, enum: [2], required: true}',
                      '{type: compound, required: true}', '{type: int, const: 3, required: true, min: 1}'):
            path.write_text(BINDINGS["pwm-three.yaml"].replace('{type: int, const: 3, required: true}', field))
            self.load()
            self.assertFalse(self.schema["providers"][0]["supported"])
            self.assertFalse(self.consumer()["editable"])
            with self.assertRaises(ValueError):
                self.assign()

    def test_channel_count_requires_a_supported_literal_integer_binding(self):
        path = self.bindings_dir / "pwm-three.yaml"
        for field in ('{type: int, const: 2}', '{type: int, enum: [2]}', '{type: compound}', '{type: int, min: 1}'):
            path.write_text(BINDINGS["pwm-three.yaml"].replace('{type: int}', field))
            self.load()
            self.assertIsNone(self.schema["providers"][0]["channel_count"])
            self.assertTrue(self.schema["providers"][0]["supported"])
        path.write_text(BINDINGS["pwm-three.yaml"].replace('  channels: {type: int}\n', ''))
        self.load()
        self.assertIsNone(self.schema["providers"][0]["channel_count"])

    def test_provider_compatible_constraints_cannot_authorize_a_different_schema(self):
        path = self.bindings_dir / "pwm-three.yaml"
        path.write_text(BINDINGS["pwm-three.yaml"] + '  compatible: {type: string-array, const: [other,device]}\n')
        self.load()
        self.assertFalse(self.schema["providers"][0]["supported"])
        self.assertIn("compatible", self.schema["providers"][0]["reason"])
        with self.assertRaises(ValueError):
            self.assign()

    def test_consumer_and_group_identity_constraints_are_not_bypassed(self):
        path = self.bindings_dir / "consumer.yaml"
        path.write_text(BINDINGS["consumer.yaml"] + '  compatible: {type: string-array, const: [other,device]}\n')
        path = self.bindings_dir / "pwm-leds.yaml"
        path.write_text(BINDINGS["pwm-leds.yaml"] + 'properties:\n  compatible: {const: [pwm-leds]}\n')
        self.source(SOURCE.replace('compatible = "pwm-leds";', 'compatible = "pwm-leds", "other,fallback";'))
        self.assertFalse(self.consumer()["editable"])
        self.assertFalse(self.schema["groups"][0]["addable"])
        self.assertIn("compatible", self.schema["groups"][0]["reason"])
        with self.assertRaises(ValueError):
            self.assign()


    def test_existing_led_updates_preserve_its_label(self):
        self.load()
        self.assign("&pwm1", [3, 1000], path="/leds/led")
        self.load()
        self.assertIn('label = "Existing";', self.draft["text"])

    def test_additional_required_child_properties_block_creation(self):
        path = self.bindings_dir / "pwm-leds.yaml"
        path.write_text(BINDINGS["pwm-leds.yaml"] + '    supply: {type: phandle, required: true}\n')
        self.load()
        self.assertFalse(self.schema["group_option"]["available"])
        self.assertFalse(self.schema["groups"][0]["addable"])


    def test_unsupported_or_custom_child_pwm_fields_do_not_create_incomplete_leds(self):
        path = self.bindings_dir / "pwm-leds.yaml"
        for field in ('{type: compound, required: true}', '{type: phandle-array, required: true, specifier-space: custom}',
                      '{type: phandle-array}', '{type: phandle-array, required: true, min: 1}'):
            path.write_text(BINDINGS["pwm-leds.yaml"].replace('{type: phandle-array, required: true}', field))
            self.load()
            self.assertFalse(self.schema["group_option"]["available"])
            self.assertFalse(self.schema["groups"][0]["addable"])

    def test_empty_provider_set_still_shows_capability_limits(self):
        self.source('/ { leds { compatible = "pwm-leds"; }; };\n')
        self.assertEqual(self.schema["providers"], [])
        self.assertEqual(self.schema["consumers"], [])
        self.assertFalse(self.schema["groups"][0]["addable"])
        self.assertIn("provider", self.schema["groups"][0]["reason"])

    def test_unbound_pwms_declaration_is_present_as_source_only(self):
        self.source('/ { mystery { pwms = <&inherited 1 100 0>; }; };\n')
        self.assertEqual(len(self.schema["consumers"]), 1)
        self.assertFalse(self.schema["consumers"][0]["editable"])

    def test_schema_revision_expiry_workspace_and_binding_changes_reject_stale_commands(self):
        self.load()
        (self.bindings_dir / "base.yaml").write_text(BINDINGS["base.yaml"] + '  changed: {type: int}\n')
        with self.assertRaisesRegex(ValueError, "changed"):
            self.assign()
        self.load()
        self.pwm.observations[self.schema["schema_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.assign()
        self.load()
        self.draft = self.drafts.source(self.guard(text=SOURCE + "/* newer */"))
        with self.assertRaisesRegex(ValueError, "earlier"):
            self.assign()
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaises(ValueError):
            self.load()

    def test_exact_command_fields_and_invalid_entry_shapes_are_rejected(self):
        self.load()
        for entry in ([], {}, {"reference": "&pwm0", "cells": [0, 1, 0], "extra": True},
                      {"reference": "&missing", "cells": [0, 1, 0]}, {"reference": "&pwm1", "cells": [0, 1, 0]}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.command("pwm_set", node_id=self.consumer()["node_id"], entry=entry)
        with self.assertRaises(ValueError):
            self.command("pwm_group_add", name="new", extra=True)
        self.assertEqual(self.draft["text"], SOURCE)

    def test_schema_is_revalidated_after_the_candidate_is_planned(self):
        self.load()
        catalog = self.pwm.observations[self.schema["schema_id"]]["catalog"]
        with patch.object(catalog, "revalidate", side_effect=[None, ValueError("Binding changed during plan")]):
            with self.assertRaisesRegex(ValueError, "during plan"):
                self.assign()
        self.assertEqual(self.draft["text"], SOURCE)
        self.assertEqual(self.draft["revision"], 0)
