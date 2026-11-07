"""A registered project can author, publish and recover a previously absent overlay."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.features.devicetree.board_sources import BoardSources
from zephyr_utilities.features.devicetree.board_topology import BoardTopologyService

import test_project_context as projects
import test_devicetree_board_topology as boards


class MissingOverlayTests(unittest.TestCase):
    def setUp(self):
        projects.ProjectContextTests.setUp(self)
        (self.source / "app.overlay").unlink()
        shutil.rmtree(self.source / "overlays")
        self.data = self.root / "data"
        self.connect()

    def connect(self):
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = self.app.context.services["devicetree_drafts"]
        self.saving = self.app.context.services["devicetree_saving"]

    def open(self, configuration_id=None):
        self.context = self.editor.open({"workspace": str(self.workspace), "project_id": self.project["id"],
                                         "configuration_id": configuration_id})
        self.document = self.context["editing_document"]
        self.assertIsNotNone(self.document)
        self.draft = self.drafts.open({**self.binding(), "document_id": self.document["id"]})
        self.path = Path(self.document["path"])

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def edit(self, text='&uart0 { status = "okay"; };\n'):
        self.draft = self.drafts.source(self.guard(text=text))

    def save(self):
        review = self.saving.review(self.guard())
        self.assertEqual(review["status"], "ready", review)
        request = self.guard(plan_id=review["plan"]["id"])
        saved = self.saving.save(request)
        self.assertEqual(saved["status"], "saved", saved)
        self.context, self.draft = saved["context"], saved["draft"]
        return review, request, saved

    def plain_project(self):
        self.project.pop("default_overlay")
        # Fixture setup changes only its synthetic saved registration.
        self.creator.store._write(self.creator.store.root / "projects" / (self.project["id"] + ".json"), self.project)
        (self.source / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20.0)\n"
            "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\nproject(sample)\n")

    def test_missing_default_opens_empty_and_first_save_keeps_history_without_rewriting_cmake(self):
        cmake = (self.source / "CMakeLists.txt").read_bytes()
        self.open()
        self.assertEqual(self.document["availability"], "absent")
        self.assertTrue(self.document["can_create"])
        self.assertTrue(self.draft["needs_creation"])
        self.assertTrue(self.draft["dirty"])
        self.assertFalse(self.draft["can_undo"])
        self.assertFalse(self.path.parent.exists())
        self.edit()
        generated = self.draft["text"]
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], "")
        self.assertTrue(self.draft["needs_creation"])
        self.assertFalse(self.path.exists())
        self.draft = self.drafts.redo(self.guard())
        review, request, saved = self.save()
        self.assertEqual(review["plan"]["operation"], "create")
        self.assertEqual(self.path.read_text(), generated)
        self.assertTrue(self.draft["baseline_exists"])
        self.assertFalse(self.draft["dirty"])
        self.assertEqual(self.saving.save(request), saved)
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], "")
        self.assertTrue(self.draft["dirty"])
        self.assertEqual(self.path.read_text(), generated)
        self.assertEqual((self.source / "CMakeLists.txt").read_bytes(), cmake)

    def test_plain_project_first_save_includes_exact_overlay_without_changing_cmake(self):
        self.plain_project()
        cmake = (self.source / "CMakeLists.txt").read_bytes()
        before = deepcopy(self.creator.store.project(self.project["id"], str(self.workspace)))
        self.open()
        self.edit()
        review, _, saved = self.save()
        self.assertTrue(review["plan"]["registration"]["required"])
        self.assertEqual(saved["registration"]["state"], "included")
        after = self.creator.store.project(self.project["id"], str(self.workspace))
        self.assertEqual(after["profile"]["fragments"]["overlays"], {"mode": "append", "files": [self.document["relative_path"]]})
        self.assertEqual(after["revision"], before["revision"] + 1)
        self.assertEqual((self.source / "CMakeLists.txt").read_bytes(), cmake)
        self.open()
        self.assertEqual(self.document["availability"], "available")
        self.assertEqual(self.document["usage"], "predicted")

    def test_blank_save_creates_an_empty_file_and_no_undoable_file_deletion(self):
        self.open()
        review, _, _ = self.save()
        self.assertFalse(review["plan"]["no_changes"])
        self.assertEqual(self.path.read_bytes(), b"")
        self.assertFalse(self.draft["can_undo"])
        self.assertFalse(self.draft["needs_creation"])

    def test_appearing_file_is_never_overwritten_by_first_save(self):
        self.open()
        self.edit()
        review = self.saving.review(self.guard())
        self.path.parent.mkdir()
        self.path.write_text("/* External writer */\n")
        result = self.saving.save(self.guard(plan_id=review["plan"]["id"]))
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.path.read_text(), "/* External writer */\n")
        record = self.drafts.store.read(self.draft["id"])
        self.assertFalse(record["baseline_exists"])
        self.assertEqual(record["text"], self.draft["text"])

    def test_restart_recovers_unsaved_source_and_history_without_creating_destination(self):
        self.open()
        self.edit()
        self.edit(self.draft["text"] + "/* second edit */\n")
        recovery_id, text = self.draft["id"], self.draft["text"]
        self.app.close()
        self.app = Application(self.workspace, self.data)
        self.addCleanup(self.app.close)
        self.connect()
        self.open()
        review = self.saving.recovery_read({"workspace": str(self.workspace), "recovery_id": recovery_id})
        self.assertTrue(review["comparison"]["can_reload"], review)
        restored = self.saving.recovery_restore({**self.binding(), "document_id": self.document["id"],
            "recovery_id": recovery_id, "expected_recovery_revision": review["record"]["revision"],
            "comparison_id": review["comparison"]["comparison_id"], "mode": "keep"})
        self.context, self.draft = restored["context"], restored["draft"]
        self.assertEqual(self.draft["text"], text)
        self.assertTrue(self.draft["needs_creation"])
        self.assertFalse(self.path.parent.exists())
        self.draft = self.drafts.undo(self.guard())
        self.assertNotIn("second edit", self.draft["text"])
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft["text"], text)
        self.save()
        self.assertEqual(self.path.read_text(), text)


class MissingOverlayObservationTests(unittest.TestCase):
    def setUp(self):
        boards.BoardTopologyTests.setUp(self)

    def test_unsaved_overlay_is_observed_without_becoming_a_firmware_file(self):
        draft = replace(self.drafts.drafts[self.draft["id"]], baseline_exists=False)
        self.overlay.unlink()
        context = deepcopy(self.context)
        context["documents"][0].update(availability="absent", can_create=True, baseline_exists=False)
        context["overlay_creation"] = {"path": "app.overlay", "supported": True,
            "input_evidence": {"status": "expected", "files": [{"path": "app.overlay", "state": "expected"}]}}
        context["input_evidence"]["predicted"] = [{"status": "expected", "files": []}]
        worker = boards.BoardTopologyTests.worker(self)
        with patch.object(BoardSources, "run", return_value=worker):
            result = BoardTopologyService.observe(context, draft)
        self.assertTrue(result["payload"]["supported"], result["payload"]["reason"])
        self.assertEqual(result["payload"]["coverage"], "board+unsaved-overlay")
        self.assertFalse(self.overlay.exists())
        self.overlay.write_text("/* raced */")
        with self.assertRaisesRegex(ValueError, "absent"):
            result["sources"].revalidate()

    binding = boards.BoardTopologyTests.binding

