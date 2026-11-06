"""Context-bound canonical overlay drafts and optional durable recovery records."""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import re
from threading import RLock
import uuid

from .source import PropertyValue, SourceDocument
from .source_syntax import MAX_BYTES, SourceError
from .recovery import DraftStore


MAX_DRAFTS = 16
MAX_RETAINED_BYTES = 8 * 1024 * 1024
HISTORY_LIMIT = 32
MAX_VISUAL_NODES = 512
MAX_VISUAL_PROPERTIES = 4096
MAX_DIAGNOSTICS = 256
MAX_DIFF_BYTES = 64 * 1024
MAX_DIFF_LINES = 400
_BINDING_FIELDS = {"workspace", "context_id", "expected_revision"}
_GUARD_FIELDS = {"draft_id", "expected_draft_revision", "expected_draft_sha256"}


def _sha(source):
    return hashlib.sha256(source).hexdigest()


def _text_bytes(text):
    if not isinstance(text, str):
        raise ValueError("The draft source must be text.")
    try:
        source = text.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("The draft source must be valid UTF-8 text.") from exc
    if len(source) > MAX_BYTES or b"\x00" in source:
        raise ValueError("Draft text must fit within 256 KiB and must not contain NUL bytes. Your current draft was retained.")
    return source


def _parse(source, draft_id, revision):
    try:
        return SourceDocument.parse(source, document_id=draft_id, revision=revision), None
    except SourceError as exc:
        # Encoding/byte limits were checked separately. Syntactically incomplete
        # typing and parser resource-limit input remain recoverable raw draft text.
        return None, str(exc)


def _diff(base, current):
    """A bounded, linear-time single-hunk review, not a publication patch."""
    if base == current:
        return {"text": "", "truncated": False}
    before, after = base.decode("utf-8").splitlines(keepends=True), current.decode("utf-8").splitlines(keepends=True)
    prefix, suffix = 0, 0
    while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
        prefix += 1
    while suffix < min(len(before), len(after)) - prefix and before[-suffix - 1] == after[-suffix - 1]:
        suffix += 1
    start = max(0, prefix - 3)
    old_end = min(len(before), len(before) - suffix + 3)
    new_end = min(len(after), len(after) - suffix + 3)
    parts = ["--- Base overlay\n", "+++ Working draft\n",
             f"@@ -{start + 1},{old_end - start} +{start + 1},{new_end - start} @@\n"]
    size = sum(len(part.encode("utf-8")) for part in parts)
    count = 0
    truncated = False
    truncation_note = "… Diff preview truncated; the complete draft remains in the source editor.\n"
    content_limit = MAX_DIFF_BYTES - len(truncation_note.encode("utf-8"))
    segments = ((" ", before[start:prefix]), ("-", before[prefix:len(before) - suffix]),
                ("+", after[prefix:len(after) - suffix]), (" ", before[len(before) - suffix:old_end]))
    for marker, lines in segments:
        for line in lines:
            rendered = marker + line.rstrip("\r\n") + "\n"
            if not line.endswith(("\r", "\n")):
                rendered += "\\ No newline at end of file\n"
            encoded = rendered.encode("utf-8")
            if count >= MAX_DIFF_LINES or size + len(encoded) > content_limit:
                truncated = True
                break
            parts.append(rendered)
            count += 1
            size += len(encoded)
        if truncated:
            break
    if truncated:
        parts.append(truncation_note)
    return {"text": "".join(parts), "truncated": truncated}


@dataclass(frozen=True)
class Draft:
    id: str
    key: tuple
    context_fingerprint: str
    workspace: str
    workspace_identity: tuple
    document_id: str
    path: str
    base: bytes
    source: bytes
    revision: int
    document: SourceDocument | None
    parse_error: str | None
    undo: tuple[bytes, ...] = ()
    redo: tuple[bytes, ...] = ()
    history_truncated: bool = False
    context: dict | None = None
    observation: dict | None = None

    @property
    def retained_bytes(self):
        return len(self.base) + len(self.source) + sum(map(len, self.undo)) + sum(map(len, self.redo))


