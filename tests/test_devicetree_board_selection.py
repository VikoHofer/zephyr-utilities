"""Exact board targets follow live SoC/variant metadata before hardware is parsed."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.board_sources import BoardSources
import test_devicetree_board_topology as topology_fixture


class BoardSelectionTests(unittest.TestCase):
    binding = topology_fixture.BoardTopologyTests.binding
    guard = topology_fixture.BoardTopologyTests.guard
    worker = topology_fixture.BoardTopologyTests.worker

    def setUp(self):
        topology_fixture.BoardTopologyTests.setUp(self)
        self.soc_file = self.zephyr / 'soc/vendor/soc.yml'
        self.soc_file.parent.mkdir(parents=True, exist_ok=True)
        self.platform_file = self.board_dir / 'selected.yaml'

    def select(self, qualifier='chip/cpuapp', *, clusters=('cpuapp', 'cpunet'), variants=(),
               qualifiers=None, second_soc=False):
        self.board_file.unlink()
        for path in self.board_dir.glob('*.yaml'):
            path.unlink()
        self.board_data = {'board': {'name': 'demo', 'socs': [{'name': 'chip', 'variants': list(variants)}]}}
        self.soc_data = {'socs': [{'name': 'chip', 'cpuclusters': [{'name': name} for name in clusters]}]}
        captured = list(qualifiers or [f'chip/{name}' for name in clusters] or ['chip'])
        if second_soc:
            self.board_data['board']['socs'].append({'name': 'other'})
            self.soc_data['socs'].append({'name': 'other'})
            captured.append('other')
        self.write_metadata()
        requested = 'demo/' + qualifier
        self.platform_file.write_text(json.dumps({'identifier': requested, 'arch': 'arm'}))
        self.context['target'] = {'requested': requested, 'board': 'demo', 'revision': None,
                                  'qualifiers': qualifier.split('/')}
        self.context['board'].update(
            socs=['chip', 'other'] if second_soc else ['chip'], qualifiers=captured,
            architectures=['arm'],
            targets=['demo/' + item for item in captured],
            source_files=[str(self.board_dir / 'board.yml'), str(self.soc_file), str(self.platform_file)],
            platforms=[{'target': requested, 'architecture': 'arm', 'source_file': str(self.platform_file),
                        'evidence': 'declared'}])
        self.board_file = self.board_dir / ('demo_' + qualifier.replace('/', '_') + '.dts')
        self.board_file.write_text(topology_fixture.TREE)
        return self.board_file

    def write_metadata(self):
        (self.board_dir / 'board.yml').write_text(json.dumps(self.board_data))
        self.soc_file.write_text(json.dumps(self.soc_data))

    def sources(self):
        return BoardSources(self.context, self.drafts.drafts[self.draft['id']])

    def rejected_without_execution(self):
        self.editor.contexts[self.context['id']].payload = deepcopy(self.context)
        before_overlay, before_board = self.overlay.read_bytes(), self.board_file.read_bytes()
        with patch.object(BoardSources, 'run', side_effect=AssertionError('Invalid selection must not execute tools')):
            observed = self.board.inspect(self.guard())
        self.assertFalse(observed['supported'])
        self.assertEqual(observed['board']['name'], 'demo')
        self.assertTrue(observed['reason'])
        self.assertEqual(self.overlay.read_bytes(), before_overlay)
        self.assertEqual(self.board_file.read_bytes(), before_board)
        self.assertEqual(self.drafts.drafts[self.draft['id']].revision, 0)
        self.assertEqual(self.app.context.jobs.list(), [])
        return observed

    def test_cpu_cluster_and_nested_variant_select_the_exact_dts(self):
        variants = [{'name': 'ns', 'cpucluster': 'cpuapp', 'variants': [{'name': 'test'}]}]
        qualifiers = ['chip/cpuapp', 'chip/cpuapp/ns', 'chip/cpuapp/ns/test', 'chip/cpunet']
        expected = self.select('chip/cpuapp/ns/test', variants=variants, qualifiers=qualifiers)
        self.assertEqual(self.sources().board_file, expected)

    def test_variant_without_cpu_clusters_uses_the_same_selection_path(self):
        expected = self.select('chip/compact', clusters=(), variants=[{'name': 'compact'}],
                               qualifiers=['chip', 'chip/compact'])
        self.assertEqual(self.sources().board_file, expected)

    def test_single_soc_shortened_filename_keeps_cluster_and_variant(self):
        full = self.select('chip/cpuapp/ns', variants=[{'name': 'ns', 'cpucluster': 'cpuapp'}],
                           qualifiers=['chip/cpuapp', 'chip/cpuapp/ns', 'chip/cpunet'])
        short = self.board_dir / 'demo_cpuapp_ns.dts'
        full.rename(short)
        self.assertEqual(self.sources().board_file, short)

    def test_shortened_filename_is_rejected_for_multiple_socs_even_when_full_exists(self):
        self.select(second_soc=True)
        (self.board_dir / 'demo_cpuapp.dts').write_text(topology_fixture.TREE)
        self.rejected_without_execution()

    def test_simultaneous_full_and_short_filenames_are_rejected(self):
        self.select()
        (self.board_dir / 'demo_cpuapp.dts').write_text(topology_fixture.TREE)
        self.rejected_without_execution()

    def test_absent_shortened_candidate_is_guarded_against_later_shadowing(self):
        self.select()
        sources = self.sources()
        (self.board_dir / 'demo_cpuapp.dts').write_text(topology_fixture.TREE)
        with self.assertRaisesRegex(ValueError, 'appeared|changed'):
            sources.revalidate()

    def test_removed_cpu_cluster_cannot_use_a_still_present_dts_file(self):
        self.select()
        self.soc_data['socs'][0]['cpuclusters'] = [{'name': 'cpunet'}]
        self.write_metadata()
        self.rejected_without_execution()

    def test_removed_variant_cannot_use_a_captured_target_and_existing_dts(self):
        self.select('chip/cpuapp/ns', variants=[{'name': 'ns', 'cpucluster': 'cpuapp'}],
                    qualifiers=['chip/cpuapp', 'chip/cpuapp/ns', 'chip/cpunet'])
        self.board_data['board']['socs'][0]['variants'] = []
        self.write_metadata()
        self.rejected_without_execution()

    def test_variant_on_another_cpu_cluster_cannot_authorize_selected_target(self):
        self.select('chip/cpuapp/ns', variants=[{'name': 'ns', 'cpucluster': 'cpuapp'}],
                    qualifiers=['chip/cpuapp', 'chip/cpuapp/ns', 'chip/cpunet'])
        self.board_data['board']['socs'][0]['variants'][0]['cpucluster'] = 'cpunet'
        self.write_metadata()
        self.rejected_without_execution()

    def test_metadata_changes_after_observation_invalidate_authority(self):
        self.select()
        sources = self.sources()
        self.soc_data['socs'][0]['cpuclusters'].append({'name': 'newcore'})
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, 'changed'):
            sources.revalidate()

    def test_duplicate_soc_definitions_are_rejected(self):
        self.select()
        duplicate = self.zephyr / 'soc/other/soc.yml'
        duplicate.parent.mkdir(parents=True)
        duplicate.write_text(json.dumps(self.soc_data))
        self.context['board']['source_files'].append(str(duplicate))
        self.rejected_without_execution()

    def test_unique_soc_extension_adds_a_declared_cpu_cluster(self):
        self.select('chip/radio', clusters=('cpuapp',), qualifiers=['chip/cpuapp', 'chip/radio'])
        extension = self.zephyr / 'soc/extension/soc.yml'
        extension.parent.mkdir(parents=True)
        extension.write_text(json.dumps({'socs': [{'extend': 'chip', 'cpuclusters': [{'name': 'radio'}]}]}))
        self.context['board']['source_files'].append(str(extension))
        self.assertEqual(self.sources().board_file, self.board_file)

    def test_orphan_extension_cannot_replace_the_base_soc(self):
        self.select()
        self.soc_file.write_text(json.dumps({'socs': [{'extend': 'chip', 'cpuclusters': [{'name': 'cpuapp'}]}]}))
        self.rejected_without_execution()

    def test_extension_repeating_an_existing_cpu_cluster_is_rejected(self):
        self.select()
        extension = self.zephyr / 'soc/extension/soc.yml'
        extension.parent.mkdir(parents=True)
        extension.write_text(json.dumps({'socs': [{'extend': 'chip', 'cpuclusters': [{'name': 'cpuapp'}]}]}))
        self.context['board']['source_files'].append(str(extension))
        self.rejected_without_execution()

    def test_duplicate_cpu_clusters_and_malformed_metadata_are_rejected(self):
        self.select()
        for source in ({'socs': [{'name': 'chip', 'cpuclusters': [{'name': 'cpuapp'}, {'name': 'cpuapp'}]}]},
                       {'socs': [{'name': 'chip', 'cpuclusters': 'cpuapp'}]},
                       {'socs': [{'name': 'chip', 'cpuclusters': [{'name': '../cpuapp'}]}]}):
            with self.subTest(source=source):
                self.soc_file.write_text(json.dumps(source))
                self.rejected_without_execution()

    def test_unknown_target_suffix_is_rejected_even_if_captured_catalog_and_file_allow_it(self):
        self.select('chip/cpuapp/missing', qualifiers=['chip/cpuapp', 'chip/cpuapp/missing', 'chip/cpunet'])
        self.rejected_without_execution()

    def test_target_spelling_and_qualifier_fields_cannot_disagree(self):
        self.select()
        self.context['target']['qualifiers'] = ['chip', 'cpunet']
        self.rejected_without_execution()

    def test_unsupported_metadata_fields_cannot_be_silently_ignored(self):
        self.select()
        original = deepcopy(self.board_data)
        for scope in ('document', 'board'):
            with self.subTest(scope=scope):
                self.board_data = deepcopy(original)
                target = self.board_data if scope == 'document' else self.board_data['board']
                target['unsupported_composition'] = 'extra.overlay'
                self.write_metadata()
                self.rejected_without_execution()

    def test_same_file_board_extension_requires_explicit_composition_support(self):
        self.select()
        base = self.board_data['board']
        self.board_data = {'boards': [base, {'extend': 'demo', 'variants': [
            {'name': 'extra', 'qualifier': 'chip/cpuapp'}]}]}
        self.write_metadata()
        self.rejected_without_execution()

    def test_board_extension_in_captured_source_closure_cannot_be_ignored(self):
        self.select()
        extension = self.zephyr / 'boards/custom/board.yml'
        extension.parent.mkdir(parents=True)
        extension.write_text(json.dumps({'board': {'extend': 'demo', 'variants': [
            {'name': 'extra', 'qualifier': 'chip/cpuapp'}]}}))
        self.context['board']['source_files'].append(str(extension))
        self.rejected_without_execution()

    def test_malformed_captured_platforms_return_an_unavailable_board_card(self):
        self.select()
        for captured in ([None], ['platform'], None):
            with self.subTest(captured=captured):
                self.context['board']['platforms'] = captured
                self.rejected_without_execution()

    def test_exact_platform_architecture_does_not_import_other_core_include_directories(self):
        self.select()
        other = self.board_dir / 'other.yaml'
        other.write_text(json.dumps({'identifier': 'demo/chip/cpunet', 'arch': 'riscv'}))
        self.context['board']['source_files'].append(str(other))
        self.context['board']['architectures'] = ['arm', 'riscv']
        self.context['board']['platforms'].append({'target': 'demo/chip/cpunet', 'architecture': 'riscv',
                                                   'source_file': str(other), 'evidence': 'declared'})
        for architecture in ('arm', 'riscv'):
            (self.zephyr / 'dts' / architecture).mkdir()
        sources = self.sources()
        self.assertEqual(sources.selection['architecture'], 'arm')
        self.assertIn(self.zephyr / 'dts/arm', sources.include_dirs)
        self.assertNotIn(self.zephyr / 'dts/riscv', sources.include_dirs)

    def test_missing_exact_platform_cannot_guess_between_architectures(self):
        self.select()
        self.platform_file.unlink()
        self.context['board']['source_files'].remove(str(self.platform_file))
        self.context['board']['platforms'] = []
        self.context['board']['architectures'] = ['arm', 'riscv']
        self.rejected_without_execution()

    def test_missing_platform_metadata_cannot_trust_a_cached_architecture_alone(self):
        self.select()
        self.platform_file.unlink()
        self.context['board']['source_files'].remove(str(self.platform_file))
        self.context['board']['platforms'] = []
        self.rejected_without_execution()

    def test_live_board_wide_platform_can_supply_one_unambiguous_architecture(self):
        self.select()
        self.platform_file.write_text(json.dumps({'identifier': 'demo', 'arch': 'arm'}))
        self.context['board']['platforms'][0]['target'] = 'demo'
        sources = self.sources()
        self.assertEqual(sources.selection['architecture'], 'arm')
        self.assertEqual(sources.selection['architecture_origin'], 'board-single-architecture')

    def test_stale_board_wide_architecture_cannot_authorize_include_selection(self):
        self.select()
        self.platform_file.write_text(json.dumps({'identifier': 'demo', 'arch': 'riscv'}))
        self.context['board']['platforms'][0].update(target='demo', architecture='riscv')
        # The saved board architecture union is still arm, despite matching current
        # platform rows. Both pieces of captured evidence must remain consistent.
        self.rejected_without_execution()

    def test_conflicting_exact_platform_architectures_are_rejected(self):
        self.select()
        conflict = self.board_dir / 'conflict.yaml'
        conflict.write_text(json.dumps({'identifier': 'demo/chip/cpuapp', 'arch': 'riscv'}))
        self.context['board']['source_files'].append(str(conflict))
        self.context['board']['architectures'] = ['arm', 'riscv']
        self.context['board']['platforms'].append({'target': 'demo/chip/cpuapp', 'architecture': 'riscv',
                                                   'source_file': str(conflict), 'evidence': 'declared'})
        self.rejected_without_execution()

    def test_new_platform_file_invalidates_an_existing_selection(self):
        self.select()
        sources = self.sources()
        (self.board_dir / 'later.yaml').write_text(json.dumps({'identifier': 'demo/chip/cpuapp', 'arch': 'arm'}))
        with self.assertRaisesRegex(ValueError, 'changed|appeared'):
            sources.revalidate()

    def test_successful_selection_does_not_execute_tools_or_change_source(self):
        self.select()
        original = self.overlay.read_bytes(), self.board_file.read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('Selection itself must not execute tools')):
            self.sources()
        self.assertEqual((self.overlay.read_bytes(), self.board_file.read_bytes()), original)
        self.assertEqual(self.app.context.jobs.list(), [])
