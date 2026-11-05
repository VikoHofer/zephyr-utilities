"""D0 feasibility evidence, using synthetic bytes and no runtime editor registration."""

from dataclasses import replace
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prototypes" / "devicetree_source.py"
SPEC = importlib.util.spec_from_file_location("devicetree_source_prototype", SCRIPT)
prototype = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prototype
SPEC.loader.exec_module(prototype)
FIXTURES = Path(__file__).parent / "fixtures" / "devicetree_editor"


class SourcePrototypeTests(unittest.TestCase):
    def fixture(self, name):
        return (FIXTURES / name).read_bytes()

    def test_token_round_trip_preserves_every_fixture_byte_including_opaque_syntax(self):
        for path in sorted(FIXTURES.glob("*.overlay")):
            with self.subTest(fixture=path.name):
                source = path.read_bytes()
                tokens = prototype.lex(source)
                self.assertEqual(prototype.round_trip(source), source)
                self.assertEqual(tokens[0].start, 0)
                self.assertEqual(tokens[-1].end, len(source))
                self.assertTrue(all(a.end == b.start for a, b in zip(tokens, tokens[1:])))

    def test_literal_edit_preserves_unrelated_bytes_and_nested_same_named_property(self):
        source = self.fixture("literals.overlay")
        edit = prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')
        expected = source.replace(b'= "disabled" /* keep after */', b'= "okay" /* keep after */', 1)
        result = edit.apply(source)
        self.assertEqual(result, expected)
        self.assertIn(b'child: device@0 {\n        status = "disabled";', result)
        self.assertEqual(edit.inverse().apply(result), source)
        self.assertEqual(prototype.round_trip(result), result)

    def test_integer_edit_preserves_brackets_comments_and_spacing(self):
        source = self.fixture("literals.overlay")
        edit = prototype.plan_literal_replacement(source, "&uart0", "current-speed", b"<115200>")
        result = edit.apply(source)
        self.assertEqual(result, source.replace(b"/* speed */ 9600", b"/* speed */ 115200"))
        self.assertEqual(edit.inverse().apply(result), source)

    def test_path_reference_and_unicode_byte_offsets(self):
        source = "/* 温度 */\n".encode() + self.fixture("path-reference.overlay")
        edit = prototype.plan_literal_replacement(source, "&{/soc/serial@1000}", "status", b'"okay"')
        self.assertEqual(edit.apply(source), source.replace(b'"disabled"', b'"okay"'))
        self.assertEqual(edit.inverse().apply(edit.apply(source)), source)

    def test_crlf_and_no_final_newline_remain_exact(self):
        source = self.fixture("literals.overlay").rstrip(b"\n").replace(b"\n", b"\r\n")
        edit = prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')
        self.assertEqual(edit.apply(source), source.replace(b'= "disabled" /* keep after */',
                                                          b'= "okay" /* keep after */', 1))
        self.assertEqual(edit.inverse().apply(edit.apply(source)), source)

    def test_mixed_line_endings_are_preserved_without_normalizing_the_document(self):
        source = b'// comment\r\n&uart0 {\n    status = "disabled";\r\n};\n'
        edit = prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')
        self.assertEqual(edit.apply(source), source.replace(b'"disabled"', b'"okay"'))
        self.assertEqual(edit.inverse().apply(edit.apply(source)), source)

    def test_bom_is_preserved_for_inspection_but_not_accepted_for_editing(self):
        source = b'\xef\xbb\xbf\n&uart0 { status = "disabled"; };'
        self.assertEqual(prototype.round_trip(source), source)
        with self.assertRaisesRegex(prototype.SourceError, "BOM"):
            prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')

    def test_no_op_and_repeated_planning_are_deterministic(self):
        source = self.fixture("literals.overlay")
        no_op = prototype.plan_literal_replacement(source, "&uart0", "status", b'"disabled"')
        self.assertEqual(no_op.apply(source), source)
        self.assertEqual(no_op.inverse().apply(source), source)
        edit = prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')
        self.assertEqual(edit, prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"'))

    def test_forward_and_inverse_guards_detect_changes_outside_the_edit_range(self):
        source = self.fixture("literals.overlay")
        edit = prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')
        with self.assertRaisesRegex(prototype.SourceError, "Stale document"):
            edit.apply(source + b"// external change\n")
        result = edit.apply(source)
        with self.assertRaisesRegex(prototype.SourceError, "Stale document"):
            edit.inverse().apply(result + b"// external change\n")
        with self.assertRaisesRegex(prototype.SourceError, "Stale document"):
            edit.apply(result)

    def test_modified_patch_ranges_and_result_hashes_are_rejected(self):
        source = self.fixture("literals.overlay")
        edit = prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')
        for changed in (replace(edit, start=-1), replace(edit, before=b"wrong"),
                        replace(edit, replacement=b'"bad"'), replace(edit, after_sha256="0" * 64)):
            with self.subTest(patch=changed), self.assertRaises(prototype.SourceError):
                changed.apply(source)

    def test_ambiguous_conditional_deleted_and_malformed_documents_refuse_edits(self):
        for name in ("repeated.overlay", "conditional.overlay", "deletion.overlay", "malformed.overlay"):
            with self.subTest(fixture=name):
                source = self.fixture(name)
                original = bytes(source)
                with self.assertRaises(prototype.SourceError):
                    prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')
                self.assertEqual(source, original)

    def test_duplicate_labeled_boolean_and_missing_properties_are_not_guessed(self):
        for source in (b'&uart0 { status = "a"; status = "b"; };',
                       b'&uart0 { prop_label: status = "a"; };',
                       b'&uart0 { status; };', b'&uart0 { other = "a"; };',
                       b'label: &uart0 { status = "a"; };',
                       b'/ { uart0: serial { status = "a"; }; };'):
            with self.subTest(source=source), self.assertRaises(prototype.SourceError):
                prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')

    def test_macros_and_arithmetic_remain_source_only(self):
        for value in (b"<BUS_RATE>", b"<(1 + 2)>", b"<NRF_PSEL(UART_TX, 0, 6)>", b"<1 2>"):
            source = b"&uart0 { rate = " + value + b"; };"
            self.assertEqual(prototype.round_trip(source), source)
            with self.assertRaises(prototype.SourceError):
                prototype.plan_literal_replacement(source, "&uart0", "rate", b"<115200>")

    def test_replacement_cannot_add_statements_change_types_or_discard_comments(self):
        source = b'&uart0 { status = "disabled"; rate = <1>; };'
        for replacement in (b'"okay"; other = <1>', b'"okay" /* hidden */', b"<1>", b'"unterminated'):
            with self.subTest(value=replacement), self.assertRaises(prototype.SourceError):
                prototype.plan_literal_replacement(source, "&uart0", "status", replacement)
        for replacement in (b"<0x100000000>", b"<-1>", b"<01>", b"<MACRO>", b"<1 2>",
                            b"<" + b"9" * 5000 + b">"):
            with self.subTest(value=replacement), self.assertRaises(prototype.SourceError):
                prototype.plan_literal_replacement(source, "&uart0", "rate", replacement)

    def test_replacement_cannot_introduce_unmodeled_line_splicing(self):
        source = b'&uart0 { status = "disabled"; };'
        for replacement in (b'"en\\\nabled"', b'"en\\\r\nabled"'):
            with self.subTest(value=replacement), self.assertRaises(prototype.SourceError):
                prototype.plan_literal_replacement(source, "&uart0", "status", replacement)

    def test_escaped_strings_and_character_literals_do_not_change_structure(self):
        source = b'&uart0 { status = "disabled"; label = "quote \\\" { }; //"; other = <MACRO(\'}\')>; };'
        edit = prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')
        self.assertEqual(edit.apply(source), source.replace(b'"disabled"', b'"okay"'))

    def test_continued_macro_definition_is_opaque(self):
        source = b'#define SHAPE { \\\n status = "macro"; }\n&uart0 { status = "disabled"; };'
        edit = prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')
        self.assertEqual(edit.apply(source), source.replace(b'"disabled"', b'"okay"'))

    def test_comments_before_conditional_directives_are_preprocessor_whitespace(self):
        for prefix in (b"/* comment */ ", b"  /* first */ /* second */ ", b"/* multi\nline */ "):
            source = prefix + b'#if CONFIG_SERIAL\n&uart0 { status = "disabled"; };\n#endif\n'
            self.assertEqual(prototype.round_trip(source), source)
            with self.subTest(prefix=prefix), self.assertRaisesRegex(prototype.SourceError, "Conditional"):
                prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')

    def test_line_splicing_cannot_hide_comment_or_conditional_context(self):
        for source in (b'// continued comment \\\n&uart0 { status = "disabled"; };',
                       b'// continued comment \\\r\n&uart0 { status = "disabled"; };',
                       b'&uart0 { status = "disabled";\n#\\\nif FLAG\nrate = <1>;\n#endif\n};'):
            self.assertEqual(prototype.round_trip(source), source)
            with self.subTest(source=source), self.assertRaisesRegex(prototype.SourceError, "Line splicing"):
                prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')

    def test_unterminated_tokens_and_invalid_encoding_are_rejected(self):
        for source in (b'&uart0 { status = "unfinished; };', b"/* unfinished", b'"escape\\',
                       b"\xff", b"// nul\x00", "not bytes"):
            with self.subTest(source=source), self.assertRaises(prototype.SourceError):
                prototype.lex(source)

    def test_source_limits_are_bounded_without_external_tools(self):
        source = b'&uart0 { status = "disabled"; child { value = <1>; }; };'
        for limit, value in (("MAX_BYTES", 10), ("MAX_TOKENS", 3), ("MAX_DEPTH", 1)):
            with self.subTest(limit=limit), patch.object(prototype, limit, value):
                with self.assertRaisesRegex(prototype.SourceError, "limit"):
                    prototype.plan_literal_replacement(source, "&uart0", "status", b'"okay"')

    def test_invalid_target_identifiers_are_rejected(self):
        source = self.fixture("literals.overlay")
        for reference, name in (("uart0", "status"), ("&uart0;", "status"),
                                ("&uart0", "status;"), ("&üart0", "status"), (None, "status")):
            with self.subTest(reference=reference, name=name), self.assertRaises(prototype.SourceError):
                prototype.plan_literal_replacement(source, reference, name, b'"okay"')
