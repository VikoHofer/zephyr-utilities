"""Pin edits resolve overlay contributors without escaping their source target."""

import unittest

from zephyr_utilities.features.devicetree.chip_source import apply_activation, reference_paths, target_node
from zephyr_utilities.features.devicetree.source import SourceDocument, SourceError


OBSERVED = {"payload": {"nodes": [
    {"path": "/soc", "labels": ["soc"]},
    {"path": "/soc/bus@0", "labels": ["bus"]},
    {"path": "/soc/bus@0/serial@1000", "labels": ["uart0", "console_uart"]},
    {"path": "/soc/pinctrl@0", "labels": ["pinctrl"]},
]}}
STATUS = {"updates": [{"reference": "&uart0", "properties": {"status": '"okay"'}}]}
PIN = {"label": "zu_uart0_default", "name": "zu-uart0-default",
       "source": "zu_uart0_default: zu-uart0-default { pinmux = <7>; };"}


class ChipSourceTargetTests(unittest.TestCase):
    def test_nested_symbolic_override_is_edited_in_place(self):
        before = b'''// Keep the surrounding layout.\r
&soc {\r
    bus@0 {\r
        serial@1000 { status = "disabled"; /* console choice */ };\r
        sensor@2000 { status = "okay"; };\r
    };\r
};\r
'''
        draft = SourceDocument.parse(before, document_id="application-overlay", revision=3)
        updated = apply_activation(draft, OBSERVED, STATUS)
        self.assertEqual(updated.source, before.replace(b'status = "disabled";', b'status = "okay";'))
        self.assertEqual(updated.id, draft.id)
        self.assertEqual(updated.revision, 4)
        self.assertEqual(draft.source, before)

    def test_nested_and_direct_overrides_of_one_target_are_ambiguous(self):
        for direct in ("&uart0", "&console_uart", "&{/soc/bus@0/serial@1000}"):
            before = ('&soc { bus@0 { serial@1000 { status = "disabled"; }; }; };\n'
                      + direct + ' { current-speed = <57600>; };\n').encode()
            with self.subTest(direct=direct):
                draft = SourceDocument.parse(before)
                with self.assertRaisesRegex(SourceError, "several source contributors"):
                    apply_activation(draft, OBSERVED, STATUS)
                self.assertEqual(draft.source, before)

    def test_repeated_symbolic_parents_cannot_hide_duplicate_child_contributors(self):
        draft = SourceDocument.parse(b'''&soc { bus@0 { serial@1000 { status = "disabled"; }; }; };
&bus { serial@1000 { current-speed = <57600>; }; };''')
        with self.assertRaisesRegex(SourceError, "several source contributors"):
            apply_activation(draft, OBSERVED, STATUS)

    def test_absolute_and_root_parents_resolve_to_the_same_target(self):
        for source in (b'&{/soc} { bus@0 { serial@1000 { status = "disabled"; }; }; };',
                       b'/ { soc { bus@0 { serial@1000 { status = "disabled"; }; }; }; };',
                       b'&bus { serial@1000 { status = "disabled"; }; };'):
            with self.subTest(source=source):
                draft = SourceDocument.parse(source)
                selected = target_node(draft, "&{/soc/bus@0/serial@1000}", reference_paths(OBSERVED))
                self.assertEqual(selected.name, "serial@1000")
                self.assertEqual(apply_activation(draft, OBSERVED, STATUS).source,
                                 source.replace(b'"disabled"', b'"okay"'))

    def test_unresolved_parent_is_not_guessed_from_child_name(self):
        draft = SourceDocument.parse(b'&unknown { bus@0 { serial@1000 { status = "disabled"; }; }; };')
        self.assertIsNone(target_node(draft, "&uart0", reference_paths(OBSERVED)))

    def test_generated_pin_node_joins_the_existing_nested_pinctrl(self):
        draft = SourceDocument.parse(b'&soc { pinctrl@0 { /* retained */ }; };')
        updated = apply_activation(draft, OBSERVED, {"pinctrl_nodes": [PIN]})
        self.assertNotIn(b"&pinctrl {", updated.source)
        parent = target_node(updated, "&pinctrl", reference_paths(OBSERVED))
        self.assertEqual(parent.name, "pinctrl@0")
        self.assertEqual([updated.node(child).labels for child in parent.children], [(PIN["label"],)])
        self.assertIn(b"/* retained */", updated.source)

    def test_generated_pin_wrapper_rejects_extra_targets_and_outer_statements(self):
        invalid_sources = [
            PIN["source"] + ' }; &uart0 { status = "disabled"; }; &pinctrl {',
            'status = "disabled"; ' + PIN["source"],
            PIN["source"] + ' /delete-node/ old-state;',
            '#include <unrequested.h>\n' + PIN["source"],
            PIN["source"] + ' other: other { pinmux = <8>; };',
        ]
        draft = SourceDocument.parse(b"// Existing application overlay\n")
        for source in invalid_sources:
            with self.subTest(source=source), self.assertRaisesRegex(SourceError, "invalid or already used identity"):
                apply_activation(draft, OBSERVED, {"pinctrl_nodes": [{**PIN, "source": source}]})
        self.assertEqual(draft.source, b"// Existing application overlay\n")

    def test_generated_pin_wrapper_allows_the_nordic_nested_group_shape(self):
        block = {**PIN, "source": 'zu_uart0_default: zu-uart0-default { group1 { psels = <1 2>; bias-pull-up; }; };'}
        updated = apply_activation(SourceDocument.parse(b""), OBSERVED, {"pinctrl_nodes": [block]})
        selected = updated.find_node("&zu_uart0_default")
        self.assertEqual([updated.node(child).name for child in selected.children], ["group1"])
        self.assertFalse(updated.syntax.blocked)


if __name__ == "__main__":
    unittest.main()
