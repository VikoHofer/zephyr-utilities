"""Read-only MNIST catalogue and transient selections for the existing creator.

This narrow version-one admission extends the D0 handoff shape for the local
MNIST recipe. Tokens select form inputs; ordinary Preview/Create still owns every
source write, and Configure/Build remain separate explicit actions.
"""

from copy import deepcopy
import re
from threading import RLock
import time
import uuid

from .creation import select_profile
from .execution_identity import execution_guard
from .mnist_sample import DESCRIPTION, SAMPLE_ID, TITLE
from .records import identifier
from .sample_check_identity import sample_input_guard
from .samples import preview_sample


_DEMO_ID = "mnist-shell"
_VARIANT_ID = "reference"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_SELECTION = {"board_id", "target", "sdk_id"}
_MAX_HANDOFFS = 32
_RETENTION = 1800
_ANALYZE = "Analyze this workspace in Projects Environment before choosing an ML demo."


class DemoHandoffService:
    """Own bounded server-memory selections without publishing files or jobs."""

    def __init__(self, context, creator):
        self.context = context
        self.creator = creator
        self.lock = RLock()
        self.handoffs = {}

    def _workspace(self, body, allowed=()):
        if not isinstance(body, dict) or set(body) - {"workspace", *allowed}:
            raise ValueError("Unknown ML demo selection option.")
        workspace = body.get("workspace")
        current = self.context.workspace.get()["path"]
        if not isinstance(workspace, str) or workspace != current:
            raise ValueError("This ML demo selection belongs to another workspace. Return to Demos and choose again.")
        return workspace

    def _snapshot(self, workspace):
        snapshot = self.creator.environments.latest(workspace)
        if (not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1 or
                snapshot.get("status") not in {"complete", "partial"} or
                not isinstance(snapshot.get("workspace"), dict) or snapshot["workspace"].get("path") != workspace or
                not isinstance(snapshot.get("fingerprint"), str) or not _SHA.fullmatch(snapshot["fingerprint"])):
            return None
        return snapshot

    def _source(self, snapshot):
        preview = preview_sample(snapshot, SAMPLE_ID, {"cwd": "", "display_configure": "", "display_build": ""},
                                 mnist_assets_root=self.creator.mnist_assets.path)
        provenance = preview["provenance"]
        fingerprint = provenance.get("source_fingerprint", provenance.get("fingerprint"))
        if not isinstance(fingerprint, str) or not _SHA.fullmatch(fingerprint):
            raise ValueError("The local MNIST runtime has no qualified source identity. Return to Demos and refresh.")
        return fingerprint, provenance

    def catalog(self, body):
        workspace = self._workspace(body)
        snapshot = self._snapshot(workspace)
        entry = {"demo_id": _DEMO_ID, "variant_id": _VARIANT_ID, "sample_id": SAMPLE_ID,
                 "title": TITLE, "framework": "TensorFlow Lite Micro", "description": DESCRIPTION,
                 "status": "missing", "requirements": [], "diagnostics": [], "source_fingerprint": None}
        diagnostics = []
        if snapshot is None:
            entry["diagnostics"] = [_ANALYZE]
            diagnostics = [_ANALYZE]
        else:
            try:
                fingerprint, provenance = self._source(snapshot)
                entry.update(status="available", source_fingerprint=fingerprint,
                             requirements=deepcopy(provenance["requirements"]))
            except FileNotFoundError as exc:
                entry["diagnostics"] = [f"A required local MNIST runtime or firmware dependency is missing: {exc.filename or exc}"]
            except (OSError, ValueError) as exc:
                entry.update(status="unsupported", diagnostics=[str(exc)])
        self._workspace(body)
        return {"schema_version": 1, "workspace": workspace, "snapshot": deepcopy(snapshot),
                "environment_fingerprint": snapshot["fingerprint"] if snapshot else None,
                "demos": [entry], "diagnostics": diagnostics}

    def _binding(self, workspace, selection):
        snapshot = self._snapshot(workspace)
        if snapshot is None:
            raise ValueError(_ANALYZE)
        profile = select_profile(workspace, selection, snapshot)
        source_fingerprint, _ = self._source(snapshot)
        binding = {"environment_fingerprint": snapshot["fingerprint"], "source_fingerprint": source_fingerprint,
                   "execution_guard": execution_guard(snapshot, profile), "input_guard": sample_input_guard(profile)}
        self._workspace({"workspace": workspace})
        if self._source(snapshot)[0] != source_fingerprint:
            raise ValueError("The MNIST source or dependency changed during selection. Return to Demos and refresh.")
        latest = self._snapshot(workspace)
        if latest is None or latest["fingerprint"] != snapshot["fingerprint"]:
            raise ValueError("The environment changed during ML demo selection. Return to Demos and refresh.")
        self._workspace({"workspace": workspace})
        return binding

    def _prune(self):
        current = time.monotonic()
        self.handoffs = {key: value for key, value in self.handoffs.items() if value["expires"] > current}

    def create(self, body):
        fields = {"demo_id", "variant_id", "source_fingerprint", "environment_fingerprint", "selection"}
        workspace = self._workspace(body, fields)
        if set(body) != {"workspace", *fields}:
            raise ValueError("Choose the MNIST demo, a complete target/SDK selection and current catalogue fingerprints.")
        if body["demo_id"] != _DEMO_ID or body["variant_id"] != _VARIANT_ID:
            raise ValueError("Choose an available curated ML demo and variant.")
        for name in ("source_fingerprint", "environment_fingerprint"):
            if not isinstance(body[name], str) or not _SHA.fullmatch(body[name]):
                raise ValueError("ML demo selections need current SHA-256 source and environment fingerprints.")
        selection = body["selection"]
        if not isinstance(selection, dict) or set(selection) != _SELECTION:
            raise ValueError("Choose a complete board target and SDK selection from the catalogue.")
        selection = deepcopy(selection)
        binding = self._binding(workspace, selection)
        if any(binding[name] != body[name] for name in ("source_fingerprint", "environment_fingerprint")):
            raise ValueError("The ML demo source or environment changed. Return to Demos and refresh before selecting it again.")
        value = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": workspace,
                 "demo_id": _DEMO_ID, "variant_id": _VARIANT_ID, "title": TITLE,
                 "source_fingerprint": binding["source_fingerprint"], "environment_fingerprint": binding["environment_fingerprint"],
                 "selection": selection, "template_id": "local-sample", "sample_id": SAMPLE_ID}
        with self.lock:
            self._prune()
            while len(self.handoffs) >= _MAX_HANDOFFS:
                self.handoffs.pop(next(iter(self.handoffs)))
            self.handoffs[value["id"]] = {"value": value, "binding": binding, "expires": time.monotonic() + _RETENTION}
        return {"id": value["id"], "route": f"projects/create/{value['id']}"}

    def resolve(self, handoff_id, workspace):
        workspace = self._workspace({"workspace": workspace})
        identifier(handoff_id)
        with self.lock:
            self._prune()
            saved = deepcopy(self.handoffs.get(handoff_id))
        if saved is None:
            raise ValueError("This ML demo selection expired or is unavailable after restart. Return to Demos and choose again.")
        value = saved["value"]
        if value["workspace"] != workspace:
            raise ValueError("This ML demo selection belongs to another workspace. Return to Demos and choose again.")
        if self._binding(workspace, value["selection"]) != saved["binding"]:
            raise ValueError("This ML demo selection is stale because its source, dependency or environment changed. Return to Demos and choose again.")
        with self.lock:
            self._prune()
            if self.handoffs.get(handoff_id) != saved:
                raise ValueError("This ML demo selection expired or is no longer available. Return to Demos and choose again.")
        return deepcopy(value)
