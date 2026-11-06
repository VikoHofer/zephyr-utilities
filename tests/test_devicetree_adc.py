"""ADC forms preserve local source, explicit identifiers and binding authority."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.features.devicetree.adc import ADCService, adc_providers
from zephyr_utilities.features.devicetree.binding_service import BindingService


BINDINGS = {
    "base.yaml": '''properties:
  compatible: {type: string-array, required: true}
  status: {type: string, enum: [okay, disabled]}
  "#address-cells": {type: int}
  "#size-cells": {type: int}
''',
    "adc.yaml": '''compatible: test,adc
include: base.yaml
io-channel-cells: [input]
properties:
  "#io-channel-cells": {type: int, const: 1, required: true}
  "#address-cells": {const: 1}
  "#size-cells": {const: 0}
child-binding:
  properties:
    reg: {type: array, required: true}
    zephyr,gain: {type: string, required: true, enum: [ADC_GAIN_1, ADC_GAIN_1_6]}
    zephyr,reference: {type: string, required: true, enum: [ADC_REF_INTERNAL, ADC_REF_VDD_1]}
    zephyr,acquisition-time: {type: int, required: true}
    zephyr,input-positive: {type: int}
    zephyr,resolution: {type: int}
''',
    "consumer.yaml": '''compatible: test,adc-consumer
include: base.yaml
properties:
  io-channels: {type: phandle-array, required: true}
  io-channel-names: {type: string-array}
''',
    "generic.yaml": '''compatible: test,generic-io
include: base.yaml
io-channel-cells: [input]
properties:
  "#io-channel-cells": {type: int, const: 1, required: true}
''',
}
SOURCE = '''/* Keep this ADC source note. */
/ {
    adc0: adc { compatible = "test,adc"; #io-channel-cells = <1>;
        #address-cells = <1>; #size-cells = <0>; status = "disabled";
        channel@1 { reg = <1>; zephyr,gain = "ADC_GAIN_1_6"; zephyr,reference = "ADC_REF_INTERNAL";
            zephyr,acquisition-time = <0>; zephyr,input-positive = <INPUT_MACRO>; zephyr,resolution = <12>; keep = "handwritten"; };
    };
    adc1: other-adc { compatible = "test,adc"; #io-channel-cells = <1>; #address-cells = <1>; #size-cells = <0>; };
    generic: generic-io { compatible = "test,generic-io"; #io-channel-cells = <1>; };
    consumer { compatible = "test,adc-consumer"; io-channels = <&adc0 1>; io-channel-names = "measurement"; keep = <CUSTOM(2)>; };
};
'''


class ADCTests(unittest.TestCase):
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
        self.adc = ADCService(self.drafts)
        self.drafts.adc = self.adc
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def load(self):
        self.schema = self.adc.inspect(self.guard())
        return self.schema

    def source(self, text):
        self.draft = self.drafts.source(self.guard(text=text))
        return self.load()

    def consumer(self):
        return next(item for item in self.schema["consumers"] if item["path"] == "/consumer")

    def provider(self, reference="&adc0"):
        return next(item for item in self.schema["providers"] if item["reference"] == reference)

    def channel(self):
        return self.schema["channels"][0]

    def command(self, kind, **fields):
        self.draft = self.drafts.command(self.guard(command={"kind": kind, "schema_id": self.schema["schema_id"], **fields}))
        return self.draft

    def assign(self, reference="&adc0", channel=2):
        return self.command("adc_set", node_id=self.consumer()["node_id"], entry={"reference": reference, "cells": [channel]})

    def configure(self, *, add=False, **values):
        fields = {"gain": "ADC_GAIN_1", "reference": "ADC_REF_VDD_1", "acquisition_time": 32769, **values}
        if add:
            return self.command("adc_channel_add", parent_id=self.provider()["id"], channel=fields.pop("channel", 2), **fields)
        return self.command("adc_channel_set", node_id=self.channel()["node_id"], **fields)

    def test_observation_reads_local_bindings_without_tools_or_source_mutation(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            self.load()
        self.assertTrue(self.provider()["supported"])
        self.assertTrue(self.provider()["addable"])
        self.assertEqual(self.provider()["cells"], ["input"])
        self.assertEqual(self.provider()["status"], "disabled")
        self.assertEqual(self.provider()["occupied_channels"], [1])
        self.assertTrue(self.consumer()["editable"])
        self.assertEqual(self.consumer()["entry"], {"reference": "&adc0", "cells": [1]})
        self.assertTrue(self.channel()["editable"])
        self.assertEqual(self.channel()["acquisition_time"], 0)
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.assertEqual(self.draft["revision"], 0)

    def test_generic_io_provider_is_not_assumed_to_be_adc(self):
        self.load()
        self.assertFalse(self.provider("&generic")["supported"])
        self.assertIn("ADC child binding", self.provider("&generic")["reason"])
        with self.assertRaises(ValueError):
            self.assign("&generic")
        self.source(SOURCE.replace('io-channels = <&adc0 1>', 'io-channels = <&generic 1>'))
        self.assertFalse(self.consumer()["editable"])
        with self.assertRaises(ValueError):
            self.assign()

    def test_provider_identity_requires_catalogue_evidence(self):
        self.load()
        document = self.drafts.drafts[self.draft["id"]].document
        catalog = self.adc.observations[self.schema["schema_id"]]["catalog"]
        projection = BindingService._projection(document, catalog)
        self.assertFalse(any(provider["supported"] for provider in adc_providers(document, projection)))
        self.assertEqual(sum(provider["supported"] for provider in adc_providers(document, projection, catalog)), 2)

    def test_consumer_assignment_preserves_name_source_and_one_step_undo(self):
        self.load()
        self.assign("&adc1", 31)
        self.assertEqual(self.draft["text"], SOURCE.replace('<&adc0 1>', '<&adc1 31>'))
        self.assertEqual(self.draft["revision"], 1)
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)
        self.draft = self.drafts.redo(self.guard())
        self.assertIn('io-channels = <&adc1 31>;', self.draft["text"])

    def test_channel_identifiers_are_api_limits_not_hardware_claims(self):
        self.load()
        for value in (0, 31):
            self.assign(channel=value)
            self.load()
            self.assertEqual(self.consumer()["entry"]["cells"], [value])
        for value in (-1, 32, 2 ** 32, True, "1"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "0 to 31"):
                self.assign(channel=value)
        self.assertIn("driver support", self.schema["diagnostics"][0])

    def test_invalid_imported_consumer_identifier_can_be_repaired(self):
        self.source(SOURCE.replace('<&adc0 1>', '<&adc0 99>'))
        self.assertTrue(self.consumer()["editable"])
        self.assign(channel=0)
        self.assertIn('io-channels = <&adc0 0>;', self.draft["text"])

    def test_missing_consumer_property_can_be_added_without_other_defaults(self):
        self.source(SOURCE.replace('io-channels = <&adc0 1>; ', ''))
        self.assertTrue(self.consumer()["editable"])
        self.assertIsNone(self.consumer()["entry"])
        self.assign()
        self.assertIn('io-channels = <&adc0 2>;', self.draft["text"])
        self.assertIn('io-channel-names = "measurement";', self.draft["text"])

    def test_multiple_opaque_missing_or_repeated_specifiers_remain_source_only(self):
        for replacement in ('io-channels = <&adc0 1>, <&adc1 2>;', 'io-channels = <&adc0 CHANNEL>;',
                            'io-channels = <&inherited 1>;', 'io-channels = <&adc0 1 2>;',
                            'io-channels = <&adc0 1>; io-channels = <&adc0 2>;', '/delete-property/ io-channels;'):
            with self.subTest(replacement=replacement):
                self.source(SOURCE.replace('io-channels = <&adc0 1>;', replacement))
                self.assertFalse(self.consumer()["editable"])
                with self.assertRaises(ValueError):
                    self.assign()

    def test_companion_names_must_be_one_literal_name(self):
        for names in ('"a", "b"', 'NAME_MACRO', '<1>'):
            self.source(SOURCE.replace('"measurement"', names))
            self.assertFalse(self.consumer()["editable"])
        self.source(SOURCE.replace('io-channel-names = "measurement";', 'io-channel-names = "a"; io-channel-names = "b";'))
        self.assertFalse(self.consumer()["editable"])

    def test_channel_edit_preserves_id_and_optional_handwritten_properties(self):
        self.load()
        self.configure()
        expected = SOURCE.replace('"ADC_GAIN_1_6"', '"ADC_GAIN_1"').replace('"ADC_REF_INTERNAL"', '"ADC_REF_VDD_1"')
        expected = expected.replace('zephyr,acquisition-time = <0>', 'zephyr,acquisition-time = <32769>')
        self.assertEqual(self.draft["text"], expected)
        self.assertEqual(self.draft["revision"], 1)
        self.assertIn('channel@1 { reg = <1>;', self.draft["text"])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)

    def test_channel_creation_uses_hex_name_and_required_properties_only(self):
        self.load()
        self.configure(add=True, channel=31, acquisition_time=0)
        self.assertIn('channel@1f {', self.draft["text"])
        self.assertIn('reg = <31>;', self.draft["text"])
        self.assertEqual(self.draft["text"].count('zephyr,input-positive'), 1)
        self.assertEqual(self.draft["text"].count('zephyr,resolution'), 1)
        self.assertIn('status = "disabled";', self.draft["text"])
        self.load()
        self.assertEqual([row["channel"] for row in self.schema["channels"]], [1, 31])
        self.assertTrue(all(row["editable"] for row in self.schema["channels"]))
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)

    def test_channel_creation_rejects_duplicate_and_out_of_range_identifiers(self):
        self.load()
        for value in (1, -1, 32, True, "2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.configure(add=True, channel=value)
        self.assertEqual(self.draft["text"], SOURCE)

    def test_config_values_satisfy_binding_enums_and_uint16_representation(self):
        self.load()
        for values in ({"gain": "ADC_GAIN_UNKNOWN"}, {"reference": "ADC_REF_UNKNOWN"}, {"gain": 1},
                       {"acquisition_time": -1}, {"acquisition_time": 65536}, {"acquisition_time": True},
                       {"acquisition_time": "ADC_ACQ_TIME_DEFAULT"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.configure(**values)
        for value in (65535, 0):
            self.configure(acquisition_time=value)
            self.load()
            self.assertEqual(self.channel()["acquisition_time"], value)

    def test_out_of_range_literal_acquisition_can_be_repaired_but_expressions_stay_source_only(self):
        self.source(SOURCE.replace('zephyr,acquisition-time = <0>', 'zephyr,acquisition-time = <65536>'))
        self.assertTrue(self.channel()["editable"])
        self.configure(acquisition_time=0)
        for text in (SOURCE.replace('<0>; zephyr,input-positive', '<ADC_ACQ_TIME_DEFAULT>; zephyr,input-positive'),
                     SOURCE.replace('"ADC_GAIN_1_6"', 'GAIN_MACRO'),
                     SOURCE.replace('zephyr,reference = "ADC_REF_INTERNAL";', '')):
            self.source(text)
            self.assertFalse(self.channel()["editable"])
            with self.assertRaises(ValueError):
                self.configure()

    def test_channel_address_mismatch_wrong_name_and_opaque_sibling_withhold_edits(self):
        for text in (SOURCE.replace('channel@1', 'channel@2'), SOURCE.replace('channel@1', 'input@1'),
                     SOURCE.replace('reg = <1>', 'reg = <CHANNEL>'),
                     SOURCE.replace('channel@1 {', 'unknown { reg = <UNKNOWN>; }; channel@1 {')):
            with self.subTest(text=text):
                self.source(text)
                self.assertFalse(self.provider()["addable"])
                self.assertFalse(self.channel()["editable"])
                with self.assertRaises(ValueError):
                    self.configure(add=True)

    def test_duplicate_channel_id_by_different_hex_spelling_is_rejected(self):
        self.source(SOURCE.replace('channel@1 {', 'channel@01 { reg = <1>; }; channel@1 {'))
        self.assertFalse(self.provider()["addable"])
        self.assertIn("Duplicate", self.provider()["add_reason"])
        self.assertFalse(any(row["editable"] for row in self.schema["channels"]))

    def test_missing_address_metadata_withholds_channels_but_keeps_consumer_form(self):
        for declaration in ('#address-cells = <1>;', '#size-cells = <0>;'):
            self.source(SOURCE.replace(declaration, '', 1))
            self.assertTrue(self.provider()["supported"])
            self.assertFalse(self.provider()["addable"])
            self.assertFalse(self.channel()["editable"])
            self.assertTrue(self.consumer()["editable"])

    def test_nested_channels_and_separate_child_compatible_remain_source_only(self):
        for text in (SOURCE.replace('keep = "handwritten"; };', 'keep = "handwritten"; nested {}; };'),
                     SOURCE.replace('channel@1 {', 'channel@1 { compatible = "test,generic-io";')):
            self.source(text)
            self.assertFalse(self.channel()["editable"])
            with self.assertRaises(ValueError):
                self.configure()

    def test_additional_required_child_fields_withhold_common_channel_form(self):
        (self.bindings_dir / "adc.yaml").write_text(BINDINGS["adc.yaml"] + '    extra: {type: int, required: true}\n')
        self.load()
        self.assertTrue(self.provider()["supported"])
        self.assertFalse(self.provider()["addable"])
        self.assertFalse(self.channel()["editable"])
        with self.assertRaisesRegex(ValueError, "additional"):
            self.configure(add=True)

    def test_common_schema_requires_supported_required_gain_reference_and_acquisition(self):
        path = self.bindings_dir / "adc.yaml"
        for old, new in (('type: string, required: true, enum: [ADC_GAIN_1, ADC_GAIN_1_6]', 'type: string, required: true'),
                         ('type: int, required: true}', 'type: int}'),
                         ('reg: {type: array, required: true}', 'reg: {type: int, required: true}'),
                         ('zephyr,reference: {type: string, required: true, enum: [ADC_REF_INTERNAL, ADC_REF_VDD_1]}',
                          'zephyr,reference: {type: compound, required: true}')):
            path.write_text(BINDINGS["adc.yaml"].replace(old, new))
            self.load()
            self.assertFalse(self.provider()["supported"])
            self.assertFalse(self.consumer()["editable"])

    def test_binding_constants_and_unsupported_constraints_are_not_bypassed(self):
        path = self.bindings_dir / "adc.yaml"
        for replacement in ('{type: int, const: 2, required: true}', '{type: int, enum: [2], required: true}',
                            '{type: int, const: 1, required: true, minimum: 0}'):
            path.write_text(BINDINGS["adc.yaml"].replace('{type: int, const: 1, required: true}', replacement))
            self.load()
            self.assertFalse(self.provider()["supported"])
        path.write_text(BINDINGS["adc.yaml"].replace('    reg: {type: array, required: true}',
                                                   '    reg: {type: array, required: true, enum: [1]}'))
        self.load()
        with self.assertRaises(ValueError):
            self.configure(add=True, channel=2)
        path.write_text(BINDINGS["adc.yaml"].replace('    zephyr,acquisition-time: {type: int, required: true}',
                                                   '    zephyr,acquisition-time: {type: int, required: true, const: 0}'))
        self.load()
        with self.assertRaises(ValueError):
            self.configure(acquisition_time=1)

    def test_provider_and_consumer_compatible_constraints_are_checked(self):
        path = self.bindings_dir / "adc.yaml"
        path.write_text(BINDINGS["adc.yaml"].replace('child-binding:', '  compatible: {const: [other,adc]}\nchild-binding:'))
        self.load()
        self.assertFalse(self.provider()["supported"])
        path.write_text(BINDINGS["adc.yaml"])
        (self.bindings_dir / "consumer.yaml").write_text(BINDINGS["consumer.yaml"] + '  compatible: {const: [other,consumer]}\n')
        self.load()
        self.assertFalse(self.consumer()["editable"])

    def test_provider_layout_count_and_ambiguous_declarations_remain_source_only(self):
        for text in (SOURCE.replace('#io-channel-cells = <1>;', '#io-channel-cells = <2>;', 1),
                     SOURCE.replace('#io-channel-cells = <1>;', '', 1),
                     SOURCE + '\n&adc0 { status = "okay"; };\n',
                     SOURCE.replace('/ {', '/ { adc0: duplicate {};', 1)):
            self.source(text)
            self.assertFalse(any(row["reference"] == "&adc0" for row in self.schema["providers"]))
            self.assertFalse(self.consumer()["editable"])
        (self.bindings_dir / "adc.yaml").write_text(BINDINGS["adc.yaml"].replace('[input]', '[channel]'))
        self.source(SOURCE)
        self.assertFalse(self.provider()["supported"])
        self.assertIn("input cell", self.provider()["reason"])

    def test_empty_source_and_unbound_consumer_show_explicit_scope(self):
        self.source('/ {};\n')
        self.assertEqual(self.schema["providers"], [])
        self.assertEqual(self.schema["channels"], [])
        self.assertEqual(self.schema["consumers"], [])
        self.source('/ { consumer { io-channels = <&external 0>; }; };\n')
        self.assertFalse(self.consumer()["editable"])

    def test_schema_revision_expiry_workspace_and_binding_changes_reject_stale_commands(self):
        self.load()
        (self.bindings_dir / "base.yaml").write_text(BINDINGS["base.yaml"] + '  extra: {type: int}\n')
        with self.assertRaisesRegex(ValueError, "changed"):
            self.assign()
        self.load()
        self.adc.observations[self.schema["schema_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.configure()
        self.load()
        self.draft = self.drafts.source(self.guard(text=SOURCE + "/* new */"))
        with self.assertRaisesRegex(ValueError, "earlier"):
            self.configure(add=True)
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaises(ValueError):
            self.load()

    def test_exact_commands_reject_renumbering_and_invalid_entry_shapes(self):
        self.load()
        for entry in ([], {}, {"reference": "&adc0", "cells": []}, {"reference": "&adc0", "cells": [1, 2]},
                      {"reference": "&missing", "cells": [1]}, {"reference": "&adc0", "cells": [1], "extra": True}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.command("adc_set", node_id=self.consumer()["node_id"], entry=entry)
        with self.assertRaises(ValueError):
            self.configure(channel=2)
        with self.assertRaises(ValueError):
            self.configure(add=True, extra=True)
        self.assertEqual(self.draft["text"], SOURCE)

    def test_binding_evidence_is_rechecked_after_source_planning(self):
        self.load()
        catalog = self.adc.observations[self.schema["schema_id"]]["catalog"]
        with patch.object(catalog, "revalidate", side_effect=[None, ValueError("Binding changed during plan")]):
            with self.assertRaisesRegex(ValueError, "during plan"):
                self.configure(add=True)
        self.assertEqual(self.draft["text"], SOURCE)
        self.assertEqual(self.draft["revision"], 0)
