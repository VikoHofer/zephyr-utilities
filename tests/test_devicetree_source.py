"""D2 source operations change selected declarations without rewriting their neighbors."""

from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.source import PropertyValue, SourceDocument
from zephyr_utilities.features.devicetree.source_syntax import SourceError


FIXTURES = Path(__file__).parent / "fixtures" / "devicetree_editor"


class DevicetreeSourceTests(unittest.TestCase):
    def document(self, source, **options):
        return SourceDocument.parse(source, document_id="synthetic-overlay", **options)

    def fixture(self, name):
        return (FIXTURES / name).read_bytes()

    def apply_reversible(self, document, change):
        before = document.source
        result = change.apply(document)
        self.assertEqual(document.source, before)
        self.assertEqual(result.id, document.id)
        self.assertEqual(result.revision, document.revision + bool(change.edits))
        restored = change.inverse().apply(result)
        self.assertEqual(restored.source, before)
        self.assertEqual(restored.revision, result.revision + bool(change.edits))
        self.assertEqual(change.inverse().inverse().apply(restored).source, result.source)
        return result

    def test_fixture_status_edit_preserves_nested_properties_macros_and_comments(self):
        source = self.fixture("literals.overlay")
        document = self.document(source)
        node = document.find_node("&uart0")
        change = document.plan_set_status(node.id, "okay")
        edited = self.apply_reversible(document, change)
        self.assertEqual(edited.source, source.replace(b'= "disabled" /* keep after */',
                                                     b'= "okay" /* keep after */', 1))
        self.assertIn(b'child: device@0 {\n        status = "disabled";', edited.source)

    def test_cell_leaf_replacements_keep_comments_and_radix(self):
        source = b"&i2c0 { timing = < /* first */ 0x01 /* second */ 2 >; };"
        document = self.document(source)
        change = document.plan_set_property(document.find_node("&i2c0").id, "timing", PropertyValue.cells((3, 4)))
        edited = self.apply_reversible(document, change)
        self.assertIn(b"/* first */", edited.source)
        self.assertIn(b"/* second */", edited.source)
        self.assertEqual(len(change.edits), 2)
        for edit in change.edits:
            self.assertIn(edit.before, (b"0x01", b"2"))
        self.assertEqual(edited.source.replace(b"0x3", b"0x01").replace(b" 4 >", b" 2 >"), source)

    def test_unicode_offsets_crlf_mixed_newlines_and_missing_final_newline_remain_exact(self):
        for source in (b'&uart0 {\r\n\tstatus = "disabled";\r\n};',
                       b'// leading\r\n&uart0 {\n status = "disabled";\r\n};\n',
                       '/* 温度 */\n&{/soc/serial@1000} { status = "disabled"; };'.encode()):
            with self.subTest(source=source):
                document = self.document(source)
                change = document.plan_set_status(document.syntax.nodes[0].id, "okay")
                self.assertEqual(self.apply_reversible(document, change).source,
                                 source.replace(b'"disabled"', b'"okay"'))

    def test_set_property_and_status_are_deterministic_and_idempotent(self):
        document = self.document(b'&uart0 { status = "disabled"; rate = <0x01>; };', revision=7)
        node = document.find_node("&uart0")
        first = document.plan_set_status(node.id, "okay")
        self.assertEqual(first, document.plan_set_status(node.id, "okay"))
        result = self.apply_reversible(document, first)
        no_op = result.plan_set_status(result.find_node("&uart0").id, "okay")
        self.assertEqual(no_op.edits, ())
        self.assertEqual(self.apply_reversible(result, no_op).source, result.source)
        numeric_no_op = document.plan_set_property(node.id, "rate", PropertyValue.cells((1,)))
        self.assertEqual(numeric_no_op.edits, ())
        self.assertEqual(numeric_no_op.apply(document).source, document.source)

    def test_boolean_and_missing_status_can_be_inserted_without_losing_existing_text(self):
        source = b"&uart0 {\n\t/* handwritten */\n\trate = <9600>;\n}; // tail\n"
        document = self.document(source)
        added = self.apply_reversible(document, document.plan_set_property(
            document.find_node("&uart0").id, "hw-flow-control", PropertyValue.boolean()))
        prop = added.find_property(added.find_node("&uart0").id, "hw-flow-control")
        self.assertIsNone(prop.value_start)
        self.assertIn(b"\t/* handwritten */\n\trate = <9600>;\n", added.source)
        self.assertTrue(added.source.endswith(b"}; // tail\n"))
        disabled = self.apply_reversible(added, added.plan_set_status(added.find_node("&uart0").id, "disabled"))
        self.assertIn(b'status = "disabled";', disabled.source)

    def test_missing_properties_are_inserted_before_children_and_node_deletions(self):
        for source in (b"&i2c0 { existing = <1>; child@0 {}; };",
                       b"&i2c0 { /delete-node/ old@0; child@1 {}; };"):
            for operation in ("status", "delete-property"):
                with self.subTest(source=source, operation=operation):
                    document = self.document(source)
                    parent = document.find_node("&i2c0")
                    change = (document.plan_set_status(parent.id, "okay") if operation == "status"
                              else document.plan_delete_property(parent.id, "old-property"))
                    edited = self.apply_reversible(document, change)
                    parent = edited.find_node("&i2c0")
                    prop = edited.find_property(parent.id, "status" if operation == "status" else "old-property")
                    children = [edited.node(child).start for child in parent.children]
                    deletions = [item.start for item in edited.syntax.deletions if item.parent_id == parent.id]
                    self.assertLessEqual(prop.end, min(children + deletions))
                    self.assertIn(b"child@", edited.source)

    def test_property_insertion_into_nested_parent_preserves_crlf_and_indentation(self):
        source = b"/ {\r\n\tbus {\r\n\t\t/* child note */\r\n\t\tchild@0 {};\r\n\t};\r\n};\r\n"
        for operation in ("status", "delete-property"):
            with self.subTest(operation=operation):
                document = self.document(source)
                parent = document.find_node("/bus")
                change = (document.plan_set_status(parent.id, "okay") if operation == "status"
                          else document.plan_delete_property(parent.id, "old-property"))
                edited = self.apply_reversible(document, change)
                declaration = (b'status = "okay";' if operation == "status" else b"/delete-property/ old-property;")
                self.assertIn(b"\r\n\t\t" + declaration + b"\r\n", edited.source)
                self.assertIn(b"\t\t/* child note */\r\n", edited.source)
                self.assertIn(b"\t\tchild@0 {};\r\n\t};\r\n};\r\n", edited.source)
                self.assertNotIn(b"\n", edited.source.replace(b"\r\n", b""))

    def test_new_nested_node_uses_surrounding_crlf_and_indent_without_reformatting(self):
        source = b"/ {\r\n\tbus {\r\n\t\t#address-cells = <1>;\r\n\t};\r\n};\r\n"
        document = self.document(source)
        change = document.plan_add_node(document.find_node("/bus").id, "child@0",
                                         properties=(("reg", PropertyValue.cells((0,))),))
        edited = self.apply_reversible(document, change)
        self.assertIn(b"\t\t#address-cells = <1>;\r\n", edited.source)
        self.assertIn(b"\r\n\t\tchild@0 {\r\n\t\t\treg = <0>;\r\n\t\t};\r\n", edited.source)
        self.assertTrue(edited.source.endswith(b"\t};\r\n};\r\n"))
        self.assertNotIn(b"\n", edited.source.replace(b"\r\n", b""))

    def test_literal_kind_changes_preserve_external_comments_and_refuse_inner_comment_loss(self):
        document = self.document(b'&uart0 { flag /* before */; value = <1>; };')
        node = document.find_node("&uart0")
        assigned = self.apply_reversible(document, document.plan_set_property(node.id, "flag", PropertyValue.string("yes")))
        self.assertIn(b"/* before */", assigned.source)
        removed_value = self.apply_reversible(assigned, assigned.plan_set_property(
            assigned.find_node("&uart0").id, "value", PropertyValue.boolean()))
        self.assertIsNone(removed_value.find_property(removed_value.find_node("&uart0").id, "value").value_start)
        commented = self.document(b"&uart0 { value = <1 /* keep */ 2>; };")
        for value in (PropertyValue.boolean(), PropertyValue.string("changed"), PropertyValue.cells((3,))):
            with self.subTest(value=value), self.assertRaises(SourceError):
                commented.plan_set_property(commented.find_node("&uart0").id, "value", value)

    def test_strings_are_escaped_as_data_without_injecting_statements(self):
        document = self.document(b'&uart0 { label = "old"; };')
        text = 'a "quote"; }; &evil { status = "okay"; }; \\ next\n温度'
        edited = self.apply_reversible(document, document.plan_set_property(
            document.find_node("&uart0").id, "label", PropertyValue.string(text)))
        self.assertEqual(len(edited.syntax.nodes), 1)
        self.assertEqual(len(edited.syntax.nodes[0].properties), 1)
        self.assertIn(b'\\"quote\\"', edited.source)
        self.assertIn(b"\\n", edited.source)
        self.assertEqual(edited.plan_set_property(edited.syntax.nodes[0].id, "label", PropertyValue.string(text)).edits, ())

    def test_comma_names_address_cells_and_labeled_properties_are_targetable(self):
        source = b"/ { #address-cells = <1>; sensor: thing@0 { p: vendor,flag; }; };"
        document = self.document(source)
        root = document.find_node("/")
        changed = self.apply_reversible(document, document.plan_set_property(root.id, "#address-cells", PropertyValue.cells((2,))))
        selected = changed.find_node("&sensor")
        edited = self.apply_reversible(changed, changed.plan_set_property(selected.id, "vendor,flag", PropertyValue.string("ready")))
        self.assertIn(b"p: vendor,flag", edited.source)
        self.assertEqual(edited.find_node("/thing@0").id, edited.find_node("&sensor").id)

    def test_repeated_overrides_require_explicit_source_occurrences(self):
        source = self.fixture("repeated.overlay")
        document = self.document(source)
        with self.assertRaises(SourceError):
            document.find_node("&uart0")
        last = document.syntax.nodes[-1]
        edited = self.apply_reversible(document, document.plan_set_status(last.id, "okay"))
        self.assertEqual(edited.source, source.replace(b'"disabled"', b'"okay"'))

    def test_duplicate_properties_require_explicit_occurrence_selection(self):
        source = b'&uart0 { status = "okay"; status = "disabled"; };'
        document = self.document(source)
        node = document.find_node("&uart0")
        with self.assertRaises(SourceError):
            document.find_property(node.id, "status")
        with self.assertRaises(SourceError):
            document.plan_set_status(node.id, "okay")
        selected = node.properties[-1]
        result = self.apply_reversible(document, document.plan_set_property(
            node.id, "status", PropertyValue.string("reserved"), property_id=selected.id))
        self.assertEqual(result.source, source.replace(b'"disabled"', b'"reserved"'))

    def test_foreign_property_and_stale_source_occurrences_cannot_be_reused(self):
        document = self.document(b"&a { p = <1>; }; &b { q = <2>; };")
        first, second = document.find_node("&a"), document.find_node("&b")
        with self.assertRaises(SourceError):
            document.plan_set_property(first.id, "p", PropertyValue.cells((3,)), property_id=second.properties[0].id)
        result = document.plan_set_property(first.id, "p", PropertyValue.cells((3,))).apply(document)
        with self.assertRaises(SourceError):
            result.node(first.id)
        with self.assertRaises(SourceError):
            result.plan_remove_property(first.properties[0].id)

    def test_remove_local_property_is_distinct_from_explicit_inherited_property_deletion(self):
        source = b'&uart0 {\n /* keep leading */\n status = "disabled"; // keep trailing\n};'
        document = self.document(source)
        prop = document.find_property(document.find_node("&uart0").id, "status")
        removed = self.apply_reversible(document, document.plan_remove_property(prop.id))
        self.assertEqual(removed.source, source[:prop.start] + source[prop.end:])
        self.assertNotIn(b"/delete-property/", removed.source)
        deleted = self.apply_reversible(removed, removed.plan_delete_property(removed.find_node("&uart0").id, "status"))
        self.assertIn(b"/delete-property/ status;", deleted.source)
        self.assertEqual(deleted.plan_delete_property(deleted.find_node("&uart0").id, "status").edits, ())

    def test_add_and_remove_nested_node_preserve_parent_and_unrelated_devices(self):
        source = b'&i2c0 {\n status = "okay";\n existing@1 { reg = <1>; };\n};\n'
        document = self.document(source)
        parent = document.find_node("&i2c0")
        added = self.apply_reversible(document, document.plan_add_node(parent.id, "sensor@48", label="sensor0",
            properties=(("compatible", PropertyValue.string("vendor,sensor")), ("reg", PropertyValue.cells((0x48,))))))
        self.assertIn(b'existing@1 { reg = <1>; };', added.source)
        self.assertIn(b'status = "okay";', added.source)
        sensor = added.find_node("&sensor0")
        self.assertEqual(sensor.parent_id, added.find_node("&i2c0").id)
        removed = self.apply_reversible(added, added.plan_remove_node(sensor.id))
        self.assertNotIn(b"sensor@48", removed.source)
        self.assertIn(b'existing@1 { reg = <1>; };', removed.source)
        self.assertNotIn(b"/delete-node/", removed.source)

    def test_empty_overlay_can_create_its_first_override_or_root(self):
        for name in ("&uart0", "/", "&{/soc/serial@1000}"):
            with self.subTest(name=name):
                document = self.document(b"/* handwritten header */\r\n")
                added = self.apply_reversible(document, document.plan_add_node(None, name,
                    properties=(("status", PropertyValue.string("okay")),)))
                self.assertTrue(added.source.startswith(document.source))
                selected = added.find_node(name)
                self.assertEqual(selected.name, name)
                self.assertIn(b'status = "okay";', added.source)
                no_op = added.plan_add_node(None, name, properties=(("status", PropertyValue.string("okay")),))
                self.assertEqual(no_op.edits, ())

    def test_repeated_identical_child_creation_is_a_no_op(self):
        document = self.document(b"&i2c0 {};")
        properties = (("compatible", PropertyValue.string("vendor,sensor")), ("reg", PropertyValue.cells((72,))))
        added = document.plan_add_node(document.find_node("&i2c0").id, "sensor@48",
                                       label="sensor0", properties=properties).apply(document)
        no_op = added.plan_add_node(added.find_node("&i2c0").id, "sensor@48", label="sensor0", properties=properties)
        self.assertEqual(no_op.edits, ())
        self.assertEqual(self.apply_reversible(added, no_op).source, added.source)

    def test_conflicting_duplicate_child_names_and_labels_are_not_created(self):
        document = self.document(b"/ { label0: first { value = <1>; }; second {}; };")
        parent = document.find_node("/")
        for name, label in (("first", None), ("third", "label0")):
            with self.subTest(name=name, label=label), self.assertRaises(SourceError):
                document.plan_add_node(parent.id, name, label=label,
                                       properties=(("value", PropertyValue.cells((2,))),))

    def test_known_references_prevent_removing_a_referenced_node(self):
        for source in (b"/ { target: device {}; user { link = <&target>; }; };",
                       b"/ { device {}; user { link = <&{/device}>; }; };"):
            with self.subTest(source=source):
                document = self.document(source)
                with self.assertRaises(SourceError):
                    document.plan_remove_node(document.find_node("/device").id)

    def test_remove_override_and_emit_node_deletion_are_distinct(self):
        source = b'&uart0 { status = "disabled"; };\n&i2c0 { status = "okay"; };\n'
        document = self.document(source)
        selected = document.find_node("&uart0")
        removed = self.apply_reversible(document, document.plan_remove_node(selected.id))
        self.assertEqual(removed.source, source[:selected.start] + source[selected.end:])
        self.assertIn(b'&i2c0 { status = "okay"; };', removed.source)
        deleted = self.apply_reversible(removed, removed.plan_delete_reference("&uart0"))
        self.assertIn(b"/delete-node/ &uart0;", deleted.source)
        self.assertEqual(deleted.plan_delete_reference("&uart0").edits, ())

    def test_nested_delete_directive_is_reversible_and_keeps_unrelated_properties_editable(self):
        document = self.document(b'&i2c0 { status = "okay"; };')
        deleted = self.apply_reversible(document, document.plan_delete_node(document.find_node("&i2c0").id, "old@10"))
        self.assertIn(b"/delete-node/ old@10;", deleted.source)
        self.assertEqual(deleted.plan_delete_node(deleted.find_node("&i2c0").id, "old@10").edits, ())
        edited = self.apply_reversible(deleted, deleted.plan_set_status(deleted.find_node("&i2c0").id, "disabled"))
        self.assertIn(b"/delete-node/ old@10;", edited.source)

    def test_deletion_conflicts_are_not_silently_replaced_by_assignment_or_child_creation(self):
        document = self.document(b"&i2c0 { /delete-property/ status; /delete-node/ sensor@48; };")
        node = document.find_node("&i2c0")
        with self.assertRaises(SourceError):
            document.plan_set_status(node.id, "okay")
        with self.assertRaises(SourceError):
            document.plan_add_node(node.id, "sensor@48")
        existing = self.document(b"&i2c0 { rate = <1>; sensor@48 {}; };")
        with self.assertRaises(SourceError):
            existing.plan_delete_property(existing.find_node("&i2c0").id, "rate")
        with self.assertRaises(SourceError):
            existing.plan_delete_node(existing.find_node("&i2c0").id, "sensor@48")

    def test_opaque_values_are_preserved_and_cannot_be_overwritten_as_literals(self):
        for value in (b"<BUS_RATE>", b"<(1 + 2)>", b"<(1 << 2)>", b"<MACRO('}')>", b'"a", "b"'):
            source = b'&uart0 { status = "disabled"; opaque = ' + value + b"; };"
            with self.subTest(value=value):
                document = self.document(source)
                changed = self.apply_reversible(document, document.plan_set_status(document.find_node("&uart0").id, "okay"))
                self.assertIn(b"opaque = " + value, changed.source)
                with self.assertRaises(SourceError):
                    document.plan_set_property(document.find_node("&uart0").id, "opaque", PropertyValue.cells((1,)))

    def test_incomplete_conditional_and_spliced_documents_cannot_plan_changes(self):
        sources = [self.fixture("conditional.overlay"), self.fixture("malformed.overlay"),
                   b'// continued \\\n&uart0 { status = "disabled"; };',
                   b'\xef\xbb\xbf&uart0 { status = "disabled"; };']
        for source in sources:
            with self.subTest(source=source):
                document = self.document(source)
                self.assertTrue(document.syntax.blocked)
                self.assertEqual(document.source, source)
                with self.assertRaises(SourceError):
                    document.plan_delete_reference("&uart0")

    def test_patch_identity_revision_hash_range_and_result_guards(self):
        document = self.document(b'&uart0 { status = "disabled"; };', revision=3)
        change = document.plan_set_status(document.find_node("&uart0").id, "okay")
        wrong_documents = [SourceDocument.parse(document.source, document_id="other", revision=3),
                           self.document(document.source, revision=4),
                           self.document(document.source + b" // changed", revision=3)]
        for wrong in wrong_documents:
            with self.subTest(document=wrong), self.assertRaises(SourceError):
                change.apply(wrong)
        edits = change.edits
        corrupted = [replace(change, after_sha256="0" * 64),
                     replace(change, edits=(replace(edits[0], start=-1),)),
                     replace(change, edits=(replace(edits[0], before=b"wrong"),)),
                     replace(change, edits=(replace(edits[0], after=b'"other"'),)),
                     replace(change, edits=edits + edits)]
        for invalid in corrupted:
            with self.subTest(change=invalid), self.assertRaises(SourceError):
                invalid.apply(document)
        edited = change.apply(document)
        with self.assertRaises(SourceError):
            change.apply(edited)
        with self.assertRaises(SourceError):
            change.inverse().apply(self.document(edited.source + b" // external", revision=edited.revision))

    def test_invalid_names_targets_values_and_document_revisions_are_rejected(self):
        document = self.document(b"&uart0 {};")
        node = document.find_node("&uart0")
        for name in ("", "p; evil", "a/b", "ü", None):
            with self.subTest(name=name), self.assertRaises(SourceError):
                document.plan_set_property(node.id, name, PropertyValue.boolean())
        for reference in ("uart0", "&uart0; /delete-node/ &other", "&ü", None):
            with self.subTest(reference=reference), self.assertRaises(SourceError):
                document.plan_delete_reference(reference)
        for values in ((-1,), (0x100000000,), (True,), (), ("1",)):
            with self.subTest(values=values), self.assertRaises((SourceError, ValueError)):
                value = PropertyValue.cells(values)
                document.plan_set_property(node.id, "bad", value)
        for revision in (-1, True, "1"):
            with self.subTest(revision=revision), self.assertRaises(SourceError):
                self.document(b"", revision=revision)
        for status in ("enabled", "invalid", 1):
            with self.subTest(status=status), self.assertRaises(SourceError):
                document.plan_set_status(node.id, status)
        with self.assertRaises(SourceError):
            document.plan_add_node(None, "/", label="invalid_root_label")

    def test_new_node_labels_cannot_duplicate_labels_inside_opaque_values(self):
        document = self.document(b"/ { data = <label0: 1>; };")
        with self.assertRaises(SourceError):
            document.plan_add_node(document.find_node("/").id, "child", label="label0")

    def test_source_operations_have_no_filesystem_or_process_side_effects(self):
        document = self.document(b'&uart0 { status = "disabled"; };')
        with patch("builtins.open", side_effect=AssertionError("No source file writes")), \
                patch("subprocess.run", side_effect=AssertionError("No external tools")), \
                patch.object(Path, "write_bytes", side_effect=AssertionError("No source file writes")), \
                patch.object(Path, "write_text", side_effect=AssertionError("No source file writes")):
            edited = self.apply_reversible(document, document.plan_set_status(document.find_node("&uart0").id, "okay"))
        self.assertIn(b'"okay"', edited.source)

    def test_visual_literal_projection_preserves_unknown_and_unsupported_values(self):
        document = self.document(b'&uart0 { flag; rate = <1 2>; label = "ready"; opaque = <MACRO(1)>; escape = "\\a"; };')
        node = document.find_node("&uart0")
        values = {prop.name: document.property_value(prop.id) for prop in node.properties}
        self.assertEqual(values["flag"], PropertyValue.boolean())
        self.assertEqual(values["rate"], PropertyValue.cells((1, 2)))
        self.assertEqual(values["label"], PropertyValue.string("ready"))
        self.assertIsNone(values["opaque"])
        self.assertIsNone(values["escape"])
        self.assertTrue(document.can_edit_node(node.id))
        deleted = self.document(b'&uart0 { status = "okay"; }; /delete-node/ &uart0;')
        self.assertFalse(deleted.can_edit_node(deleted.find_node("&uart0").id))
