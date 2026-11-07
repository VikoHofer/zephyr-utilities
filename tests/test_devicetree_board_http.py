"""Hardware inspection and retired creation paths share guarded HTTP transport."""

from copy import deepcopy
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from zephyr_utilities.app import create_server
from zephyr_utilities.features.devicetree.board_sources import BoardSources
import test_devicetree_board_topology as topology_fixture
import test_projects_api as http_fixture


PREFIX = '/api/devicetree/editor/'
RETIRED_COMMANDS = ('board_module_add', 'module_add', 'module_update', 'module_remove',
                    'peripheral_add', 'gpio_group_add', 'gpio_add', 'pwm_group_add', 'pwm_led_add')


class BoardObservationHttpTests(unittest.TestCase):
    requires_loopback = True
    binding = topology_fixture.BoardTopologyTests.binding
    guard = topology_fixture.BoardTopologyTests.guard
    worker = topology_fixture.BoardTopologyTests.worker
    request = http_fixture.ProjectsHttpTests.request

    def setUp(self):
        with patch.object(topology_fixture, 'Application', side_effect=self.make_server):
            topology_fixture.BoardTopologyTests.setUp(self)
        self.board = self.app.context.services['devicetree_board']
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def make_server(self, workspace, data):
        self.server = create_server(workspace, data, 0)
        return self.server.app

    def close_server(self):
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()

    def post(self, operation, body):
        with self.request(PREFIX + operation, method='POST', body=body) as response:
            return json.load(response)

    def test_board_and_chip_routes_enforce_authentication_origin_and_fields(self):
        with patch.object(BoardSources, 'run', side_effect=AssertionError('Rejected requests must not inspect hardware')):
            for route in ('drafts/board', 'chip/start', 'chip/result', 'chip/cancel'):
                for extra, expected in (({'authorized': False}, 403), ({'origin': 'https://outside.invalid'}, 403), ({}, 400)):
                    with self.subTest(route=route, extra=extra), self.assertRaises(HTTPError) as error:
                        self.request(PREFIX + route, method='POST', body={}, **extra)
                    self.assertEqual(error.exception.code, expected)
                    error.exception.close()
        self.assertEqual(self.board.observations, {})
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_hardware_observation_preserves_sources_and_has_no_recipe_catalogue(self):
        before = deepcopy(self.draft)
        board_bytes, overlay_bytes = self.board_file.read_bytes(), self.overlay.read_bytes()
        with patch.object(BoardSources, 'run', return_value=self.worker()):
            observed = self.post('drafts/board', self.guard())
        self.assertTrue(observed['supported'], observed['reason'])
        self.assertEqual(observed['controllers'][0]['id'], 'hardware:/i2c')
        self.assertNotIn('connection_options', observed)
        self.assertEqual(self.post('drafts/get', {**self.binding(), 'draft_id': before['id']}), before)
        self.assertEqual(self.board_file.read_bytes(), board_bytes)
        self.assertEqual(self.overlay.read_bytes(), overlay_bytes)

    def test_retired_routes_services_and_creation_commands_cannot_mutate_history(self):
        for name in ('devicetree_modules', 'devicetree_layout', 'devicetree_board_connections'):
            self.assertNotIn(name, self.app.context.services)
        for route in ('drafts/modules', 'drafts/module-layout', 'drafts/module-layout/save'):
            with self.subTest(route=route), self.assertRaises(HTTPError) as error:
                self.post(route, self.guard())
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        self.draft = self.post('drafts/source', self.guard(text=self.draft['text'] + '\n/* retained edit */\n'))
        self.draft = self.post('drafts/undo', self.guard())
        before = self.post('drafts/get', {**self.binding(), 'draft_id': self.draft['id']})
        self.assertTrue(before['can_redo'])
        for kind in RETIRED_COMMANDS:
            with self.subTest(kind=kind), self.assertRaises(HTTPError) as error:
                self.post('drafts/command', self.guard(command={'kind': kind, 'schema_id': 'old-schema'}))
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            self.assertEqual(self.post('drafts/get', {**self.binding(), 'draft_id': self.draft['id']}), before)
        self.draft = self.post('drafts/redo', self.guard())
        self.assertIn('retained edit', self.draft['text'])
        self.assertNotIn('retained edit', self.overlay.read_text())

    def test_existing_sensor_source_still_supports_undo_save_and_reopen(self):
        application = self.workspace / 'legacy'
        application.mkdir()
        (application / 'CMakeLists.txt').write_text('message(FATAL_ERROR "Never configure fixture")\n')
        overlay = application / 'app.overlay'
        original = '/* Existing user device */\n&i2c0 { existing: sensor@76 { compatible = "bosch,bme280"; reg = <0x76>; }; };\n'
        overlay.write_text(original)
        self.context = self.editor.open({'workspace': str(self.workspace), 'application_path': str(application)})
        self.draft = self.drafts.open({**self.binding(), 'document_id': self.context['documents'][0]['id']})
        text = original + '\n/* Preserved user note */\n'
        self.draft = self.post('drafts/source', self.guard(text=text))
        self.draft = self.post('drafts/undo', self.guard())
        self.assertEqual(self.draft['text'], original)
        self.draft = self.post('drafts/redo', self.guard())
        review = self.post('drafts/review', self.guard())
        saved = self.post('drafts/save', self.guard(plan_id=review['plan']['id']))
        self.assertEqual(saved['status'], 'saved', saved)
        self.assertEqual(overlay.read_text(), text)
        self.context = saved['context']
        self.draft = self.post('drafts/open', {**self.binding(), 'document_id': saved['draft']['document_id']})
        self.assertEqual(self.draft['text'], text)
        self.assertIn('existing: sensor@76', overlay.read_text())
