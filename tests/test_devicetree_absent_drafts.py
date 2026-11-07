"""An absent overlay has durable creation intent and ordinary source history."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
from threading import RLock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from zephyr_utilities.features.devicetree.drafts import DraftService
from zephyr_utilities.features.devicetree.publication import OverlayPublisher


class AbsentDraftTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.application.mkdir(parents=True)
        self.path = self.application / "overlays" / "demo_chip.overlay"
        self.selected = {"id": uuid.uuid4().hex, "path": str(self.path), "availability": "absent",
                         "can_create": True, "sha256": hashlib.sha256(b"").hexdigest(), "text": ""}
        self.context = {"id": uuid.uuid4().hex, "revision": 1, "workspace": str(self.workspace),
                        "workspace_identity": [self.workspace.stat().st_dev, self.workspace.stat().st_ino],
                        "fingerprint": "selection", "application": {"path": str(self.application)},
                        "documents": [self.selected], "project": None}
        self.editor = SimpleNamespace(lock=RLock(), current=Mock(side_effect=lambda _body: deepcopy(self.context)),
                                      document=Mock(side_effect=lambda _body: {"document": deepcopy(self.selected)}),
                                      contexts={self.context["id"]: SimpleNamespace(source_guard={"directory": [1, 2]})})
        self.publisher = OverlayPublisher(self.root / "data")
        self.drafts = DraftService(self.editor, storage=self.root / "data", publisher=self.publisher)
        self.addCleanup(self.drafts.close)

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": 1}

    def open(self):
        return self.drafts.open({**self.binding(), "document_id": self.selected["id"]})

    def guard(self, draft, **extra):
        return {**self.binding(), "draft_id": draft["id"], "expected_draft_revision": draft["revision"],
                "expected_draft_sha256": draft["sha256"], **extra}

    def restart(self):
        self.drafts.close()
        self.context["id"] = uuid.uuid4().hex
        self.editor.contexts[self.context["id"]] = SimpleNamespace(source_guard={"directory": [1, 2]})
        self.drafts = DraftService(self.editor, storage=self.root / "data", publisher=self.publisher)
        self.addCleanup(self.drafts.close)

    def test_open_creates_only_a_durable_empty_draft_and_reuses_it(self):
        draft = self.open()
        self.assertFalse(self.path.parent.exists())
        self.assertFalse(draft["baseline_exists"])
        self.assertTrue(draft["needs_creation"])
        self.assertTrue(draft["dirty"])
        self.assertEqual((draft["text"], draft["revision"], draft["can_undo"], draft["can_redo"]), ("", 0, False, False))
        self.assertTrue(draft["capabilities"]["edit"])
        self.assertTrue(draft["capabilities"]["save"])
        self.assertEqual(self.open(), draft)
        records, errors = self.drafts.store.records(str(self.workspace))
        self.assertEqual(errors, [])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["schema_version"], 2)
        self.assertIs(records[0]["baseline_exists"], False)
        self.assertEqual(records[0]["observation"]["missing_directories"], [str(self.path.parent)])

    def test_undo_to_blank_keeps_creation_identity_and_redo_history_durable(self):
        draft = self.open()
        text = '&uart0 { status = "okay"; };\n'
        edited = self.drafts.source(self.guard(draft, text=text))
        undone = self.drafts.undo(self.guard(edited))
        self.assertEqual(undone["text"], "")
        self.assertTrue(undone["dirty"])
        self.assertTrue(undone["needs_creation"])
        record = self.drafts.store.read(undone["id"])
        self.assertEqual(record["text"], "")
        self.assertEqual(record["redo"], [text])
        self.assertEqual(record["undo"], [])
        redone = self.drafts.redo(self.guard(undone))
        self.assertEqual(redone["text"], text)
        self.assertFalse(redone["baseline_exists"])
        self.assertFalse(self.path.exists())

    def test_reopening_untouched_creation_reuses_recovery_even_at_its_record_limit(self):
        draft = self.open()
        with patch("zephyr_utilities.features.devicetree.recovery.MAX_RECORDS", 1):
            for _ in range(3):
                self.restart()
                reopened = self.open()
                self.assertEqual(reopened["id"], draft["id"])
                self.assertEqual((reopened["revision"], reopened["text"]), (0, ""))
                record = self.drafts.store.read(draft["id"])
                self.assertEqual(record["context"]["id"], self.context["id"])
                self.assertEqual(len(self.drafts.store.records(str(self.workspace))[0]), 1)
        self.assertFalse(self.path.parent.exists())

    def test_reopening_never_reuses_edited_or_undone_empty_recovery(self):
        draft = self.open()
        edited = self.drafts.source(self.guard(draft, text='&uart0 { status = "okay"; };\n'))
        edited_record = self.drafts.store.read(edited["id"])
        self.restart()
        self.assertNotEqual(self.open()["id"], edited["id"])
        self.assertEqual(self.drafts.store.read(edited["id"]), edited_record)
        current = self.open()
        changed = self.drafts.source(self.guard(current, text='&uart0 { status = "disabled"; };\n'))
        undone = self.drafts.undo(self.guard(changed))
        undone_record = self.drafts.store.read(undone["id"])
        self.restart()
        self.assertNotEqual(self.open()["id"], undone["id"])
        self.assertEqual(self.drafts.store.read(undone["id"]), undone_record)

    def test_reopening_different_selection_does_not_reuse_pristine_recovery(self):
        draft = self.open()
        before = self.drafts.store.read(draft["id"])
        self.restart()
        self.context["fingerprint"] = "different-selection"
        self.assertNotEqual(self.open()["id"], draft["id"])
        self.assertEqual(self.drafts.store.read(draft["id"]), before)

    def test_empty_file_appearing_before_open_cannot_be_treated_as_absent(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "changed while opening"):
            self.open()
        self.assertEqual(self.drafts.drafts, {})
        self.assertEqual(self.path.read_bytes(), b"")

    def test_absent_and_empty_existing_baselines_never_share_a_draft_key(self):
        draft = self.open()
        self.selected.update(availability="available")
        with self.assertRaisesRegex(ValueError, "different or changed"):
            self.drafts.get({**self.binding(), "draft_id": draft["id"]})
        self.assertEqual(self.drafts.drafts[draft["id"]].source, b"")

    def test_new_draft_recovery_failure_does_not_acknowledge_a_session_draft(self):
        with patch.object(self.drafts.store, "write", side_effect=OSError("Recovery volume full")):
            with self.assertRaisesRegex(OSError, "volume full"):
                self.open()
        self.assertEqual(self.drafts.drafts, {})
        self.assertFalse(self.path.parent.exists())

    def test_ordinary_missing_document_does_not_authorize_creation(self):
        for changes in ({"availability": "missing"}, {"can_create": False}):
            original = deepcopy(self.selected)
            self.selected.update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "available document"):
                self.open()
            self.selected.clear()
            self.selected.update(original)
        self.assertFalse(self.path.parent.exists())
