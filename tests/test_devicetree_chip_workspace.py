"""Automatic chip jobs retain context authority and never require an overlay file."""

from concurrent.futures import CancelledError
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
from threading import Event, Thread
import time
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.board_sources import BoardSources
from zephyr_utilities.features.devicetree.chip_workspace import ChipWorkspaceService

import test_devicetree_board_topology as board_fixtures


CATALOG = {"schema_version": 1, "supported": True, "reason": "", "chip": {"name": "Fixture chip"},
           "pins": [{"id": "P0.00", "name": "P0.00", "functions": [], "assignments": []}], "diagnostics": []}


class ChipWorkspaceTests(unittest.TestCase):
    binding = board_fixtures.BoardTopologyTests.binding
    guard = board_fixtures.BoardTopologyTests.guard
    worker = board_fixtures.BoardTopologyTests.worker

    def setUp(self):
        board_fixtures.BoardTopologyTests.setUp(self)
        self.chip = ChipWorkspaceService(self.drafts)
        self.addCleanup(self.chip.close)
        self.catalog_patch = patch('zephyr_utilities.features.devicetree.chip_catalog.observe_chip',
                                   side_effect=lambda *_args, **_kwargs: deepcopy(CATALOG))
        self.catalog = self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

        def run(sources, draft=None):
            sources.read(sources.board_file)
            return self.worker()

        self.run_patch = patch.object(BoardSources, 'run', autospec=True, side_effect=run)
        self.run = self.run_patch.start()
        self.addCleanup(self.run_patch.stop)

    def wait(self, started):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            job = self.app.context.jobs.get(started['job_id'])
            if job['status'] not in {'queued', 'running'}:
                return job
            time.sleep(0.01)
        self.fail('Chip observation did not finish.')

    def result(self, started):
        return self.chip.result({**self.binding(), 'job_id': started['job_id']})

    def test_empty_overlay_loads_chip_in_shared_job_without_source_writes(self):
        before = self.overlay.read_bytes(), self.board_file.read_bytes()
        started = self.chip.start(self.guard())
        self.assertEqual(self.wait(started)['status'], 'completed')
        result = self.result(started)
        self.assertEqual(result['result']['chip']['name'], 'Fixture chip')
        self.assertEqual(result['result']['coverage'], 'board+selected-overlay')
        self.assertEqual(result['result']['draft_id'], self.draft['id'])
        self.assertEqual(result['result']['draft_revision'], 0)
        self.assertEqual(result['result']['selection']['board_source'], str(self.board_file))
        self.assertEqual(self.app.context.jobs.get(started['job_id'])['metadata']['kind'], 'chip-discovery')
        self.assertEqual((self.overlay.read_bytes(), self.board_file.read_bytes()), before)

    def test_resolved_revision_evidence_reaches_the_automatic_chip_result(self):
        original = self.run.side_effect
        revision = {'requested': None, 'effective': '0.14.0', 'default': '0.14.0',
                    'format': 'major.minor.patch', 'match': 'default', 'exact': False}

        def selected(sources, draft=None):
            sources.selection.update(revision=revision, board_overlays=['/fixture/revision.overlay'])
            return original(sources, draft)

        self.run.side_effect = selected
        started = self.chip.start(self.guard())
        self.assertEqual(self.wait(started)['status'], 'completed')
        result = self.result(started)['result']
        self.assertEqual(result['selection']['revision'], revision)
        self.assertEqual(result['selection']['board_overlays'], ['/fixture/revision.overlay'])
        self.assertEqual(result['target'], self.context['target'])

    def test_identical_observation_is_reused_but_changed_source_is_not_cached(self):
        first = self.chip.start(self.guard())
        self.assertEqual(self.wait(first)['status'], 'completed')
        self.assertEqual(self.chip.start(self.guard())['job_id'], first['job_id'])
        self.assertEqual(self.run.call_count, 1)
        self.board_file.write_text(self.board_file.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.result(first)
        second = self.chip.start(self.guard())
        self.assertNotEqual(second['job_id'], first['job_id'])
        self.assertEqual(self.wait(second)['status'], 'completed')
        self.assertEqual(self.run.call_count, 2)

    def test_missing_overlay_still_displays_board_only_and_does_not_create_file(self):
        old = deepcopy(self.context)
        self.overlay.unlink()
        self.context = self.editor.open({'workspace': str(self.workspace), 'application_path': str(self.application)})
        for key in ('target', 'board', 'environment', 'input_evidence'):
            self.context[key] = old[key]
        self.editor.contexts[self.context['id']].payload = deepcopy(self.context)
        started = self.chip.start(self.binding())
        self.assertEqual(self.wait(started)['status'], 'completed')
        result = self.result(started)['result']
        self.assertEqual(result['coverage'], 'board-only')
        self.assertIn('board defaults only', result['assignment_reason'])
        self.assertEqual(result['chip']['name'], 'Fixture chip')
        self.assertIsNone(result['draft_id'])
        self.assertFalse(self.overlay.exists())

    def test_unavailable_observation_is_retried_after_dependency_becomes_available(self):
        original = self.run.side_effect
        self.run.side_effect = ValueError('Missing selected pin source')
        first = self.chip.start(self.guard())
        self.assertEqual(self.wait(first)['status'], 'completed')
        self.assertFalse(self.result(first)['result']['supported'])
        self.run.side_effect = original
        second = self.chip.start(self.guard())
        self.assertNotEqual(first['job_id'], second['job_id'])
        self.assertEqual(self.wait(second)['status'], 'completed')
        self.assertTrue(self.result(second)['result']['supported'])

    def test_application_dependency_repair_retries_a_supported_board_only_fallback(self):
        original = self.run.side_effect

        def missing_application_include(sources, draft=None):
            if draft is not None:
                raise ValueError('Application include file is unavailable')
            return original(sources, draft)

        self.run.side_effect = missing_application_include
        first = self.chip.start(self.guard())
        self.assertEqual(self.wait(first)['status'], 'completed')
        fallback = self.result(first)['result']
        self.assertTrue(fallback['supported'])
        self.assertEqual(fallback['coverage'], 'board-only')
        self.assertIn('Application include file', fallback['assignment_reason'])
        self.run.side_effect = original
        second = self.chip.start(self.guard())
        self.assertNotEqual(first['job_id'], second['job_id'])
        self.assertEqual(self.wait(second)['status'], 'completed')
        self.assertEqual(self.result(second)['result']['coverage'], 'board+selected-overlay')

    def test_invalid_source_retains_text_and_labels_fallback_as_board_only(self):
        self.draft = self.drafts.source(self.guard(text='/ { unfinished {'))
        started = self.chip.start(self.guard())
        self.assertEqual(self.wait(started)['status'], 'completed')
        result = self.result(started)['result']
        self.assertEqual(result['coverage'], 'board-only')
        self.assertIn('Repair incomplete overlay', result['assignment_reason'])
        self.assertEqual(self.drafts.drafts[self.draft['id']].source, b'/ { unfinished {')

    def test_slow_discovery_releases_editor_locks_and_rejects_changed_draft(self):
        entered, release, edited = Event(), Event(), Event()
        original = self.run.side_effect

        def slow(sources, draft=None):
            entered.set()
            release.wait(2)
            return original(sources, draft)

        self.run.side_effect = slow
        started = self.chip.start(self.guard())
        self.assertTrue(entered.wait(1))

        def change():
            self.draft = self.drafts.source(self.guard(text='/* edited while loading */\n'))
            edited.set()

        thread = Thread(target=change)
        thread.start()
        try:
            self.assertTrue(edited.wait(1), 'Discovery held the draft lock while running tools.')
        finally:
            release.set()
            thread.join(2)
        self.assertEqual(self.wait(started)['status'], 'failed')
        with self.assertRaisesRegex(ValueError, 'revision|changed'):
            self.result(started)

    def test_job_cannot_be_read_or_cancelled_from_another_context(self):
        started = self.chip.start(self.guard())
        self.wait(started)
        other = self.editor.open({'workspace': str(self.workspace), 'application_path': str(self.application)})
        body = {'workspace': str(self.workspace), 'context_id': other['id'],
                'expected_revision': other['revision'], 'job_id': started['job_id']}
        for action in (self.chip.result, self.chip.cancel):
            with self.assertRaisesRegex(ValueError, 'another editor context'):
                action(body)

    def test_duplicate_running_requests_share_job_and_workspace_switch_rejects_late_result(self):
        entered, release = Event(), Event()
        original = self.run.side_effect

        def slow(sources, draft=None):
            entered.set()
            release.wait(2)
            return original(sources, draft)

        self.run.side_effect = slow
        started = self.chip.start(self.guard())
        self.assertTrue(entered.wait(1))
        try:
            self.assertEqual(self.chip.start(self.guard())['job_id'], started['job_id'])
            other = self.root / 'other-workspace'
            other.mkdir()
            self.app.context.workspace.set(other)
        finally:
            release.set()
        self.assertEqual(self.wait(started)['status'], 'failed')
        with self.assertRaisesRegex(ValueError, 'workspace changed'):
            self.result(started)
        self.assertEqual(self.run.call_count, 1)

    def test_rapid_draft_revisions_coalesce_and_only_latest_guards_receive_result(self):
        entered, latest, release = Event(), Event(), Event()
        original = self.run.side_effect

        def superseded(sources, draft=None):
            entered.set()
            if draft.revision < 12:
                if sources.cancel.wait(2):
                    raise CancelledError()
                self.fail('A superseded observation was not interrupted.')
            latest.set()
            release.wait(2)
            return original(sources, draft)

        self.run.side_effect = superseded
        old_guard = self.guard()
        started = self.chip.start(old_guard)
        self.assertTrue(entered.wait(1))
        try:
            for revision in range(1, 13):
                self.draft = self.drafts.source(self.guard(text=f'/* revision {revision} */\n'))
                current = self.chip.start(self.guard())
                self.assertEqual(current['job_id'], started['job_id'])
            self.assertTrue(latest.wait(1))
            self.assertEqual(len(self.app.context.jobs.list()), 1)
            for action in (self.chip.result, self.chip.cancel):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'another draft revision'):
                    action({**old_guard, 'job_id': started['job_id']})
            with self.assertRaises(ValueError):
                self.chip.start(old_guard)
            self.assertEqual(self.chip.result({**self.guard(), 'job_id': started['job_id']})['status'], 'running')
        finally:
            release.set()
        self.assertEqual(self.wait(started)['status'], 'completed')
        result = self.chip.result({**self.guard(), 'job_id': started['job_id']})['result']
        self.assertEqual(result['draft_revision'], 12)
        self.assertEqual(result['draft_sha256'], self.draft['sha256'])

    def test_coalescing_draft_work_does_not_cancel_board_only_observation(self):
        board_entered, draft_entered, release = Event(), Event(), Event()
        board_tokens = []
        original = self.run.side_effect

        def work(sources, draft=None):
            if draft is None:
                board_tokens.append(sources.cancel)
                board_entered.set()
                release.wait(2)
            elif draft.revision == 0:
                draft_entered.set()
                if sources.cancel.wait(2):
                    raise CancelledError()
            return original(sources, draft)

        self.run.side_effect = work
        board = self.chip.start(self.binding())
        draft = self.chip.start(self.guard())
        self.assertTrue(board_entered.wait(1))
        self.assertTrue(draft_entered.wait(1))
        try:
            self.draft = self.drafts.source(self.guard(text='/* latest source */\n'))
            current = self.chip.start(self.guard())
            self.assertEqual(current['job_id'], draft['job_id'])
            self.assertFalse(board_tokens[0].is_set())
        finally:
            release.set()
        self.assertEqual(self.wait(board)['status'], 'completed')
        self.assertEqual(self.wait(current)['status'], 'completed')
        self.assertEqual(self.result(board)['result']['coverage'], 'board-only')

    def test_queued_revisions_coalesce_without_taking_other_features_queue_slots(self):
        release = Event()
        entered = [Event(), Event()]
        other_cancellations = []

        def blocker(index):
            def run(emit, cancel):
                other_cancellations.append(cancel)
                entered[index].set()
                release.wait(3)
                return {'finished': True}
            return run

        self.app.context.jobs.submit('projects', blocker(0))
        self.app.context.jobs.submit('memory', blocker(1))
        self.assertTrue(all(event.wait(1) for event in entered))
        try:
            started = self.chip.start(self.guard())
            self.assertEqual(started['status'], 'queued')
            for revision in range(1, 13):
                self.draft = self.drafts.source(self.guard(text=f'/* queued revision {revision} */\n'))
                self.assertEqual(self.chip.start(self.guard())['job_id'], started['job_id'])
            self.assertEqual(len(self.app.context.jobs.list()), 3)
            self.assertFalse(any(event.is_set() for event in other_cancellations))
            self.assertEqual(self.run.call_count, 0)
        finally:
            release.set()
        self.assertEqual(self.wait(started)['status'], 'completed')
        self.assertEqual(self.result(started)['result']['draft_revision'], 12)
        self.assertEqual(self.run.call_count, 1)

    def test_explicit_cancel_stops_latest_coalesced_attempt(self):
        entered = Event()

        def waiting(sources, draft=None):
            entered.set()
            if sources.cancel.wait(2):
                raise CancelledError()
            self.fail('The current attempt did not observe explicit cancellation.')

        self.run.side_effect = waiting
        started = self.chip.start(self.guard())
        self.assertTrue(entered.wait(1))
        self.draft = self.drafts.source(self.guard(text='/* current revision */\n'))
        self.assertEqual(self.chip.start(self.guard())['job_id'], started['job_id'])
        self.chip.cancel({**self.guard(), 'job_id': started['job_id']})
        self.assertEqual(self.wait(started)['status'], 'cancelled')
        self.assertEqual(self.draft['text'], '/* current revision */\n')

    def test_discovery_cancel_signals_worker_and_retains_source(self):
        entered = Event()

        def cancelled(sources, draft=None):
            entered.set()
            if sources.cancel.wait(2):
                raise CancelledError()
            self.fail('Discovery did not observe cancellation.')

        self.run.side_effect = cancelled
        before = self.overlay.read_bytes()
        started = self.chip.start(self.guard())
        self.assertTrue(entered.wait(1))
        self.chip.cancel({**self.binding(), 'job_id': started['job_id']})
        self.assertEqual(self.wait(started)['status'], 'cancelled')
        self.assertEqual(self.overlay.read_bytes(), before)

    def test_unknown_context_and_partial_draft_guards_do_not_submit_jobs(self):
        for body in ({**self.binding(), 'draft_id': self.draft['id']},
                     {**self.guard(), 'context_id': 'f' * 32}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.chip.start(body)
        self.assertEqual(self.app.context.jobs.list(), [])


class ChipWorkerCancellationTests(unittest.TestCase):
    def test_cancellation_stops_running_preprocessor_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / 'pid'
            cancel, errors = Event(), []

            def run():
                try:
                    BoardSources._run([sys.executable, '-c',
                                       'import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)',
                                       str(marker)], temporary, cancel=cancel)
                except Exception as exc:
                    errors.append(exc)

            thread = Thread(target=run)
            thread.start()
            deadline = time.monotonic() + 3
            try:
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists(), 'Tool process did not start.')
            finally:
                cancel.set()
                thread.join(2)
            self.assertFalse(thread.is_alive(), 'Cancellation did not stop the tool promptly.')
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], CancelledError)
            with self.assertRaises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)
