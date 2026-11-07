"""Peripheral forms preserve shared buses and publish only guarded atomic drafts."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request


BINDINGS = {
    "base.yaml": '''properties:
  compatible: {type: string-array, required: true}
  status: {type: string, enum: [okay, disabled]}
''',
    "i2c.yaml": '''compatible: test,i2c
include: base.yaml
bus: i2c
properties:
  clock-frequency: {type: int, enum: [100000, 400000]}
  "#address-cells": {type: int, const: 1}
  "#size-cells": {type: int, const: 0}
''',
    "spi.yaml": '''compatible: test,spi
include: base.yaml
bus: spi
properties:
  cs-gpios: {type: phandle-array}
  "#address-cells": {type: int, const: 1}
  "#size-cells": {type: int, const: 0}
''',
    "uart.yaml": '''compatible: test,uart
include: base.yaml
bus: uart
properties:
  current-speed: {type: int, enum: [9600, 115200]}
''',
    "temp-i2c.yaml": '''compatible: test,temperature
include: base.yaml
on-bus: i2c
properties:
  reg: {type: array, required: true}
  rate: {type: int, default: 100}
''',
    "temp-spi.yaml": '''compatible: test,temperature
include: base.yaml
on-bus: spi
properties:
  reg: {type: array, required: true}
  spi-max-frequency: {type: int, required: true, enum: [1000000, 4000000]}
''',
    "gps.yaml": '''compatible: test,gps
include: base.yaml
on-bus: uart
''',
    "gpio.yaml": '''compatible: test,gpio
include: base.yaml
gpio-cells: [pin, flags]
properties:
  gpio-controller: {type: boolean}
  "#gpio-cells": {type: int, const: 2}
''',
    "leds.yaml": '''compatible: gpio-leds
include: base.yaml
child-binding:
  properties:
    gpios: {type: phandle-array, required: true}
    label: {type: string}
''',
    "keys.yaml": '''compatible: gpio-keys
include: base.yaml
child-binding:
  properties:
    gpios: {type: phandle-array, required: true}
    label: {type: string}
    zephyr,code: {type: int}
''',
}

SOURCE = '''/* Preserve this hardware note. */
/ {
    i2c0: i2c {
        compatible = "test,i2c";
        #address-cells = <1>;
        #size-cells = <0>;
        status = "disabled";
        clock-frequency = <100000>;
        first: sensor@48 {
            compatible = "test,temperature";
            reg = <0x48>;
            handwritten = <SOME_MACRO(2)>;
        };
    };
    spi0: spi {
        compatible = "test,spi";
        #address-cells = <1>;
        #size-cells = <0>;
        cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;
    };
    uart0: uart { compatible = "test,uart"; status = "disabled"; current-speed = <9600>; };
    gpio0: gpio { compatible = "test,gpio"; gpio-controller; #gpio-cells = <2>; };
    leds { compatible = "gpio-leds"; };
};
'''


class PeripheralTests(unittest.TestCase):
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
        for name, text in BINDINGS.items():
            (self.bindings_dir / name).write_text(text)
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = self.app.context.services["devicetree_drafts"]
        self.peripherals = self.app.context.services["devicetree_peripherals"]
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def load(self):
        self.schema = self.peripherals.inspect(self.guard())
        return self.schema

    def parent(self, kind):
        return next(item for item in [*self.schema["controllers"], *self.schema["gpio_groups"]] if item["kind"] == kind)

    def command(self, kind, **fields):
        self.draft = self.drafts.command(self.guard(command={"kind": kind, "schema_id": self.schema["schema_id"], **fields}))
        return self.draft

    def source(self, text):
        self.draft = self.drafts.source(self.guard(text=text))
        self.load()


    def test_explicit_topology_uses_schemas_without_tools_or_disk_changes(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            self.load()
        self.assertEqual([item["kind"] for item in self.schema["controllers"]], ["i2c", "spi", "uart"])
        self.assertEqual(self.parent("spi")["chip_selects"], [0, 1])
        self.assertEqual(self.parent("i2c")["children"][0]["address"], 0x48)
        self.assertEqual(self.parent("spi")["candidates"][0]["fields"][0]["name"], "spi-max-frequency")
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.assertEqual(self.draft["revision"], 0)

    def test_controller_fields_are_explicit_atomic_and_binding_validated(self):
        self.load()
        before = self.draft["text"]
        self.command("peripheral_controller", node_id=self.parent("uart")["node_id"], values={"status": "okay", "current-speed": 115200})
        self.assertEqual(self.draft["revision"], 1)
        self.assertIn('status = "okay"; current-speed = <115200>;', self.draft["text"])
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], before)
        self.load()
        for values in ({"current-speed": 123}, {"pinctrl-0": []}, {"status": []}, []):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.command("peripheral_controller", node_id=self.parent("uart")["node_id"], values=values)

    def test_existing_i2c_devices_share_bus_and_updates_preserve_source(self):
        self.source(SOURCE.replace('        first: sensor@48 {', '        second: sensor@49 { compatible = "test,temperature"; reg = <0x49>; };\n        first: sensor@48 {'))
        child = next(item for item in self.parent("i2c")["children"] if item["label"] == "first")
        self.command("peripheral_update", node_id=child["node_id"], address=0x50, values={})
        self.assertIn("first: sensor@50", self.draft["text"])
        self.assertIn("handwritten = <SOME_MACRO(2)>;", self.draft["text"])
        self.load()
        self.assertEqual(len(self.parent("i2c")["children"]), 2)
        before = self.draft["revision"]
        child = next(item for item in self.parent("i2c")["children"] if item["label"] == "first")
        self.command("peripheral_update", node_id=child["node_id"], address=0x50, values={})
        self.assertEqual(self.draft["revision"], before)
        self.assertEqual(self.overlay.read_text(), SOURCE)

    def test_existing_device_updates_reject_duplicate_and_reserved_addresses(self):
        self.source(SOURCE.replace('        first: sensor@48 {', '        other@49 { compatible = "test,temperature"; reg = <0x49>; status = "disabled"; };\n        first: sensor@48 {'))
        child = next(item for item in self.parent("i2c")["children"] if item["label"] == "first")
        before = self.draft["text"]
        for address in (0x49, 0, 7, 0x78, 0x100, True, "0x50"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                self.command("peripheral_update", node_id=child["node_id"], address=address, values={})
        self.assertEqual(self.draft["text"], before)

    def test_existing_spi_device_updates_validate_frequency_and_chip_select(self):
        self.source(SOURCE.replace('cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;', 'cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>; sensor@0 { compatible = "test,temperature"; reg = <0>; spi-max-frequency = <1000000>; };'))
        child = self.parent("spi")["children"][0]
        with self.assertRaises(ValueError):
            self.command("peripheral_update", node_id=child["node_id"], address=0, values={"spi-max-frequency": 123})
        with self.assertRaisesRegex(ValueError, "cs-gpios"):
            self.command("peripheral_update", node_id=child["node_id"], address=2, values={})
        self.command("peripheral_update", node_id=child["node_id"], address=1, values={"spi-max-frequency": 4000000})
        self.assertIn("sensor@1", self.draft["text"])
        self.assertIn("4000000", self.draft["text"])


    def test_existing_gpio_button_update_keeps_provider_and_identity(self):
        self.source(SOURCE.replace('    leds { compatible = "gpio-leds"; };', '    buttons { compatible = "gpio-keys"; user: user { gpios = <&gpio0 8 1>; zephyr,code = <11>; }; };'))
        self.command("gpio_update", node_id=self.parent("button")["children"][0]["node_id"], values={"zephyr,code": 12})
        self.assertIn("zephyr,code = <12>;", self.draft["text"])
        self.assertIn("gpios = <&gpio0 8 1>;", self.draft["text"])

    def test_removal_preserves_shared_parent_and_chip_select_array(self):
        self.source(SOURCE.replace('cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;', 'cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>; sensor@0 { compatible = "test,temperature"; reg = <0>; spi-max-frequency = <1000000>; }; sensor@1 { compatible = "test,temperature"; reg = <1>; spi-max-frequency = <4000000>; };'))
        self.command("peripheral_remove", node_id=self.parent("spi")["children"][0]["node_id"])
        self.assertIn("cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;", self.draft["text"])
        self.assertIn("sensor@1", self.draft["text"])
        self.assertIn("spi0: spi", self.draft["text"])

    def test_removal_refuses_consumers_and_nested_handwritten_children(self):
        for text in (SOURCE.replace('/ {', '/ { aliases { sensor = &first; };', 1),
                     SOURCE.replace('handwritten = <SOME_MACRO(2)>;', 'handwritten = <SOME_MACRO(2)>; nested { keep = <1>; };')):
            self.source(text)
            child = self.parent("i2c")["children"][0]
            self.assertFalse(child["removable"])
            with self.assertRaises(ValueError):
                self.command("peripheral_remove", node_id=child["node_id"])

    def test_address_change_refuses_known_path_consumers(self):
        self.source(SOURCE.replace('/ {', '/ { aliases { sensor = "/i2c/sensor@48"; };', 1))
        child = self.parent("i2c")["children"][0]
        with self.assertRaisesRegex(ValueError, "path"):
            self.command("peripheral_update", node_id=child["node_id"], address=0x49, values={})

    def test_opaque_or_inconsistent_sibling_and_cell_layout_withhold_allocation(self):
        for text in (SOURCE.replace('reg = <0x48>;', 'reg = <ADDRESS_MACRO>;'),
                     SOURCE.replace('sensor@48', 'sensor@49'), SOURCE.replace('#address-cells = <1>;', '#address-cells = <3>;', 1)):
            self.source(text)
            self.assertFalse(self.parent("i2c")["addable"])

    def test_collisions_in_imported_source_are_reported(self):
        text = SOURCE.replace('        first: sensor@48 {', '        other@48 { compatible = "test,temperature"; reg = <0x48>; };\n        first: sensor@48 {')
        self.source(text)
        self.assertIn("Duplicate I2C address", self.parent("i2c")["diagnostics"][0])

    def test_unresolved_provider_and_external_controller_have_no_guessed_forms(self):
        self.source(SOURCE.replace('cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;', 'cs-gpios = <&external 1 0>;') + '\n&external_uart { status = "okay"; };\n')
        self.assertFalse(self.parent("spi")["addable"])
        self.assertEqual(len(self.schema["controllers"]), 3)

    def test_schema_draft_expiry_and_workspace_guards(self):
        self.load()
        (self.bindings_dir / "base.yaml").write_text(BINDINGS["base.yaml"] + '  changed: {type: int}\n')
        with self.assertRaisesRegex(ValueError, "changed"):
            self.command("peripheral_controller", node_id=self.parent("uart")["node_id"], values={"status": "okay"})
        self.load()
        self.peripherals.observations[self.schema["schema_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.command("peripheral_controller", node_id=self.parent("uart")["node_id"], values={"status": "okay"})
        self.load()
        self.draft = self.drafts.source(self.guard(text=SOURCE + "/* new revision */"))
        with self.assertRaisesRegex(ValueError, "earlier"):
            self.command("peripheral_controller", node_id=self.parent("uart")["node_id"], values={"status": "okay"})
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaises(ValueError):
            self.load()

    def test_route_and_exact_command_fields(self):
        result = self.app.router.dispatch(Request("POST", "/api/devicetree/editor/drafts/peripherals", body=self.guard())).data
        self.assertEqual(result["draft_id"], self.draft["id"])
        self.load()
        with self.assertRaises(ValueError):
            self.command("peripheral_remove", node_id=self.parent("i2c")["node_id"], path="/untrusted")
        self.assertEqual(self.overlay.read_text(), SOURCE)

    def test_existing_device_address_obeys_binding_constraints(self):
        path = self.bindings_dir / "temp-i2c.yaml"
        path.write_text(path.read_text().replace('reg: {type: array, required: true}', 'reg: {type: array, required: true, const: [72]}'))
        self.load()
        with self.assertRaisesRegex(ValueError, "constant"):
            self.command("peripheral_update", node_id=self.parent("i2c")["children"][0]["node_id"], address=0x49, values={})
        self.assertEqual(self.draft["text"], SOURCE)

    def test_non_array_reg_and_custom_gpio_specifier_are_source_only(self):
        path = self.bindings_dir / "temp-i2c.yaml"
        path.write_text(path.read_text().replace('reg: {type: array, required: true}', 'reg: {type: compound, required: true}'))
        path = self.bindings_dir / "leds.yaml"
        path.write_text(path.read_text().replace('type: phandle-array, required: true', 'type: phandle-array, required: true, specifier-space: custom'))
        self.load()
        self.assertFalse(self.parent("i2c")["candidates"][0]["addable"])
        self.assertFalse(self.parent("i2c")["children"][0]["editable"])
        self.assertFalse(self.parent("led")["addable"])

    def test_ordinary_binding_path_consumers_block_removal_and_address_change(self):
        path = self.bindings_dir / "temp-i2c.yaml"
        path.write_text(path.read_text() + '  peer-path: {type: path}\n')
        self.source(SOURCE.replace('        first: sensor@48 {', '        consumer@49 { compatible = "test,temperature"; reg = <0x49>; peer-path = "/i2c/sensor@48"; };\n        first: sensor@48 {'))
        target = next(child for child in self.parent("i2c")["children"] if child["label"] == "first")
        self.assertFalse(target["removable"])
        self.assertFalse(target["address_editable"])
        for kind, extra in (("peripheral_remove", {}), ("peripheral_update", {"address": 0x50, "values": {}})):
            with self.assertRaisesRegex(ValueError, "path"):
                self.command(kind, node_id=target["node_id"], **extra)

    def test_gpio_child_with_required_identity_fields_is_not_silently_incomplete(self):
        path = self.bindings_dir / "leds.yaml"
        path.write_text(path.read_text() + '    reg: {type: array, required: true}\n')
        self.load()
        self.assertFalse(self.parent("led")["addable"])

    def test_address_normalization_does_not_break_a_binding_path_consumer(self):
        path = self.bindings_dir / "temp-i2c.yaml"
        path.write_text(path.read_text() + '  peer-path: {type: path}\n')
        text = SOURCE.replace('sensor@48', 'sensor@048')
        text = text.replace('        first: sensor@048 {', '        consumer@49 { compatible = "test,temperature"; reg = <0x49>; peer-path = "/i2c/sensor@048"; };\n        first: sensor@048 {')
        self.source(text)
        target = next(child for child in self.parent("i2c")["children"] if child["label"] == "first")
        with self.assertRaisesRegex(ValueError, "path"):
            self.command("peripheral_update", node_id=target["node_id"], address=0x48, values={})

    def test_repeated_path_properties_still_protect_their_targets(self):
        path = self.bindings_dir / "temp-i2c.yaml"
        path.write_text(path.read_text() + '  peer-path: {type: path}\n')
        text = SOURCE.replace('        first: sensor@48 {', '        consumer@49 { compatible = "test,temperature"; reg = <0x49>; peer-path = "/i2c/sensor@48"; peer-path = "/i2c/sensor@49"; };\n        first: sensor@48 {')
        self.source(text)
        target = next(child for child in self.parent("i2c")["children"] if child["label"] == "first")
        self.assertFalse(target["removable"])
        self.assertFalse(target["address_editable"])
