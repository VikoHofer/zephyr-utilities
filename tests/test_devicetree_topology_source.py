"""D7 atomic field/address updates preserve local source and reference dependencies."""

import unittest

from zephyr_utilities.features.devicetree.source import PropertyValue, SourceDocument
from zephyr_utilities.features.devicetree.source_syntax import SourceError


class DevicetreeTopologySourceTests(unittest.TestCase):
    def document(self, source):
        return SourceDocument.parse(source.encode() if isinstance(source, str) else source, document_id="d7-topology", revision=4)

    def apply(self, document, change):
        result = change.apply(document)
        self.assertEqual(result.id, document.id)
        self.assertEqual(result.revision, document.revision + bool(change.edits))
        self.assertEqual(change.inverse().apply(result).source, document.source)
        return result

    def test_address_and_fields_update_the_existing_node_in_one_revision(self):
        source = b'''/ {
    bus {
        sensor: device@28 {
            reg = <0x28>; /* address note */
            status = "disabled";
            unknown = <MACRO(1)>;
        };
        peer: other@30 { reg = <0x30>; };
    };
    aliases { sensor0 = &sensor; };
};
'''
        document = self.document(source)
        change = document.plan_update_node(document.find_node("&sensor").id, unit_address=0x29,
            properties=(("status", PropertyValue.string("okay")), ("sample-rate", PropertyValue.cells([20]))))
        changed = self.apply(document, change)
        self.assertEqual(len(changed.syntax.nodes), len(document.syntax.nodes))
        self.assertIn(b"sensor: device@29", changed.source)
        self.assertIn(b"reg = <0x29>; /* address note */", changed.source)
        self.assertIn(b'status = "okay";', changed.source)
        self.assertIn(b"sample-rate = <20>;", changed.source)
        self.assertIn(b"unknown = <MACRO(1)>;", changed.source)
        self.assertIn(b"peer: other@30 { reg = <0x30>; };", changed.source)
        self.assertIn(b"aliases { sensor0 = &sensor; };", changed.source)
        self.assertEqual(changed.find_node("&sensor").labels, ("sensor",))
        self.assertNotIn(b"device@28", changed.source)
        self.assertEqual(document.source, source)

    def test_repeated_updates_reuse_the_node_and_equal_values_are_noops(self):
        document = self.document('/ { bus { child: device@1 { reg = <1>; }; }; };')
        for address in (2, 3, 4):
            document = self.apply(document, document.plan_update_node(document.find_node("&child").id, unit_address=address))
            self.assertEqual(len(document.syntax.nodes), 3)
            self.assertEqual(document.find_node("&child").name, f"device@{address:x}")
        change = document.plan_update_node(document.find_node("&child").id, unit_address=4)
        self.assertEqual(change.edits, ())
        self.apply(document, change)
        self.assertEqual(document.plan_update_node(document.find_node("&child").id).edits, ())

    def test_header_labels_and_comments_survive_and_new_address_is_lowercase_hex(self):
        document = self.document('/ { bus { first: /* keep */ second: vendor,device@1 /* header */ { reg = <0X1>; }; }; };')
        changed = self.apply(document, document.plan_update_node(document.find_node("&first").id, unit_address=0xab))
        self.assertIn(b"first: /* keep */ second: vendor,device@ab /* header */", changed.source)
        self.assertIn(b"reg = <0XAB>;", changed.source)
        self.assertEqual(changed.find_node("&second").labels, ("first", "second"))

    def test_absent_reg_and_multiple_properties_are_inserted_as_one_group(self):
        source = b'/ {\r\n\tbus {\r\n\t\tchild: sensor {\r\n\t\t\t/* keep child note */\r\n\t\t\tpart {};\r\n\t\t};\r\n\t};\r\n};\r\n'
        document = self.document(source)
        changed = self.apply(document, document.plan_update_node(document.find_node("&child").id, unit_address=10,
            properties=(("compatible", PropertyValue.string("test,sensor")), ("enabled", PropertyValue.boolean()))))
        self.assertIn(b"child: sensor@a", changed.source)
        self.assertIn(b'\t\t\tcompatible = "test,sensor";\r\n\t\t\tenabled;\r\n\t\t\treg = <10>;\r\n', changed.source)
        self.assertIn(b"\t\t\t/* keep child note */\r\n", changed.source)
        self.assertNotIn(b"\n", changed.source.replace(b"\r\n", b""))
        node = changed.find_node("&child")
        self.assertLess(changed.find_property(node.id, "reg").end, changed.node(node.children[0]).start)

    def test_insertions_precede_deletions_without_reordering_surrounding_source(self):
        document = self.document('&uart0 { /* before */ old = <1>; /delete-node/ unused; child {}; };')
        changed = self.apply(document, document.plan_update_node(document.find_node("&uart0").id,
            properties=(("speed", PropertyValue.cells([9600])), ("flow", PropertyValue.boolean())), remove_properties=("old",)))
        self.assertIn(b"/* before */", changed.source)
        self.assertNotIn(b"old =", changed.source)
        self.assertLess(changed.source.index(b"speed ="), changed.source.index(b"/delete-node/ unused"))
        self.assertLess(changed.source.index(b"flow;"), changed.source.index(b"/delete-node/ unused"))
        self.assertIn(b"/delete-node/ unused; child {};", changed.source)

    def test_boolean_removal_and_field_changes_are_atomic_and_preserve_neighbors(self):
        document = self.document('&uart0 { hw-flow-control; /* retain */ current-speed = <9600>; /delete-property/ parity; };')
        changed = self.apply(document, document.plan_update_node(document.find_node("&uart0").id,
            properties=(("current-speed", PropertyValue.cells([115200])),),
            remove_properties=("hw-flow-control", "parity", "absent")))
        self.assertNotIn(b"hw-flow-control", changed.source)
        self.assertNotIn(b"/delete-property/ parity", changed.source)
        self.assertIn(b"/* retain */ current-speed = <115200>;", changed.source)

    def test_failed_field_validation_cannot_partially_change_address_or_other_fields(self):
        source = b'/ { child: device@1 { reg = <1>; mode = <MACRO(2)>; }; };'
        document = self.document(source)
        with self.assertRaisesRegex(SourceError, "Opaque"):
            document.plan_update_node(document.find_node("&child").id, unit_address=2,
                properties=(("new", PropertyValue.boolean()), ("mode", PropertyValue.cells([3]))))
        self.assertEqual(document.source, source)
        self.assertEqual(document.revision, 4)

    def test_opaque_escapes_and_comment_losing_shape_changes_are_refused(self):
        for value in (b'"\\q"', b'<1 /* keep */ 2>'):
            document = self.document(b'/ { child { x = ' + value + b'; }; };')
            with self.subTest(value=value), self.assertRaises(SourceError):
                document.plan_update_node(document.find_node("/child").id, properties=(("x", PropertyValue.cells([3])),))

    def test_invalid_inputs_duplicate_fields_and_reg_ownership_are_rejected(self):
        document = self.document('/ { child { reg = <1>; }; };')
        node = document.find_node("/child")
        invalid = [dict(unit_address=value) for value in (-1, 0x100000000, True, "2", [], {})]
        invalid.extend((dict(properties={"x": PropertyValue.boolean()}), dict(properties=(("x", "raw"),)),
                        dict(properties=(("bad; source", PropertyValue.boolean()),)), dict(remove_properties="x"),
                        dict(remove_properties=(None,)), dict(properties=(("x", PropertyValue.boolean()),) * 2),
                        dict(remove_properties=("x", "x")),
                        dict(properties=(("x", PropertyValue.boolean()),), remove_properties=("x",)),
                        dict(unit_address=2, properties=(("reg", PropertyValue.cells([2])),)),
                        dict(unit_address=2, remove_properties=("reg",)),
                        dict(properties=tuple((f"p{index}", PropertyValue.boolean()) for index in range(129)))))
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(SourceError):
                document.plan_update_node(node.id, **fields)

    def test_reg_must_be_a_unique_literal_single_cell(self):
        for declarations in ('reg = <1 2>;', 'reg = <MACRO(1)>;', 'reg;', 'reg = <1>; reg = <1>;', '/delete-property/ reg;'):
            document = self.document('/ { child: device@1 { ' + declarations + ' }; };')
            with self.subTest(declarations=declarations), self.assertRaisesRegex(SourceError, "single-cell"):
                document.plan_update_node(document.find_node("&child").id, unit_address=2)

    def test_only_local_child_headers_can_receive_unit_addresses(self):
        for source, selector in (('/ {};', '/'), ('&bus {};', '&bus'), ('&{/bus} {};', '&{/bus}')):
            document = self.document(source)
            with self.subTest(source=source), self.assertRaisesRegex(SourceError, "child"):
                document.plan_update_node(document.find_node(selector).id, unit_address=1)
        document = self.document('&external { child: device@1 { reg = <1>; }; };')
        changed = self.apply(document, document.plan_update_node(document.find_node("&child").id, unit_address=2))
        self.assertIn(b"device@2", changed.source)

    def test_child_name_collisions_and_proposed_name_deletions_are_refused(self):
        sources = ('/ { child: device@1 { reg = <1>; }; device@2 {}; };',
                   '/ { child: device@1 { reg = <1>; }; device@1 {}; };',
                   '/ { child: device@1 { reg = <1>; }; /delete-node/ device@2; };',
                   '/ { child: device@1 { reg = <1>; }; }; /delete-node/ &{/device@2};',
                   '/ { child: device@1 { reg = <1>; }; }; / { device@2 {}; };')
        for source in sources:
            document = self.document(source)
            with self.subTest(source=source), self.assertRaises(SourceError):
                document.plan_update_node(document.find_node("&child").id, unit_address=2)

    def test_known_node_and_subtree_path_consumers_prevent_address_changes(self):
        for consumer in ('other { ref = <&{/device@1}>; };',
                         'other { ref = <&{/device@1/part}>; };',
                         'aliases { sensor0 = "/device@1"; };',
                         'chosen { thing = "/device@1/part"; };'):
            document = self.document('/ { child: device@1 { reg = <1>; part {}; }; ' + consumer + ' };')
            with self.subTest(consumer=consumer), self.assertRaisesRegex(SourceError, "path"):
                document.plan_update_node(document.find_node("&child").id, unit_address=2)
            changed = self.apply(document, document.plan_update_node(document.find_node("&child").id,
                properties=(("status", PropertyValue.string("okay")),)))
            self.assertIn(consumer.encode(), changed.source)
        document = self.document('/ { child: device@1 { reg = <1>; self = <&{/device@1}>; }; };')
        with self.assertRaisesRegex(SourceError, "path"):
            document.plan_update_node(document.find_node("&child").id, unit_address=2)

    def test_unrelated_paths_do_not_block_and_same_address_keeps_path_consumers(self):
        document = self.document('/ { child: device@1 { reg = <1>; }; other { ref = <&{/device@10}>; }; };')
        changed = self.apply(document, document.plan_update_node(document.find_node("&child").id, unit_address=2))
        self.assertIn(b"ref = <&{/device@10}>;", changed.source)
        document = self.document('/ { child: device@1 { reg = <1>; }; aliases { x = &{/device@1}; }; };')
        self.assertEqual(document.plan_update_node(document.find_node("&child").id, unit_address=1).edits, ())

    def test_referenced_property_and_value_labels_cannot_be_removed_by_batch(self):
        for declaration in ('value: data = <1>;', 'data = <value: 1>;'):
            document = self.document('/ { consumer = <&value>; child { ' + declaration + ' }; };')
            with self.subTest(declaration=declaration), self.assertRaisesRegex(SourceError, "label"):
                document.plan_update_node(document.find_node("/child").id, remove_properties=("data",))

    def test_repeated_properties_and_node_deletion_conflicts_block_batches(self):
        for source in ('/ { child: device { x = <1>; x = <2>; }; };',
                       '/ { child: device { /delete-property/ x; }; };',
                       '/ { child: device { x = <1>; }; }; /delete-node/ &child;'):
            document = self.document(source)
            with self.subTest(source=source), self.assertRaises(SourceError):
                document.plan_update_node(document.find_node("&child").id, properties=(("x", PropertyValue.cells([3])),))

    def test_add_two_devices_and_remove_one_preserves_controller_peer_and_consumers(self):
        document = self.document('/ { bus { #address-cells = <1>; #size-cells = <0>; }; };')
        for address in (1, 2):
            document = self.apply(document, document.plan_add_node(document.find_node("/bus").id,
                f"sensor@{address}", label=f"sensor{address}", properties=(("reg", PropertyValue.cells([address])),)))
        changed = self.apply(document, document.plan_remove_node(document.find_node("&sensor1").id))
        self.assertIn(b"#address-cells = <1>; #size-cells = <0>;", changed.source)
        self.assertEqual(changed.find_node("&sensor2").name, "sensor@2")
        protected = self.apply(document, document.plan_set_named_reference("aliases", "sensor0", "&sensor1"))
        with self.assertRaisesRegex(SourceError, "references"):
            protected.plan_remove_node(protected.find_node("&sensor1").id)

    def test_batch_rejects_stale_node_ids_and_stale_changes(self):
        document = self.document('/ { child { value = <1>; }; };')
        node = document.find_node("/child")
        change = document.plan_update_node(node.id, properties=(("value", PropertyValue.cells([2])),))
        changed = self.apply(document, change)
        with self.assertRaisesRegex(SourceError, "stale"):
            changed.plan_update_node(node.id, properties=(("value", PropertyValue.cells([3])),))
        with self.assertRaisesRegex(SourceError, "Stale"):
            change.apply(changed)
