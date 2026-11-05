"""Editor validation binds public Projects actions to exact canonical candidates."""

from copy import deepcopy
from unittest.mock import patch
import unittest
import uuid

from zephyr_utilities.core.http import Request

import test_project_context as project_fixtures


class Provider:
    def __init__(self):
        self.plans = {}
        self.submissions = {}
        self.cancelled = []
        self.current = {"status": "configured", "stale": False, "diagnostics": []}

    def review(self, selection, candidate):
        plan = {"id": uuid.uuid4().hex, "candidate": deepcopy(candidate), "mode": "saved-source"}
        self.plans[plan["id"]] = plan
        return {"plan": plan}

    def submit(self, plan_id):
        self.submissions.setdefault(plan_id, {"plan_id": plan_id, "job": {"id": uuid.uuid4().hex, "status": "running"}})
        return self.submissions[plan_id]

    def result(self, plan_id):
        return {**deepcopy(self.current), "plan_id": plan_id, "candidate": self.plans[plan_id]["candidate"]}

    def cancel(self, plan_id):
        self.cancelled.append(plan_id)
        self.current["status"] = "cancelled"

    def handoff(self, plan_id):
        return {"feature": "projects", "view": "applications", "plan_id": plan_id}


class DevicetreeValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = project_fixtures.ProjectContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app = self.fixture.app
        self.workspace = str(self.fixture.workspace)
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = self.app.context.services["devicetree_drafts"]
        self.service = self.app.context.services["devicetree_validation"]
        self.provider = Provider()
        self.app.context.project_validation = self.provider
        self.context = self.editor.open({"workspace": self.workspace, "project_id": self.fixture.project["id"],
                                         "configuration_id": None})
        selected = next(item for item in self.context["documents"] if item["relative_path"] == "app.overlay")
        self.draft = self.drafts.open({**self.binding(), "document_id": selected["id"]})

    def binding(self):
        return {"workspace": self.workspace, "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def review(self):
        result = self.service.review(self.guard())
        self.assertEqual(result["status"], "ready", result)
        return result["plan"]

    def read(self, plan):
        return {"workspace": self.workspace, "plan_id": plan["id"]}

    def test_review_exact_saved_candidate_never_submits_or_writes(self):
        before = self.fixture.bytes_in_tree()
        with patch.object(self.provider, "submit", side_effect=AssertionError("Explicit action required")):
            plan = self.review()
        self.assertEqual(plan["candidate"], {"path": self.draft["path"], "text": self.draft["text"], "sha256": self.draft["sha256"]})
        self.assertEqual(self.fixture.bytes_in_tree(), before)

    def test_unsaved_source_requires_separate_reviewed_save(self):
        self.draft = self.drafts.source(self.guard(text='&uart0 { status = "disabled"; };\n'))
        with patch.object(self.provider, "review", side_effect=AssertionError("No unsaved staging")):
            result = self.service.review(self.guard())
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Save", result["message"])
        self.assertNotEqual((self.fixture.source / "app.overlay").read_text(), self.draft["text"])

    def test_source_change_after_review_prevents_first_submission(self):
        plan = self.review()
        old = self.guard(plan_id=plan["id"])
        self.draft = self.drafts.source(self.guard(text='&uart0 { status = "disabled"; };\n'))
        with self.assertRaises(ValueError):
            self.service.submit(old)
        with self.assertRaises(ValueError):
            self.service.submit(self.guard(plan_id=plan["id"]))
        self.assertEqual(self.provider.submissions, {})

    def test_acknowledged_submission_lookup_is_idempotent_after_typing(self):
        plan = self.review()
        request = self.guard(plan_id=plan["id"])
        first = self.service.submit(request)
        self.draft = self.drafts.source(self.guard(text='&uart0 { status = "disabled"; };\n'))
        self.assertEqual(first, self.service.submit(request))
        self.assertEqual(len(self.provider.submissions), 1)
        result = self.service.status(self.read(plan))
        self.assertTrue(result["draft_changed"])
        self.assertTrue(result["stale"])

    def test_external_edit_blocks_initial_submission(self):
        plan = self.review()
        (self.fixture.source / "app.overlay").write_text('&uart0 { status = "disabled"; };\n')
        with self.assertRaises(ValueError):
            self.service.submit(self.guard(plan_id=plan["id"]))
        self.assertEqual(self.provider.submissions, {})

    def test_edit_then_undo_does_not_silently_rebind_review_to_new_revision(self):
        plan = self.review()
        self.service.submit(self.guard(plan_id=plan["id"]))
        self.draft = self.drafts.source(self.guard(text='&uart0 { status = "disabled"; };\n'))
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["sha256"], plan["candidate"]["sha256"])
        self.assertTrue(self.service.status(self.read(plan))["draft_changed"])

    def test_only_matching_candidate_diagnostics_have_editable_source_spans(self):
        plan = self.review()
        candidate = plan["candidate"]
        valid = {"message": "Property is invalid", "path": candidate["path"], "line": 1, "sha256": candidate["sha256"]}
        self.provider.current["diagnostics"] = [valid, {**valid, "path": "/generated/tree.dts"},
            {**valid, "sha256": "0" * 64}, {**valid, "line": 10000}, {**valid, "line": True}]
        result = self.service.status(self.read(plan))
        self.assertEqual(result["diagnostics"][0]["source_span"], {"start": 0, "end": len(candidate["text"].rstrip().encode())})
        self.assertTrue(all("source_span" not in item for item in result["diagnostics"][1:]))
        self.provider.current["stale"] = True
        self.assertTrue(all("source_span" not in item for item in self.service.status(self.read(plan))["diagnostics"]))

    def test_cancel_and_handoff_are_explicit_and_do_not_submit(self):
        plan = self.review()
        self.service.submit(self.guard(plan_id=plan["id"]))
        self.assertEqual(self.service.cancel(self.read(plan))["status"], "cancelled")
        self.assertEqual(self.provider.cancelled, [plan["id"]])
        self.assertEqual(self.service.handoff(self.read(plan))["feature"], "projects")
        self.service.close()
        self.assertEqual(self.provider.cancelled, [plan["id"]])

    def test_workspace_switch_cannot_retarget_or_cancel_accepted_work(self):
        plan = self.review()
        self.service.submit(self.guard(plan_id=plan["id"]))
        other = self.fixture.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        for method in (self.service.status, self.service.cancel, self.service.handoff):
            with self.assertRaises(ValueError):
                method(self.read(plan))
        self.assertEqual(self.provider.cancelled, [])
        self.app.context.workspace.set(self.workspace)
        self.assertEqual(self.service.status(self.read(plan))["plan_id"], plan["id"])

    def test_unavailable_provider_is_explained_without_tool_fallback(self):
        self.app.context.project_validation = None
        self.assertEqual(self.service.review(self.guard())["status"], "blocked")

    def test_directory_only_context_cannot_invent_build_selection(self):
        self.context = self.editor.open({"workspace": self.workspace, "application_path": str(self.fixture.source)})
        selected = next(item for item in self.context["documents"] if item["relative_path"] == "app.overlay")
        self.draft = self.drafts.open({**self.binding(), "document_id": selected["id"]})
        self.assertIn("registered", self.service.review(self.guard())["message"])

    def test_routes_dispatch_and_reject_unknown_fields_and_plan_ownership(self):
        result = self.app.router.dispatch(Request("POST", "/api/devicetree/editor/drafts/validation/review", body=self.guard())).data
        plan = result["plan"]
        for body in ({}, None, {**self.read(plan), "extra": True}, {**self.read(plan), "plan_id": "bad"}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.service.status(body)
        with self.assertRaises(ValueError):
            self.service.submit(self.guard(plan_id=uuid.uuid4().hex))

    def test_review_capacity_retains_existing_work(self):
        with patch("zephyr_utilities.features.devicetree.validation.MAX_VALIDATIONS", 1):
            plan = self.review()
            self.assertEqual(self.service.review(self.guard())["status"], "blocked")
            self.assertEqual(self.service.status(self.read(plan))["plan_id"], plan["id"])
