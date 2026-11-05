"""Bind explicit Projects configuration reviews to canonical editor candidates."""

from copy import deepcopy
import re
from threading import RLock

from .drafts import _GUARD_FIELDS, _sha


MAX_VALIDATIONS = 64


class ValidationService:
    """No tool execution or source publication outside the public provider."""

    def __init__(self, drafts):
        self.drafts = drafts
        self.context = drafts.editor.context
        self.plans = {}
        self.lock = RLock()

    def _provider(self):
        provider = self.context.project_validation
        if provider is None:
            raise ValueError("Configuration validation is unavailable. Enable Projects and select a registered application.")
        return provider

    def _candidate(self, body, extra=()):
        binding = self.drafts._body(body, _GUARD_FIELDS | set(extra))
        context = self.drafts._context(binding)
        draft = self.drafts._scope(body, context)
        self.drafts._guard(body, draft)
        return draft, context

    def review(self, body):
        with self.lock, self.drafts.lock:
            draft, context = self._candidate(body)
            if draft.source != draft.base:
                return {"status": "blocked", "message": "Save this draft first, then review Configure. Faithful unsaved staging is not available for this layout."}
            if draft.document is None or draft.document.syntax.state == "incomplete":
                return {"status": "blocked", "message": "Repair incomplete source before reviewing Configure. Your draft was retained."}
            selection = self.drafts.editor.contexts[context["id"]].selection
            if selection is None:
                return {"status": "blocked", "message": "Select a registered application and configuration to validate its saved overlay in Projects."}
            if len(self.plans) >= MAX_VALIDATIONS:
                return {"status": "blocked", "message": "The session validation review limit was reached. Existing jobs and Projects attempts remain available; restart the application for new reviews."}
            try:
                response = self._provider().review(deepcopy(selection), {
                    "path": draft.path, "text": draft.source.decode("utf-8"), "sha256": _sha(draft.source)})
                plan = response["plan"]
                self._candidate(body)
            except (OSError, ValueError) as exc:
                return {"status": "blocked", "message": str(exc)}
            self.plans[plan["id"]] = {"guard": deepcopy(body), "draft_id": draft.id,
                "candidate": deepcopy(plan["candidate"]), "submitted": False}
            return {"status": "ready", "plan": deepcopy(plan)}

    def _lookup(self, body, *, mutation=False):
        if mutation:
            self.drafts._body(body, _GUARD_FIELDS | {"plan_id"})
        elif not isinstance(body, dict) or set(body) != {"workspace", "plan_id"}:
            raise ValueError("Choose the exact supported validation request fields.")
        plan_id = body["plan_id"]
        if not isinstance(plan_id, str) or not re.fullmatch(r"[a-f0-9]{32}", plan_id):
            raise ValueError("Choose a valid validation plan.")
        record = self.plans.get(plan_id)
        if record is None:
            raise ValueError("This validation review is no longer available. Review Configure again; prior attempts remain in Projects.")
        if (body["workspace"] != record["guard"]["workspace"] or
                body["workspace"] != self.context.workspace.get()["path"]):
            raise ValueError("The selected workspace changed. The captured configuration job was not redirected or cancelled.")
        if mutation and {key: value for key, value in body.items() if key != "plan_id"} != record["guard"]:
            raise ValueError("The draft or selection changed after review. Review Configure again.")
        return record

    def submit(self, body):
        with self.lock, self.drafts.lock:
            record = self._lookup(body, mutation=True)
            # An acknowledged submission can be looked up again after a lost HTTP
            # response without executing another command or using a newer draft.
            if not record["submitted"]:
                self._candidate(body, ("plan_id",))
            response = self._provider().submit(body["plan_id"])
            record["submitted"] = True
            return deepcopy(response)

    def _result(self, plan_id, record):
        result = deepcopy(self._provider().result(plan_id))
        candidate = record["candidate"]
        draft = self.drafts.drafts.get(record["draft_id"])
        changed = (draft is None or draft.revision != record["guard"]["expected_draft_revision"] or
                   _sha(draft.source) != candidate["sha256"])
        result["draft_changed"] = changed
        result["stale"] = bool(result.get("stale") or changed)
        if changed:
            result["message"] = "This result belongs to an earlier saved candidate. Save and review Configure again for the current draft."
        result["candidate"] = deepcopy(candidate)
        lines = candidate["text"].splitlines(keepends=True)
        offsets, total = [], 0
        for line in lines:
            offsets.append(total)
            total += len(line.encode("utf-8"))
        for diagnostic in result.get("diagnostics", [])[:200]:
            diagnostic.pop("source_span", None)
            line = diagnostic.get("line")
            if (diagnostic.get("path") == candidate["path"] and
                    diagnostic.get("sha256") == candidate["sha256"] and
                    type(line) is int and 1 <= line <= len(lines) and not result["stale"]):
                diagnostic["source_span"] = {"start": offsets[line - 1],
                    "end": offsets[line - 1] + len(lines[line - 1].rstrip("\r\n").encode("utf-8"))}
        return result

    def status(self, body):
        with self.lock, self.drafts.lock:
            record = self._lookup(body)
            return self._result(body["plan_id"], record)

    def cancel(self, body):
        with self.lock:
            self._lookup(body)
            self._provider().cancel(body["plan_id"])
            return self.status(body)

    def handoff(self, body):
        with self.lock:
            self._lookup(body)
            return deepcopy(self._provider().handoff(body["plan_id"]))

    def close(self):
        with self.lock:
            self.plans.clear()
