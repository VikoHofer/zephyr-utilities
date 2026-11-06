"""Selected board hardware exists independently of aliases and overlay declarations."""

from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.features.devicetree.board_sources import BoardSources
from zephyr_utilities.features.devicetree.board_topology import BoardTopologyService
from zephyr_utilities.features.devicetree.source import SourceDocument


TREE = '''/dts-v1/;
/ {
    compatible = "test,board";
    gpioa: gpio { compatible = "test,gpio"; gpio-controller; #gpio-cells = <2>; status = "disabled"; };
    i2c0: i2c { compatible = "test,i2c"; #address-cells = <1>; #size-cells = <0>; status = "okay"; };
};
'''
BINDINGS = {
    'base.yaml': 'properties:\n  compatible: {type: string-array}\n  status: {type: string}\n',
    'gpio.yaml': '''compatible: test,gpio
include: base.yaml
gpio-cells: [pin, flags]
properties:
  gpio-controller: {type: boolean}
  "#gpio-cells": {type: int, const: 2}
  ngpios: {type: int, default: 16}
  gpio-reserved-ranges: {type: array}
''',
    'i2c.yaml': '''compatible: test,i2c
include: base.yaml
bus: i2c
properties:
  "#address-cells": {type: int, const: 1}
  "#size-cells": {type: int, const: 0}
''',
}


class BoardTopologyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'workspace'
        self.application = self.workspace / 'app'
        self.application.mkdir(parents=True)
        (self.application / 'CMakeLists.txt').write_text('message(FATAL_ERROR "Do not execute")\n')
        self.overlay = self.application / 'app.overlay'
        self.overlay.write_text('/* Empty overlay has no aliases or hardware copies. */\n')
        self.zephyr = self.workspace / 'zephyr'
        self.board_dir = self.zephyr / 'boards/vendor/demo'
        self.board_dir.mkdir(parents=True)
        self.board_file = self.board_dir / 'demo_chip.dts'
        self.board_file.write_text(TREE)
        (self.board_dir / 'board.yml').write_text('board: {name: demo, socs: [{name: chip}]}\n')
        self.soc_file = self.zephyr / 'soc/vendor/chip/soc.yml'
        self.soc_file.parent.mkdir(parents=True)
        self.soc_file.write_text('socs: [{name: chip}]\n')
        self.platform_file = self.board_dir / 'demo_chip.yaml'
        self.platform_file.write_text('identifier: demo/chip\narch: arm\n')
        library = self.zephyr / 'scripts/dts/python-devicetree/src/devicetree'
        library.mkdir(parents=True)
        (library / 'dtlib.py').write_text('# Parser fixture fingerprint only; never executed.\n')
        self.bindings = self.zephyr / 'dts/bindings'
        self.bindings.mkdir(parents=True)
        for name, value in BINDINGS.items():
            (self.bindings / name).write_text(value)
        self.app = Application(self.workspace, self.root / 'data')
        self.addCleanup(self.app.close)
        self.editor = self.app.context.services['devicetree_editor']
        self.drafts = self.app.context.services['devicetree_drafts']
        self.board = BoardTopologyService(self.drafts)
        self.context = self.editor.open({'workspace': str(self.workspace), 'application_path': str(self.application)})
        self.context.update(target={'requested': 'demo/chip', 'board': 'demo', 'revision': None, 'qualifiers': ['chip']},
                            board={'id': 'demo', 'name': 'demo', 'vendor': 'vendor', 'socs': ['chip'], 'status': 'discovered',
                                   'directories': [str(self.board_dir)], 'source_files': [str(self.board_dir / 'board.yml'), str(self.soc_file), str(self.platform_file)],
                                   'qualifiers': ['chip'],
                                   'platforms': [{'target': 'demo/chip', 'architecture': 'arm', 'source_file': str(self.platform_file)}],
                                   'architectures': ['arm'], 'targets': ['demo/chip'], 'default_revision': None},
                            environment={'zephyr': {'path': str(self.zephyr)}, 'tools': [{'name': 'Python', 'path': sys.executable}],
                                         'sdk': {'toolchains': [{'family': 'GNU', 'compiler': sys.executable}]}},
                            input_evidence={'predicted': [{'status': 'predicted', 'files': [{'path': 'app.overlay', 'state': 'selected'}]}]})
        self.context['documents'][0]['usage'] = 'predicted'
        self.editor.contexts[self.context['id']].payload = deepcopy(self.context)
        self.draft = self.drafts.open({**self.binding(), 'document_id': self.context['documents'][0]['id']})

    def binding(self):
        return {'workspace': str(self.workspace), 'context_id': self.context['id'], 'expected_revision': self.context['revision']}

    def guard(self, **extra):
        return {**self.binding(), 'draft_id': self.draft['id'], 'expected_draft_revision': self.draft['revision'],
                'expected_draft_sha256': self.draft['sha256'], **extra}

    def worker(self, text=TREE):
        document = SourceDocument.parse(text.encode())
        return {'source': text, 'nodes': [{'path': document._path(node),
                'source': {'path': str(self.board_file), 'line': 1}, 'property_sources': {}}
                for node in document.syntax.nodes], 'connector_maps': []}

    def inspect(self, text=TREE):
        with patch.object(BoardSources, 'run', return_value=self.worker(text)):
            self.payload = self.board.inspect(self.guard())
        return self.payload

    def validate(self):
        return self.board.validate(self.guard(observation_id=self.payload['observation_id']), self.context,
                                   self.drafts.drafts[self.draft['id']])

    def test_blank_overlay_has_controller_and_disabled_gpio_without_any_alias(self):
        before = self.overlay.read_bytes()
        payload = self.inspect()
        self.assertTrue(payload['supported'], payload['reason'])
        self.assertEqual(payload['controllers'][0]['id'], 'hardware:/i2c')
        self.assertEqual(payload['controllers'][0]['reference'], '&i2c0')
        self.assertEqual(payload['gpio_providers'][0]['status'], 'disabled')
        self.assertEqual(payload['gpio_providers'][0]['pin_count'], 16)
        self.assertEqual(payload['gpio_providers'][0]['pin_count_origin'], 'binding-default')
        self.assertEqual(self.overlay.read_bytes(), before)
        self.assertEqual(self.draft['revision'], 0)
        self.assertEqual(self.app.context.jobs.list(), [])
        self.assertEqual(self.validate()['document'].source.decode(), TREE)

    def test_explicit_gpio_ranges_and_path_reference_are_observed(self):
        payload = self.inspect(TREE.replace('gpioa: ', '').replace('gpio-controller;', 'gpio-controller; ngpios = <8>; gpio-reserved-ranges = <2 2>;'))
        gpio = payload['gpio_providers'][0]
        self.assertEqual(gpio['reference'], '&{/gpio}')
        self.assertEqual(gpio['pin_count_origin'], 'explicit')
        self.assertEqual(gpio['pin_count'], 8)
        self.assertEqual(gpio['reserved_ranges'], [[2, 2]])

    def test_missing_board_metadata_produces_visible_unavailable_board(self):
        self.editor.contexts[self.context['id']].payload['board'] = None
        payload = self.board.inspect(self.guard())
        self.assertFalse(payload['supported'])
        self.assertEqual(payload['board']['name'], 'demo')
        self.assertTrue(payload['reason'])
        self.assertEqual(payload['controllers'], [])

    def test_unmodeled_pin_routing_remains_unknown_even_without_matching_binding(self):
        text = TREE.replace('compatible = "test,board";', 'compatible = "test,board"; pinctrl-0 = <&gpioa>;')
        payload = self.inspect(text)
        self.assertTrue(payload['supported'])
        self.assertFalse(payload['pin_routing_complete'])
        self.assertEqual(len(payload['unknown_pinctrl_states']), 1)

    def test_invalid_observation_identifiers_are_validation_errors(self):
        self.inspect()
        for identifier in ([], {}, None):
            with self.assertRaises(ValueError):
                self.board.validate(self.guard(observation_id=identifier), self.context, self.drafts.drafts[self.draft['id']])

    def test_worker_failure_preserves_draft_and_useful_reason(self):
        with patch.object(BoardSources, 'run', side_effect=ValueError('Missing module include')):
            payload = self.board.inspect(self.guard())
        self.assertFalse(payload['supported'])
        self.assertIn('Missing module include', payload['reason'])
        self.assertEqual(self.draft['revision'], 0)

    def test_revision_expiry_and_binding_changes_invalidate_mutation_authority(self):
        self.inspect()
        (self.bindings / 'gpio.yaml').write_text(BINDINGS['gpio.yaml'] + '  custom: {type: int}\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.validate()
        self.inspect()
        self.board.observations[self.payload['observation_id']]['expires'] = 0
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.validate()
        self.inspect()
        self.draft = self.drafts.source(self.guard(text='/* changed */\n'))
        with self.assertRaisesRegex(ValueError, 'earlier'):
            self.validate()

    def test_board_metadata_and_parser_code_changes_invalidate_observation(self):
        self.inspect()
        (self.board_dir / 'board.yml').write_text('board: {name: changed}\n')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            self.validate()
        (self.board_dir / 'board.yml').write_text('board: {name: demo, socs: [{name: chip}]}\n')
        self.inspect()
        (self.zephyr / 'scripts/dts/python-devicetree/src/devicetree/dtlib.py').write_text('# changed parser\n')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            self.validate()

    def sources(self):
        return BoardSources(self.context, self.drafts.drafts[self.draft['id']])

    def test_full_and_short_board_filenames_cannot_be_ambiguous(self):
        (self.board_dir / 'demo.dts').write_text(TREE)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            self.sources()

    def test_stale_captured_board_metadata_cannot_authorize_new_source_identity(self):
        (self.board_dir / 'board.yml').write_text('board: {name: demo, socs: [{name: replacement}]}\n')
        with self.assertRaisesRegex(ValueError, 'declarations differ'):
            self.sources()

    def test_revision_and_multiple_overlay_composition_are_not_guessed(self):
        for changed in ({'target': {**self.context['target'], 'revision': '1'}},
                        {'board': {**self.context['board'], 'directories': [str(self.board_dir), str(self.application)]}}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                BoardSources({**self.context, **changed}, self.drafts.drafts[self.draft['id']])
        context = deepcopy(self.context)
        context['input_evidence']['predicted'][0]['files'].append({'path': 'second.overlay', 'state': 'selected'})
        with self.assertRaisesRegex(ValueError, 'only selected'):
            BoardSources(context, self.drafts.drafts[self.draft['id']])

    def test_staging_reads_literal_include_closure_without_writing_sources(self):
        header = self.board_dir / 'board-pins.h'
        header.write_text('#define PIN 3\n')
        self.board_file.write_text('#include "board-pins.h"\n' + TREE)
        sources = self.sources()
        with tempfile.TemporaryDirectory() as temporary:
            staged = sources._stage(self.board_file, Path(temporary))
            self.assertIn('#include "' + temporary, staged.read_text())
        self.assertEqual(header.read_text(), '#define PIN 3\n')
        header.write_text('#define PIN 4\n')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            sources.revalidate()

    def test_staging_rejects_dynamic_absolute_and_binary_includes(self):
        for directive in ('#include GENERATED_HEADER\n', '#include "/etc/passwd"\n', '/incbin/("secret");\n', '#include_next "x.h"\n',
                          '#inc\\\nlude "/etc/passwd"\n', '#\\\ninclude "/etc/passwd"\n', '%:include "/etc/passwd"\n',
                          '??=include "/etc/passwd"\n', '#/**/include "/etc/passwd"\n', '#include"/etc/passwd"\n',
                          '#include</etc/passwd>\n', '#pragma GCC dependency "/etc/passwd"\n', '_Pragma("GCC dependency other")\n'):
            self.board_file.write_text(directive + TREE)
            sources = self.sources()
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                sources._stage(self.board_file, Path(temporary))

    def test_input_line_markers_and_external_file_probes_are_rejected(self):
        for directive in ('#line 1 "/etc/passwd"\n', '# 1 "/etc/passwd"\n', '#if __has_include("/etc/passwd")\n#endif\n'):
            self.board_file.write_text(directive + TREE)
            sources = self.sources()
            with tempfile.TemporaryDirectory() as temporary, self.assertRaises(ValueError):
                sources._stage(self.board_file, Path(temporary))

    def test_no_whitespace_local_include_is_staged_instead_of_bypassing_closure(self):
        header = self.board_dir / 'pins.h'
        header.write_text('#pragma once\n#define PIN 3\n')
        self.board_file.write_text('#include"pins.h"\n' + TREE)
        sources = self.sources()
        with tempfile.TemporaryDirectory() as temporary:
            staged = sources._stage(self.board_file, Path(temporary))
            self.assertIn(str(header), sources.files)
            self.assertNotIn('"pins.h"', staged.read_text())

    def test_missing_include_candidates_are_guarded_against_later_shadowing(self):
        self.board_file.write_text('#if 0\n#include "future.h"\n#endif\n' + TREE)
        sources = self.sources()
        with tempfile.TemporaryDirectory() as temporary:
            sources._stage(self.board_file, Path(temporary))
        (self.board_dir / 'future.h').write_text('#define PIN 1\n')
        with self.assertRaisesRegex(ValueError, 'appeared'):
            sources.revalidate()

    def test_worker_is_invoked_with_fixed_preprocess_only_arguments(self):
        sources = self.sources()
        calls = []

        def run(command, directory):
            calls.append(command)
            if '-E' in command:
                Path(command[command.index('-o') + 1]).write_text(TREE)
            else:
                request = json.loads(Path(command[-1]).read_text())
                worker = self.worker()
                for node in worker['nodes']:
                    node['source']['path'] = str(sources.staged[str(self.board_file)])
                Path(request['output']).write_text(json.dumps(worker))

        with patch.object(BoardSources, '_run', side_effect=run):
            sources.run(self.drafts.drafts[self.draft['id']])
        self.assertIn('-nostdinc', calls[0])
        self.assertIn('-undef', calls[0])
        self.assertIn('-D__DTS__', calls[0])
        self.assertEqual(calls[1][1:3], ['-I', '-B'])
        self.assertFalse(any('cmake' in item or 'west' in item for command in calls for item in command))

    def test_resource_wrapper_caps_output_during_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'too-large'
            command = [sys.executable, '-c', 'import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b"x" * 5000000)', str(destination)]
            with self.assertRaisesRegex(ValueError, 'discovery failed'):
                BoardSources._run(command, temporary)
            self.assertLessEqual(destination.stat().st_size, 4 * 1024 * 1024)

    def test_preprocessing_preserves_strings_and_real_line_positions(self):
        from zephyr_utilities.features.devicetree.board_sources import _preprocessing_text
        text = '#define VALUE(a) \\\n+((a) + 1)\nlabel = "http://example.invalid/*literal*/";\n'
        result = _preprocessing_text(text)
        self.assertIn('label = "http://example.invalid/*literal*/";', result)
        self.assertIn('#line 3', result)
