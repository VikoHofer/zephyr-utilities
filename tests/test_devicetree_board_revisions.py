"""Board revisions automatically compose selected source before application edits."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.board_sources import BoardSources
import test_devicetree_board_selection as selection_fixture
import test_devicetree_board_topology as topology_fixture


class BoardRevisionTests(unittest.TestCase):
    setUp = selection_fixture.BoardSelectionTests.setUp
    select = selection_fixture.BoardSelectionTests.select
    write_metadata = selection_fixture.BoardSelectionTests.write_metadata
    sources = selection_fixture.BoardSelectionTests.sources
    binding = topology_fixture.BoardTopologyTests.binding
    guard = topology_fixture.BoardTopologyTests.guard
    worker = topology_fixture.BoardTopologyTests.worker

    def revision(self, requested=None, *, default='2.0.0', revisions=('1.0.0', '2.0.0'),
                 fmt='major.minor.patch', exact=False):
        self.board_data['board']['revision'] = {
            'format': fmt, 'default': default, 'exact': exact,
            'revisions': [{'name': value} for value in revisions],
        }
        self.write_metadata()
        self.context['board'].update(revisions=list(revisions), default_revision=default,
                                     revision_format=fmt, revision_exact=exact)
        target = self.context['target']
        target['revision'] = requested
        target['requested'] = 'demo' + ('@' + requested if requested is not None else '') + '/' + '/'.join(target['qualifiers'])
        self.context['board']['targets'] += [
            'demo@' + revision + '/' + qualifier
            for revision in revisions for qualifier in self.context['board']['qualifiers']]

    def test_default_revision_uses_its_own_overlay_without_changing_target(self):
        self.select()
        self.revision()
        selected = self.board_dir / 'demo_chip_cpuapp_2_0_0.overlay'
        selected.write_text('&gpioa { ngpios = <12>; };\n')
        older = self.board_dir / 'demo_chip_cpuapp_1_0_0.overlay'
        older.write_text('&gpioa { ngpios = <4>; };\n')
        before = deepcopy(self.context)
        sources = self.sources()
        self.assertEqual(sources.selection['revision'], {
            'requested': None, 'effective': '2.0.0', 'default': '2.0.0',
            'format': 'major.minor.patch', 'match': 'default', 'exact': False})
        self.assertEqual(sources.board_overlays, [selected])
        self.assertEqual(sources.selection['board_overlays'], [str(selected)])
        self.assertNotIn(str(older), sources.files)
        self.assertEqual(self.context, before)

    def test_explicit_revision_uses_its_resolved_suffix(self):
        self.select()
        overlay = self.board_dir / 'demo_chip_cpuapp_1_0_0.overlay'
        overlay.write_text('&gpioa { ngpios = <4>; };\n')
        for requested in ('1.0.0',):
            with self.subTest(requested=requested):
                self.revision(requested)
                sources = self.sources()
                self.assertEqual(sources.selection['revision']['effective'], '1.0.0')
                self.assertEqual(sources.selection['revision']['match'], 'exact')
                self.assertEqual(sources.board_overlays, [overlay])
                self.assertEqual(sources.selection['target'], 'demo@' + requested + '/chip/cpuapp')

    def test_default_without_revision_overlay_uses_base_source(self):
        self.select()
        self.revision()
        sources = self.sources()
        self.assertEqual(sources.board_overlays, [])
        self.assertEqual(sources.board_file, self.board_file)
        self.assertIn(str(self.board_dir / 'demo_chip_cpuapp_2_0_0.overlay'), sources.absent)
        self.assertIn(str(self.board_dir / 'demo_cpuapp_2_0_0.overlay'), sources.absent)

    def test_short_revision_overlay_requires_a_single_soc_and_one_naming_style(self):
        self.select()
        self.revision()
        shortened = self.board_dir / 'demo_cpuapp_2_0_0.overlay'
        shortened.write_text('&gpioa { ngpios = <4>; };\n')
        self.assertEqual(self.sources().board_overlays, [shortened])
        full = self.board_dir / 'demo_chip_cpuapp_2_0_0.overlay'
        full.write_text('&gpioa { ngpios = <8>; };\n')
        with self.assertRaisesRegex(ValueError, 'filenames conflict'):
            self.sources()
        full.unlink()
        self.select(second_soc=True)
        self.revision()
        with self.assertRaisesRegex(ValueError, 'multiple SoCs'):
            self.sources()

    def test_revision_overlay_cannot_fall_back_to_another_core_or_variant(self):
        self.select('chip/cpuapp/ns', variants=[{'name': 'ns', 'cpucluster': 'cpuapp'}],
                    qualifiers=['chip/cpuapp', 'chip/cpuapp/ns', 'chip/cpunet'])
        self.revision()
        for name in ('demo_chip_cpuapp_2_0_0.overlay', 'demo_chip_cpunet_2_0_0.overlay', 'demo_2_0_0.overlay'):
            (self.board_dir / name).write_text('&gpioa { ngpios = <4>; };\n')
        self.assertEqual(self.sources().board_overlays, [])

    def test_live_revision_metadata_must_match_the_captured_board(self):
        self.select()
        self.revision()
        self.board_data['board']['revision']['default'] = '1.0.0'
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, 'revision metadata changed'):
            self.sources()

    def test_exact_miss_unknown_format_and_undeclared_revision_do_not_run_tools(self):
        self.select()
        cases = [dict(requested='1.5.0', exact=True), dict(fmt='custom'), dict(requested='0.1.0')]
        for values in cases:
            with self.subTest(values=values), patch('subprocess.Popen', side_effect=AssertionError('No commands expected')):
                self.revision(**values)
                with self.assertRaises(ValueError):
                    self.sources()

    def test_target_revision_spelling_must_match_its_requested_target(self):
        self.select()
        self.revision('1.0.0')
        self.context['target']['requested'] = 'demo@2.0.0/chip/cpuapp'
        with self.assertRaisesRegex(ValueError, 'do not agree'):
            self.sources()

    def test_effective_revision_platform_architecture_precedes_other_revisions(self):
        self.select()
        self.revision()
        current = 'demo@2.0.0/chip/cpuapp'
        self.platform_file.write_text(json.dumps({'identifier': current, 'arch': 'arm'}))
        self.context['board']['platforms'][0]['target'] = current
        older = self.board_dir / 'older.yaml'
        older.write_text(json.dumps({'identifier': 'demo@1.0.0/chip/cpuapp', 'arch': 'riscv'}))
        self.context['board']['source_files'].append(str(older))
        self.context['board']['platforms'].append({'target': 'demo@1.0.0/chip/cpuapp', 'architecture': 'riscv', 'source_file': str(older)})
        self.context['board']['architectures'] = ['arm', 'riscv']
        self.assertEqual(self.sources().selection['architecture'], 'arm')
        self.revision('1.0.0')
        self.assertEqual(self.sources().selection['architecture'], 'riscv')

    def test_compatible_but_unlisted_revision_is_not_admitted_as_a_project_target(self):
        self.select()
        self.revision('1.5.0')
        with self.assertRaisesRegex(ValueError, 'captured board metadata'):
            self.sources()

    def test_unrevisioned_target_architecture_is_retained_when_no_revision_platform_exists(self):
        self.select()
        self.revision()
        self.assertEqual(self.sources().selection['architecture'], 'arm')

    def test_changed_or_new_revision_overlay_invalidates_selection(self):
        self.select()
        self.revision()
        overlay = self.board_dir / 'demo_chip_cpuapp_2_0_0.overlay'
        sources = self.sources()
        overlay.write_text('&gpioa { ngpios = <4>; };\n')
        with self.assertRaisesRegex(ValueError, 'appeared'):
            sources.revalidate()
        sources = self.sources()
        overlay.write_text('&gpioa { ngpios = <8>; };\n')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            sources.revalidate()

    def test_revision_overlay_includes_are_guarded_without_writing_source(self):
        self.select()
        self.revision()
        overlay = self.board_dir / 'demo_chip_cpuapp_2_0_0.overlay'
        included = self.board_dir / 'revision-pins.dtsi'
        included.write_text('&gpioa { ngpios = <4>; };\n')
        overlay.write_text('#include "revision-pins.dtsi"\n')
        sources = self.sources()
        before = overlay.read_bytes(), included.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            sources._stage(overlay, Path(temporary))
        self.assertEqual((overlay.read_bytes(), included.read_bytes()), before)
        included.write_text('&gpioa { ngpios = <8>; };\n')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            sources.revalidate()

    def test_preprocessor_orders_board_revision_then_application_with_original_provenance(self):
        self.select()
        self.revision()
        revision = self.board_dir / 'demo_chip_cpuapp_2_0_0.overlay'
        revision.write_text('&gpioa { ngpios = <4>; };\n')
        sources = self.sources()
        calls = []

        def run(command, directory):
            calls.append(command)
            if '-E' in command:
                Path(command[command.index('-o') + 1]).write_text(topology_fixture.TREE)
            else:
                request = json.loads(Path(command[-1]).read_text())
                worker = self.worker()
                for node in worker['nodes']:
                    node['source']['path'] = str(sources.staged[str(self.board_file)])
                    node['property_sources'] = {'ngpios': {'path': str(sources.staged[str(revision)]), 'line': 1}}
                Path(request['output']).write_text(json.dumps(worker))

        before = self.board_file.read_bytes(), revision.read_bytes(), self.overlay.read_bytes()
        with patch.object(BoardSources, '_run', side_effect=run):
            worker = sources.run(self.drafts.drafts[self.draft['id']])
        includes = [calls[0][index + 1] for index, argument in enumerate(calls[0]) if argument == '-include']
        self.assertEqual(includes, [str(sources.staged[str(path)]) for path in (self.board_file, revision, self.overlay)])
        self.assertTrue(all(node['property_sources']['ngpios']['path'] == str(revision) for node in worker['nodes']))
        self.assertEqual((self.board_file.read_bytes(), revision.read_bytes(), self.overlay.read_bytes()), before)
        self.assertFalse(any('cmake' in argument or 'west' in argument for command in calls for argument in command))

    def test_observation_exposes_selected_revision_and_revision_overlay_diagnostics(self):
        self.select()
        self.revision()
        revision = self.board_dir / 'demo_chip_cpuapp_2_0_0.overlay'
        revision.write_text('&gpioa { ngpios = <4>; };\n')
        self.editor.contexts[self.context['id']].payload = deepcopy(self.context)
        with patch.object(BoardSources, 'run', return_value=self.worker()):
            result = self.board.inspect(self.guard())
        self.assertTrue(result['supported'], result['reason'])
        self.assertEqual(result['selection']['revision']['effective'], '2.0.0')
        self.assertEqual(result['selection']['board_overlays'], [str(revision)])
        self.assertTrue(any('Board revision: 2.0.0 (default)' in message for message in result['diagnostics']))
