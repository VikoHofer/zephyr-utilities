"""Guarded diagnostic observations; findings never become global draft blockers."""

from copy import deepcopy
import hashlib
import time
import uuid

from .binding_service import BindingService
from .bindings import BindingCatalog
from .drafts import _GUARD_FIELDS
from .peripherals import PeripheralService
from .pwm_resources import pwm_diagnostics
from .resource_graph import MAX_DIAGNOSTICS, add_pin_state_consumers, build_resource_graph, diagnostic, local_diagnostics, pin_diagnostics


class ResourceService(BindingService):
    """Unify current local graph and optional verified pin evidence without execution."""

    commands = frozenset()

    def __init__(self, drafts, pins=None):
        super().__init__(drafts)
        self.pins = pins if pins is not None else drafts.pins

    def inspect(self, body):
        binding = self.drafts._body(body, _GUARD_FIELDS)
        with self.drafts.editor.lock, self.drafts.lock:
            context = self.drafts._context(binding)
            draft = self.drafts._scope(body, context)
            self.drafts._guard(body, draft)
            document = draft.document
            now = time.monotonic()
            self.observations = {key: value for key, value in self.observations.items() if value["expires"] > now}
            catalog, pin_evidence = None, None
            pins = {"supported": False, "reason": "Pin capabilities are unknown without a verified target adapter.", "adapter": None, "target": (context.get("target") or {}).get("requested")}
            if document is None or document.syntax.blocked or self.drafts._visual_limit(document):
                reason = draft.parse_error or "Source is incomplete, structurally unsupported or exceeds the visual inspection limit."
                graph = {"nodes": [], "references": [], "pin_state_consumers": [], "coverage": "incomplete-source", "external_unknown": True,
                         "unknown_properties": [], "truncated": False}
                payload = {"nodes": [], "bindings": {"nodes": [], "targets": []}, "graph": graph,
                           "coverage": "incomplete-source", "diagnostics": [diagnostic("source-coverage-unknown", "unknown", reason)]}
            else:
                roots = self._roots(context)
                for observed in reversed(list(self.observations.values())):
                    candidate = observed.get("catalog")
                    if candidate and candidate.workspace == context["workspace"] and candidate.roots == roots:
                        try:
                            candidate.revalidate()
                            catalog = candidate
                        except ValueError:
                            pass
                        break
                if catalog is None:
                    catalog = BindingCatalog(context["workspace"], roots)
                projection = PeripheralService._projection(document, catalog)
                graph = build_resource_graph(document, projection)
                diagnostics = local_diagnostics(document, projection, graph)
                diagnostics.extend(pwm_diagnostics(document, projection))
                diagnostics.extend(diagnostic("binding-catalogue-coverage", "unknown", message) for message in catalog.diagnostics)
                if self.pins:
                    try:
                        pins = self.pins.inspect(body)
                        pin_evidence = self.pins.observations[pins["schema_id"]]["evidence"]
                    except (OSError, ValueError) as exc:
                        pins["reason"] = str(exc)
                diagnostics.extend(pin_diagnostics(document, pins))
                if add_pin_state_consumers(document, graph, pins):
                    diagnostics.append(diagnostic("pin-state-consumers-truncated", "unknown", "Known pin-state consumers exceed the bounded graph limit. Removal remains unavailable for a truncated dependency graph."))
                diagnostics.append(diagnostic("local-coverage", "info", "Checks cover this overlay's literal declarations and any verified pin adapter evidence. Inherited hardware, external consumers, electrical suitability and runtime behavior remain unknown."))
                payload = {"nodes": graph["nodes"], "bindings": projection, "graph": graph,
                           "coverage": "literal-local-source", "diagnostics": diagnostics}
                catalog.revalidate()
            self.drafts._scope(body, self.drafts._context(binding))
            self.drafts._guard(body, self.drafts.drafts[draft.id])
            if len(payload["diagnostics"]) > MAX_DIAGNOSTICS:
                payload["diagnostics"] = payload["diagnostics"][:MAX_DIAGNOSTICS - 1] + [diagnostic("diagnostics-truncated", "unknown", "The diagnostic list exceeds its 256-entry limit. Source and the dependency graph remain available for inspection.")]
            schema_id = uuid.uuid4().hex
            fingerprint = hashlib.sha256(((catalog.fingerprint if catalog else "incomplete") + ":" + str(pins.get("fingerprint", "unknown"))).encode()).hexdigest()
            payload.update(schema_version=1, schema_id=schema_id, fingerprint=fingerprint,
                           draft_id=draft.id, revision=draft.revision, sha256=hashlib.sha256(draft.source).hexdigest(),
                           pins={key: pins.get(key) for key in ("supported", "reason", "adapter", "target")})
            while len(self.observations) >= 16:
                self.observations.pop(next(iter(self.observations)))
            self.observations[schema_id] = {"guard": deepcopy(body), "catalog": catalog, "pin_evidence": pin_evidence,
                                           "payload": deepcopy(payload), "expires": time.monotonic() + 600}
            return payload

    def validate_observation(self, body, context, draft):
        """Consumers hold editor/draft locks; exact scope and all observed files revalidate."""
        self.drafts._body(body, _GUARD_FIELDS | {"schema_id"})
        if not isinstance(body["schema_id"], str):
            raise ValueError("Load current resource diagnostics before reviewing removal.")
        guard = {key: value for key, value in body.items() if key != "schema_id"}
        observed = self.observations.get(body["schema_id"])
        if not observed or observed["guard"] != guard or observed["expires"] <= time.monotonic():
            raise ValueError("Resource diagnostics belong to an earlier draft/context or expired. Load diagnostics again.")
        self.drafts._scope(guard, context)
        self.drafts._guard(guard, draft)
        if observed["catalog"]:
            observed["catalog"].revalidate()
        if observed["pin_evidence"]:
            observed["pin_evidence"].revalidate()
        return deepcopy(observed["payload"])
