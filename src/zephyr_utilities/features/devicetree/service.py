"""Read-only editor contexts pinned to project selections and current overlay bytes."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from threading import RLock
import time
import uuid

from ...core.source_files import WorkspaceSourceReader


MAX_OVERLAYS = 128
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
EXCLUDED_DIRS = frozenset({"build", "out", "output", "dist", "target", "node_modules", "venv",
                           "__pycache__", "CMakeFiles", "generated", "cache", "tools"})
EXCLUDED_PREFIXES = (".", "build-", "build_", "cmake-build-")
BUILD_MARKERS = ("CMakeCache.txt", "build.ninja")


def _diagnostic(code, message):
    return {"code": code, "severity": "warning", "message": message}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@dataclass
class EditorScope:
    payload: dict
    selection: dict | None
    source_guard: dict | None
    expires: float


class EditorService:
    """Bounded session observations; no parser, writer, job submission or persistence."""

    def __init__(self, context, *, max_contexts=16, ttl=1800):
        if type(max_contexts) is not int or not 1 <= max_contexts <= 64 or not 0 < ttl <= 86400:
            raise ValueError("Choose bounded editor context retention.")
        self.context = context
        self.max_contexts, self.ttl = max_contexts, ttl
        self.contexts, self.lock = {}, RLock()

    def _workspace(self, body, fields):
        if not isinstance(body, dict) or set(body) != {"workspace", *fields}:
            raise ValueError("Choose the exact supported editor selection fields.")
        workspace = self.context.workspace.get()["path"]
        if body["workspace"] != workspace:
            raise ValueError("The selected workspace changed. Open the editor context again.")
        return workspace

    def _provider(self):
        provider = self.context.project_context
        if provider is None:
            raise ValueError("Project selection is unavailable. Open an application directory or enable Projects.")
        return provider

    def catalog(self, body):
        workspace = self._workspace(body, ())
        if self.context.project_context is None:
            return {"workspace": workspace, "projects": [], "diagnostics": [
                _diagnostic("projects-unavailable", "Project selection is unavailable; an application directory can still be opened.")]}
        return deepcopy(self._provider().catalog(workspace))

    @staticmethod
    def _source_directory(reader, application):
        if application == reader.workspace:
            raise ValueError("Choose an application directory inside the workspace, not the workspace itself.")
        identity = reader.directory_identity(application)
        for name in BUILD_MARKERS:
            try:
                (application / name).lstat()
            except FileNotFoundError:
                continue
            raise ValueError("Choose application sources, not a generated build directory.")
        marker = reader.read_text(application / "CMakeLists.txt", limit=MAX_DOCUMENT_BYTES)
        if identity != reader.directory_identity(application):
            raise ValueError("The application directory changed while opening its sources.")
        return {"directory": list(identity), "cmake_sha256": marker["sha256"]}

    @staticmethod
    def _path_in_application(reader, application, value):
        path = reader.path(value, base=application)
        if not path.is_relative_to(application) or path.suffix != ".overlay":
            raise ValueError("Choose an overlay inside the selected application.")
        relative = path.relative_to(application)
        if (any(part in EXCLUDED_DIRS or part.startswith(EXCLUDED_PREFIXES) for part in relative.parts[:-1])
                or relative.name.startswith(".")):
            raise ValueError("Choose an overlay outside hidden, generated and tool directories.")
        return path

    def _document_observation(self, reader, application, path):
        summary = {"id": hashlib.sha256(str(path).encode()).hexdigest()[:32], "path": str(path),
                   "relative_path": path.relative_to(application).as_posix(), "sha256": None,
                   "availability": "available", "reason": "", "origins": ["Current application overlay"],
                   "usage": "unknown"}
        try:
            parent = path.parent
            while parent != application:
                reader.directory_identity(parent)
                for marker in BUILD_MARKERS:
                    try:
                        (parent / marker).lstat()
                    except FileNotFoundError:
                        continue
                    raise ValueError("Overlay is inside a generated build directory.")
                parent = parent.parent
            observed = reader.read_text(path, limit=MAX_DOCUMENT_BYTES)
            summary["sha256"] = observed["sha256"]
        except FileNotFoundError:
            summary.update(availability="missing", reason="The selected overlay file is missing.")
        except (OSError, ValueError) as exc:
            summary.update(availability="unavailable", reason=str(exc))
        if reader.bytes_read > MAX_TOTAL_BYTES:
            raise ValueError("Editor source observation exceeded the 8 MiB read limit.")
        return summary

    def _documents(self, reader, application, payload):
        paths = {path for path in reader.list_files(application, {".overlay"}, max_files=1000,
                    max_dirs=256, max_depth=16, exclude_dirs=EXCLUDED_DIRS,
                    exclude_prefixes=EXCLUDED_PREFIXES, stop_names=BUILD_MARKERS)
                 if not path.name.startswith(".")}
        fragments = payload.get("fragments")
        explicit = fragments["overlays"]["files"] if fragments else []
        default = payload.get("default_overlay")
        wanted = [*explicit, *([default["path"]] if default and default.get("path") else [])]
        for value in wanted:
            try:
                paths.add(self._path_in_application(reader, application, value))
            except (OSError, ValueError) as exc:
                payload["diagnostics"].append(_diagnostic("overlay-unavailable", f"{value}: {exc}"))
        if len(paths) > MAX_OVERLAYS:
            raise ValueError("The editor supports at most 128 overlay candidates per context.")
        documents = []
        predicted = payload.get("input_evidence", {}).get("predicted", [])
        captured = payload.get("input_evidence", {}).get("captured", [])
        for path in sorted(paths):
            summary = self._document_observation(reader, application, path)
            if summary["relative_path"] in explicit:
                summary["origins"].append("Selected fragment recipe")
            if default and summary["relative_path"] == default.get("path"):
                summary["origins"].append("Exact-target default overlay")
            for reports, usage in ((captured, "captured"), (predicted, "predicted")):
                for report in reports:
                    for item in report.get("files", []):
                        if item.get("path") in {summary["relative_path"], summary["path"]}:
                            if item.get("state") in {"active", "expected", "selected"}:
                                summary["usage"] = usage
            documents.append(summary)
        return documents

    def open(self, body):
        registered = isinstance(body, dict) and "project_id" in body
        workspace = self._workspace(body, ("project_id", "configuration_id") if registered else ("application_path",))
        reader = WorkspaceSourceReader(workspace)
        if registered:
            selection = self._provider().capture(workspace, body["project_id"], body["configuration_id"])
            payload = deepcopy(selection)
            application = reader.path(payload["application"]["path"])
        else:
            application = reader.path(body["application_path"])
            selection = None
            payload = {"schema_version": 1, "workspace": workspace,
                       "workspace_identity": list(reader.directory_identity(workspace)),
                       "application": {"path": str(application), "name": application.name, "identity": None,
                                       "availability": {"state": "available", "reason": ""}},
                       "project": None, "configuration": None, "target": None, "environment": None,
                       "fragments": None, "default_overlay": None, "roots": [],
                       "input_evidence": {"predicted": [], "captured": [], "coverage": "partial"},
                       "diagnostics": [_diagnostic("unconfigured-sources",
                           "Directory-only context: target, environment and overlay selection are unknown. No project registration was created.")]}
        payload.update(id=uuid.uuid4().hex, revision=1, image=None, build_evidence=None,
                       documents=[], editing_document=None, capabilities={"edit": False, "save": False})
        source_guard = None
        if payload["application"]["availability"]["state"] == "available":
            try:
                source_guard = self._source_directory(reader, application)
                payload["documents"] = self._documents(reader, application, payload)
                if source_guard != self._source_directory(reader, application):
                    raise ValueError("The application inputs changed while being observed.")
            except (OSError, ValueError) as exc:
                if not registered:
                    raise ValueError(f"Cannot open application sources: {exc}") from exc
                payload["application"]["availability"] = {"state": "unavailable", "reason": str(exc)}
                payload["diagnostics"].append(_diagnostic("sources-unavailable", str(exc)))
                payload["documents"], source_guard = [], None
        default = payload.get("default_overlay")
        if default and payload.get("target") and default.get("target") == payload["target"]["requested"]:
            payload["editing_document"] = next((deepcopy(doc) for doc in payload["documents"]
                                                 if doc["relative_path"] == default.get("path")
                                                 and doc["availability"] == "available"
                                                 and doc["usage"] == "predicted"), None)
        payload["fingerprint"] = _fingerprint({"workspace": workspace, "application": str(application),
                                               "selection": selection, "sources": source_guard,
                                               "documents": payload["documents"]})
        if registered:
            self._provider().revalidate(selection)
        if self.context.workspace.get()["path"] != workspace:
            raise ValueError("The selected workspace changed. Open the editor context again.")
        with self.lock:
            self._expire()
            while len(self.contexts) >= self.max_contexts:
                self.contexts.pop(next(iter(self.contexts)))
            self.contexts[payload["id"]] = EditorScope(deepcopy(payload), selection, source_guard,
                                                       time.monotonic() + self.ttl)
        return deepcopy(payload)

    def _expire(self):
        current = time.monotonic()
        self.contexts = {key: value for key, value in self.contexts.items() if value.expires > current}

    def _scope(self, body, extra=()):
        workspace = self._workspace(body, ("context_id", "expected_revision", *extra))
        if not isinstance(body["context_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", body["context_id"]):
            raise ValueError("Choose a valid editor context.")
        if type(body["expected_revision"]) is not int or body["expected_revision"] < 1:
            raise ValueError("Choose a valid editor context revision.")
        self._expire()
        scope = self.contexts.get(body["context_id"])
        if scope is None:
            raise ValueError("The editor context expired. Open the application again.")
        payload = scope.payload
        if payload["workspace"] != workspace or payload["revision"] != body["expected_revision"]:
            raise ValueError("The editor context changed. Open the application again.")
        reader = WorkspaceSourceReader(workspace)
        if list(reader.directory_identity(workspace)) != payload["workspace_identity"]:
            raise ValueError("The workspace directory changed. Open the application again.")
        if scope.selection is not None:
            self._provider().revalidate(scope.selection)
        application = reader.path(payload["application"]["path"])
        if scope.source_guard is not None:
            if self._source_directory(reader, application) != scope.source_guard:
                raise ValueError("The application source context changed. Open it again.")
            current_documents = self._documents(reader, application, deepcopy(payload))
            if self._source_directory(reader, application) != scope.source_guard:
                raise ValueError("The application source context changed. Open it again.")
            if current_documents != payload["documents"]:
                raise ValueError("An overlay changed or became unavailable. Open the application again.")
        if self.context.workspace.get()["path"] != workspace:
            raise ValueError("The selected workspace changed. Open the editor context again.")
        return scope, reader

    def current(self, body):
        with self.lock:
            scope, _ = self._scope(body)
            return deepcopy(scope.payload)

    @staticmethod
    def _selected(scope, document_id):
        if not isinstance(document_id, str):
            raise ValueError("Choose an overlay from this editor context.")
        selected = next((doc for doc in scope.payload["documents"] if doc["id"] == document_id), None)
        if selected is None or selected["availability"] != "available":
            raise ValueError("Choose an available overlay from this editor context.")
        return selected

    def select(self, body):
        with self.lock:
            scope, _ = self._scope(body, ("document_id",))
            selected = self._selected(scope, body["document_id"])
            scope.payload["editing_document"] = deepcopy(selected)
            scope.payload["revision"] += 1
            return deepcopy(scope.payload)

    def document(self, body):
        with self.lock:
            scope, reader = self._scope(body, ("document_id",))
            selected = self._selected(scope, body["document_id"])
            document = reader.read_text(reader.path(selected["path"]), limit=MAX_DOCUMENT_BYTES)
            if document["sha256"] != selected["sha256"]:
                raise ValueError("The overlay changed. Open the application again.")
            if self._source_directory(reader, reader.path(scope.payload["application"]["path"])) != scope.source_guard:
                raise ValueError("The application source context changed. Open it again.")
            if self.context.workspace.get()["path"] != scope.payload["workspace"]:
                raise ValueError("The selected workspace changed. Open the editor context again.")
            return {"context_id": scope.payload["id"], "revision": scope.payload["revision"],
                    "document": {**deepcopy(selected), "text": document["text"]}}

    def close(self):
        with self.lock:
            self.contexts.clear()
