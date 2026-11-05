"""D4 reviewed Save and explicit reconciliation preserve bytes and context identity."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.features.devicetree.drafts import DraftService
from zephyr_utilities.features.devicetree.publication import OverlayPublisher
from zephyr_utilities.features.devicetree.saving import SavingService
from zephyr_utilities.features.devicetree.service import EditorService

import test_project_context as project_fixtures


SOURCE = '/* Temperature */\r\n&uart0 { status = "disabled"; rate = <9600>; };\r\n'


class DevicetreeSavingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.application.mkdir(parents=True)
        (self.application / "CMakeLists.txt").write_text('message(FATAL_ERROR "Never execute")\n')
        self.overlay = self.application / "app.overlay"
        self.overlay.write_bytes(SOURCE.encode())
        self.app = Application(self.workspace, self.root / "data", features=())
        self.addCleanup(self.app.close)
        self.storage = self.app.context.feature_data("devicetree")
        self.editor = EditorService(self.app.context)
        self.publisher = OverlayPublisher(self.storage)
        self.drafts = DraftService(self.editor, storage=self.storage, publisher=self.publisher)
        self.saving = SavingService(self.drafts, self.publisher)
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.document_id = self.context["documents"][0]["id"]
        self.draft = self.drafts.open({**self.binding(), "document_id": self.document_id})

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"],
                "expected_revision": self.context["revision"]}

    def request(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"],
                "expected_draft_revision": self.draft["revision"], "expected_draft_sha256": self.draft["sha256"], **extra}

    def edit(self, text=None):
        self.draft = self.drafts.source(self.request(text=text or self.draft["text"].replace('"disabled"', '"okay"')))
        return self.draft

    def review(self):
        result = self.saving.review(self.request())
        self.assertEqual(result["status"], "ready", result)
        return result["plan"]

    def adopt(self, result):
        self.context, self.draft = result["context"], result["draft"]
        return result

    def save(self):
        plan = self.review()
        result = self.saving.save(self.request(plan_id=plan["id"]))
        self.assertIn(result["status"], {"saved", "unchanged"}, result)
        return self.adopt(result)

    def test_review_save_reopen_exact_bytes_no_configuration_or_jobs(self):
        self.edit()
        before = self.overlay.read_bytes()
        with patch("subprocess.run", side_effect=AssertionError("No tools")), \
                patch("subprocess.Popen", side_effect=AssertionError("No tools")), \
                patch.object(self.app.context.jobs, "submit", side_effect=AssertionError("No jobs")):
            plan = self.review()
            self.assertEqual(plan["candidate_text"], self.draft["text"])
            self.assertEqual(plan["validation"]["state"], "unvalidated")
            self.assertEqual(self.overlay.read_bytes(), before)
            result = self.adopt(self.saving.save(self.request(plan_id=plan["id"])))
        self.assertEqual(result["status"], "saved", result)
        self.assertFalse(self.draft["dirty"])
        self.assertEqual(self.overlay.read_bytes(), self.draft["text"].encode())
        self.assertIn(b"\r\n", self.overlay.read_bytes())
        reopened = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        draft = self.drafts.open({"workspace": str(self.workspace), "context_id": reopened["id"],
                                 "expected_revision": reopened["revision"], "document_id": self.document_id})
        self.assertEqual(draft["id"], self.draft["id"])
        self.assertEqual(draft["text"], self.draft["text"])

    def test_acknowledgement_retry_uses_receipt_before_obsolete_context(self):
        self.edit()
        plan = self.review()
        request = self.request(plan_id=plan["id"])
        result = self.saving.save(request)
        with patch.object(self.publisher, "publish", side_effect=AssertionError("No replay")):
            self.assertEqual(self.saving.save(request), result)
        with self.assertRaises(ValueError):
            self.saving.save({**request, "expected_draft_revision": request["expected_draft_revision"] + 1})

    def test_repeated_save_is_noop_and_undo_after_save_is_unsaved_inverse(self):
        self.edit()
        first = self.save()
        inode, revision = self.overlay.stat().st_ino, self.draft["revision"]
        second = self.save()
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(self.overlay.stat().st_ino, inode)
        self.assertEqual(self.draft["revision"], revision)
        self.draft = self.drafts.undo(self.request())
        self.assertTrue(self.draft["dirty"])
        self.assertEqual(self.draft["text"], SOURCE)
        self.assertEqual(self.overlay.read_bytes(), first["draft"]["text"].encode())
        self.save()
        self.assertEqual(self.overlay.read_bytes(), SOURCE.encode())

    def test_opaque_blocked_source_can_save_but_incomplete_source_cannot(self):
        for text in ('#if CONFIG_TEST\n&uart0 { rate = <MACRO(1)>; };\n#endif\n',
                     '\ufeff&uart0 { status = "okay"; };\n'):
            self.edit(text)
            self.assertEqual(self.draft["syntax"]["state"], "opaque")
            self.assertTrue(self.draft["syntax"]["blocked"])
            self.assertTrue(self.draft["capabilities"]["save"])
            self.save()
            self.assertEqual(self.overlay.read_bytes(), text.encode())
        before = self.overlay.read_bytes()
        self.edit('&uart0 { status = "unterminated')
        self.assertFalse(self.draft["capabilities"]["save"])
        self.assertEqual(self.saving.review(self.request())["status"], "blocked")
        self.assertEqual(self.overlay.read_bytes(), before)

    def test_external_change_blocks_save_and_comparison_keeps_three_sources(self):
        self.edit()
        plan = self.review()
        external = '&uart0 { status = "reserved"; };\n'
        self.overlay.write_text(external)
        result = self.saving.save(self.request(plan_id=plan["id"]))
        self.assertEqual(result["status"], "conflict")
        comparison = result["comparison"]
        self.assertEqual(comparison["base_text"], SOURCE)
        self.assertEqual(comparison["text"], self.draft["text"])
        self.assertEqual(comparison["current_text"], external)
        self.assertEqual(self.overlay.read_text(), external)
        self.assertTrue(comparison["can_reload"])

    def test_identical_byte_inode_replacement_requires_explicit_rebase(self):
        self.edit()
        replacement = self.application / "replacement"
        replacement.write_bytes(self.overlay.read_bytes())
        replacement.replace(self.overlay)
        reviewed = self.saving.review(self.request())
        self.assertEqual(reviewed["status"], "conflict")
        comparison = reviewed["comparison"]
        self.adopt(self.saving.reload(self.request(comparison_id=comparison["comparison_id"], mode="keep")))
        self.assertTrue(self.draft["dirty"])
        self.save()

    def test_comparison_guard_refuses_unseen_replacements_and_expiry(self):
        self.edit()
        comparison = self.saving.compare(self.request())
        replacement = self.application / "replacement"
        replacement.write_bytes(self.overlay.read_bytes())
        replacement.replace(self.overlay)
        with self.assertRaisesRegex(ValueError, "changed after comparison"):
            self.saving.reload(self.request(comparison_id=comparison["comparison_id"], mode="discard"))
        comparison = self.saving.compare(self.request())
        self.saving.comparisons[comparison["comparison_id"]]["deadline"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.saving.reload(self.request(comparison_id=comparison["comparison_id"], mode="discard"))

    def test_reload_discard_clears_history_and_keep_requires_new_save_review(self):
        self.edit()
        self.overlay.write_text('&uart0 { rate = <19200>; };\n')
        comparison = self.saving.compare(self.request())
        local = self.draft["text"]
        self.adopt(self.saving.reload(self.request(comparison_id=comparison["comparison_id"], mode="keep")))
        self.assertEqual(self.draft["text"], local)
        self.assertTrue(self.draft["dirty"])
        self.assertEqual(self.overlay.read_text(), '&uart0 { rate = <19200>; };\n')
        comparison = self.saving.compare(self.request())
        self.adopt(self.saving.reload(self.request(comparison_id=comparison["comparison_id"], mode="discard")))
        self.assertFalse(self.draft["dirty"])
        self.assertFalse(self.draft["can_undo"])
        self.assertFalse(self.draft["can_redo"])

    def test_missing_readonly_and_relocated_sources_retain_draft(self):
        self.edit()
        self.overlay.chmod(0o444)
        self.assertEqual(self.saving.review(self.request())["status"], "conflict")
        self.overlay.chmod(0o644)
        self.overlay.unlink()
        comparison = self.saving.compare(self.request())
        self.assertEqual(comparison["status"], "unavailable")
        self.assertFalse(comparison["can_reload"])
        self.assertEqual(comparison["text"], self.draft["text"])
        self.overlay.write_bytes(SOURCE.encode())
        self.application.rename(self.workspace / "moved-app")
        self.assertEqual(self.saving.compare(self.request())["status"], "unavailable")

    def test_application_replacement_and_cmake_change_block_rebase(self):
        self.edit()
        (self.application / "CMakeLists.txt").write_text('project(other)\n')
        comparison = self.saving.compare(self.request())
        self.assertFalse(comparison["can_reload"])
        self.assertIn("context", comparison["message"])

    def test_stale_or_changed_plan_and_malformed_modes_do_not_publish(self):
        self.edit()
        plan = self.review()
        original = self.request(plan_id=plan["id"])
        self.edit(self.draft["text"].replace("9600", "19200"))
        with self.assertRaises(ValueError):
            self.saving.save(original)
        with self.assertRaises(ValueError):
            self.saving.save(self.request(plan_id=plan["id"]))
        self.assertEqual(self.overlay.read_bytes(), SOURCE.encode())
        for mode in ({}, [], None):
            with self.assertRaises(ValueError):
                self.saving.reload(self.request(mode=mode, comparison_id="a" * 32))

    def test_prepublication_failure_keeps_source_and_draft(self):
        self.edit()
        plan = self.review()
        with patch.object(self.publisher, "publish", side_effect=OSError("No space")):
            result = self.saving.save(self.request(plan_id=plan["id"]))
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.overlay.read_bytes(), SOURCE.encode())
        self.assertEqual(self.drafts.drafts[self.draft["id"]].source.decode(), self.draft["text"])

    def test_uncertain_publication_consumes_plan_and_never_replays(self):
        self.edit()
        plan = self.review()
        request = self.request(plan_id=plan["id"])
        receipt = {"status": "uncertain", "message": "Inspect retained versions", "id": plan["id"]}
        with patch.object(self.publisher, "publish", return_value=receipt) as publish:
            result = self.saving.save(request)
            self.assertEqual(self.saving.save(request), result)
            publish.assert_called_once()
        self.assertEqual(result["receipt"]["status"], "uncertain")

    def test_saved_file_recovery_update_failure_is_reported_truthfully(self):
        self.edit()
        plan = self.review()
        with patch.object(self.drafts.store, "write", side_effect=OSError("Full recovery volume")):
            result = self.saving.save(self.request(plan_id=plan["id"]))
        self.assertEqual(result["status"], "saved")
        self.assertEqual(result["draft"]["recovery"]["state"], "failed")
        self.assertIn("overlay was saved", result["message"])
        self.assertEqual(self.overlay.read_bytes(), self.draft["text"].encode())

    def test_workspace_change_after_publication_retains_receipt_without_replay(self):
        self.edit()
        plan = self.review()
        request = self.request(plan_id=plan["id"])
        other = self.root / "other"
        other.mkdir()
        write = self.drafts.store.write

        def switched(value):
            write(value)
            self.app.context.workspace.set(other)

        with patch.object(self.drafts.store, "write", side_effect=switched):
            result = self.saving.save(request)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["receipt"]["status"], "saved")
        self.assertEqual(self.overlay.read_bytes(), self.draft["text"].encode())
        self.app.context.workspace.set(self.workspace)
        with patch.object(self.publisher, "publish", side_effect=AssertionError("No replay")):
            self.assertEqual(self.saving.save(request), result)

    def test_registered_configurations_compete_for_same_file(self):
        fixture = project_fixtures.ProjectContextTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        named = fixture.configuration()
        editor = EditorService(fixture.app.context)
        publisher = OverlayPublisher(fixture.app.context.feature_data("devicetree"))
        drafts = DraftService(editor, storage=fixture.app.context.feature_data("devicetree"), publisher=publisher)
        saving = SavingService(drafts, publisher)
        requests = []
        for configuration in (None, named["id"]):
            context = editor.open({"workspace": str(fixture.workspace), "project_id": fixture.project["id"],
                                   "configuration_id": configuration})
            selected = next(doc for doc in context["documents"] if doc["relative_path"] == "app.overlay")
            binding = {"workspace": str(fixture.workspace), "context_id": context["id"], "expected_revision": context["revision"]}
            draft = drafts.open({**binding, "document_id": selected["id"]})
            def guard(value):
                return {**binding, "draft_id": value["id"], "expected_draft_revision": value["revision"],
                        "expected_draft_sha256": value["sha256"]}
            draft = drafts.source({**guard(draft), "text": draft["text"].replace('"okay"', '"disabled"')})
            request = guard(draft)
            plan = saving.review(request)["plan"]
            requests.append({**request, "plan_id": plan["id"]})
        self.assertEqual(saving.save(requests[0])["status"], "saved")
        self.assertEqual(saving.save(requests[1])["status"], "conflict")
        self.assertEqual(fixture.creator.store.configuration(named["id"], fixture.project["id"], str(fixture.workspace)), named)

    def test_plan_expiry_and_workspace_switch_refuse_publication(self):
        self.edit()
        plan = self.review()
        self.saving.plans[plan["id"]]["deadline"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.saving.save(self.request(plan_id=plan["id"]))
        plan = self.review()
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaises(ValueError):
            self.saving.save(self.request(plan_id=plan["id"]))
        self.assertEqual(self.overlay.read_bytes(), SOURCE.encode())

    def test_caller_cannot_edit_server_plan_or_use_foreign_context(self):
        self.edit()
        plan = self.review()
        plan["candidate_text"] = "unreviewed"
        context = deepcopy(self.context)
        context["fingerprint"] = "foreign"
        self.editor.contexts[self.context["id"]].payload = context
        with self.assertRaises(ValueError):
            self.saving.save(self.request(plan_id=plan["id"]))
        self.assertEqual(self.overlay.read_bytes(), SOURCE.encode())
