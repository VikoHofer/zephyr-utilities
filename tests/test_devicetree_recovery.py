"""Durable D4 recovery reconciles source without automatic publication."""

from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.drafts import DraftService
from zephyr_utilities.features.devicetree.publication import OverlayPublisher
from zephyr_utilities.features.devicetree.saving import SavingService
from zephyr_utilities.features.devicetree.service import EditorService

import test_devicetree_saving as fixtures
import test_project_context as project_fixtures


class DevicetreeRecoveryTests(unittest.TestCase):
    setUp = fixtures.DevicetreeSavingTests.setUp
    binding = fixtures.DevicetreeSavingTests.binding
    request = fixtures.DevicetreeSavingTests.request
    edit = fixtures.DevicetreeSavingTests.edit
    review = fixtures.DevicetreeSavingTests.review
    adopt = fixtures.DevicetreeSavingTests.adopt
    save = fixtures.DevicetreeSavingTests.save

    def restart(self):
        self.drafts.close()
        self.editor.close()
        self.saving.close()
        self.editor = EditorService(self.app.context)
        self.publisher = OverlayPublisher(self.storage)
        self.drafts = DraftService(self.editor, storage=self.storage, publisher=self.publisher)
        self.saving = SavingService(self.drafts, self.publisher)
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.document_id})

    def recovery(self, recovery_id):
        return self.saving.recovery_read({"workspace": str(self.workspace), "recovery_id": recovery_id})

    def restore(self, review, *, mode="keep"):
        return self.saving.recovery_restore({**self.binding(), "document_id": self.document_id,
            "recovery_id": review["record"]["id"], "expected_recovery_revision": review["record"]["revision"],
            "comparison_id": review["comparison"]["comparison_id"], "mode": mode})

    def record_path(self, recovery_id):
        return self.storage / "drafts" / f"{recovery_id}.json"

    def test_restart_lists_and_restores_only_after_explicit_action(self):
        original = self.overlay.read_bytes()
        self.edit()
        recovery_id, text = self.draft["id"], self.draft["text"]
        self.restart()
        self.assertEqual(self.draft["text"], fixtures.SOURCE)
        listed = self.saving.recovery_list({"workspace": str(self.workspace)})
        self.assertEqual([entry["id"] for entry in listed["records"]], [recovery_id])
        review = self.recovery(recovery_id)
        self.assertEqual(review["comparison"]["status"], "current")
        self.assertEqual(self.overlay.read_bytes(), original)
        self.adopt(self.restore(review))
        self.assertEqual(self.draft["text"], text)
        self.assertTrue(self.draft["dirty"])
        self.assertEqual(self.overlay.read_bytes(), original)
        self.save()
        self.assertEqual(self.overlay.read_bytes(), text.encode())

    def test_external_change_requires_review_then_explicit_rebase_and_new_save(self):
        self.edit()
        recovery_id, text = self.draft["id"], self.draft["text"]
        external = '&uart0 { status = "reserved"; };\n'
        self.overlay.write_text(external)
        self.restart()
        review = self.recovery(recovery_id)
        self.assertEqual(review["comparison"]["status"], "conflict")
        self.assertEqual(review["comparison"]["current_text"], external)
        self.adopt(self.restore(review))
        self.assertEqual(self.draft["text"], text)
        self.assertTrue(self.draft["dirty"])
        self.assertEqual(self.overlay.read_text(), external)
        self.save()
        self.assertEqual(self.overlay.read_bytes(), text.encode())

    def test_incomplete_source_survives_restart_and_remains_unsaveable(self):
        self.edit('&uart0 { status = "unterminated')
        recovery_id, text = self.draft["id"], self.draft["text"]
        self.restart()
        review = self.recovery(recovery_id)
        self.assertEqual(review["record"]["text"], text)
        self.adopt(self.restore(review))
        self.assertEqual(self.draft["text"], text)
        self.assertEqual(self.draft["nodes"], [])
        self.assertEqual(self.saving.review(self.request())["status"], "blocked")
        self.assertEqual(self.overlay.read_bytes(), fixtures.SOURCE.encode())

    def test_saved_baseline_and_exact_undo_history_survive_explicit_recovery(self):
        self.edit()
        self.save()
        recovery_id, saved_text = self.draft["id"], self.draft["text"]
        self.restart()
        self.adopt(self.restore(self.recovery(recovery_id)))
        self.assertFalse(self.draft["dirty"])
        self.assertTrue(self.draft["can_undo"])
        self.draft = self.drafts.undo(self.request())
        self.assertTrue(self.draft["dirty"])
        self.assertEqual(self.draft["text"], fixtures.SOURCE)
        self.assertEqual(self.overlay.read_bytes(), saved_text.encode())

    def test_restore_refuses_an_existing_edited_session_draft(self):
        self.edit()
        recovery_id = self.draft["id"]
        self.restart()
        self.edit('&uart0 { rate = <19200>; };\n')
        review = self.recovery(recovery_id)
        with self.assertRaisesRegex(ValueError, "already has an edited"):
            self.restore(review)
        comparison = self.saving.compare(self.request())
        self.adopt(self.saving.reload(self.request(mode="discard", comparison_id=comparison["comparison_id"])))
        review = self.recovery(recovery_id)
        self.adopt(self.restore(review))
        self.assertIn('"okay"', self.draft["text"])

    def test_recovery_comparison_detects_changed_record_or_current_file(self):
        self.edit()
        recovery_id = self.draft["id"]
        self.restart()
        review = self.recovery(recovery_id)
        self.overlay.write_text('&uart0 { rate = <19200>; };\n')
        with self.assertRaisesRegex(ValueError, "changed after comparison"):
            self.restore(review)
        self.restart()
        review = self.recovery(recovery_id)
        value = json.loads(self.record_path(recovery_id).read_text())
        value["revision"] += 1
        self.record_path(recovery_id).write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "record changed"):
            self.restore(review)

    def test_replaced_application_context_blocks_restore_but_keeps_exportable_text(self):
        self.edit()
        recovery_id, text = self.draft["id"], self.draft["text"]
        original = self.workspace / "old-app"
        self.application.rename(original)
        self.application.mkdir()
        (self.application / "CMakeLists.txt").write_bytes((original / "CMakeLists.txt").read_bytes())
        self.overlay.write_bytes((original / "app.overlay").read_bytes())
        self.restart()
        review = self.recovery(recovery_id)
        self.assertFalse(review["comparison"]["can_reload"])
        self.assertEqual(review["record"]["text"], text)
        with self.assertRaises(ValueError):
            self.restore(review)

    def test_unknown_schema_and_malformed_records_are_preserved(self):
        self.edit()
        recovery_id = self.draft["id"]
        path = self.record_path(recovery_id)
        original = json.loads(path.read_text())
        variants = [dict(original, schema_version=2), dict(original, observation=None),
                    dict(original, observation=[]), dict(original, context={}),
                    dict(original, document_id=[]), dict(original, undo=None),
                    {key: value for key, value in original.items() if key != "updated_at"},
                    dict(original, context={**original["context"], "project": {}, "configuration": {}})]
        for value in variants:
            with self.subTest(value=value):
                content = json.dumps(value).encode()
                path.write_bytes(content)
                listing = self.saving.recovery_list({"workspace": str(self.workspace)})
                self.assertTrue(listing["diagnostics"])
                with self.assertRaises(ValueError):
                    self.recovery(recovery_id)
                self.assertEqual(path.read_bytes(), content)
                with self.assertRaises(ValueError):
                    self.edit(self.draft["text"] + "\n")
                self.assertEqual(path.read_bytes(), content)
        path.write_bytes(b'{"schema_version": 9, "id": "' + recovery_id.encode() + b'"}')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.edit(self.draft["text"] + "\n")
        self.assertEqual(path.read_bytes(), before)

    def test_recovery_failure_does_not_acknowledge_or_replace_canonical_text(self):
        self.edit()
        previous = deepcopy(self.draft)
        before = self.record_path(self.draft["id"]).read_bytes()
        with patch.object(self.drafts.store, "write", side_effect=OSError("Full volume")):
            with self.assertRaises(OSError):
                self.edit(self.draft["text"].replace("9600", "19200"))
        current = self.drafts.get({**self.binding(), "draft_id": previous["id"]})
        self.assertEqual(current["revision"], previous["revision"])
        self.assertEqual(current["text"], previous["text"])
        self.assertEqual(current["recovery"]["state"], "failed")
        self.assertEqual(self.record_path(previous["id"]).read_bytes(), before)
        self.edit(self.draft["text"].replace("9600", "38400"))
        self.assertEqual(self.draft["recovery"]["state"], "durable")

    def test_storage_bounds_preserve_old_records_and_pending_source(self):
        self.edit()
        previous = self.record_path(self.draft["id"]).read_bytes()
        with patch("zephyr_utilities.features.devicetree.recovery.MAX_STORE_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "storage is full"):
                self.edit(self.draft["text"] + "\n")
        self.assertEqual(self.record_path(self.draft["id"]).read_bytes(), previous)

    def test_workspace_change_during_persistence_prevents_canonical_acknowledgement(self):
        previous = self.draft
        other = self.root / "other"
        other.mkdir()
        write = self.drafts.store.write

        def changed(value):
            write(value)
            self.app.context.workspace.set(other)

        with patch.object(self.drafts.store, "write", side_effect=changed), self.assertRaises(ValueError):
            self.edit()
        self.assertEqual(self.drafts.drafts[previous["id"]].revision, previous["revision"])
        self.assertEqual(self.drafts.drafts[previous["id"]].source.decode(), previous["text"])
        self.assertEqual(self.overlay.read_bytes(), fixtures.SOURCE.encode())

    def test_external_change_during_reload_persistence_does_not_adopt_stale_base(self):
        self.edit()
        previous = self.draft
        comparison = self.saving.compare(self.request())
        write = self.drafts.store.write

        def changed(value):
            write(value)
            self.overlay.write_text('&uart0 { rate = <19200>; };\n')

        with patch.object(self.drafts.store, "write", side_effect=changed), self.assertRaises(ValueError):
            self.saving.reload(self.request(comparison_id=comparison["comparison_id"], mode="discard"))
        self.assertEqual(self.drafts.drafts[previous["id"]].revision, previous["revision"])
        self.assertEqual(self.drafts.drafts[previous["id"]].source.decode(), previous["text"])

    def test_recovery_discard_is_revision_guarded_and_does_not_write_firmware(self):
        self.edit()
        request = {"workspace": str(self.workspace), "recovery_id": self.draft["id"],
                   "expected_recovery_revision": self.draft["revision"]}
        with self.assertRaises(ValueError):
            self.saving.recovery_discard({**request, "expected_recovery_revision": 0})
        result = self.saving.recovery_discard(request)
        self.assertEqual(result["status"], "discarded")
        self.assertFalse(self.record_path(self.draft["id"]).exists())
        self.assertEqual(self.overlay.read_bytes(), fixtures.SOURCE.encode())

    def test_storage_symlinks_and_hardlinked_records_are_not_followed(self):
        self.edit()
        recovery_id = self.draft["id"]
        path = self.record_path(recovery_id)
        other = path.with_suffix(".copy")
        os.link(path, other)
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.recovery(recovery_id)
        other.unlink()
        path.rename(other)
        path.symlink_to(other)
        with self.assertRaises(OSError):
            self.recovery(recovery_id)
        self.assertTrue(other.exists())

    def test_recovery_from_other_workspace_is_not_available(self):
        self.edit()
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        listing = self.saving.recovery_list({"workspace": str(other)})
        self.assertEqual(listing["records"], [])
        with self.assertRaises(ValueError):
            self.saving.recovery_read({"workspace": str(other), "recovery_id": self.draft["id"]})

    def test_changed_registered_configuration_revision_refuses_recovery(self):
        fixture = project_fixtures.ProjectContextTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        named = fixture.configuration()
        storage = fixture.app.context.feature_data("devicetree")
        editor = EditorService(fixture.app.context)
        publisher = OverlayPublisher(storage)
        drafts = DraftService(editor, storage=storage, publisher=publisher)
        saving = SavingService(drafts, publisher)
        request = {"workspace": str(fixture.workspace), "project_id": fixture.project["id"], "configuration_id": named["id"]}
        context = editor.open(request)
        selected = next(doc for doc in context["documents"] if doc["relative_path"] == "app.overlay")
        binding = {"workspace": str(fixture.workspace), "context_id": context["id"], "expected_revision": context["revision"]}
        draft = drafts.open({**binding, "document_id": selected["id"]})
        draft = drafts.source({**binding, "draft_id": draft["id"], "expected_draft_revision": draft["revision"],
                               "expected_draft_sha256": draft["sha256"], "text": draft["text"].replace('"okay"', '"disabled"')})
        fixture.creator.store.update_configuration({**named, "revision": 1}, named)
        current = editor.open(request)
        review = saving.recovery_read({"workspace": str(fixture.workspace), "recovery_id": draft["id"]})
        self.assertFalse(review["comparison"]["can_reload"])
        with self.assertRaises(ValueError):
            saving.recovery_restore({"workspace": str(fixture.workspace), "context_id": current["id"],
                "expected_revision": current["revision"], "document_id": selected["id"],
                "recovery_id": draft["id"], "expected_recovery_revision": draft["revision"],
                "comparison_id": review["comparison"]["comparison_id"], "mode": "keep"})
        self.assertEqual((fixture.source / "app.overlay").read_text(), '&uart0 { status = "okay"; };\n')
