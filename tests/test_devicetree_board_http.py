"""Board observation and inherited commands share authenticated canonical draft HTTP."""

from copy import deepcopy
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from zephyr_utilities.app import create_server
from zephyr_utilities.features.devicetree.board_sources import BoardSources
import test_devicetree_board_connections as connection_fixture
import test_devicetree_board_topology as topology_fixture
import test_projects_api as http_fixture


PREFIX = '/api/devicetree/editor/drafts/'


class BoardConnectionHttpTests(unittest.TestCase):
    requires_loopback = True
    binding = topology_fixture.BoardTopologyTests.binding
    guard = topology_fixture.BoardTopologyTests.guard
    worker = topology_fixture.BoardTopologyTests.worker
    request = http_fixture.ProjectsHttpTests.request

    def setUp(self):
        # The fixture supplies board/environment evidence; all operations below use
        # the composition root's real service instances and shared HTTP transport.
        with patch.object(topology_fixture, 'Application', side_effect=self.make_server):
            topology_fixture.BoardTopologyTests.setUp(self)
        for name, source in connection_fixture.RECIPES.items():
            (self.bindings / name).write_text(source)
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

    def command(self, observation):
        return {'kind': 'board_module_add', 'observation_id': observation['observation_id'],
                'connection': {'kind': 'i2c', 'controller_id': 'hardware:/i2c', 'compatible': 'bosch,bme280',
                               'address': 0x76, 'enable': False, 'name': 'bme280', 'label': 'environment'}}

    def test_board_route_enforces_shared_authentication_origin_and_body_guards(self):
        with patch.object(BoardSources, 'run', side_effect=AssertionError('Rejected requests must not inspect hardware')):
            for extra, expected in (({'authorized': False}, 403), ({'origin': 'https://outside.invalid'}, 403), ({}, 400)):
                with self.subTest(extra=extra), self.assertRaises(HTTPError) as error:
                    self.request(PREFIX + 'board', method='POST', body={}, **extra)
                self.assertEqual(error.exception.code, expected)
                error.exception.close()
        self.assertEqual(self.board.observations, {})
        self.assertEqual(self.drafts.drafts[self.draft['id']].revision, 0)

    def test_read_only_inspection_connection_and_single_undo_use_shared_http(self):
        original = deepcopy(self.draft)
        board_bytes, overlay_bytes = self.board_file.read_bytes(), self.overlay.read_bytes()
        with patch.object(BoardSources, 'run', return_value=self.worker()) as worker, \
                patch.object(self.app.context.jobs, 'submit', side_effect=AssertionError('No Configure or build jobs')):
            observed = self.post('board', self.guard())
            self.assertTrue(observed['supported'], observed['reason'])
            self.assertEqual(observed['controllers'][0]['id'], 'hardware:/i2c')
            self.assertTrue(observed['connection_options']['hardware:/i2c'][0]['addable'])
            self.assertEqual(self.post('get', {**self.binding(), 'draft_id': self.draft['id']})['text'], original['text'])
            self.assertEqual(self.drafts.drafts[self.draft['id']].revision, 0)
            self.assertEqual(self.board_file.read_bytes(), board_bytes)
            self.assertEqual(self.overlay.read_bytes(), overlay_bytes)

            self.draft = self.post('command', self.guard(command=self.command(observed)))
            self.assertEqual(self.draft['revision'], original['revision'] + 1)
            self.assertIn('&i2c0 {', self.draft['text'])
            self.assertIn('environment: bme280@76', self.draft['text'])
            self.assertNotIn('test,i2c', self.draft['text'])
            self.assertTrue(self.draft['can_undo'])
            with self.assertRaises(HTTPError) as error:
                self.post('command', self.guard(command=self.command(observed)))
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            self.draft = self.post('undo', self.guard())
            self.assertEqual(self.draft['text'], original['text'])
            self.assertFalse(self.draft['can_undo'])
            self.assertTrue(self.draft['can_redo'])
            worker.assert_called_once()
        self.assertEqual(self.board_file.read_bytes(), board_bytes)
        self.assertEqual(self.overlay.read_bytes(), overlay_bytes)
        self.assertEqual(self.app.context.jobs.list(), [])

    def test_unavailable_board_is_a_read_only_response_with_a_reason(self):
        captured = self.editor.contexts[self.context['id']].payload
        captured['board'] = None
        captured['environment'] = None
        original = self.draft['text']
        with patch.object(BoardSources, 'run', side_effect=AssertionError('No environment means no worker')):
            observed = self.post('board', self.guard())
        self.assertFalse(observed['supported'])
        self.assertTrue(observed['board']['name'])
        self.assertTrue(observed['reason'])
        self.assertEqual(observed['controllers'], [])
        self.assertEqual(observed['connection_options'], {})
        with self.assertRaises(HTTPError) as error:
            self.post('command', self.guard(command=self.command(observed)))
        self.assertEqual(error.exception.code, 400)
        error.exception.close()
        self.assertEqual(self.post('get', {**self.binding(), 'draft_id': self.draft['id']})['text'], original)
        self.assertEqual(self.overlay.read_text(), original)
        self.assertEqual(self.app.context.jobs.list(), [])
