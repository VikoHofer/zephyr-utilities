"""Guarded asynchronous chip observations through the application's shared job queue."""

from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
import json
from threading import Event, RLock
import time

from .board_topology import BoardTopologyService
from .drafts import _BINDING_FIELDS, _GUARD_FIELDS


class _AttemptCancellation:
    """A newer source revision interrupts this attempt without cancelling its job."""

    def __init__(self, shared, attempt):
        self.shared, self.attempt = shared, attempt

    def is_set(self):
        return self.shared.is_set() or self.attempt.is_set()

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return False
            self.attempt.wait(0.05 if remaining is None else min(0.05, remaining))
        return True


class ChipWorkspaceService:
    """Observe a captured target automatically; source publication remains separate."""

    def __init__(self, drafts):
        self.drafts = drafts
        self.editor = drafts.editor
        self.jobs = self.editor.context.jobs
        self.records = {}
        self.lock = RLock()
        self.closed = False

    def _capture(self, body):
        if not isinstance(body, dict) or set(body) not in (_BINDING_FIELDS, _BINDING_FIELDS | _GUARD_FIELDS):
            raise ValueError("Choose the chip context and, when available, all current draft guards.")
        binding = {key: body[key] for key in _BINDING_FIELDS}
        with self.editor.lock, self.drafts.lock:
            context = self.editor.current(binding)
            draft = None
            if _GUARD_FIELDS <= set(body):
                draft = self.drafts._scope(body, context)
                self.drafts._guard(body, draft)
            return context, draft

    def _revalidate(self, record, *, sources=False):
        self._capture(record["guard"])
        observation = record.get("observation")
        if sources and observation:
            for name in ("sources", "catalog"):
                if observation.get(name) is not None:
                    observation[name].revalidate()

    @staticmethod
    def _envelope(job):
        return {"job_id": job["id"], "status": job["status"], "result": deepcopy(job.get("result")),
                "error": job.get("error"), "logs": list(job.get("logs", []))}

    def _prune(self):
        for identifier, record in list(self.records.items()):
            try:
                job = self.jobs.get(identifier)
            except FileNotFoundError:
                del self.records[identifier]
                continue
            if record["expires"] <= time.monotonic():
                if job["status"] in {"queued", "running"}:
                    self.jobs.cancel(identifier)
                del self.records[identifier]
        while len(self.records) >= 16:
            idle = next((identifier for identifier in self.records
                         if self.jobs.get(identifier)["status"] not in {"queued", "running"}), None)
            if idle is None:
                raise ValueError("Too many chip observations are running. Wait for one to finish.")
            del self.records[idle]

    def start(self, body):
        context, draft = self._capture(body)
        key = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if self.closed:
                raise ValueError("Chip discovery is shutting down.")
            self._prune()
            for identifier, existing in self.records.items():
                if existing["key"] != key or existing.get("cancel_requested"):
                    continue
                job = self.jobs.get(identifier)
                if job["status"] not in {"queued", "running", "completed"}:
                    continue
                if job["status"] == "completed" and not (job.get("result") or {}).get("supported"):
                    continue
                if (job["status"] == "completed" and (job.get("result") or {}).get("draft_id")
                        and job["result"].get("coverage") == "board-only"):
                    continue
                try:
                    self._revalidate(existing, sources=job["status"] == "completed")
                except (OSError, ValueError):
                    continue
                return self._envelope(job)
            if draft is not None:
                for identifier, existing in self.records.items():
                    old = existing["guard"]
                    if (old.get("draft_id") != draft.id or old["context_id"] != body["context_id"]
                            or old["workspace"] != body["workspace"] or existing["worker_finished"]
                            or existing.get("cancel_requested")):
                        continue
                    job = self.jobs.get(identifier)
                    if job["status"] not in {"queued", "running"}:
                        continue
                    if old["expected_draft_revision"] > body["expected_draft_revision"]:
                        raise ValueError("A newer chip observation already owns this draft. Refresh the current source.")
                    existing["attempt_cancel"].set()
                    existing.update(key=key, guard=deepcopy(body), context=context, draft=draft,
                                    observation=None, expires=time.monotonic() + 1800)
                    return self._envelope(job)
            record = {"key": key, "guard": deepcopy(body), "context": context, "draft": draft,
                      "observation": None, "attempt_cancel": Event(), "worker_finished": False,
                      "expires": time.monotonic() + 1800}

            def discover(emit, cancel):
                while True:
                    with self.lock:
                        snapshot = {name: record[name] for name in ("key", "guard", "context", "draft")}
                        attempt = record["attempt_cancel"] = Event()
                    if cancel.is_set():
                        raise CancelledError()
                    try:
                        payload, observed = self._observe(snapshot["context"], snapshot["draft"], snapshot["guard"],
                                                          emit, _AttemptCancellation(cancel, attempt))
                    except Exception:
                        with self.lock:
                            if not cancel.is_set() and record["key"] != snapshot["key"]:
                                continue
                            record["worker_finished"] = True
                        raise
                    with self.lock:
                        if cancel.is_set():
                            record["worker_finished"] = True
                            raise CancelledError()
                        if record["key"] != snapshot["key"]:
                            continue
                        record["observation"] = observed
                        record["worker_finished"] = True
                        return payload

            job = self.jobs.submit("devicetree", discover, resource_key="chip:" + key,
                                   metadata={"kind": "chip-discovery", "context_id": context["id"],
                                             "workspace": context["workspace"], "target": context.get("target")})
            self.records[job["id"]] = record
            return self._envelope(job)

    def _observe(self, context, draft, guard, emit, cancel):
        emit("Reading the selected chip and board sources…")
        if cancel.is_set():
            raise CancelledError()
        observed = BoardTopologyService.observe(context, draft, cancel=cancel)
        assignment_reason = ""
        if draft is not None and not observed["payload"]["supported"]:
            assignment_reason = observed["payload"]["reason"]
            emit("Application assignments are unavailable. Reading board defaults…")
            observed = BoardTopologyService.observe(context, cancel=cancel)
        topology = observed["payload"]
        if cancel.is_set():
            raise CancelledError()
        if topology["supported"]:
            from .chip_catalog import observe_chip
            emit("Reading pin capabilities and configured assignments…")
            payload = observe_chip(observed["sources"], topology, observed["document"], target=context.get("target"))
        else:
            target = context.get("target") or {}
            payload = {"schema_version": 1, "supported": False, "reason": topology["reason"],
                       "chip": {"name": target.get("requested") or "Selected chip", "vendor": None,
                                "soc": None, "package": None, "layout": "logical"},
                       "pins": [], "diagnostics": topology["diagnostics"]}
        coverage = topology["coverage"]
        if coverage == "board-only":
            reason = (observed["sources"].composition_reason if observed.get("sources") else
                      "No application overlay is applied to this chip observation.")
            assignment_reason = " ".join(filter(None, (assignment_reason, reason)))
        payload.update(context_id=context["id"], expected_revision=context["revision"],
                       target=deepcopy(context.get("target")), board=topology["board"],
                       draft_id=draft.id if draft else None, draft_revision=draft.revision if draft else None,
                       draft_sha256=hashlib.sha256(draft.source).hexdigest() if draft else None,
                       coverage=coverage, assignment_reason=assignment_reason)
        from .chip_activation import prepare_activation
        payload["activation"] = prepare_activation(observed, payload, draft)
        payload["editing_available"] = payload["activation"]["available"]
        self._revalidate({"guard": guard, "observation": observed}, sources=True)
        if cancel.is_set():
            raise CancelledError()
        emit("Chip observation ready." if payload.get("supported") else "Chip support is unavailable for this selection.")
        return payload, observed

    def _request(self, body):
        allowed = (_BINDING_FIELDS | {"job_id"}, _BINDING_FIELDS | _GUARD_FIELDS | {"job_id"})
        if not isinstance(body, dict) or set(body) not in allowed or not isinstance(body["job_id"], str):
            raise ValueError("Choose a chip discovery job from this editor context.")
        binding = {key: body[key] for key in _BINDING_FIELDS}
        self.editor.current(binding)
        with self.lock:
            record = self.records.get(body["job_id"])
            if record is None or record["expires"] <= time.monotonic():
                raise ValueError("This chip observation expired. Reopen the chip workspace.")
            if binding != {key: record["guard"][key] for key in _BINDING_FIELDS}:
                raise ValueError("The chip discovery job belongs to another editor context.")
            if _GUARD_FIELDS <= set(body) and any(body[name] != record["guard"].get(name) for name in _GUARD_FIELDS):
                raise ValueError("The chip discovery job belongs to another draft revision. Refresh the current source.")
        return record, self.jobs.get(body["job_id"])

    def result(self, body):
        record, job = self._request(body)
        self._revalidate(record, sources=job["status"] == "completed")
        return self._envelope(job)

    def cancel(self, body):
        with self.lock:
            record, job = self._request(body)
            if job["status"] in {"queued", "running"}:
                record["cancel_requested"] = True
                job = self.jobs.cancel(job["id"])
        return self._envelope(job)

    def close(self):
        with self.lock:
            self.closed = True
            self.records.clear()
