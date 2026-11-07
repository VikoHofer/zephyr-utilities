"""Accelerated and Python-only YAML parsing retain the same bounded data contract."""

import importlib.util
import unittest
from unittest.mock import patch

import yaml

from zephyr_utilities.features.devicetree import bindings


class BindingParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import an isolated copy as on an installation without LibYAML. Other
        # binding services retain their normal module and class identities.
        spec = importlib.util.spec_from_file_location(
            "zephyr_utilities.features.devicetree._bindings_python_test", bindings.__file__)
        fallback = importlib.util.module_from_spec(spec)
        with patch.dict(vars(yaml)):
            vars(yaml).pop("CSafeLoader", None)
            spec.loader.exec_module(fallback)
        cls.parsers = (bindings, fallback)

    def test_available_loader_and_python_fallback_preserve_nested_binding_values(self):
        text = '''description: |
  GPIO & peripheral settings
  Keep the quoted literals and numeric constraints.
compatible: "vendor,device"
include:
  - name: base.yaml
    property-allowlist: [rate, mode]
properties:
  rate: {type: int, required: true, default: 0x20, enum: [16, 32]}
  mode: {type: string, default: "off", deprecated: false}
child-binding:
  properties:
    enabled: {type: boolean}
    optional: {default: null}
'''
        expected = {
            "description": "GPIO & peripheral settings\nKeep the quoted literals and numeric constraints.\n",
            "compatible": "vendor,device",
            "include": [{"name": "base.yaml", "property-allowlist": ["rate", "mode"]}],
            "properties": {
                "rate": {"type": "int", "required": True, "default": 32, "enum": [16, 32]},
                "mode": {"type": "string", "default": "off", "deprecated": False}},
            "child-binding": {"properties": {"enabled": {"type": "boolean"}, "optional": {"default": None}}}}
        for parser in self.parsers:
            with self.subTest(parser=parser.__name__):
                self.assertEqual(parser.binding_mapping(text), expected)
        self.assertIs(self.parsers[1]._SafeLoader, yaml.SafeLoader)

    def test_both_loaders_reject_anchors_aliases_and_tags_before_construction(self):
        inputs = (
            "value: &unused 1\n", "value: *missing\n", "value: !include secret.yaml\n",
            "value: !!str 1\n", "value: !!python/object/apply:os.system ['never execute']\n")
        for parser in self.parsers:
            for text in inputs:
                with self.subTest(parser=parser.__name__, text=text):
                    with patch.object(yaml, "load", side_effect=AssertionError("Rejected before construction")):
                        with self.assertRaises(ValueError):
                            parser.binding_mapping(text)

    def test_both_loaders_reject_nested_duplicates_and_nontext_mapping_keys(self):
        inputs = (
            "properties: {rate: {type: int, type: string}}\n", "compatible: one\ncompatible: two\n",
            "1: value\n", "true: value\n", "null: value\n", "? [one, two]\n: value\n")
        for parser in self.parsers:
            for text in inputs:
                with self.subTest(parser=parser.__name__, text=text), self.assertRaisesRegex(ValueError, "unique text keys"):
                    parser.binding_mapping(text)

    def test_both_loaders_enforce_depth_event_and_utf8_byte_limits(self):
        for parser in self.parsers:
            with self.subTest(parser=parser.__name__):
                self.assertIn("value", parser.binding_mapping("value: " + "[" * 31 + "0" + "]" * 31))
                with self.assertRaisesRegex(ValueError, "nesting"):
                    parser.binding_mapping("value: " + "[" * 32 + "0" + "]" * 32)
                with patch.object(parser, "MAX_EVENTS", 8):
                    self.assertEqual(parser.binding_mapping("key: value"), {"key": "value"})
                with patch.object(parser, "MAX_EVENTS", 7), self.assertRaisesRegex(ValueError, "event limits"):
                    parser.binding_mapping("key: value")
                with patch.object(parser, "MAX_FILE_BYTES", 9):
                    self.assertEqual(parser.binding_mapping("key: éé"), {"key": "éé"})
                with patch.object(parser, "MAX_FILE_BYTES", 8), self.assertRaisesRegex(ValueError, "file limit"):
                    parser.binding_mapping("key: éé")

    def test_both_loaders_reject_invalid_yaml_and_nonmapping_documents(self):
        for parser in self.parsers:
            for text in ("value: [", "one: 1\n---\ntwo: 2\n", "[one, two]", "", "null"):
                with self.subTest(parser=parser.__name__, text=text), self.assertRaises(ValueError):
                    parser.binding_mapping(text)
