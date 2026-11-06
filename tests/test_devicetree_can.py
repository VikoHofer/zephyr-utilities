"""CAN forms preserve local source and distinguish explicit limits from unknowns."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.features.devicetree.can import CANService, can_evidence


BINDINGS = {
    "base.yaml": '''properties:
  compatible: {type: string-array, required: true}
  status: {type: string, enum: [okay, disabled]}
''',
    "can-controller.yaml": '''include: base.yaml
properties:
  bitrate: {type: int, default: 125000}
  bus-speed: {type: int, deprecated: true}
  sample-point: {type: int}
  phys: {type: phandle}
child-binding:
  properties:
    min-bitrate: {type: int}
    max-bitrate: {type: int, required: true}
''',
    "loopback.yaml": '''compatible: zephyr,can-loopback
include: can-controller.yaml
properties:
  bitrate-data: {type: int}
  bus-speed-data: {type: int, deprecated: true}
  sample-point-data: {type: int}
''',
    "flexcan.yaml": '''compatible: nxp,flexcan
include: can-controller.yaml
''',
    "mcp2515.yaml": '''compatible: microchip,mcp2515
include: can-controller.yaml
on-bus: spi
''',
    "spi.yaml": '''compatible: test,spi
include: base.yaml
bus: spi
''',
    "other.yaml": '''compatible: test,not-can
include: can-controller.yaml
''',
    "gpio.yaml": '''compatible: test,gpio
include: base.yaml
gpio-cells: [pin, flags]
properties:
  gpio-controller: {type: boolean}
  "#gpio-cells": {type: int, const: 2, required: true}
''',
    "phy.yaml": '''compatible: can-transceiver-gpio
include: base.yaml
properties:
  "#phy-cells": {type: int, const: 0, required: true}
  min-bitrate: {type: int}
  max-bitrate: {type: int, required: true}
  enable-gpios: {type: phandle-array}
  standby-gpios: {type: phandle-array}
''',
}
SOURCE = '''/* Keep this CAN source note. */
/ {
    gpio0: gpio { compatible = "test,gpio"; gpio-controller; #gpio-cells = <2>; };
    phy0: phy { compatible = "vendor,transceiver", "can-transceiver-gpio";
        #phy-cells = <0>; min-bitrate = <10000>; max-bitrate = <1000000>;
        enable-gpios = <&gpio0 1 0>; status = "disabled"; };
    phy1: fast-phy { compatible = "can-transceiver-gpio"; #phy-cells = <0>;
        max-bitrate = <8000000>; standby-gpios = <&gpio0 2 1>; };
    can0: can { compatible = "zephyr,can-loopback"; bitrate = <250000>;
        sample-point = <875>; phys = <&phy0>; keep = <CUSTOM(2)>; };
    flexcan { compatible = "nxp,flexcan";
        can-transceiver { min-bitrate = <20000>; max-bitrate = <500000>; }; };
    spi { compatible = "test,spi"; chip { compatible = "microchip,mcp2515"; }; };
    unknown { compatible = "test,not-can"; bitrate = <250000>; };
};
'''


class CANTests(unittest.TestCase):
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
        self.can = CANService(self.drafts)
        self.drafts.can = self.can
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def load(self):
        self.schema = self.can.inspect(self.guard())
        return self.schema

    def source(self, text):
        self.draft = self.drafts.source(self.guard(text=text))
        return self.load()

    def controller(self, path="/can"):
        return next(item for item in self.schema["controllers"] if item["path"] == path)

    def phy(self, reference="&phy0"):
        return next(item for item in self.schema["transceivers"] if item["reference"] == reference)

    def command(self, values, *, path="/can", **extra):
        command = {"kind": "can_controller_set", "schema_id": self.schema["schema_id"],
                   "node_id": self.controller(path)["node_id"], "values": values, **extra}
        self.draft = self.drafts.command(self.guard(command=command))
        return self.draft

    def test_local_observation_is_read_only_and_profiles_are_explicit(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            self.load()
        self.assertEqual([row["path"] for row in self.schema["controllers"]], ["/can", "/flexcan", "/spi/chip"])
        self.assertEqual(self.controller()["bitrate"], 250000)
        self.assertEqual(self.controller()["sample_point"], 875)
        self.assertEqual(self.controller()["phys"], "&phy0")
        self.assertEqual(self.phy()["status"], "disabled")
        self.assertTrue(self.phy()["supported"])
        self.assertEqual(self.controller()["limits"]["source"], "phys")
        self.assertEqual(self.controller()["limits"]["node_id"], self.phy()["node_id"])
        self.assertIn("timing feasibility", self.schema["diagnostics"][0])
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.assertEqual(self.draft["revision"], 0)

    def test_subset_update_preserves_other_properties_comments_and_one_history_action(self):
        self.load()
        self.command({"bitrate": 500000, "sample-point": 800})
        expected = SOURCE.replace('bitrate = <250000>', 'bitrate = <500000>', 1).replace('sample-point = <875>', 'sample-point = <800>')
        self.assertEqual(self.draft["text"], expected)
        self.assertEqual(self.draft["revision"], 1)
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft["text"], expected)

    def test_each_profile_accepts_selected_settings_without_defaults(self):
        self.load()
        for path in ("/can", "/flexcan", "/spi/chip"):
            self.command({"sample-point": 0}, path=path)
            self.load()
            self.assertEqual(self.controller(path)["sample_point"], 0)
        self.assertIsNone(self.controller("/spi/chip")["bitrate"])
        self.assertIsNone(self.controller("/flexcan")["bitrate"])
        self.assertEqual(self.draft["text"].count('bitrate = <250000>'), 2)

    def test_assigning_existing_phy_changes_only_reference(self):
        self.load()
        self.command({"phys": "&{/fast-phy}"})
        self.assertEqual(self.draft["text"], SOURCE.replace('phys = <&phy0>', 'phys = <&phy1>'))
        self.load()
        self.assertEqual(self.controller()["limits"]["max_bitrate"], 8000000)

    def test_missing_rates_and_optional_limits_remain_unknown(self):
        self.load()
        self.assertIsNone(self.phy("&phy1")["min_bitrate"])
        self.assertIsNone(self.controller("/spi/chip")["nominal_rate"]["value"])
        self.command({"phys": "&phy1"}, path="/spi/chip")
        self.load()
        self.assertIsNone(self.controller("/spi/chip")["bitrate"])
        self.assertIsNone(self.controller("/spi/chip")["sample_point"])
        self.assertIn("remain unknown", " ".join(self.controller("/spi/chip")["notes"]))

    def test_nominal_and_sample_api_ranges_are_independent_of_timing_feasibility(self):
        self.load()
        for rate, point in ((1, 0), (1000000, 999), (123457, 802)):
            self.command({"bitrate": rate, "sample-point": point}, path="/spi/chip")
            self.load()
            self.assertEqual(self.controller("/spi/chip")["bitrate"], rate)
        for name, value in (("bitrate", 0), ("bitrate", 1000001), ("bitrate", True), ("bitrate", "500000"),
                            ("sample-point", -1), ("sample-point", 1000), ("sample-point", True), ("sample-point", "800")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.command({name: value}, path="/spi/chip")

    def test_current_active_phy_limits_bound_rate_edits(self):
        self.load()
        with self.assertRaisesRegex(ValueError, "explicit limits"):
            self.command({"bitrate": 9999})
        self.command({"bitrate": 10000})
        self.load()
        self.command({"phys": "&phy1", "bitrate": 1})
        self.load()
        self.assertEqual(self.controller()["bitrate"], 1)

    def test_passive_child_limits_apply_until_explicit_active_assignment(self):
        self.load()
        controller = self.controller("/flexcan")
        self.assertEqual(controller["limits"]["source"], "passive")
        self.assertEqual(controller["passive_transceiver"]["max_bitrate"], 500000)
        with self.assertRaisesRegex(ValueError, "explicit limits"):
            self.command({"bitrate": 600000}, path="/flexcan")
        self.command({"phys": "&phy0", "bitrate": 600000}, path="/flexcan")
        self.assertIn('can-transceiver { min-bitrate = <20000>; max-bitrate = <500000>; };', self.draft["text"])
        self.load()
        self.assertEqual(self.controller("/flexcan")["limits"]["source"], "phys")
        self.assertIn("takes precedence", " ".join(self.controller("/flexcan")["notes"]))

    def test_active_phy_precedence_does_not_apply_passive_limits(self):
        self.source(SOURCE.replace('keep = <CUSTOM(2)>;', 'keep = <CUSTOM(2)>; can-transceiver { max-bitrate = <BAD>; };'))
        self.assertFalse(self.controller()["passive_transceiver"]["supported"])
        self.command({"bitrate": 500000})
        self.assertIn('max-bitrate = <BAD>', self.draft["text"])

    def test_malformed_effective_limits_withhold_rate_and_phy_but_not_sample(self):
        for value in ('<0>', '<BAD>', '<10000 20000>'):
            self.source(SOURCE.replace('max-bitrate = <1000000>', f'max-bitrate = {value}'))
            self.assertFalse(self.phy()["supported"])
            self.assertFalse(self.controller()["fields"]["bitrate"]["editable"])
            self.assertFalse(self.controller()["fields"]["phys"]["editable"])
            self.assertTrue(self.controller()["fields"]["sample-point"]["editable"])
            self.command({"sample-point": 800})
        self.source(SOURCE.replace('min-bitrate = <10000>', 'min-bitrate = <2000000>'))
        self.assertIn("exceeds", self.phy()["reason"])

    def test_malformed_passive_child_limits_are_not_assumed_absent(self):
        for replacement in ('max-bitrate = <BAD>', 'max-bitrate = <0>', 'min-bitrate = <1>'):
            self.source(SOURCE.replace('max-bitrate = <500000>', replacement))
            self.assertFalse(self.controller("/flexcan")["fields"]["bitrate"]["editable"])
            self.assertFalse(self.controller("/flexcan")["fields"]["phys"]["editable"])
            self.command({"sample-point": 800}, path="/flexcan")

    def test_passive_child_name_is_exact_and_unknown_lookalike_not_assumed(self):
        self.source(SOURCE.replace('can-transceiver {', 'can-transceiver@0 {'))
        self.assertIsNone(self.controller("/flexcan")["passive_transceiver"])
        self.assertEqual(self.controller("/flexcan")["limits"]["source"], "unknown")

    def test_normalized_passive_child_names_withhold_mutation_authority(self):
        for name in ('can_transceiver', 'can.transceiver', 'can,transceiver', 'can+transceiver', 'CAN-TRANSCEIVER'):
            self.source(SOURCE.replace('can-transceiver {', name + ' {'))
            controller = self.controller('/flexcan')
            self.assertIsNotNone(controller['passive_transceiver'])
            self.assertFalse(controller['fields']['bitrate']['editable'])
            self.assertFalse(controller['fields']['phys']['editable'])
            self.command({'sample-point': 800}, path='/flexcan')
        self.source(SOURCE.replace('can-transceiver {', 'can_transceiver { max-bitrate = <10000>; }; can-transceiver {'))
        self.assertFalse(self.controller('/flexcan')['fields']['bitrate']['editable'])

    def test_deprecated_only_bitrate_does_not_get_silently_shadowed(self):
        self.source(SOURCE.replace('bitrate = <250000>', 'bus-speed = <250000>', 1))
        self.assertFalse(self.controller()["fields"]["bitrate"]["editable"])
        self.assertEqual(self.controller()["nominal_rate"]["value"], 250000)
        with self.assertRaisesRegex(ValueError, "deprecated"):
            self.command({"bitrate": 500000})
        self.command({"sample-point": 800, "phys": "&phy1"})
        self.assertIn('bus-speed = <250000>', self.draft["text"])

    def test_modern_bitrate_precedence_preserves_deprecated_alias(self):
        self.source(SOURCE.replace('bitrate = <250000>;', 'bitrate = <250000>; bus-speed = <OLD_VALUE>;', 1))
        self.command({"bitrate": 500000, "phys": "&phy1"})
        self.assertIn('bus-speed = <OLD_VALUE>', self.draft["text"])

    def test_fd_data_rate_is_preserved_and_checked_when_selecting_phy(self):
        for name in ('bitrate-data', 'bus-speed-data'):
            self.source(SOURCE.replace('phys = <&phy0>;', f'phys = <&phy1>; {name} = <2000000>; sample-point-data = <750>;'))
            with self.assertRaisesRegex(ValueError, "CAN FD"):
                self.command({"phys": "&phy0"})
            self.command({"bitrate": 500000})
            self.assertIn(f'{name} = <2000000>; sample-point-data = <750>', self.draft["text"])

    def test_unknown_explicit_data_rate_withholds_phy_assignment(self):
        self.source(SOURCE.replace('phys = <&phy0>;', 'phys = <&phy0>; bitrate-data = <CONFIG_CAN_DEFAULT_BITRATE_DATA>;'))
        self.assertFalse(self.controller()["fields"]["phys"]["editable"])
        self.command({"bitrate": 500000})

    def test_present_opaque_repeated_deleted_or_inherited_fields_are_source_only(self):
        for replacement in ('phys = <&missing>;', 'phys = <&phy0>, <&phy1>;', 'phys = <PHANDLE>;', '/delete-property/ phys;',
                            'phys = <&phy0>; phys = <&phy1>;'):
            self.source(SOURCE.replace('phys = <&phy0>;', replacement))
            self.assertFalse(self.controller()["fields"]["phys"]["editable"])
            self.assertFalse(self.controller()["fields"]["bitrate"]["editable"])
            self.command({"sample-point": 800})
        for replacement in ('bitrate = <RATE>;', 'bitrate = <1 2>;', 'bitrate = <250000>; bitrate = <500000>;', '/delete-property/ bitrate;'):
            self.source(SOURCE.replace('bitrate = <250000>;', replacement, 1))
            self.assertFalse(self.controller()["fields"]["bitrate"]["editable"])
            self.command({"sample-point": 800})

    def test_numeric_invalid_source_rates_can_be_repaired(self):
        self.source(SOURCE.replace('bitrate = <250000>', 'bitrate = <0>', 1).replace('sample-point = <875>', 'sample-point = <1000>'))
        self.assertEqual(self.controller()["bitrate"], 0)
        self.assertEqual(self.controller()["sample_point"], 1000)
        self.assertTrue(self.controller()["identity_supported"])
        self.command({"bitrate": 500000, "sample-point": 800})

    def test_transceiver_needs_zero_cells_supported_limits_and_one_control(self):
        for text in (SOURCE.replace('#phy-cells = <0>;', '', 1), SOURCE.replace('#phy-cells = <0>', '#phy-cells = <1>', 1),
                     SOURCE.replace('enable-gpios = <&gpio0 1 0>;', '', 1),
                     SOURCE.replace('enable-gpios = <&gpio0 1 0>', 'enable-gpios = <&inherited 1 0>'),
                     SOURCE.replace('enable-gpios = <&gpio0 1 0>', 'enable-gpios = <&gpio0 1 FLAG>'),
                     SOURCE.replace('enable-gpios = <&gpio0 1 0>', 'enable-gpios = <&gpio0 1 0>, <&gpio0 2 0>')):
            self.source(text)
            self.assertFalse(self.phy()["supported"])
            with self.assertRaises(ValueError):
                self.command({"phys": "&phy0"}, path="/spi/chip")

    def test_explicit_binding_constraints_are_not_bypassed(self):
        path = self.bindings_dir / 'can-controller.yaml'
        path.write_text(BINDINGS['can-controller.yaml'].replace('bitrate: {type: int, default: 125000}', 'bitrate: {type: int, enum: [125000, 250000]}'))
        self.load()
        with self.assertRaisesRegex(ValueError, "permitted"):
            self.command({"bitrate": 500000})
        self.command({"bitrate": 125000})
        path.write_text(BINDINGS['can-controller.yaml'].replace('sample-point: {type: int}', 'sample-point: {type: int, const: 800}'))
        self.load()
        with self.assertRaisesRegex(ValueError, "constant"):
            self.command({"sample-point": 750})

    def test_transceiver_gpio_control_api_width_and_layout_are_required(self):
        for pin, flags in ((32, 0), (1, 65536)):
            self.source(SOURCE.replace('enable-gpios = <&gpio0 1 0>', f'enable-gpios = <&gpio0 {pin} {flags}>'))
            self.assertFalse(self.phy()['supported'])
        self.source(SOURCE.replace('enable-gpios = <&gpio0 1 0>', 'enable-gpios = <&gpio0 31 65535>'))
        self.assertTrue(self.phy()['supported'])
        path = self.bindings_dir / 'gpio.yaml'
        for content in (BINDINGS['gpio.yaml'].replace('[pin, flags]', '[flags, pin]'),
                        BINDINGS['gpio.yaml'].replace('const: 2', 'const: 3'),
                        BINDINGS['gpio.yaml'] + '  compatible: {const: ["other,gpio"]}\n'):
            path.write_text(content)
            self.load()
            self.assertFalse(self.phy()['supported'])
        path.write_text(BINDINGS['gpio.yaml'])
        self.source(SOURCE.replace('gpio-controller;', '', 1))
        self.assertFalse(self.phy()['supported'])

    def test_phy_limit_and_cell_bindings_are_validated(self):
        path = self.bindings_dir / 'phy.yaml'
        for content in (BINDINGS['phy.yaml'].replace('const: 0', 'const: 1'),
                        BINDINGS['phy.yaml'].replace('max-bitrate: {type: int, required: true}', 'max-bitrate: {type: int, required: true, enum: [8000000]}'),
                        BINDINGS['phy.yaml'].replace('max-bitrate: {type: int, required: true}', 'max-bitrate: {type: int, required: true, minimum: 1}')):
            path.write_text(content)
            self.load()
            self.assertFalse(self.phy()["supported"])

    def test_common_binding_shape_is_required_even_for_a_known_compatible(self):
        path = self.bindings_dir / 'can-controller.yaml'
        for content in (BINDINGS['can-controller.yaml'].replace('sample-point: {type: int}', 'sample-point: {type: string}'),
                        BINDINGS['can-controller.yaml'].replace('  phys: {type: phandle}\n', '')):
            path.write_text(content)
            self.load()
            self.assertFalse(self.controller()['identity_supported'])
            self.assertFalse(self.controller()['editable'])
            with self.assertRaises(ValueError):
                self.command({'bitrate': 500000})

    def test_binding_ambiguity_and_reference_overrides_withhold_mutations(self):
        self.source(SOURCE + '\n&can0 { status = "okay"; };\n')
        self.assertFalse(self.controller()["editable"])
        self.assertFalse(self.controller()["identity_supported"])
        self.source(SOURCE)
        (self.bindings_dir / 'duplicate.yaml').write_text(BINDINGS['loopback.yaml'])
        self.load()
        self.assertFalse(self.controller()["editable"])
        with self.assertRaises(ValueError):
            self.command({"sample-point": 800})

    def test_child_binding_compatible_metadata_does_not_prove_profile_identity(self):
        (self.bindings_dir / 'holder.yaml').write_text('''compatible: test,holder
include: base.yaml
child-binding:
  compatible: zephyr,can-loopback
  properties:
    bitrate: {type: int}
    sample-point: {type: int}
    phys: {type: phandle}
''')
        self.source('/ { holder { compatible = "test,holder"; can { bitrate = <250000>; }; }; };')
        controller = self.controller('/holder/can')
        self.assertFalse(controller['identity_supported'])
        self.assertFalse(controller['editable'])
        self.assertIn('literal local compatible', controller['reason'])

    def test_literal_fallback_compatible_requires_the_matching_profile_binding(self):
        self.source(SOURCE.replace('compatible = "zephyr,can-loopback"', 'compatible = "custom,loopback", "zephyr,can-loopback"'))
        self.command({"sample-point": 800})
        (self.bindings_dir / 'custom.yaml').write_text('compatible: custom,loopback\ninclude: can-controller.yaml\n')
        self.load()
        self.assertFalse(self.controller()['identity_supported'])

    def test_bus_specific_profile_requires_a_resolved_parent_bus(self):
        self.source(SOURCE.replace('spi { compatible = "test,spi";', 'spi {'))
        self.assertFalse(self.controller('/spi/chip')['editable'])

    def test_shared_evidence_projection_and_empty_document_are_bounded(self):
        self.load()
        document = self.drafts.drafts[self.draft['id']].document
        self.assertEqual(can_evidence(document, self.schema)['controllers'], self.schema['controllers'])
        self.source('/ {};\n')
        self.assertEqual(self.schema['controllers'], [])
        self.assertEqual(self.schema['transceivers'], [])

    def test_command_requires_exact_nonempty_selected_fields(self):
        self.load()
        for values in ({}, [], None, {'sample_point': 800}, {'bitrate-data': 2000000}, {'phys': None}, {'phys': '&missing'},
                       {'phys': '&gpio0'}, {'sample-point': None}, {'bitrate': 500000, 'delete': True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.command(values)
        with self.assertRaises(ValueError):
            self.command({'sample-point': 800}, extra=True)
        self.assertEqual(self.draft['text'], SOURCE)

    def test_revision_expiry_workspace_and_binding_changes_reject_stale_evidence(self):
        self.load()
        (self.bindings_dir / 'base.yaml').write_text(BINDINGS['base.yaml'] + '  changed: {type: int}\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.command({'sample-point': 800})
        self.load()
        self.can.observations[self.schema['schema_id']]['expires'] = 0
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.command({'sample-point': 800})
        self.load()
        self.draft = self.drafts.source(self.guard(text=SOURCE + '/* changed */'))
        with self.assertRaisesRegex(ValueError, 'earlier'):
            self.command({'sample-point': 800})
        other = self.root / 'other'
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaises(ValueError):
            self.load()

    def test_catalogue_revalidated_after_planning_and_failed_plan_never_commits(self):
        self.load()
        catalog = self.can.observations[self.schema['schema_id']]['catalog']
        with patch.object(catalog, 'revalidate', side_effect=[None, ValueError('Binding changed during plan')]):
            with self.assertRaisesRegex(ValueError, 'during plan'):
                self.command({'sample-point': 800})
        self.assertEqual(self.draft['revision'], 0)
        self.assertEqual(self.draft['text'], SOURCE)
