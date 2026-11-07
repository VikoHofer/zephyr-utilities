"""Retiring device creation preserves existing source, history and durable recovery."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request


SOURCE = '''/* Existing user hardware remains user data. */
&i2c0 {
    saved_sensor: sensor@76 {
        compatible = "bosch,bme280";
        reg = <0x76>;
        status = "okay";
    };
};
/ {
    saved_leds {
        compatible = "gpio-leds";
        saved_led: led { gpios = <&gpio0 7 0>; };
    };
};
'''
RETIRED = ('board_module_add', 'module_add', 'module_update', 'module_remove',
           'peripheral_add', 'gpio_group_add', 'gpio_add', 'pwm_group_add', 'pwm_led_add')


class DevicetreeRetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'workspace'
        self.application = self.workspace / 'app'
        self.application.mkdir(parents=True)
        (self.application / 'CMakeLists.txt').write_text('message(FATAL_ERROR "Never execute")\n')
        self.overlay = self.application / 'app.overlay'
        self.overlay.write_text(SOURCE)
        self.app = Application(self.workspace, self.root / 'data')
        self.addCleanup(lambda: self.app.close())
        self.open()

    def open(self):
        self.editor = self.app.context.services['devicetree_editor']
        self.drafts = self.app.context.services['devicetree_drafts']
        self.saving = self.app.context.services['devicetree_saving']
        self.context = self.editor.open({'workspace': str(self.workspace), 'application_path': str(self.application)})
        self.document_id = self.context['documents'][0]['id']
        self.draft = self.drafts.open({**self.binding(), 'document_id': self.document_id})

    def binding(self):
        return {'workspace': str(self.workspace), 'context_id': self.context['id'],
                'expected_revision': self.context['revision']}

    def guard(self, **extra):
        return {**self.binding(), 'draft_id': self.draft['id'], 'expected_draft_revision': self.draft['revision'],
                'expected_draft_sha256': self.draft['sha256'], **extra}

    def current(self):
        return self.drafts.get({**self.binding(), 'draft_id': self.draft['id']})

    def test_old_services_routes_and_browser_assets_are_unavailable(self):
        before = self.overlay.read_bytes()
        for name in ('devicetree_modules', 'devicetree_layout', 'devicetree_board_connections'):
            self.assertNotIn(name, self.app.context.services)
        for suffix in ('drafts/modules', 'drafts/module-layout', 'drafts/module-layout/save'):
            with self.subTest(route=suffix), self.assertRaises(FileNotFoundError):
                self.app.router.dispatch(Request('POST', '/api/devicetree/editor/' + suffix, body=self.guard()))
        for name in ('board.js', 'board-view.js', 'modules.js', 'module-view.js',
                     'peripherals.js', 'peripheral-view.js', 'pwm.js', 'pwm-view.js',
                     'adc.js', 'adc-view.js', 'can.js', 'can-view.js',
                     'interrupt-gpio.js', 'interrupt-gpio-view.js'):
            with self.subTest(asset=name), self.assertRaises(FileNotFoundError):
                self.app.asset('/assets/devicetree/' + name)
        self.assertEqual(self.overlay.read_bytes(), before)
        self.assertEqual(self.current()['text'], SOURCE)

    def test_rejected_creation_commands_preserve_redo_source_and_recovery_record(self):
        changed = SOURCE.replace('status = "okay";', 'status = "disabled";')
        self.draft = self.drafts.source(self.guard(text=changed))
        self.draft = self.drafts.undo(self.guard())
        before = deepcopy(self.current())
        self.assertTrue(before['can_redo'])
        storage = self.app.context.feature_data('devicetree')
        record_path = storage / 'drafts' / (self.draft['id'] + '.json')
        record = record_path.read_bytes()
        for kind in RETIRED:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'exact supported visual command fields'):
                self.drafts.command(self.guard(command={'kind': kind, 'schema_id': 'retired-observation'}))
            self.assertEqual(self.current(), before)
            self.assertEqual(record_path.read_bytes(), record)
            self.assertEqual(self.overlay.read_text(), SOURCE)
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft['text'], changed)
        self.assertIn('saved_sensor: sensor@76', self.draft['text'])
        self.assertIn('saved_led: led', self.draft['text'])
        self.assertEqual(self.overlay.read_text(), SOURCE)

    def test_restart_and_recovery_preserve_sensor_text_history_and_legacy_layout_bytes(self):
        changed = SOURCE.replace('status = "okay";', 'status = "disabled";')
        self.draft = self.drafts.source(self.guard(text=changed))
        self.draft = self.drafts.undo(self.guard())
        recovery_id = self.draft['id']
        storage = self.app.context.feature_data('devicetree')
        record_path = storage / 'drafts' / (recovery_id + '.json')
        record = record_path.read_bytes()
        scope = {'workspace': str(self.workspace), 'workspace_identity': self.context['workspace_identity'],
                 'application': str(self.application), 'path': str(self.overlay),
                 'project': None, 'configuration': None, 'target': None}
        layout_id = hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        layout_path = storage / 'module-layouts' / (layout_id + '.json')
        layout_path.parent.mkdir(parents=True)
        layout = json.dumps({'schema_version': 1, 'layout_id': layout_id, 'revision': 1, 'scope': scope,
                             'positions': {'node:/saved_leds': {'x': 3, 'y': 4}}}).encode()
        layout_path.write_bytes(layout)
        self.app.close()
        self.app = Application(self.workspace, self.root / 'data')
        self.open()
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.assertEqual(record_path.read_bytes(), record)
        self.assertEqual(layout_path.read_bytes(), layout)
        listing = self.saving.recovery_list({'workspace': str(self.workspace)})
        self.assertIn(recovery_id, {item['id'] for item in listing['records']})
        review = self.saving.recovery_read({'workspace': str(self.workspace), 'recovery_id': recovery_id})
        self.assertEqual(review['record']['text'], SOURCE)
        restored = self.saving.recovery_restore({**self.binding(), 'document_id': self.document_id,
                    'recovery_id': recovery_id, 'expected_recovery_revision': review['record']['revision'],
                    'comparison_id': review['comparison']['comparison_id'], 'mode': 'keep'})
        self.context, self.draft = restored['context'], restored['draft']
        self.assertTrue(self.draft['can_redo'])
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft['text'], changed)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft['text'], SOURCE)
        self.assertEqual(self.overlay.read_text(), SOURCE)
        self.assertEqual(layout_path.read_bytes(), layout)
