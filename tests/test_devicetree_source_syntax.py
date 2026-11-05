"""Source occurrence indexing preserves bytes and avoids interpreting macros/includes."""

from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree import source_syntax as syntax

FIXTURES = Path(__file__).parent / "fixtures" / "devicetree_editor"


class SourceSyntaxTests(unittest.TestCase):
    def assert_coverage(self, source):
        parsed = syntax.parse(source)
        self.assertEqual(b"".join(source[token.start:token.end] for token in parsed.tokens), source)
        if source:
            self.assertEqual(parsed.tokens[0].start, 0)
            self.assertEqual(parsed.tokens[-1].end, len(source))
            self.assertTrue(all(left.end == right.start for left, right in zip(parsed.tokens, parsed.tokens[1:])))
        return parsed

    def test_entire_prototype_corpus_has_lossless_token_coverage(self):
        for path in sorted(FIXTURES.glob("*.overlay")):
            with self.subTest(path=path.name):
                self.assert_coverage(path.read_bytes())
        self.assertEqual(syntax.parse(b"").state, "complete")

    def test_nested_nodes_labels_vendor_properties_and_boolean_ranges(self):
        source = b'/* before */ / { leds { a: b: led@0 { vendor,name = "x"; #address-cells = <1>; ready; }; }; }; // after'
        parsed = self.assert_coverage(source)
        self.assertEqual((parsed.state, parsed.blocked), ("complete", False))
        self.assertEqual([node.name for node in parsed.nodes], ["/", "leds", "led@0"])
        root, group, child = parsed.nodes
        self.assertEqual(root.children, (group.id,))
        self.assertEqual(group.children, (child.id,))
        self.assertEqual(child.parent_id, group.id)
        self.assertEqual(child.labels, ("a", "b"))
        self.assertEqual(source[child.start:child.end], b'a: b: led@0 { vendor,name = "x"; #address-cells = <1>; ready; };')
        self.assertEqual(source[child.open_brace:child.open_brace + 1], b"{")
        self.assertEqual(source[child.close_brace:child.close_brace + 1], b"}")
        self.assertEqual([prop.name for prop in child.properties], ["vendor,name", "#address-cells", "ready"])
        self.assertIsNone(child.properties[-1].value_start)
        self.assertIsNone(child.properties[-1].value_end)
        prop = child.properties[0]
        self.assertEqual(source[prop.start:prop.name_end], b"vendor,name")
        self.assertEqual(source[prop.value_start:prop.value_end], b'"x"')

    def test_reference_headers_and_repeated_contributors_have_distinct_occurrence_ids(self):
        source = b'first: &uart0 { status = "a"; }; &uart0 { status = "b"; }; &{/soc/serial@1000} { status = "c"; };'
        parsed = syntax.parse(source)
        self.assertFalse(parsed.blocked)
        self.assertEqual([node.name for node in parsed.nodes], ["&uart0", "&uart0", "&{/soc/serial@1000}"])
        self.assertEqual(parsed.nodes[0].labels, ("first",))
        ids = [node.id for node in parsed.nodes]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(syntax.parse(source), parsed)
        self.assertNotEqual(syntax.parse(source + b" ").nodes[0].id, ids[0])

    def test_root_node_labels_are_incomplete_and_block_editing(self):
        parsed = self.assert_coverage(b'/dts-v1/; rootlabel: / {};')
        self.assertEqual(parsed.state, "incomplete")
        self.assertTrue(parsed.blocked)
        self.assertEqual(parsed.nodes, ())
        self.assertIn("root node", parsed.diagnostics[-1].message)

    def test_duplicate_properties_and_labels_are_not_collapsed(self):
        parsed = syntax.parse(b'&uart0 { x: speed = <1>; x: speed = <2>; };')
        self.assertFalse(parsed.blocked)
        properties = parsed.nodes[0].properties
        self.assertEqual([prop.name for prop in properties], ["speed", "speed"])
        self.assertEqual([prop.labels for prop in properties], [("x",), ("x",)])
        self.assertNotEqual(properties[0].id, properties[1].id)

    def test_comments_strings_and_opaque_values_do_not_change_structure(self):
        source = b'&uart0 { /* } ; */ status = "disabled"; label = "brace } ; // and \\""; pins = <MACRO(\'}\', (1 << 2))>; };'
        parsed = self.assert_coverage(source)
        self.assertFalse(parsed.blocked)
        self.assertEqual(parsed.state, "opaque")
        self.assertEqual([prop.name for prop in parsed.nodes[0].properties], ["status", "label", "pins"])
        self.assertIn("opaque-value", {item.code for item in parsed.diagnostics})

    def test_property_ranges_keep_adjacent_and_internal_comments(self):
        source = b'&a { /* preceding */ p: value /* after name */ = /* before */ <1 /* inside */ 2> /* after */; // following\n};'
        parsed = syntax.parse(source)
        prop = parsed.nodes[0].properties[0]
        self.assertEqual(source[prop.start:prop.name_end], b"p: value")
        self.assertEqual(source[prop.value_start:prop.value_end], b"<1 /* inside */ 2>")
        self.assertTrue(source[prop.start:prop.end].endswith(b"/* after */;"))
        self.assertNotIn(b"preceding", source[prop.start:prop.end])
        self.assertNotIn(b"following", source[prop.start:prop.end])

    def test_includes_and_preprocessor_definitions_are_opaque_without_loading_files(self):
        source = b'#include "missing.h"\n#define SHAPE { \\\n status = "x"; }\n/include/ "missing.dtsi"\n&uart0 { status = "disabled"; };'
        with patch("builtins.open", side_effect=AssertionError("No files")), \
                patch("subprocess.run", side_effect=AssertionError("No tools")):
            parsed = self.assert_coverage(source)
        self.assertFalse(parsed.blocked)
        self.assertEqual(parsed.state, "opaque")
        self.assertEqual(len(parsed.nodes), 1)
        self.assertEqual({item.code for item in parsed.diagnostics}, {"preprocessor", "include"})

    def test_conditional_directives_after_comments_are_globally_blocked(self):
        for prefix in (b"", b"/* prefix */ ", b"/* multi\nline */ "):
            source = prefix + b'#if ENABLE\n&a { status = "okay"; };\n#else\n&a { status = "disabled"; };\n#endif\n'
            with self.subTest(prefix=prefix):
                parsed = self.assert_coverage(source)
                self.assertTrue(parsed.blocked)
                self.assertEqual(parsed.state, "opaque")
                self.assertIn("conditional", {item.code for item in parsed.diagnostics})

    def test_physical_line_splicing_is_preserved_but_blocks_edits(self):
        for source in (b'// continued \\\n&a { x = <1>; };',
                       b'&a { text = "a\\\r\nb"; };',
                       b'&a {\n#\\\nif FLAG\nx = <1>;\n#endif\n};'):
            with self.subTest(source=source):
                parsed = self.assert_coverage(source)
                self.assertTrue(parsed.blocked)
                self.assertIn("line-splice", {item.code for item in parsed.diagnostics})

    def test_bom_and_mixed_newlines_are_not_normalized(self):
        source = b'\xef\xbb\xbf/* comment */\r\n&a {\n x = <1>;\r\n};'
        parsed = self.assert_coverage(source)
        self.assertTrue(parsed.blocked)
        self.assertEqual(parsed.state, "opaque")
        self.assertEqual(parsed.tokens[0].kind, "bom")

    def test_incomplete_source_retains_exact_bytes_and_never_admits_edits(self):
        for source in (b'&a { status = "unfinished; };', b"/* unfinished", b'"escape\\',
                       b"&a { x = <1>;", b"&a { x = <1> };", b"&a { x =; };",
                       b"&a { x = <1]; };", b";", b"&a { child { }; };"):
            # The last input is complete and acts as a control for the missing-close cases.
            with self.subTest(source=source):
                parsed = self.assert_coverage(source)
                if source == b"&a { child { }; };":
                    self.assertFalse(parsed.blocked)
                    continue
                self.assertTrue(parsed.blocked)
                self.assertEqual(parsed.state, "incomplete")
                self.assertIn("incomplete", {item.code for item in parsed.diagnostics})

    def test_unknown_balanced_structure_is_opaque_not_claimed_invalid(self):
        for source in (b'&a { EXPAND_NODE(x); status = "okay"; };', b'UNKNOWN { x = <1>; };',
                       b'&a { x = <1> child {}; };'):
            parsed = self.assert_coverage(source)
            self.assertTrue(parsed.blocked)
            self.assertEqual(parsed.state, "opaque")
            self.assertIn("opaque-structure", {item.code for item in parsed.diagnostics})

    def test_missing_property_terminator_cannot_hide_another_assignment(self):
        parsed = self.assert_coverage(b'&a { speed = <1> status = "okay"; };')
        self.assertEqual(parsed.state, "incomplete")
        self.assertTrue(parsed.blocked)

    def test_incomplete_parent_does_not_publish_orphan_child_occurrences(self):
        parsed = self.assert_coverage(b'&a { child {}; missing-terminator };')
        self.assertEqual(parsed.state, "incomplete")
        self.assertEqual(parsed.nodes, ())
        self.assertEqual(parsed.deletions, ())

    def test_delete_operations_are_distinct_from_declarations(self):
        source = b'/delete-node/ &old; /delete-node/ &{/soc/old@0}; &a { /delete-property/ status; /delete-node/ child@0; child@1 {}; };'
        parsed = self.assert_coverage(source)
        self.assertFalse(parsed.blocked)
        self.assertEqual(parsed.state, "opaque")
        self.assertEqual([item.target for item in parsed.deletions], ["&old", "&{/soc/old@0}", "child@0"])
        self.assertIsNone(parsed.deletions[0].parent_id)
        self.assertEqual(parsed.deletions[-1].parent_id, parsed.nodes[0].id)
        prop = parsed.nodes[0].properties[0]
        self.assertEqual((prop.kind, prop.name), ("delete-property", "status"))
        self.assertEqual(len(parsed.nodes), 2)
        self.assertEqual(source[prop.start:prop.end], b"/delete-property/ status;")

    def test_properties_and_property_deletions_must_precede_subnodes(self):
        for source in (b'&a { child {}; later = <1>; };',
                       b'&a { /delete-node/ child; later; };',
                       b'&a { child {}; /delete-property/ later; };',
                       b'&a { /delete-node/ child; /delete-property/ later; };'):
            with self.subTest(source=source):
                parsed = self.assert_coverage(source)
                self.assertEqual(parsed.state, "incomplete")
                self.assertTrue(parsed.blocked)
                self.assertEqual(parsed.nodes, ())
                self.assertIn("precede child", parsed.diagnostics[-1].message)

    def test_dynamic_deletion_targets_are_preserved_and_blocked(self):
        for source in (b"/delete-node/ NODE_NAME;", b"&a { /delete-node/ NAME(0); };", b"/delete-property/ name;"):
            parsed = self.assert_coverage(source)
            self.assertTrue(parsed.blocked)
            self.assertEqual(parsed.state, "opaque")

    def test_byte_offsets_follow_utf8_not_character_count(self):
        source = '/* 温度 */\n&a { value = "日本"; };'.encode()
        parsed = syntax.parse(source)
        prop = parsed.nodes[0].properties[0]
        self.assertEqual(prop.start, source.index(b"value"))
        self.assertEqual(source[prop.value_start:prop.value_end], '"日本"'.encode())

    def test_invalid_source_and_resource_limits_raise_without_constructing_graph(self):
        for source in (b"\xff", b"// nul\x00", "not bytes", bytearray(b"")):
            with self.subTest(source=source), self.assertRaises(syntax.SourceError):
                syntax.parse(source)
        source = b"&a { child { x = <1>; }; };"
        for name, value in (("MAX_BYTES", 10), ("MAX_TOKENS", 3), ("MAX_DEPTH", 1)):
            with self.subTest(limit=name), patch.object(syntax, name, value):
                with self.assertRaisesRegex(syntax.SourceError, "limit"):
                    syntax.parse(source)

    def test_returned_model_is_immutable(self):
        parsed = syntax.parse(b"&a { x = <1>; };")
        with self.assertRaises(FrozenInstanceError):
            parsed.nodes[0].name = "changed"
        self.assertIsInstance(parsed.nodes[0].properties, tuple)
