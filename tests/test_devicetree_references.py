"""D6 typed arrays and literal references preserve source and reject unknown targets."""

import unittest

from zephyr_utilities.features.devicetree.source import PropertyValue, SourceDocument
from zephyr_utilities.features.devicetree.source_syntax import SourceError


class DevicetreeReferenceTests(unittest.TestCase):
    def document(self, text):
        return SourceDocument.parse(text.encode() if isinstance(text, str) else text, document_id="d6-source")

    def reversible(self, document, change):
        result = change.apply(document)
        self.assertEqual(change.inverse().apply(result).source, document.source)
        return result

    def value(self, document, selector, name):
        return document.property_value(document.find_property(document.find_node(selector).id, name).id)

    def test_string_arrays_round_trip_escaped_text_and_preserve_unrelated_macros(self):
        source = b'/ { compatible = "old,a", /* keep */ "old,b"; expression = <MACRO(1)>; };'
        document = self.document(source)
        self.assertEqual(self.value(document, "/", "compatible"), PropertyValue.strings(["old,a", "old,b"]))
        changed = self.reversible(document, document.plan_set_property(document.find_node("/").id, "compatible",
            PropertyValue.strings(["new,a", 'quoted "text"; };'])) )
        self.assertIn(b'"new,a", /* keep */ "quoted \\"text\\"; };"', changed.source)
        self.assertIn(b"expression = <MACRO(1)>;", changed.source)
        self.assertEqual(len(changed.syntax.nodes), 1)
        self.assertEqual(self.value(changed, "/", "compatible"), PropertyValue.strings(["new,a", 'quoted "text"; };']))

    def test_byte_array_leaf_edit_preserves_comments_case_and_noop(self):
        document = self.document(b"&device { data = [ /* a */ 00 /* b */ AB ff ]; };")
        node = document.find_node("&device")
        self.assertEqual(self.value(document, "&device", "data"), PropertyValue.bytes([0, 171, 255]))
        no_op = document.plan_set_property(node.id, "data", PropertyValue.bytes([0, 171, 255]))
        self.assertEqual(no_op.edits, ())
        changed = self.reversible(document, document.plan_set_property(node.id, "data", PropertyValue.bytes([1, 205, 254])))
        self.assertIn(b"[ /* a */ 01 /* b */ CD fe ]", changed.source)

    def test_phandle_arrays_preserve_provider_cell_comments_and_radix(self):
        document = self.document(b'/ { p0: gpio0 {}; p1: gpio1 {}; user { gpios = <&p0 /* pin */ 0x01 0>, <&p1 2 /* flags */ 1>; }; };')
        value = self.value(document, "/user", "gpios")
        self.assertEqual(value, PropertyValue.phandle_array([("&p0", [1, 0]), ("&p1", [2, 1])]))
        changed = self.reversible(document, document.plan_set_property(document.find_node("/user").id, "gpios",
            PropertyValue.phandle_array([("&p1", [3, 0]), ("&p0", [4, 1])])))
        self.assertIn(b"<&p1 /* pin */ 0x3 0>, <&p0 4 /* flags */ 1>", changed.source)
        self.assertEqual(changed.syntax.state, "complete")

    def test_one_cell_block_can_hold_multiple_literal_providers(self):
        document = self.document(b"/ { gpios = <&a 1 0 &b 2 3>; };")
        self.assertEqual(self.value(document, "/", "gpios"), PropertyValue.phandle_array([("&a", [1, 0]), ("&b", [2, 3])]))
        changed = self.reversible(document, document.plan_set_property(document.find_node("/").id, "gpios",
            PropertyValue.phandle_array([("&a", [3, 0]), ("&b", [2, 1])])))
        self.assertEqual(changed.source, b"/ { gpios = <&a 3 0 &b 2 1>; };")

    def test_phandles_and_zero_cell_provider_entries_are_equivalent_noops(self):
        document = self.document(b"/ { handles = <&a &{/b}>; };")
        self.assertEqual(self.value(document, "/", "handles"), PropertyValue.phandles(["&a", "&{/b}"]))
        for value in (PropertyValue.phandles(["&a", "&{/b}"]), PropertyValue.phandle_array([("&a", []), ("&{/b}", [])])):
            self.assertEqual(document.plan_set_property(document.find_node("/").id, "handles", value).edits, ())

    def test_bare_references_and_quoted_paths_are_distinct_supported_values(self):
        document = self.document(b'/ { target: device {}; aliases { led0 = &target; }; chosen { path = "/device"; }; };')
        self.assertEqual(self.value(document, "/aliases", "led0"), PropertyValue.reference("&target"))
        self.assertEqual(document.plan_set_property(document.find_node("/chosen").id, "path", PropertyValue.path("/device")).edits, ())
        changed = self.reversible(document, document.plan_set_property(document.find_node("/aliases").id, "led0", PropertyValue.reference("&{/device}")))
        self.assertIn(b"led0 = &{/device};", changed.source)

    def test_shape_changes_with_internal_comments_remain_explicit_source_only(self):
        for source, name, value in (
                (b'/ { names = "a", /* keep */ "b"; };', "names", PropertyValue.strings(["c"])),
                (b'/ { data = [01 /* keep */ 02]; };', "data", PropertyValue.bytes([3])),
                (b'/ { refs = <&a /* keep */ 1 2>; };', "refs", PropertyValue.phandle_array([("&a", [3])]))):
            with self.subTest(source=source):
                document = self.document(source)
                with self.assertRaisesRegex(SourceError, "comments"):
                    document.plan_set_property(document.find_node("/").id, name, value)

    def test_unknown_provider_macros_and_unsupported_values_remain_opaque(self):
        for expression in ("<&a PIN FLAGS>", "<0 &a 1>", '"a", MACRO', "[01 BAD]"):
            with self.subTest(expression=expression):
                document = self.document(f'/ {{ x = {expression}; status = "disabled"; }};')
                self.assertIsNone(self.value(document, "/", "x"))
                changed = self.reversible(document, document.plan_set_status(document.find_node("/").id, "okay"))
                self.assertIn(expression.encode(), changed.source)
                with self.assertRaises(SourceError):
                    document.plan_set_property(document.find_node("/").id, "x", PropertyValue.cells([1]))

    def test_typed_values_reject_injection_and_invalid_ranges(self):
        calls = [lambda: PropertyValue.strings("value"), lambda: PropertyValue.strings([]),
                 lambda: PropertyValue.bytes([True]), lambda: PropertyValue.bytes([-1]), lambda: PropertyValue.bytes([256]),
                 lambda: PropertyValue.reference("&a; bad;"), lambda: PropertyValue.reference("&{/../bad}"),
                 lambda: PropertyValue.reference("&{/empty//part}"), lambda: PropertyValue.path("relative"),
                 lambda: PropertyValue.phandles(["node"]), lambda: PropertyValue.phandle_array([("&a", ["MACRO"])]),
                 lambda: PropertyValue.phandle_array([("&a", [-1])]), lambda: PropertyValue.phandle_array([("&a", "1")])]
        for call in calls:
            with self.subTest(call=call), self.assertRaises(SourceError):
                call()

    def test_inventory_distinguishes_missing_and_duplicate_local_targets(self):
        document = self.document(b'/ { good: a {}; duplicate: b {}; duplicate: c {}; aliases { x = &good; }; user { x = <&missing &duplicate>; }; };')
        references = {item["reference"]: item for item in document.reference_inventory()["references"]}
        self.assertEqual(references["&good"]["state"], "resolved")
        self.assertEqual(references["&missing"]["state"], "missing")
        self.assertEqual(references["&duplicate"]["state"], "ambiguous")
        self.assertEqual(len(references["&duplicate"]["target_ids"]), 2)

    def test_alias_and_chosen_add_update_remove_are_reversible_and_target_checked(self):
        document = self.document(b"/ {\r\n\tfirst: a {};\r\n\tsecond: b {};\r\n};\r\n")
        alias = self.reversible(document, document.plan_set_named_reference("aliases", "led0", "&first"))
        self.assertIn(b"\r\n\taliases {\r\n\t\tled0 = &first;\r\n\t};", alias.source)
        changed = self.reversible(alias, alias.plan_set_named_reference("aliases", "led0", "&second"))
        self.assertEqual(changed.plan_set_named_reference("aliases", "led0", "&second").edits, ())
        chosen = self.reversible(changed, changed.plan_set_named_reference("chosen", "zephyr,console", "&first"))
        self.assertIn(b"zephyr,console = &first;", chosen.source)
        removed = self.reversible(chosen, chosen.plan_remove_named_reference("aliases", "led0"))
        self.assertNotIn(b"led0", removed.source)
        self.assertIn(b"chosen", removed.source)
        self.assertEqual(removed.plan_remove_named_reference("aliases", "led0").edits, ())
        for kind, name, reference in (("aliases", "INVALID", "&first"), ("chosen", "x", "&absent")):
            with self.assertRaises(SourceError):
                document.plan_set_named_reference(kind, name, reference)

    def test_named_reference_can_add_missing_root_without_touching_override(self):
        document = self.document(b"&bus { sensor: child {}; };\n")
        changed = self.reversible(document, document.plan_set_named_reference("aliases", "sensor0", "&sensor"))
        self.assertTrue(changed.source.startswith(document.source))
        self.assertIn(b"sensor0 = &sensor;", changed.source)

    def test_ambiguous_roots_blocks_targets_and_deleted_targets_are_refused(self):
        sources = [b"/ { target: device {}; }; / {};",
                   b"/ { target: device {}; aliases {}; aliases {}; };",
                   b"/ { target: device {}; target: other {}; };",
                   b"/ { target: device {}; }; /delete-node/ &target;"]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(SourceError):
                self.document(source).plan_set_named_reference("aliases", "x", "&target")

    def test_add_label_noop_collision_and_root_refusal(self):
        document = self.document(b"/ { device {}; other: second {}; };")
        changed = self.reversible(document, document.plan_add_label(document.find_node("/device").id, "new_label"))
        self.assertEqual(changed.find_node("&new_label").name, "device")
        self.assertEqual(changed.plan_add_label(changed.find_node("&new_label").id, "new_label").edits, ())
        with self.assertRaises(SourceError):
            document.plan_add_label(document.find_node("/device").id, "other")
        with self.assertRaises(SourceError):
            document.plan_add_label(document.find_node("/").id, "root")

    def test_label_rename_updates_every_literal_reference_but_not_comments_or_strings(self):
        document = self.document(b'/ { old: device {}; aliases { a = &old; }; chosen { c = &old; }; user { r = <&old 1>; text = "&old"; }; }; &old {}; // &old\n')
        node = document.find_node("/device")
        with self.assertRaisesRegex(SourceError, "closure"):
            document.plan_rename_label(node.id, "old", "new")
        changed = self.reversible(document, document.plan_rename_label(node.id, "old", "new", external_references_checked=True))
        self.assertIn(b'new: device', changed.source)
        self.assertEqual(changed.source.count(b"&new"), 4)
        self.assertIn(b'text = "&old";', changed.source)
        self.assertTrue(changed.source.endswith(b"// &old\n"))

    def test_rename_rejects_includes_opaque_expressions_and_duplicate_labels_even_with_external_scan(self):
        for source in (b'#include "other.dtsi"\n/ { old: device {}; };',
                       b'/include/ "other.dtsi"\n/ { old: device {}; };',
                       b'/ { x = <MACRO(1)>; old: device {}; };',
                       b'/ { old: device {}; old: other {}; };'):
            document = self.document(source)
            if not document.syntax.nodes:
                self.assertTrue(document.syntax.blocked)
                continue
            with self.subTest(source=source), self.assertRaises(SourceError):
                document.plan_rename_label(document.syntax.nodes[1].id, "old", "new", external_references_checked=True)

    def test_reset_does_not_materialize_defaults_and_guards_property_labels(self):
        document = self.document(b'/ { x = <1>; /delete-property/ y; };')
        root = document.find_node("/")
        reset = self.reversible(document, document.plan_reset_property(root.id, "x"))
        self.assertNotIn(b"x =", reset.source)
        reset = self.reversible(reset, reset.plan_reset_property(reset.find_node("/").id, "y"))
        self.assertNotIn(b"/delete-property/", reset.source)
        self.assertEqual(reset.plan_reset_property(reset.find_node("/").id, "missing").edits, ())
        labelled = self.document(b'/ { value: data = <1>; other = <&value>; };')
        with self.assertRaisesRegex(SourceError, "label"):
            labelled.plan_reset_property(labelled.find_node("/").id, "data")

    def test_reset_and_node_removal_guard_labels_inside_opaque_value_ranges(self):
        document = self.document(b'/ { user = <&cell>; device { data = <cell: 1>; }; };')
        node = document.find_node("/device")
        self.assertIsNone(self.value(document, "/device", "data"))
        with self.assertRaisesRegex(SourceError, "label"):
            document.plan_reset_property(node.id, "data")
        with self.assertRaisesRegex(SourceError, "label"):
            document.plan_remove_node(node.id)

    def test_removing_targets_rejects_bare_and_quoted_alias_paths(self):
        for target in ('&target', '"/device"'):
            document = self.document(f'/ {{ target: device {{}}; aliases {{ alias0 = {target}; }}; }};')
            with self.subTest(target=target), self.assertRaises(SourceError):
                document.plan_remove_node(document.find_node("/device").id)
