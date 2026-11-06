"""The editor and Projects share exact metadata semantics without tool execution."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from zephyr_utilities.core.hardware_metadata import (MetadataError, board_qualifiers,
                                                    parse_hardware_metadata, soc_entries)


class HardwareMetadataTests(unittest.TestCase):
    def test_soc_hierarchy_and_top_level_extensions_preserve_identity(self):
        data = parse_hardware_metadata('''family:
  - name: family_a
    series:
      - name: series_a
        socs:
          - name: dual
            cpuclusters: [{name: app}, {name: net}]
    socs: [{name: simple}]
series:
  - name: standalone
    socs: [{name: chip}]
socs:
  - extend: dual
    cpuclusters: [{name: radio}]
''')
        entries = soc_entries(data)
        self.assertEqual([row['name'] for row in entries], ['dual', 'simple', 'chip', 'dual'])
        self.assertEqual(entries[0]['cpuclusters'], ['app', 'net'])
        self.assertEqual((entries[0]['family'], entries[0]['series']), ('family_a', 'series_a'))
        self.assertTrue(entries[-1]['extension'])
        self.assertFalse(entries[0]['extension'])

    def test_cpu_clusters_and_nested_variants_expand_without_mutating_inputs(self):
        definition = {'socs': [{'name': 'dual', 'variants': [
            {'name': 'ns', 'cpucluster': 'app', 'variants': [{'name': 'boot'}]}]},
            {'name': 'simple', 'variants': [{'name': 'wireless'}]}]}
        systems = {'dual': {'status': 'discovered', 'cpuclusters': ['app', 'net']},
                   'simple': {'status': 'discovered', 'cpuclusters': []}}
        before = deepcopy((definition, systems))
        self.assertEqual(board_qualifiers(definition, systems),
                         ['dual/app', 'dual/app/ns', 'dual/app/ns/boot', 'dual/net', 'simple', 'simple/wireless'])
        self.assertEqual((definition, systems), before)

    def test_missing_conflicting_and_invalid_cluster_selection_are_rejected(self):
        board = {'socs': [{'name': 'chip', 'variants': [{'name': 'ns', 'cpucluster': 'app'}]}]}
        for systems in ({}, {'chip': {'status': 'conflict', 'cpuclusters': ['app']}},
                        {'chip': {'status': 'discovered', 'cpuclusters': []}},
                        {'chip': {'status': 'discovered', 'cpuclusters': ['net']}}):
            with self.subTest(systems=systems), self.assertRaises(MetadataError):
                board_qualifiers(board, systems)

    def test_duplicate_qualifiers_and_duplicate_cpu_clusters_are_rejected(self):
        with self.assertRaisesRegex(MetadataError, 'duplicated'):
            soc_entries({'socs': [{'name': 'chip', 'cpuclusters': [{'name': 'app'}, {'name': 'app'}]}]})
        systems = {'chip': {'status': 'discovered', 'cpuclusters': []}}
        for board in ({'socs': [{'name': 'chip'}, {'name': 'chip'}]},
                      {'socs': [{'name': 'chip', 'variants': [{'name': 'ns'}, {'name': 'ns'}]}]}):
            with self.subTest(board=board), self.assertRaisesRegex(MetadataError, 'Duplicate'):
                board_qualifiers(board, systems)

    def test_duplicate_yaml_keys_aliases_and_executable_tags_are_rejected(self):
        for text in ('socs: []\nsocs: []\n', 'socs: &s []\nfamily: *s\n',
                     'socs: !!python/object/apply:os.system ["false"]\n'):
            with self.subTest(text=text), self.assertRaises(MetadataError):
                parse_hardware_metadata(text)

    def test_unknown_declaration_fields_and_unsafe_identifiers_are_rejected(self):
        for data in ({'socs': [{'name': '../chip'}]},
                     {'socs': [{'name': 'chip', 'execute': 'script'}]},
                     {'soc': [{'name': 'chip'}]}):
            with self.subTest(data=data), self.assertRaises(MetadataError):
                soc_entries(data)

    def test_limits_cover_unadorned_cpu_cluster_expansion_and_variant_depth(self):
        systems = {'chip': {'status': 'discovered', 'cpuclusters': ['one', 'two', 'three']}}
        with patch('zephyr_utilities.core.hardware_metadata.MAX_QUALIFIERS', 2), self.assertRaisesRegex(MetadataError, 'qualifier limit'):
            board_qualifiers({'socs': [{'name': 'chip'}]}, systems)
        variants = [{'name': 'ns', 'variants': [{'name': 'boot'}]}]
        with patch('zephyr_utilities.core.hardware_metadata.MAX_DEPTH', 0), self.assertRaisesRegex(MetadataError, 'nesting'):
            board_qualifiers({'socs': [{'name': 'chip', 'variants': variants}]},
                             {'chip': {'status': 'discovered', 'cpuclusters': []}})

    def test_yaml_byte_event_and_depth_limits_fail_before_projection(self):
        for bound, limit, text in (('MAX_FILE_BYTES', 4, 'socs: []\n'),
                                   ('MAX_EVENTS', 2, 'socs: []\n'),
                                   ('MAX_DEPTH', 1, 'socs: [[[]]]\n')):
            with self.subTest(bound=bound), patch('zephyr_utilities.core.hardware_metadata.' + bound, limit), self.assertRaises(MetadataError):
                parse_hardware_metadata(text)


if __name__ == '__main__':
    unittest.main()
