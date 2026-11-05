"""Binding forms use exact draft/history guards without materializing defaults."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request


SOURCE = '''/* Keep this comment and macro exactly. */
/ {
    bus0: bus {
        compatible = "test,bus";
        sensor0: sensor@1 {
            compatible = "test,sensor";
            reg = <1>;
            mode = "fast";
            raw = <SOME_MACRO(3)>;
        };
    };
    gpio0: gpio@0 {
        compatible = "test,gpio";
        gpio-controller;
        #gpio-cells = <2>;
    };
    leds {
        compatible = "test,leds";
        user_led: led0 {
            gpios = <&gpio0 4 0>;
            label = "Green";
        };
    };
};
'''

BINDINGS = {
    "base.yaml": '''properties:
  compatible:
    type: string-array
  required-value:
    type: int
    required: true
  speed:
    type: int
    default: 100
''',
    "bus.yaml": '''compatible: test,bus
bus: i2c
properties:
  compatible:
    type: string-array
''',
    "sensor.yaml": '''compatible: test,sensor
on-bus: i2c
include: base.yaml
properties:
  mode:
    type: string
    enum: [fast, slow]
  reg:
    type: array
  raw:
    type: int
  enabled:
    type: boolean
  names:
    type: string-array
  payload:
    type: uint8-array
  peer:
    type: phandle
  peers:
    type: phandles
  location:
    type: path
''',
    "gpio.yaml": '''compatible: test,gpio
gpio-cells: [pin, flags]
properties:
  gpio-controller:
    type: boolean
  "#gpio-cells":
    type: int
    const: 2
''',
    "leds.yaml": '''compatible: test,leds
child-binding:
  properties:
    label:
      type: string
    gpios:
      type: phandle-array
      required: true
''',
}


class BindingServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.source = self.workspace / "app"
        self.bindings_dir = self.source / "dts/bindings"
        self.bindings_dir.mkdir(parents=True)
        (self.source / "CMakeLists.txt").write_text('message(FATAL_ERROR "Never execute this fixture")\n')
        self.overlay = self.source / "app.overlay"
        self.overlay.write_bytes(SOURCE.encode())
        for name, text in BINDINGS.items():
            (self.bindings_dir / name).write_text(text)
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = self.app.context.services["devicetree_drafts"]
        self.bindings = self.app.context.services["devicetree_bindings"]
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.source)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def load(self):
        self.schema = self.bindings.inspect(self.guard())
        return self.schema

    def node(self, name):
        return next(item for item in self.schema["nodes"] if item["name"] == name)

    def prop(self, node, name):
        return next(item for item in self.node(node)["properties"] if item["name"] == name)

    def command(self, kind, **fields):
        self.draft = self.drafts.command(self.guard(command={"kind": kind, "schema_id": self.schema["schema_id"], **fields}))
        return self.draft

    def set(self, node, name, value):
        return self.command("binding_set", node_id=self.node(node)["node_id"], name=name, value=value)

    def test_load_includes_bus_and_child_schemas_without_tools_or_writes(self):
        before = self.overlay.read_bytes()
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")), patch.object(self.app.context.jobs, "submit", side_effect=AssertionError("No jobs")):
            self.load()
        self.assertEqual(self.node("sensor@1")["binding"]["state"], "matched")
        self.assertEqual(self.node("led0")["binding"]["state"], "matched")
        self.assertEqual(self.prop("sensor@1", "speed")["default"], 100)
        self.assertIsNone(self.prop("sensor@1", "speed")["local"])
        self.assertTrue(self.prop("sensor@1", "required-value")["required"])
        self.assertNotIn("required-value", self.draft["text"])
        self.assertEqual(self.overlay.read_bytes(), before)
        self.assertEqual(self.schema["sha256"], self.draft["sha256"])

    def test_set_and_history_preserve_macros_comments_and_disk(self):
        self.load()
        self.set("sensor@1", "speed", 200)
        self.assertIn("speed = <200>;", self.draft["text"])
        self.assertIn("raw = <SOME_MACRO(3)>;", self.draft["text"])
        self.assertIn("/* Keep this comment and macro exactly. */", self.draft["text"])
        self.assertEqual(self.overlay.read_text(), SOURCE)
        candidate = self.draft["text"]
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft["text"], candidate)

    def test_enum_constant_and_typed_scalar_values_are_enforced(self):
        self.load()
        for node, name, value in (("sensor@1", "mode", "invalid"), ("gpio@0", "#gpio-cells", 3),
                                  ("sensor@1", "speed", True), ("sensor@1", "speed", -1), ("sensor@1", "enabled", 1)):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.set(node, name, value)
        self.assertEqual(self.draft["text"], SOURCE)
        self.set("sensor@1", "mode", "slow")
        self.assertIn('mode = "slow";', self.draft["text"])

    def test_signed_binding_constraints_are_explained_as_source_only(self):
        path = self.bindings_dir / "base.yaml"
        path.write_text(path.read_text() + "    const: -1\n")
        self.load()
        self.assertFalse(self.prop("sensor@1", "speed")["editable"])
        self.assertIn("unsigned 32-bit", self.prop("sensor@1", "speed")["reason"])

    def test_arrays_strings_bytes_and_node_references_share_commands(self):
        for name, value, rendered in (("names", ["a", 'b"c'], 'names = "a", "b\\"c";'),
                                     ("payload", [0, 17, 255], "payload = [00 11 ff];"),
                                     ("reg", [2, 3], "reg = <2 3>;"),
                                     ("peer", "&gpio0", "peer = <&gpio0>;"),
                                     ("peers", ["&gpio0", "&user_led"], "peers = <&gpio0 &user_led>;"),
                                     ("location", "/gpio@0", "location = &gpio0;")):
            with self.subTest(name=name):
                self.load()
                self.set("sensor@1", name, value)
                self.assertIn(rendered, self.draft["text"])
                self.load()
                self.assertTrue(self.prop("sensor@1", name)["editable"])

    def test_array_enums_validate_each_element(self):
        path = self.bindings_dir / "sensor.yaml"
        path.write_text(path.read_text().replace("  reg:\n    type: array", "  reg:\n    type: array\n    enum: [1, 2]")
                        .replace("  names:\n    type: string-array", "  names:\n    type: string-array\n    enum: [first, second]"))
        self.load()
        with self.assertRaises(ValueError):
            self.set("sensor@1", "reg", [1, 3])
        self.set("sensor@1", "reg", [1, 2])
        self.load()
        with self.assertRaises(ValueError):
            self.set("sensor@1", "names", ["first", "unknown"])
        self.set("sensor@1", "names", ["first", "second"])

    def test_unchanged_catalog_reuse_still_detects_changed_binding(self):
        self.load()
        original = self.bindings.observations[self.schema["schema_id"]]["catalog"]
        self.set("sensor@1", "speed", 200)
        self.load()
        self.assertIs(self.bindings.observations[self.schema["schema_id"]]["catalog"], original)
        (self.bindings_dir / "base.yaml").write_text(BINDINGS["base.yaml"].replace("default: 100", "default: 150"))
        self.load()
        self.assertIsNot(self.bindings.observations[self.schema["schema_id"]]["catalog"], original)
        self.assertEqual(self.prop("sensor@1", "speed")["default"], 150)

    def test_delete_cannot_remove_referenced_value_label(self):
        self.draft = self.drafts.source(self.guard(text=SOURCE.replace("reg = <1>;", "reg = <cell: 1>; consumer = <&cell>;")))
        self.load()
        with self.assertRaisesRegex(ValueError, "references"):
            self.command("binding_delete", node_id=self.node("sensor@1")["node_id"], name="reg")

    def test_provider_cells_come_from_actual_provider_and_reject_wrong_counts(self):
        self.load()
        prop = self.prop("led0", "gpios")
        self.assertEqual(prop["providers"][0]["reference"], "&gpio0")
        self.assertEqual(prop["providers"][0]["cells"], ["pin", "flags"])
        for value in ([{"reference": "&gpio0", "cells": [1]}], [{"reference": "&missing", "cells": [1, 0]}]):
            with self.assertRaises(ValueError):
                self.set("led0", "gpios", value)
        self.set("led0", "gpios", [{"reference": "&gpio0", "cells": [7, 1]}])
        self.assertIn("gpios = <&gpio0 7 1>;", self.draft["text"])
        self.load()
        self.assertEqual(self.prop("led0", "gpios")["local"]["parsed"], [{"reference": "&gpio0", "cells": [7, 1]}])

    def test_provider_count_mismatch_or_multiple_contributors_withholds_form(self):
        for suffix, source in (("", SOURCE.replace("#gpio-cells = <2>", "#gpio-cells = <3>")),
                               ('\n&gpio0 { #gpio-cells = <3>; };\n', SOURCE)):
            self.draft = self.drafts.source(self.guard(text=source + suffix))
            self.load()
            self.assertEqual(self.prop("led0", "gpios")["providers"], [])
            self.assertFalse(self.prop("led0", "gpios")["editable"])

    def test_unsupported_existing_references_and_cells_are_never_truncated(self):
        for literal in ("<&gpio0 4 0 99>", "<&external 4 0>"):
            with self.subTest(literal=literal):
                self.draft = self.drafts.source(self.guard(text=SOURCE.replace("<&gpio0 4 0>", literal)))
                self.load()
                self.assertFalse(self.prop("led0", "gpios")["editable"])
                with self.assertRaises(ValueError):
                    self.set("led0", "gpios", [{"reference": "&gpio0", "cells": [4, 0]}])
        for name, literal in (("peer", "<&gpio0 4 0>"), ("peer", "<&external>"), ("location", "<&gpio0 4 0>")):
            self.draft = self.drafts.source(self.guard(text=SOURCE.replace('mode = "fast";', f'mode = "fast"; {name} = {literal};')))
            self.load()
            self.assertFalse(self.prop("sensor@1", name)["editable"])

    def test_ancestor_overrides_and_non_node_label_collisions_withhold_providers(self):
        sources = [SOURCE.replace('    gpio0: gpio@0 {', '    holder: folder { gpio0: gpio@0 {')
                   .replace('    leds {', '    };\n    leds {') + '\n&holder { gpio@0 { #gpio-cells = <3>; }; };\n',
                   SOURCE.replace('/ {', '/ { gpio0: scalar = <1>;', 1)]
        for source in sources:
            self.draft = self.drafts.source(self.guard(text=source))
            self.load()
            self.assertFalse(self.prop("led0", "gpios")["editable"])
            self.assertNotIn("&gpio0", [item["reference"] for item in self.schema["targets"]])

    def test_named_path_strings_resolve_as_local_targets(self):
        self.draft = self.drafts.source(self.guard(text=SOURCE.replace('/ {', '/ { aliases { led0 = "/gpio@0"; };', 1)))
        self.load()
        self.assertEqual(self.schema["aliases"][0]["reference"], "/gpio@0")
        self.assertEqual(self.schema["aliases"][0]["state"], "local")

    def test_current_path_references_normalize_for_forms_without_mutating_source(self):
        source = SOURCE.replace("<&gpio0 4 0>", "<&{/gpio@0} 4 0>")
        source = source.replace('mode = "fast";', 'mode = "fast"; location = "/gpio@0";')
        self.draft = self.drafts.source(self.guard(text=source))
        self.load()
        self.assertEqual(self.prop("led0", "gpios")["local"]["parsed"], [{"reference": "&gpio0", "cells": [4, 0]}])
        self.assertEqual(self.prop("sensor@1", "location")["local"]["parsed"], "&gpio0")
        self.assertEqual(self.draft["text"], source)

    def test_malformed_reference_values_return_validation_errors(self):
        self.load()
        for value in ({}, [], True, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.set("sensor@1", "peer", value)

    def test_reset_removes_override_and_delete_inserts_directive_without_default(self):
        self.load()
        self.set("sensor@1", "speed", 200)
        self.load()
        self.command("binding_reset", node_id=self.node("sensor@1")["node_id"], name="speed")
        self.assertNotIn("speed", self.draft["text"])
        self.load()
        self.command("binding_delete", node_id=self.node("sensor@1")["node_id"], name="mode")
        self.assertIn("/delete-property/ mode;", self.draft["text"])
        self.assertNotIn('mode = "fast"', self.draft["text"])
        self.load()
        self.assertTrue(self.prop("sensor@1", "mode")["deleted"])
        self.command("binding_reset", node_id=self.node("sensor@1")["node_id"], name="mode")
        self.assertNotIn("/delete-property/ mode;", self.draft["text"])

    def test_false_boolean_is_explicit_deletion_and_reset_preserves_unknown_inheritance(self):
        self.load()
        self.set("sensor@1", "enabled", True)
        self.load()
        self.set("sensor@1", "enabled", False)
        self.assertIn("/delete-property/ enabled;", self.draft["text"])
        self.load()
        self.command("binding_reset", node_id=self.node("sensor@1")["node_id"], name="enabled")
        self.assertNotIn("enabled", self.draft["text"])

    def test_aliases_chosen_and_labels_are_atomic_history_commands(self):
        for kind, name, reference in (("set_alias", "status-led", "&user_led"), ("set_chosen", "zephyr,console", "&sensor0")):
            self.load()
            before = self.draft["text"]
            self.command(kind, name=name, reference=reference)
            self.assertIn(f"{name} = {reference};", self.draft["text"])
            self.draft = self.drafts.undo(self.guard())
            self.assertEqual(self.draft["text"], before)
            self.draft = self.drafts.redo(self.guard())
        self.load()
        self.command("add_label", node_id=self.node("leds")["node_id"], label="led_group")
        self.assertIn("led_group: leds", self.draft["text"])
        self.load()
        self.command("remove_alias", name="status-led")
        self.assertNotIn("status-led =", self.draft["text"])

    def test_unresolved_references_and_stale_schema_cannot_mutate_draft(self):
        self.load()
        for kind, fields in (("set_alias", {"name": "status-led", "reference": "&external"}),
                             ("binding_set", {"node_id": self.node("sensor@1")["node_id"], "name": "peer", "value": "&external"})):
            with self.assertRaises(ValueError):
                self.command(kind, **fields)
        old = self.schema
        self.draft = self.drafts.source(self.guard(text=SOURCE + "/* Another edit */\n"))
        self.schema = old
        with self.assertRaisesRegex(ValueError, "earlier"):
            self.set("sensor@1", "speed", 200)

    def test_binding_changes_and_new_duplicate_candidates_block_application(self):
        for mutation in (lambda: (self.bindings_dir / "base.yaml").write_text(BINDINGS["base.yaml"] + "  other: {type: int}\n"),
                         lambda: (self.bindings_dir / "duplicate.yaml").write_text(BINDINGS["sensor.yaml"])):
            self.load()
            mutation()
            with self.assertRaisesRegex(ValueError, "changed"):
                self.set("sensor@1", "speed", 200)
            self.assertEqual(self.draft["text"], SOURCE)
        self.load()
        self.assertEqual(self.node("sensor@1")["binding"]["state"], "ambiguous")

    def test_missing_bindings_and_opaque_values_are_explicit(self):
        self.load()
        self.assertFalse(self.prop("sensor@1", "raw")["editable"])
        self.assertIn("expression", self.prop("sensor@1", "raw")["reason"])
        self.draft = self.drafts.source(self.guard(text=SOURCE.replace("test,sensor", "test,unknown")))
        self.load()
        self.assertEqual(self.node("sensor@1")["binding"]["state"], "missing")

    def test_route_unknown_fields_workspace_and_expiry_guards(self):
        response = self.app.router.dispatch(Request("POST", "/api/devicetree/editor/drafts/bindings", body=self.guard())).data
        self.assertEqual(response["draft_id"], self.draft["id"])
        with self.assertRaises(ValueError):
            self.bindings.inspect(self.guard(path="/untrusted"))
        self.load()
        self.bindings.observations[self.schema["schema_id"]]["expires"] = 0
        with self.assertRaises(ValueError):
            self.set("sensor@1", "speed", 200)
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaises(ValueError):
            self.bindings.inspect(self.guard())

    def test_directory_only_searches_application_bindings_not_unselected_zephyr(self):
        self.load()
        self.assertEqual(self.bindings.observations[self.schema["schema_id"]]["catalog"].roots, [str(self.bindings_dir)])