class DraftService:
    """One revision/history for visual and raw-source changes; no firmware writes."""

    def __init__(self, editor_service, *, max_drafts=MAX_DRAFTS, max_bytes=MAX_RETAINED_BYTES,
                 history_limit=HISTORY_LIMIT, storage=None, publisher=None):
        if (type(max_drafts) is not int or not 1 <= max_drafts <= MAX_DRAFTS
                or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RETAINED_BYTES
                or type(history_limit) is not int or not 1 <= history_limit <= HISTORY_LIMIT):
            raise ValueError("Choose supported draft retention limits.")
        self.editor = editor_service
        self.max_drafts, self.max_bytes, self.history_limit = max_drafts, max_bytes, history_limit
        self.drafts = {}
        self.lock = RLock()
        self.store = DraftStore(storage) if storage is not None else None
        self.publisher = publisher
        self.recovery_failures = {}
        self.bindings = None
        self.peripherals = None
        self.pins = None
        self.modules = None
        self.removal = None

    def _capture(self, context):
        captured = deepcopy(context)
        captured["source_guard"] = deepcopy(self.editor.contexts[context["id"]].source_guard)
        return captured

    def _record(self, draft):
        return {"schema_version": 1, "id": draft.id, "workspace": draft.workspace,
                "context": deepcopy(draft.context), "observation": deepcopy(draft.observation),
                "path": draft.path, "document_id": draft.document_id,
                "base_text": draft.base.decode("utf-8"), "text": draft.source.decode("utf-8"),
                "base_sha256": _sha(draft.base), "sha256": _sha(draft.source),
                "revision": draft.revision, "undo": [source.decode("utf-8") for source in draft.undo],
                "redo": [source.decode("utf-8") for source in draft.redo],
                "history_truncated": draft.history_truncated,
                "updated_at": datetime.now(timezone.utc).isoformat()}

    def _persist(self, draft):
        if self.store is not None:
            try:
                self.store.write(self._record(draft))
            except (OSError, ValueError) as exc:
                self.recovery_failures[draft.id] = f"Recovery metadata needs verification: {exc}. Keep or export the current source."
                raise
            self.recovery_failures.pop(draft.id, None)

    @staticmethod
    def _body(body, extra):
        if not isinstance(body, dict) or set(body) != _BINDING_FIELDS | set(extra):
            raise ValueError("Choose the exact supported draft request fields.")
        return {field: body[field] for field in _BINDING_FIELDS}

    def _context(self, binding):
        return self.editor.current(binding)

    @staticmethod
    def _document(context, document_id):
        if not isinstance(document_id, str):
            raise ValueError("Choose an available document from this editor context.")
        selected = next((entry for entry in context["documents"] if entry["id"] == document_id), None)
        if selected is None or selected["availability"] != "available":
            raise ValueError("Choose an available document from this editor context.")
        return selected

    @staticmethod
    def _key(context, selected):
        return (context["workspace"], tuple(context["workspace_identity"]), context["fingerprint"],
                selected["id"], selected["path"], selected["sha256"])

    def _scope(self, body, context):
        draft_id = body["draft_id"]
        if not isinstance(draft_id, str) or not re.fullmatch(r"[a-f0-9]{32}", draft_id):
            raise ValueError("Choose a valid draft identity.")
        draft = self.drafts.get(draft_id)
        if draft is None:
            raise ValueError("This session draft is unavailable. Reopen the overlay; keep any pending local text.")
        selected = self._document(context, draft.document_id)
        if self._key(context, selected) != draft.key:
            raise ValueError("The draft belongs to a different or changed application context. Its text was retained.")
        return draft

    @staticmethod
    def _guard(body, draft):
        revision, digest = body["expected_draft_revision"], body["expected_draft_sha256"]
        if type(revision) is not int or revision < 0 or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Choose valid draft revision and source hash preconditions.")
        if revision != draft.revision or digest != _sha(draft.source):
            raise ValueError("The draft changed. Recheck it before retrying; your pending text must not replace a newer revision.")

    def _within_budget(self, candidate, *, replacing=None, removing=()):
        size = candidate.retained_bytes + sum(draft.retained_bytes for key, draft in self.drafts.items()
                                              if key != replacing and key not in removing)
        if size > self.max_bytes:
            raise ValueError("The session draft memory limit was reached. Existing drafts were retained; keep your pending source text.")

    @staticmethod
    def _visual_limit(document):
        return (len(document.syntax.nodes) > MAX_VISUAL_NODES
                or sum(len(node.properties) for node in document.syntax.nodes) > MAX_VISUAL_PROPERTIES)

    def _recovery_status(self, draft):
        if draft.id in self.recovery_failures:
            return {"state": "failed", "message": self.recovery_failures[draft.id]}
        if self.store is not None:
            return {"state": "durable", "message": "Acknowledged draft changes are retained for recovery. Unsent typing is not durable."}
        return {"state": "session", "message": "Drafts are retained only in this application session."}

    def _payload(self, draft):
        document = draft.document
        nodes = []
        if document is None:
            syntax = {"state": "incomplete", "blocked": True, "diagnostics": [
                {"code": "source-limit", "message": draft.parse_error, "start": 0, "end": len(draft.source)}]}
        else:
            parsed = document.syntax
            diagnostics = [{"code": item.code, "message": item.message, "start": item.start, "end": item.end}
                           for item in parsed.diagnostics[:MAX_DIAGNOSTICS]]
            if len(parsed.diagnostics) > MAX_DIAGNOSTICS:
                diagnostics.append({"code": "diagnostics-truncated", "message": "Additional source diagnostics were omitted.",
                                    "start": 0, "end": len(draft.source)})
            blocked = parsed.blocked or self._visual_limit(document)
            if self._visual_limit(document):
                diagnostics.append({"code": "visual-limit", "message": "This draft exceeds the visual node/property limit. Source editing and undo remain available.",
                                    "start": 0, "end": len(draft.source)})
            syntax = {"state": parsed.state, "blocked": blocked, "diagnostics": diagnostics}
            if not blocked:
                for node in parsed.nodes:
                    editable = document.can_edit_node(node.id)
                    deleted_names = {prop.name for prop in node.properties if prop.kind == "delete-property"}
                    properties = []
                    for prop in node.properties:
                        value = document.property_value(prop.id)
                        properties.append({"id": prop.id, "name": prop.name, "kind": prop.kind,
                            "start": prop.start, "end": prop.end,
                            "value": None if value is None else {"kind": value.kind,
                                "value": list(value.value) if value.kind == "cells" else value.value},
                            "editable": editable and value is not None and prop.name not in deleted_names})
                    nodes.append({"id": node.id, "name": node.name, "labels": list(node.labels),
                        "parent_id": node.parent_id, "start": node.start, "end": node.end,
                        "editable": editable, "properties": properties})
        return {"schema_version": 1, "id": draft.id, "context_fingerprint": draft.context_fingerprint,
                "document_id": draft.document_id, "path": draft.path, "revision": draft.revision,
                "sha256": _sha(draft.source), "base_sha256": _sha(draft.base),
                "text": draft.source.decode("utf-8"), "dirty": draft.source != draft.base,
                "can_undo": bool(draft.undo), "can_redo": bool(draft.redo),
                "history_limit": self.history_limit, "history_truncated": draft.history_truncated,
                "syntax": syntax, "nodes": nodes, "diff": _diff(draft.base, draft.source),
                "capabilities": {"edit": True, "save": self.publisher is not None and document is not None
                                 and document.syntax.state != "incomplete"},
                "recovery": self._recovery_status(draft)}

    def open(self, body):
        binding = self._body(body, {"document_id"})
        with self.editor.lock, self.lock:
            context = self._context(binding)
            selected = self._document(context, body["document_id"])
            key = self._key(context, selected)
            existing = next((draft for draft in self.drafts.values() if draft.key == key), None)
            if existing is not None:
                payload = self._payload(existing)
                self._scope({"draft_id": existing.id}, self._context(binding))
                return payload
            observed = self.editor.document({**binding, "document_id": selected["id"]})["document"]
            source = _text_bytes(observed["text"])
            observation = self.publisher.observe(context["workspace"], selected["path"]) if self.publisher is not None else None
            if observation is not None and (observation["sha256"] != observed["sha256"] or observation["text"] != observed["text"]):
                raise ValueError("The overlay changed while opening its draft. Open it again.")
            draft_id = uuid.uuid4().hex
            document, error = _parse(source, draft_id, 0)
            draft = Draft(draft_id, key, context["fingerprint"], context["workspace"], tuple(context["workspace_identity"]),
                          selected["id"], selected["path"], source, source, 0, document, error,
                          context=self._capture(context), observation=observation)
            removing = []
            if len(self.drafts) >= self.max_drafts:
                clean = next((item.id for item in self.drafts.values()
                              if item.source == item.base and not item.undo and not item.redo), None)
                if clean is None:
                    raise ValueError("The session draft count limit was reached. Existing edited drafts were retained.")
                removing.append(clean)
            self._within_budget(draft, removing=removing)
            payload = self._payload(draft)
            verified = self._context(binding)
            if self._key(verified, self._document(verified, selected["id"])) != key:
                raise ValueError("The editor context changed while opening the draft.")
            for draft_id in removing:
                del self.drafts[draft_id]
            self.drafts[draft.id] = draft
            return payload

    def get(self, body):
        binding = self._body(body, {"draft_id"})
        with self.editor.lock, self.lock:
            draft = self._scope(body, self._context(binding))
            payload = self._payload(draft)
            self._scope(body, self._context(binding))
            return payload

    def _commit(self, body, binding, draft, source, *, undo=None, redo=(), parsed=None):
        if source == draft.source:
            payload = self._payload(draft)
            self._scope(body, self._context(binding))
            return payload
        if undo is None:
            undo = (*draft.undo, draft.source)
        limit_reached = len(undo) + len(redo) > self.history_limit
        if limit_reached:
            keep = max(0, self.history_limit - len(redo))
            undo = undo[-keep:] if keep else ()
        revision = draft.revision + 1
        if parsed is not None:
            if parsed.id != draft.id or parsed.revision != revision or parsed.source != source:
                raise ValueError("The visual command returned an inconsistent draft revision.")
            document, error = parsed, None
        else:
            document, error = _parse(source, draft.id, revision)
        candidate = replace(draft, source=source, revision=revision, document=document, parse_error=error,
                            undo=tuple(undo), redo=tuple(redo),
                            history_truncated=draft.history_truncated or limit_reached)
        self._within_budget(candidate, replacing=draft.id)
        payload = self._payload(candidate)
        self._scope(body, self._context(binding))
        self._persist(candidate)
        self._scope(body, self._context(binding))
        self.drafts[draft.id] = candidate
        payload["recovery"] = self._recovery_status(candidate)
        return payload

    def source(self, body):
        binding = self._body(body, _GUARD_FIELDS | {"text"})
        source = _text_bytes(body["text"])
        with self.editor.lock, self.lock:
            draft = self._scope(body, self._context(binding))
            self._guard(body, draft)
            return self._commit(body, binding, draft, source)

    @staticmethod
    def _value(value):
        if not isinstance(value, dict) or set(value) != {"kind", "value"}:
            raise ValueError("Choose the exact typed property value fields.")
        if value["kind"] == "boolean":
            if value["value"] is not None:
                raise ValueError("A boolean property has no assigned value.")
            return PropertyValue.boolean()
        if value["kind"] == "string":
            return PropertyValue.string(value["value"])
        if value["kind"] == "cells":
            return PropertyValue.cells(value["value"])
        raise ValueError("Choose a supported literal property type.")

    def command(self, body):
        binding = self._body(body, _GUARD_FIELDS | {"command"})
        command = body["command"]
        if not isinstance(command, dict):
            raise ValueError("Choose a supported visual draft command.")
        kind = command.get("kind")
        if not isinstance(kind, str):
            raise ValueError("Choose a supported visual draft command kind.")
        fields = {"set_status": {"kind", "node_id", "status"},
                  "set_property": {"kind", "node_id", "name", "value"},
                  "remove_property": {"kind", "property_id"}}.get(kind)
        optional = {"property_id"} if kind == "set_property" else set()
        binding_command = self.bindings is not None and kind in self.bindings.commands
        peripheral_command = self.peripherals is not None and kind in self.peripherals.commands
        pin_command = self.pins is not None and kind in self.pins.commands
        module_command = self.modules is not None and kind in self.modules.commands
        removal_command = self.removal is not None and kind in self.removal.commands
        if not any((binding_command, peripheral_command, pin_command, module_command, removal_command)) and (fields is None or not fields <= set(command) or set(command) - fields - optional):
            raise ValueError("Choose the exact supported visual command fields.")
        with self.editor.lock, self.lock:
            context = self._context(binding)
            draft = self._scope(body, context)
            self._guard(body, draft)
            document = draft.document
            if document is None or document.syntax.blocked or self._visual_limit(document):
                raise ValueError("This draft has incomplete or unsupported structure. Repair the source before using visual controls.")
            if removal_command:
                change = self.removal.plan(body, context, draft)
            elif module_command:
                change = self.modules.plan(body, context, draft)
            elif pin_command:
                change = self.pins.plan(body, context, draft)
            elif peripheral_command:
                change = self.peripherals.plan(body, context, draft)
            elif binding_command:
                change = self.bindings.plan(body, context, draft)
            elif kind == "set_status":
                change = document.plan_set_status(command["node_id"], command["status"])
            elif kind == "set_property":
                change = document.plan_set_property(command["node_id"], command["name"], self._value(command["value"]),
                                                    property_id=command.get("property_id"))
            else:
                change = document.plan_remove_property(command["property_id"])
            candidate = change.apply(document)
            return self._commit(body, binding, draft, candidate.source, parsed=candidate)

    def _history(self, body, *, redo):
        binding = self._body(body, _GUARD_FIELDS)
        with self.editor.lock, self.lock:
            draft = self._scope(body, self._context(binding))
            self._guard(body, draft)
            stack = draft.redo if redo else draft.undo
            if not stack:
                return self._commit(body, binding, draft, draft.source)
            source = stack[-1]
            undo = (*draft.undo, draft.source) if redo else draft.undo[:-1]
            future = draft.redo[:-1] if redo else (*draft.redo, draft.source)
            return self._commit(body, binding, draft, source, undo=undo, redo=future)

    def undo(self, body):
        return self._history(body, redo=False)

    def redo(self, body):
        return self._history(body, redo=True)

    def close(self):
        with self.lock:
            self.drafts.clear()
            self.recovery_failures.clear()
