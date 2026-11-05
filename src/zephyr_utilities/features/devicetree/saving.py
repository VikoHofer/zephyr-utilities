"""Reviewed existing-overlay publication, explicit reconciliation and draft recovery."""

from copy import deepcopy
from dataclasses import replace
import time
import uuid

from .drafts import Draft, _GUARD_FIELDS, _diff, _parse, _sha, _text_bytes
from .recovery import identifier, validate_record


REVIEW_TTL = 600
MAX_REVIEWS = 32


def _stable(context):
    """Selection identity excludes observations of overlay contents and build evidence."""
    keys = ("workspace", "workspace_identity", "application", "project", "configuration", "target",
            "environment", "fragments", "default_overlay", "roots", "source_guard")
    return {key: deepcopy(context.get(key)) for key in keys}


class SavingService:
    """Publication is a separate explicit action; recovery never writes firmware."""

    def __init__(self, drafts, publisher):
        self.drafts, self.editor, self.publisher = drafts, drafts.editor, publisher
        self.plans, self.comparisons, self.completed = {}, {}, {}

    def _workspace(self, body, fields):
        return self.editor._workspace(body, fields)

    def _retained(self, body):
        """Read a known draft even when live source hashes make its D1 context stale."""
        workspace = self.editor.context.workspace.get()["path"]
        if body["workspace"] != workspace:
            raise ValueError("The selected workspace changed. Return to the draft's workspace first.")
        draft = self.drafts.drafts.get(identifier(body["draft_id"]))
        if draft is None or draft.workspace != workspace or draft.context is None:
            raise ValueError("This draft is unavailable. Open its recovery record instead.")
        identifier(body["context_id"])
        if type(body["expected_revision"]) is not int or body["expected_revision"] < 1:
            raise ValueError("Choose a valid editor context revision.")
        scope = self.editor.contexts.get(body["context_id"])
        if scope is not None:
            context = scope.payload
            if context["revision"] != body["expected_revision"] or context["fingerprint"] != draft.context_fingerprint:
                raise ValueError("The selected context does not own this draft. Open the matching application.")
        elif (body["context_id"], body["expected_revision"]) != (draft.context["id"], draft.context["revision"]):
            raise ValueError("The editor context expired. Reopen the matching application or use recovery.")
        self.drafts._guard(body, draft)
        return draft

    def _fresh(self, captured, document_id):
        if captured["workspace"] != self.editor.context.workspace.get()["path"]:
            raise ValueError("The selected workspace changed. Return to the recorded workspace first.")
        if captured.get("project") is not None:
            request = {"workspace": captured["workspace"], "project_id": captured["project"]["id"],
                       "configuration_id": captured["configuration"]["id"]}
        else:
            request = {"workspace": captured["workspace"], "application_path": captured["application"]["path"]}
        context = self.editor.open(request)
        current = self.drafts._capture(context)
        if _stable(current) != _stable(captured):
            raise ValueError("The application, configuration, target or directory identity changed. Export the retained text; it cannot be restored into this context.")
        selected = self.drafts._document(context, document_id)
        context = self.editor.select({"workspace": context["workspace"], "context_id": context["id"],
                                      "expected_revision": context["revision"], "document_id": selected["id"]})
        return context, self.drafts._capture(context)

    @staticmethod
    def _binding(context):
        return {"workspace": context["workspace"], "context_id": context["id"], "expected_revision": context["revision"]}

    @staticmethod
    def _trim(mapping):
        now = time.monotonic()
        for key in [key for key, value in mapping.items() if value["deadline"] <= now]:
            del mapping[key]
        while len(mapping) >= MAX_REVIEWS:
            del mapping[next(iter(mapping))]

    def _compare(self, *, workspace, path, base, source, captured, document_id, owner, revision):
        observation, context = None, None
        reason = ""
        try:
            observation = self.publisher.observe(workspace, path)
            context, _ = self._fresh(captured, document_id)
            selected = self.drafts._document(context, document_id)
            if selected["sha256"] != observation["sha256"] or selected["path"] != path:
                raise ValueError("The current file changed during comparison. Compare again.")
        except (OSError, ValueError) as exc:
            reason = str(exc)
        current = observation["text"] if observation is not None else None
        available = observation is not None and context is not None and not reason
        status = "unavailable" if not available else "current" if observation["sha256"] == _sha(base) else "conflict"
        self._trim(self.comparisons)
        comparison_id = uuid.uuid4().hex
        self.comparisons[comparison_id] = {"deadline": time.monotonic() + REVIEW_TTL,
            "workspace": workspace, "owner": owner, "revision": revision, "observation": observation,
            "context": context, "available": available}
        return {"status": status, "comparison_id": comparison_id, "path": path,
                "base_text": base.decode("utf-8"), "text": source.decode("utf-8"), "current_text": current,
                "current_sha256": observation["sha256"] if observation is not None else None,
                "diff": _diff(observation["text"].encode("utf-8") if observation is not None else base, source),
                "can_reload": available, "message": reason or (
                    "The file still matches the draft baseline." if status == "current" else
                    "The file differs from the draft baseline. Compare both versions before discarding or rebasing the draft.")}

    def compare(self, body):
        self.drafts._body(body, _GUARD_FIELDS)
        with self.editor.lock, self.drafts.lock:
            draft = self._retained(body)
            return self._compare_draft(draft)

    def _compare_draft(self, draft):
        return self._compare(workspace=draft.workspace, path=draft.path, base=draft.base,
                             source=draft.source, captured=draft.context, document_id=draft.document_id,
                             owner=("draft", draft.id), revision=draft.revision)

    def _comparison(self, comparison_id, *, owner, revision, workspace):
        selected = self.comparisons.get(identifier(comparison_id))
        if (selected is None or selected["deadline"] <= time.monotonic() or selected["owner"] != owner
                or selected["revision"] != revision or selected["workspace"] != workspace or not selected["available"]):
            raise ValueError("The comparison expired or changed. Compare the current file again.")
        expected = selected["observation"]
        if self.publisher.observe(workspace, expected["path"]) != expected:
            raise ValueError("The file changed after comparison. Compare again before continuing.")
        return selected

    def review(self, body):
        binding = self.drafts._body(body, _GUARD_FIELDS)
        with self.editor.lock, self.drafts.lock:
            draft = self._retained(body)
            try:
                context = self.drafts._context(binding)
                self.drafts._scope(body, context)
                observation = self.publisher.observe(draft.workspace, draft.path)
                if observation != draft.observation:
                    raise ValueError("The overlay identity or contents changed since this draft was opened.")
                self.publisher.check_writable(observation)
            except (OSError, ValueError) as exc:
                return {"status": "conflict", "message": str(exc), "comparison": self.compare(body)}
            if draft.document is None or draft.document.syntax.state == "incomplete":
                return {"status": "blocked", "message": "This source is incomplete or outside the supported structural limits. Repair it before saving; the text remains available in recovery and export."}
            self._trim(self.plans)
            plan_id = uuid.uuid4().hex
            selected = self.drafts._document(context, draft.document_id)
            overlays = (context.get("fragments") or {}).get("overlays", {})
            usage = selected.get("usage", "unknown")
            usage_message = "Overlay use is observed or predicted separately. Saving does not activate this overlay or change the configuration recipe."
            if overlays.get("mode") == "replace" and selected["relative_path"] not in overlays.get("files", []):
                usage = "inactive"
                usage_message = "This overlay is absent from the selected replacement recipe. Saving it does not activate it."
            elif usage == "captured":
                usage_message = "Use was captured in a historical configuration attempt; current use is not established by this Save."
            elif usage == "predicted":
                usage_message = "Use is predicted from the selected configuration and current filenames, not confirmed by this Save."
            plan = {"id": plan_id, "expires_at": time.time() + REVIEW_TTL, "path": draft.path,
                    "operation": "replace", "candidate_text": draft.source.decode("utf-8"),
                    "candidate_sha256": _sha(draft.source), "base_sha256": _sha(draft.base),
                    "diff": _diff(draft.base, draft.source), "no_changes": draft.source == draft.base,
                    "validation": {"state": "unvalidated", "message": "Source structure is retained; Zephyr semantic validation has not run. Saving does not configure or build the application."},
                    "usage": {"state": usage, "message": usage_message},
                    "effects": {"state": "unknown", "message": "Other configurations may share this overlay. Saving does not change their recipe."}}
            self.plans[plan_id] = {"deadline": time.monotonic() + REVIEW_TTL, "body": deepcopy(body),
                                   "plan": plan, "observation": deepcopy(observation)}
            return {"status": "ready", "plan": deepcopy(plan)}

    def _envelope(self, context, draft, *, status, receipt=None, message=None):
        selected = self.drafts._document(context, draft.document_id)
        result = {"status": status, "context": deepcopy(context),
                  "document": {**deepcopy(selected), "text": draft.base.decode("utf-8")},
                  "draft": self.drafts._payload(draft)}
        if receipt is not None:
            result["receipt"] = deepcopy(receipt)
        if message:
            result["message"] = message
        return result

    def _rebind(self, draft, context, captured, observation, *, source, keep_history=True, increment=True):
        selected = self.drafts._document(context, draft.document_id)
        revision = draft.revision + int(increment)
        document, error = _parse(source, draft.id, revision)
        candidate = replace(draft, key=self.drafts._key(context, selected), context_fingerprint=context["fingerprint"],
                            base=_text_bytes(observation["text"]), source=source, revision=revision,
                            context=captured, observation=deepcopy(observation), document=document, parse_error=error,
                            undo=draft.undo if keep_history else (), redo=draft.redo if keep_history else (),
                            history_truncated=draft.history_truncated if keep_history else False)
        self.drafts._within_budget(candidate, replacing=draft.id)
        return candidate

    def save(self, body):
        binding = self.drafts._body(body, _GUARD_FIELDS | {"plan_id"})
        plan_id = identifier(body["plan_id"])
        original = {key: value for key, value in body.items() if key != "plan_id"}
        with self.editor.lock, self.drafts.lock:
            # Acknowledgement loss must not replay publication or require the old base context.
            completed = self.completed.get(plan_id)
            if completed is not None:
                if original != completed["body"] or body["workspace"] != self.editor.context.workspace.get()["path"]:
                    raise ValueError("This Save receipt belongs to another reviewed request or workspace.")
                return deepcopy(completed["result"])
            plan = self.plans.get(plan_id)
            if plan is None or plan["deadline"] <= time.monotonic() or original != plan["body"]:
                raise ValueError("The Save review expired or changed. Review the current draft again.")
            draft = self._retained(original)

            def revalidate():
                current = self.drafts._scope(original, self.drafts._context(binding))
                self.drafts._guard(original, current)

            try:
                revalidate()
                receipt = self.publisher.publish(draft.workspace, draft.path, plan["observation"],
                                                 draft.source, plan_id, revalidate)
            except (OSError, ValueError) as exc:
                return {"status": "conflict", "message": str(exc), "comparison": self._compare_draft(draft)}
            if receipt["status"] not in {"saved", "unchanged"}:
                result = {"status": "conflict", "receipt": receipt,
                          "message": receipt.get("message", "Publication needs reconciliation; retained source has not been discarded."),
                          "comparison": self._compare_draft(draft)}
            else:
                try:
                    context, captured = self._fresh(draft.context, draft.document_id)
                    observation = self.publisher.observe(draft.workspace, draft.path)
                    if observation["sha256"] != _sha(draft.source):
                        raise ValueError("The file changed after publication. Compare it before continuing.")
                    candidate = self._rebind(draft, context, captured, observation, source=draft.source,
                                             increment=receipt["status"] == "saved")
                    message = None
                    try:
                        self.drafts._persist(candidate)
                    except (OSError, ValueError) as exc:
                        message = f"The overlay was saved, but recovery metadata needs verification: {exc}. Keep or export the current source."
                    self.editor.current(self._binding(context))
                    if self.publisher.observe(draft.workspace, draft.path) != observation:
                        raise ValueError("The file changed while recovery metadata was being recorded. Compare it before continuing.")
                    self.drafts.drafts[draft.id] = candidate
                    result = self._envelope(context, candidate, status=receipt["status"], receipt=receipt, message=message)
                    if message:
                        result["draft"]["recovery"] = {"state": "failed", "message": message}
                except (OSError, ValueError) as exc:
                    result = {"status": "conflict", "receipt": receipt,
                              "message": f"Publication completed, but the current context needs reconciliation: {exc}",
                              "comparison": self._compare_draft(draft)}
            self.completed[plan_id] = {"body": deepcopy(original), "result": deepcopy(result)}
            while len(self.completed) > 64:
                del self.completed[next(iter(self.completed))]
            self.plans.pop(plan_id, None)
            return result

    def reload(self, body):
        self.drafts._body(body, _GUARD_FIELDS | {"comparison_id", "mode"})
        if not isinstance(body["mode"], str) or body["mode"] not in {"keep", "discard"}:
            raise ValueError("Choose whether to keep or discard the draft source.")
        with self.editor.lock, self.drafts.lock:
            draft = self._retained(body)
            comparison = self._comparison(body["comparison_id"], owner=("draft", draft.id),
                                          revision=draft.revision, workspace=draft.workspace)
            context, captured = self._fresh(draft.context, draft.document_id)
            observation = self.publisher.observe(draft.workspace, draft.path)
            if observation != comparison["observation"]:
                raise ValueError("The file changed after comparison. Compare it again.")
            source = draft.source if body["mode"] == "keep" else _text_bytes(observation["text"])
            candidate = self._rebind(draft, context, captured, observation, source=source,
                                     keep_history=body["mode"] == "keep")
            self.drafts._persist(candidate)
            self.editor.current(self._binding(context))
            if self.publisher.observe(draft.workspace, draft.path) != observation:
                raise ValueError("The file changed while recording the reload. Compare it again.")
            self.drafts.drafts[draft.id] = candidate
            self.comparisons.pop(body["comparison_id"], None)
            return self._envelope(context, candidate, status="reloaded")

    def _store(self):
        if self.drafts.store is None:
            raise ValueError("Draft recovery is unavailable in this application session.")
        return self.drafts.store

    @staticmethod
    def _record(value, workspace):
        return validate_record(value, workspace)

    def recovery_list(self, body):
        workspace = self._workspace(body, ())
        records, diagnostics = self._store().records(workspace)
        summaries = []
        for value in records:
            try:
                base, source = self._record(value, workspace)
                summaries.append({"id": value["id"], "path": value["path"], "revision": value["revision"],
                                  "updated_at": value.get("updated_at"), "status": "available", "dirty": base != source})
            except (ValueError, TypeError, KeyError) as exc:
                diagnostics.append(f"{value.get('id', 'record')}: {exc}")
        try:
            publications = self.publisher.receipts(workspace)
        except (OSError, ValueError) as exc:
            publications = []
            diagnostics.append(f"Cannot read publication recovery: {exc}")
        return {"records": summaries, "diagnostics": diagnostics, "publications": publications}

    def recovery_read(self, body):
        workspace = self._workspace(body, ("recovery_id",))
        with self.editor.lock, self.drafts.lock:
            value = self._store().read(body["recovery_id"])
            base, source = self._record(value, workspace)
            comparison = self._compare(workspace=workspace, path=value["path"], base=base, source=source,
                captured=value["context"], document_id=value["document_id"], owner=("recovery", value["id"]),
                revision=value["revision"])
            return {"record": {key: deepcopy(value[key]) for key in ("id", "path", "revision", "text", "base_text", "updated_at", "context")},
                    "comparison": comparison}

    def recovery_restore(self, body):
        binding = self.drafts._body(body, {"document_id", "recovery_id", "expected_recovery_revision", "comparison_id", "mode"})
        if not isinstance(body["mode"], str) or body["mode"] not in {"keep", "discard"}:
            raise ValueError("Choose whether to keep or discard the recovered source.")
        with self.editor.lock, self.drafts.lock:
            workspace = body["workspace"]
            value = self._store().read(body["recovery_id"])
            _, source = self._record(value, workspace)
            if type(body["expected_recovery_revision"]) is not int or body["expected_recovery_revision"] != value["revision"]:
                raise ValueError("The recovery record changed. Review it again.")
            comparison = self._comparison(body["comparison_id"], owner=("recovery", value["id"]),
                                          revision=value["revision"], workspace=workspace)
            context = self.drafts._context(binding)
            captured = self.drafts._capture(context)
            if _stable(captured) != _stable(value["context"]):
                raise ValueError("This recovery belongs to another or changed application/configuration. Export its source instead.")
            selected = self.drafts._document(context, body["document_id"])
            if selected["path"] != value["path"] or selected["id"] != value["document_id"]:
                raise ValueError("Choose the recovery record's original overlay destination.")
            context, captured = self._fresh(value["context"], selected["id"])
            observation = self.publisher.observe(workspace, selected["path"])
            if observation != comparison["observation"]:
                raise ValueError("The file changed after comparison. Review recovery again.")
            key = self.drafts._key(context, selected)
            existing = next((draft for draft in self.drafts.drafts.values() if draft.key == key), None)
            if existing is not None and (existing.source != existing.base or existing.undo or existing.redo):
                raise ValueError("This overlay already has an edited session draft. Discard it explicitly before restoring recovery.")
            if existing is None and len(self.drafts.drafts) >= self.drafts.max_drafts:
                raise ValueError("The session draft limit was reached. Existing drafts were retained.")
            draft_id = existing.id if existing is not None else uuid.uuid4().hex
            revision = (existing.revision + 1) if existing is not None else 0
            base = _text_bytes(observation["text"])
            source = source if body["mode"] == "keep" else base
            document, error = _parse(source, draft_id, revision)
            history_matches = body["mode"] == "keep" and observation["sha256"] == value["base_sha256"]
            undo = tuple(_text_bytes(text) for text in value["undo"]) if history_matches else ()
            redo = tuple(_text_bytes(text) for text in value["redo"]) if history_matches else ()
            if len(undo) + len(redo) > self.drafts.history_limit:
                raise ValueError("This recovery history exceeds the current session's history limit. Export its source instead.")
            draft = Draft(draft_id, key, context["fingerprint"], workspace, tuple(context["workspace_identity"]),
                          selected["id"], selected["path"], base, source, revision, document, error,
                          undo=undo, redo=redo, history_truncated=value["history_truncated"] if history_matches else False,
                          context=captured, observation=observation)
            self.drafts._within_budget(draft, replacing=existing.id if existing is not None else None)
            self.drafts._persist(draft)
            self.editor.current(self._binding(context))
            if self.publisher.observe(workspace, selected["path"]) != observation:
                raise ValueError("The file changed while recording recovery. Review it again.")
            self.drafts.drafts[draft.id] = draft
            self.comparisons.pop(body["comparison_id"], None)
            return self._envelope(context, draft, status="restored")

    def recovery_discard(self, body):
        workspace = self._workspace(body, ("recovery_id", "expected_recovery_revision"))
        with self.editor.lock, self.drafts.lock:
            value = self._store().read(body["recovery_id"])
            self._record(value, workspace)
            self._store().remove(body["recovery_id"], body["expected_recovery_revision"])
            return {"status": "discarded", "id": value["id"]}

    def close(self):
        self.plans.clear()
        self.comparisons.clear()
        self.completed.clear()
