"""Creation Save and recovery share one source/history without conflating absence."""

from copy import deepcopy
import hashlib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from zephyr_utilities.features.devicetree.drafts import DraftService
from zephyr_utilities.features.devicetree.saving import SavingService

import test_devicetree_absent_drafts as draft_fixtures


class OverlayCreationSavingTests(unittest.TestCase):
    binding = draft_fixtures.AbsentDraftTests.binding
    open = draft_fixtures.AbsentDraftTests.open
    guard = draft_fixtures.AbsentDraftTests.guard

    def setUp(self):
        draft_fixtures.AbsentDraftTests.setUp(self)
        self.selected.update(relative_path="overlays/demo_chip.overlay", usage="planned")
        self.template = deepcopy(self.context)
        self.editor.context = SimpleNamespace(workspace=SimpleNamespace(get=lambda: {"path": str(self.workspace)}))
        self.editor.open = self.fresh
        self.editor.select = self.current
        self.editor.current = self.current
        self.editor.review_creation = Mock(return_value=None)
        self.editor._workspace = lambda body, _fields: body["workspace"]
        self.editor.contexts[self.context["id"]].payload = deepcopy(self.context)
        self.saving = SavingService(self.drafts, self.publisher)
        self.draft = self.open()

    def current(self, body):
        selected = deepcopy(self.editor.contexts[body["context_id"]].payload)
        observed = self.publisher.observe(self.workspace, self.path, allow_absent=True)
        document = selected["documents"][0]
        if (document["availability"] == "absent") != (observed.get("exists", True) is False) or document["sha256"] != observed["sha256"]:
            raise ValueError("The overlay changed. Open it again.")
        return selected

    def fresh(self, _body):
        context = deepcopy(self.template)
        observed = self.publisher.observe(self.workspace, self.path, allow_absent=True)
        selected = context["documents"][0]
        selected.update(availability="available" if observed.get("exists", True) else "absent",
                        sha256=observed["sha256"], text=observed["text"])
        context.update(id=uuid.uuid4().hex, fingerprint="existing" if observed.get("exists", True) else "selection")
        self.editor.contexts[context["id"]] = SimpleNamespace(payload=deepcopy(context), source_guard={"directory": [1, 2]})
        return context

    def request(self, **extra):
        return self.guard(self.draft, **extra)

    def edit(self, text='&uart0 { status = "okay"; };\n'):
        self.draft = self.drafts.source(self.request(text=text))

    def reviewed(self):
        review = self.saving.review(self.request())
        self.assertEqual(review["status"], "ready", review)
        return self.request(plan_id=review["plan"]["id"]), review["plan"]

    def adopt(self, result):
        self.context, self.draft = result["context"], result["draft"]
        return result

    def test_empty_draft_can_be_created_and_followup_save_is_noop(self):
        request, plan = self.reviewed()
        self.assertEqual(plan["operation"], "create")
        self.assertFalse(plan["no_changes"])
        self.assertFalse(self.path.parent.exists())
        result = self.adopt(self.saving.save(request))
        self.assertEqual(result["status"], "saved", result)
        self.assertEqual(self.path.read_bytes(), b"")
        self.assertTrue(self.draft["baseline_exists"])
        self.assertFalse(self.draft["dirty"])
        inode = self.path.stat().st_ino
        request, plan = self.reviewed()
        self.assertEqual(plan["operation"], "replace")
        self.assertTrue(plan["no_changes"])
        self.assertEqual(self.saving.save(request)["status"], "unchanged")
        self.assertEqual(self.path.stat().st_ino, inode)

    def test_creation_keeps_undo_redo_and_retry_uses_receipt(self):
        self.edit()
        text = self.draft["text"]
        request, _ = self.reviewed()
        result = self.adopt(self.saving.save(request))
        self.assertEqual(result["status"], "saved", result)
        with patch.object(self.publisher, "publish", side_effect=AssertionError("No replay")):
            self.assertEqual(self.saving.save(request), result)
        self.draft = self.drafts.undo(self.request())
        self.assertEqual(self.draft["text"], "")
        self.assertTrue(self.draft["baseline_exists"])
        self.assertTrue(self.draft["dirty"])
        self.assertEqual(self.path.read_text(), text)
        self.draft = self.drafts.redo(self.request())
        self.assertEqual(self.draft["text"], text)
        self.assertFalse(self.draft["dirty"])

    def test_appeared_empty_file_requires_explicit_reconciliation(self):
        self.edit()
        request, _ = self.reviewed()
        self.path.parent.mkdir()
        self.path.write_bytes(b"")
        result = self.saving.save(request)
        self.assertEqual(result["status"], "conflict")
        self.assertFalse(result["comparison"]["baseline_exists"])
        self.assertTrue(result["comparison"]["current_exists"])
        self.assertEqual(result["comparison"]["status"], "conflict")
        self.assertEqual(self.path.read_bytes(), b"")
        self.assertFalse(self.drafts.drafts[self.draft["id"]].baseline_exists)
        self.assertTrue(self.draft["text"])

    def test_absent_recovery_restores_history_without_creating_any_directory(self):
        self.edit()
        text = self.draft["text"]
        self.draft = self.drafts.undo(self.request())
        record_id = self.draft["id"]
        self.saving.close()
        self.drafts.close()
        self.drafts = DraftService(self.editor, storage=self.root / "data", publisher=self.publisher)
        self.addCleanup(self.drafts.close)
        self.saving = SavingService(self.drafts, self.publisher)
        listing = self.saving.recovery_list({"workspace": str(self.workspace)})
        self.assertTrue(listing["records"][0]["needs_creation"])
        review = self.saving.recovery_read({"workspace": str(self.workspace), "recovery_id": record_id})
        self.assertFalse(review["comparison"]["current_exists"])
        self.assertTrue(review["comparison"]["can_reload"])
        result = self.saving.recovery_restore({**self.binding(), "document_id": self.selected["id"],
            "recovery_id": record_id, "expected_recovery_revision": review["record"]["revision"],
            "comparison_id": review["comparison"]["comparison_id"], "mode": "keep"})
        self.adopt(result)
        self.assertFalse(self.draft["baseline_exists"])
        self.assertTrue(self.draft["can_redo"])
        self.assertFalse(self.path.parent.exists())
        self.draft = self.drafts.redo(self.request())
        self.assertEqual(self.draft["text"], text)
        self.assertFalse(self.path.exists())

    def test_recovery_into_appeared_empty_file_does_not_reuse_absent_history(self):
        self.edit()
        record_id = self.draft["id"]
        self.drafts.close()
        self.path.parent.mkdir()
        self.path.write_bytes(b"")
        self.context = self.fresh({})
        self.drafts = DraftService(self.editor, storage=self.root / "data", publisher=self.publisher)
        self.addCleanup(self.drafts.close)
        self.saving = SavingService(self.drafts, self.publisher)
        review = self.saving.recovery_read({"workspace": str(self.workspace), "recovery_id": record_id})
        self.assertEqual(review["comparison"]["status"], "conflict")
        restored = self.saving.recovery_restore({**self.binding(), "document_id": self.selected["id"],
            "recovery_id": record_id, "expected_recovery_revision": review["record"]["revision"],
            "comparison_id": review["comparison"]["comparison_id"], "mode": "keep"})
        self.assertTrue(restored["draft"]["baseline_exists"])
        self.assertFalse(restored["draft"]["can_undo"])
        self.assertTrue(restored["draft"]["dirty"])
        self.assertEqual(self.path.read_bytes(), b"")

    def test_registration_failure_keeps_saved_file_and_retry_never_republishes(self):
        self.edit()
        registration = {"id": uuid.uuid4().hex, "required": True, "state": "planned", "message": "Include this overlay."}
        self.editor.review_creation.return_value = registration
        self.editor.register_creation = Mock(side_effect=ValueError("Configuration temporarily unavailable"))
        request, plan = self.reviewed()
        self.assertEqual(plan["registration"], registration)
        result = self.adopt(self.saving.save(request))
        self.assertEqual(result["status"], "saved", result)
        self.assertEqual(result["registration"]["state"], "failed")
        self.assertIn("inclusion", result["message"])
        self.assertEqual(self.path.read_text(), self.draft["text"])
        self.assertTrue(self.draft["can_undo"])
        inode = self.path.stat().st_ino
        self.editor.register_creation.side_effect = lambda *_args: {"state": "included", "message": "Included.", "context": self.fresh({})}
        with patch.object(self.publisher, "publish", side_effect=AssertionError("No replay")):
            retried = self.adopt(self.saving.save(request))
        self.assertEqual(retried["registration"]["state"], "included")
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertEqual(self.editor.register_creation.call_count, 2)
        self.assertTrue(self.draft["can_undo"])

    def test_registration_retry_cannot_discard_new_source_edits(self):
        self.edit()
        self.editor.review_creation.return_value = {"id": uuid.uuid4().hex, "required": True}
        self.editor.register_creation = Mock(side_effect=ValueError("Temporary failure"))
        request, _ = self.reviewed()
        self.adopt(self.saving.save(request))
        self.edit('/* Later source */\n')
        current = self.draft["text"]
        with self.assertRaisesRegex(ValueError, "draft changed"):
            self.saving.save(request)
        self.assertEqual(self.drafts.drafts[self.draft["id"]].source.decode(), current)
        self.assertNotEqual(hashlib.sha256(current.encode()).hexdigest(), self.publisher.observe(self.workspace, self.path)["sha256"])

    def test_registration_review_error_is_visible_and_writes_nothing(self):
        self.editor.review_creation.side_effect = ValueError("Configuration changed")
        result = self.saving.review(self.request())
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Configuration changed", result["message"])
        self.assertFalse(self.path.parent.exists())
