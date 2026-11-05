"""D3 drafts retain source and history without changing firmware or context identity."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.features.devicetree.drafts import DraftService
from zephyr_utilities.features.devicetree.preview import PREVIEW_FEATURE
from zephyr_utilities.features.devicetree.service import EditorService

import test_project_context as project_fixtures


SOURCE = '/* 温度 */\r\n&uart0 { status = "disabled"; rate = <9600>; flag; };\r\n'


class DevicetreeDraftTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.application.mkdir(parents=True)
        (self.application / "CMakeLists.txt").write_text('message(FATAL_ERROR "Do not execute")\n')
        self.overlay = self.application / "app.overlay"
        self.overlay.write_bytes(SOURCE.encode("utf-8"))
        self.app = Application(self.workspace, self.root / "data", features=(PREVIEW_FEATURE,))
        self.addCleanup(self.app.close)
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = DraftService(self.editor)
        self.addCleanup(self.drafts.close)
        self.context = self.open_context()
        self.document_id = self.context["documents"][0]["id"]

    def open_context(self):
        return self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})

    def binding(self, context=None):
        context = context or self.context
        return {"workspace": context["workspace"], "context_id": context["id"], "expected_revision": context["revision"]}

    def open(self, *, service=None, context=None, document_id=None):
        return (service or self.drafts).open({**self.binding(context), "document_id": document_id or self.document_id})

    def request(self, draft, **extra):
        return {**self.binding(), "draft_id": draft["id"], "expected_draft_revision": draft["revision"],
                "expected_draft_sha256": draft["sha256"], **extra}

    def source(self, draft, text, *, service=None):
        return (service or self.drafts).source(self.request(draft, text=text))

    def command(self, draft, command, *, service=None):
        return (service or self.drafts).command(self.request(draft, command=command))

    def current(self, draft, *, service=None, context=None):
        return (service or self.drafts).get({**self.binding(context), "draft_id": draft["id"]})

    def test_open_projects_current_source_and_utf8_ranges_without_side_effects(self):
        before = {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("No tools")), \
                patch("subprocess.Popen", side_effect=AssertionError("No tools")), \
                patch.object(self.app.context.jobs, "submit", side_effect=AssertionError("No jobs")), \
                patch.object(Path, "write_bytes", side_effect=AssertionError("No writes")), \
                patch.object(Path, "write_text", side_effect=AssertionError("No writes")):
            draft = self.open()
            current = self.current(draft)
        self.assertEqual(before, {path.relative_to(self.root): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        self.assertEqual(draft, current)
        self.assertEqual(draft["text"], SOURCE)
        self.assertEqual(draft["revision"], 0)
        self.assertFalse(draft["dirty"])
        self.assertEqual(draft["capabilities"], {"edit": True, "save": False})
        node = draft["nodes"][0]
        self.assertEqual(SOURCE.encode()[node["start"]:node["end"]].decode(), '&uart0 { status = "disabled"; rate = <9600>; flag; };')
        self.assertEqual([prop["value"] for prop in node["properties"]], [
            {"kind": "string", "value": "disabled"}, {"kind": "cells", "value": [9600]}, {"kind": "boolean", "value": None}])

    def test_visual_and_source_changes_share_one_revision_history_and_diff(self):
        first = self.open()
        second = self.command(first, {"kind": "set_status", "node_id": first["nodes"][0]["id"], "status": "okay"})
        self.assertEqual(second["text"], SOURCE.replace('"disabled"', '"okay"'))
        self.assertEqual(second["revision"], 1)
        self.assertTrue(second["dirty"])
        self.assertIn('-&uart0 { status = "disabled";', second["diff"]["text"])
        third = self.source(second, second["text"].replace("<9600>", "<115200>"))
        self.assertEqual(third["nodes"][0]["properties"][1]["value"]["value"], [115200])
        undone = self.drafts.undo(self.request(third))
        self.assertEqual(undone["text"], second["text"])
        self.assertEqual(undone["revision"], 3)
        restored = self.drafts.undo(self.request(undone))
        self.assertEqual(restored["text"], SOURCE)
        self.assertFalse(restored["dirty"])
        self.assertTrue(restored["can_redo"])
        redone = self.drafts.redo(self.request(restored))
        self.assertEqual(redone["text"], second["text"])
        self.assertEqual(redone["revision"], 5)
        self.assertEqual(self.overlay.read_bytes(), SOURCE.encode())

    def test_invalid_typing_is_retained_with_no_stale_nodes_and_can_undo_redo(self):
        first = self.open()
        broken = self.source(first, '/* 温度 */\n&uart0 { status = "unterminated')
        self.assertEqual(broken["nodes"], [])
        self.assertEqual(broken["syntax"]["state"], "incomplete")
        self.assertTrue(broken["syntax"]["blocked"])
        self.assertTrue(broken["text"].endswith('"unterminated'))
        with self.assertRaises(ValueError):
            self.command(broken, {"kind": "set_status", "node_id": first["nodes"][0]["id"], "status": "okay"})
        restored = self.drafts.undo(self.request(broken))
        self.assertEqual(restored["text"], SOURCE)
        self.assertTrue(restored["nodes"])
        repeated = self.drafts.redo(self.request(restored))
        self.assertEqual(repeated["text"], broken["text"])
        self.assertEqual(repeated["nodes"], [])
        repaired = self.source(repeated, '&uart0 { status = "okay"; };')
        self.assertFalse(repaired["syntax"]["blocked"])
        self.assertFalse(repaired["can_redo"])

    def test_source_parser_limits_are_recoverable_but_encoding_limits_reject_without_change(self):
        first = self.open()
        limited = self.source(first, "word " * 17000)
        self.assertEqual(limited["text"], "word " * 17000)
        self.assertTrue(limited["syntax"]["blocked"])
        self.assertEqual(limited["nodes"], [])
        self.assertEqual(limited["syntax"]["diagnostics"][0]["code"], "source-limit")
        for text in ("x" * (256 * 1024 + 1), "bad\0source", "\ud800"):
            with self.subTest(text=text[:10]), self.assertRaises(ValueError):
                self.source(limited, text)
            self.assertEqual(self.current(limited)["text"], limited["text"])
        self.assertEqual(self.drafts.undo(self.request(limited))["text"], SOURCE)

    def test_visual_projection_limits_preserve_large_draft_and_history(self):
        first = self.open()
        text = "/ {" + "".join(f"child{i} {{}};" for i in range(513)) + "};"
        limited = self.source(first, text)
        self.assertEqual(limited["text"], text)
        self.assertEqual(limited["nodes"], [])
        self.assertTrue(limited["syntax"]["blocked"])
        self.assertIn("visual-limit", [entry["code"] for entry in limited["syntax"]["diagnostics"]])
        self.assertEqual(self.drafts.undo(self.request(limited))["text"], SOURCE)

    def test_opaque_values_and_deletion_conflicts_are_not_projected_as_editable_literals(self):
        draft = self.source(self.open(), '&uart0 { status = "okay"; macro = <MACRO(1)>; rate = <1>; /delete-property/ rate; };')
        props = draft["nodes"][0]["properties"]
        self.assertTrue(props[0]["editable"])
        self.assertIsNone(props[1]["value"])
        self.assertFalse(props[1]["editable"])
        self.assertFalse(props[2]["editable"])
        with self.assertRaises(ValueError):
            self.command(draft, {"kind": "set_property", "node_id": draft["nodes"][0]["id"], "name": "macro",
                                 "value": {"kind": "cells", "value": [2]}})
        self.assertEqual(self.current(draft), draft)

    def test_literal_commands_support_explicit_occurrences_and_removal(self):
        draft = self.source(self.open(), '&uart0 { p = <1>; p = <2>; };')
        node = draft["nodes"][0]
        with self.assertRaises(ValueError):
            self.command(draft, {"kind": "set_property", "node_id": node["id"], "name": "p",
                                 "value": {"kind": "cells", "value": [3]}})
        changed = self.command(draft, {"kind": "set_property", "node_id": node["id"], "name": "p",
                                       "property_id": node["properties"][1]["id"], "value": {"kind": "cells", "value": [3]}})
        self.assertEqual(changed["text"], '&uart0 { p = <1>; p = <3>; };')
        removed = self.command(changed, {"kind": "remove_property", "property_id": changed["nodes"][0]["properties"][0]["id"]})
        self.assertEqual(removed["text"], '&uart0 {  p = <3>; };')

    def test_no_op_commands_and_source_do_not_advance_or_clear_redo(self):
        draft = self.open()
        self.assertEqual(self.source(draft, draft["text"]), draft)
        self.assertEqual(self.command(draft, {"kind": "set_status", "node_id": draft["nodes"][0]["id"], "status": "disabled"}), draft)
        changed = self.source(draft, draft["text"] + "// edit")
        undone = self.drafts.undo(self.request(changed))
        self.assertTrue(undone["can_redo"])
        self.assertEqual(self.source(undone, undone["text"]), undone)

    def test_stale_revision_hash_and_source_identity_cannot_overwrite_newer_text(self):
        first = self.open()
        newer = self.source(first, first["text"] + "// newer")
        for request in (self.request(first, text="old"),
                        {**self.request(newer, text="old"), "expected_draft_sha256": "0" * 64},
                        {**self.request(newer, text="old"), "expected_draft_revision": True}):
            with self.assertRaises(ValueError):
                self.drafts.source(request)
        with self.assertRaises(ValueError):
            self.command(newer, {"kind": "set_status", "node_id": first["nodes"][0]["id"], "status": "okay"})
        self.assertEqual(self.current(newer), newer)

    def test_reopening_same_valid_context_restores_draft_and_history(self):
        original = self.open()
        changed = self.source(original, original["text"] + "// retained")
        reopened = self.open(context=self.open_context())
        self.assertEqual(reopened, changed)
        self.assertEqual(reopened["id"], original["id"])
        selected_context = self.editor.select({**self.binding(), "document_id": self.document_id})
        with self.assertRaises(ValueError):
            self.current(changed)
        self.context = selected_context
        self.assertEqual(self.open(), changed)

    def test_workspace_switch_retains_old_draft_without_allowing_cross_workspace_requests(self):
        first = self.open()
        changed = self.source(first, first["text"] + "// retained")
        second_workspace = self.root / "other"
        second_workspace.mkdir()
        self.app.context.workspace.set(str(second_workspace))
        with self.assertRaises(ValueError):
            self.source(changed, "wrong workspace")
        self.app.context.workspace.set(str(self.workspace))
        self.context = self.open_context()
        self.assertEqual(self.open(), changed)

    def test_external_file_change_rejects_reads_and_mutations_without_losing_draft(self):
        draft = self.source(self.open(), '&uart0 { status = "okay"; };')
        self.overlay.write_text("// external source\n")
        for operation in (lambda: self.current(draft), lambda: self.source(draft, "replace external")):
            with self.assertRaises(ValueError):
                operation()
        self.context = self.open_context()
        current = self.open()
        self.assertNotEqual(current["id"], draft["id"])
        self.assertEqual(current["text"], "// external source\n")
        self.assertEqual(self.drafts.drafts[draft["id"]].source.decode(), draft["text"])

    def test_context_change_during_projection_prevents_mutation_commit(self):
        draft = self.open()
        original = self.drafts._payload
        second = self.root / "other"
        second.mkdir()
        def switched(candidate):
            result = original(candidate)
            self.app.context.workspace.set(str(second))
            return result
        with patch.object(self.drafts, "_payload", side_effect=switched), self.assertRaises(ValueError):
            self.source(draft, "// pending")
        self.app.context.workspace.set(str(self.workspace))
        self.assertEqual(self.current(draft), draft)

    def test_edited_drafts_are_never_evicted_to_make_room(self):
        service = DraftService(self.editor, max_drafts=1)
        draft = self.open(service=service)
        edited = self.source(draft, draft["text"] + "// retained", service=service)
        (self.application / "second.overlay").write_text("&other {};\n")
        self.context = self.open_context()
        second = next(item for item in self.context["documents"] if item["relative_path"] == "second.overlay")
        with self.assertRaisesRegex(ValueError, "count limit"):
            self.open(service=service, document_id=second["id"])
        self.assertEqual(service.drafts[edited["id"]].source.decode(), edited["text"])

    def test_history_budget_rolls_oldest_history_with_visible_metadata(self):
        service = DraftService(self.editor, history_limit=2)
        draft = self.open(service=service)
        for number in range(3):
            draft = self.source(draft, SOURCE + f"// {number}", service=service)
        self.assertTrue(draft["history_truncated"])
        self.assertEqual(draft["history_limit"], 2)
        draft = service.undo(self.request(draft))
        draft = service.undo(self.request(draft))
        self.assertEqual(draft["text"], SOURCE + "// 0")
        self.assertFalse(draft["can_undo"])
        self.assertTrue(draft["can_redo"])

    def test_memory_overflow_rejects_before_changing_source_or_history(self):
        service = DraftService(self.editor, max_bytes=len(SOURCE.encode()) * 3)
        draft = self.open(service=service)
        with self.assertRaisesRegex(ValueError, "memory limit"):
            self.source(draft, SOURCE + "// exceeds", service=service)
        self.assertEqual(self.current(draft, service=service), draft)

    def test_diff_preview_is_bounded_without_truncating_current_text(self):
        draft = self.open()
        text = "/*" + "x" * 100000 + "*/"
        changed = self.source(draft, text)
        self.assertEqual(changed["text"], text)
        self.assertTrue(changed["diff"]["truncated"])
        self.assertLessEqual(len(changed["diff"]["text"].encode()), 64 * 1024)
        self.assertEqual(self.drafts.undo(self.request(changed))["text"], SOURCE)

    def test_payloads_are_detached_and_close_discards_only_session_memory(self):
        draft = self.open()
        expected = deepcopy(draft)
        draft["nodes"][0]["properties"][0]["value"]["value"] = "tampered"
        draft["text"] = "tampered"
        self.assertEqual(self.current(expected), expected)
        self.drafts.close()
        with self.assertRaises(ValueError):
            self.current(expected)
        self.assertEqual(self.overlay.read_bytes(), SOURCE.encode())

    def test_request_and_command_shapes_are_strict_and_do_not_raise_type_errors(self):
        draft = self.open()
        for request in ([], {**self.binding(), "document_id": self.document_id, "unexpected": 1},
                        {**self.binding(), "document_id": []}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.drafts.open(request)
        for command in ({"kind": []}, {"kind": {}}, {"kind": "save"},
                        {"kind": "set_status", "node_id": draft["nodes"][0]["id"], "status": "okay", "unexpected": 1},
                        {"kind": "set_property", "node_id": draft["nodes"][0]["id"], "name": "x", "value": {"kind": "boolean", "value": True}}):
            with self.subTest(command=command), self.assertRaises(ValueError):
                self.command(draft, command)
        self.assertEqual(self.current(draft), draft)

    def test_original_and_named_configurations_keep_separate_drafts_for_same_file(self):
        fixture = project_fixtures.ProjectContextTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        named = fixture.configuration()
        editor = EditorService(fixture.app.context)
        service = DraftService(editor)
        opened = []
        contexts = []
        for configuration_id in (None, named["id"]):
            context = editor.open({"workspace": str(fixture.workspace), "project_id": fixture.project["id"],
                                   "configuration_id": configuration_id})
            selected = next(item for item in context["documents"] if item["relative_path"] == "app.overlay")
            binding = {"workspace": str(fixture.workspace), "context_id": context["id"], "expected_revision": context["revision"]}
            draft = service.open({**binding, "document_id": selected["id"]})
            contexts.append((binding, selected["id"]))
            opened.append(service.source({**binding, "draft_id": draft["id"], "expected_draft_revision": draft["revision"],
                "expected_draft_sha256": draft["sha256"], "text": f'&uart0 {{ label = "{configuration_id}"; }};'}))
        self.assertNotEqual(opened[0]["id"], opened[1]["id"])
        self.assertNotEqual(opened[0]["context_fingerprint"], opened[1]["context_fingerprint"])
        for (binding, document_id), draft in zip(contexts, opened):
            self.assertEqual(service.open({**binding, "document_id": document_id}), draft)
        with self.assertRaises(ValueError):
            service.get({**contexts[1][0], "draft_id": opened[0]["id"]})
