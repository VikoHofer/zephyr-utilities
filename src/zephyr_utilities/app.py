"""Composition root for one server, shared services and explicitly registered features."""

from http.server import ThreadingHTTPServer
from pathlib import Path, PurePosixPath
import secrets

from . import __version__
from .core.builds import BuildInspectionService
from .core.context import ApplicationContext
from .core.features import validate_features
from .core.http import Handler, Response, Router
from .core.jobs import JobManager
from .core.workspace import WorkspaceService, list_folders
from .features import builtin_features
from .features.projects.editor_context import ProjectsEditorContextProvider
from .features.projects.editor_validation import ProjectsValidationProvider

WEB_ROOT = Path(__file__).parent / "web"
MIME_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".woff2": "font/woff2",
}


class Application:
    def __init__(self, initial_path, data_dir, *, features=None):
        workspace = WorkspaceService(initial_path)
        self.features = validate_features(builtin_features() if features is None else features)
        self.token = secrets.token_urlsafe(32)
        self.context = ApplicationContext(workspace, JobManager(), Path(data_dir).expanduser().resolve(),
                                          builds=BuildInspectionService())
        self.router = Router()
        try:
            self._register_shared_routes()
            for feature in self.features:
                if feature.register:
                    feature.register(self.router, self.context)
            creator = self.context.services.get("project_creator")
            if creator is not None:
                self.context.project_context = ProjectsEditorContextProvider(self.context, creator)
                self.context.project_validation = ProjectsValidationProvider(self.context, creator)
        except Exception:
            self.close()
            raise

    @property
    def initial_path(self):
        return self.context.workspace.get()["path"]

    @property
    def store(self):
        """Compatibility access for the original Memory Explorer API and integrations."""
        return self.context.services["memory"].store

    def session(self):
        return {"token": self.token, "initial_path": self.initial_path,
                "storage_path": str(self.context.data_dir), "version": __version__}

    def _register_shared_routes(self):
        self.router.add("GET", "/api/features", lambda request: {
            "features": [feature.descriptor() for feature in self.features]})
        self.router.add("GET", "/api/workspace", lambda request: self.context.workspace.get())
        self.router.add("PATCH", "/api/workspace", lambda request: self.context.workspace.set(
            request.body.get("path")))
        self.router.add("GET", "/api/folders", lambda request: list_folders(
            request.query_value("path", self.initial_path)))
        self.router.add("GET", "/api/jobs", lambda request: {
            "jobs": self.context.jobs.list(request.query_value("feature") or None)})
        self.router.add("GET", "/api/jobs/:id", lambda request: self.context.jobs.get(request.params["id"]))
        self.router.add("POST", "/api/jobs/:id/cancel", lambda request: self.context.jobs.cancel(
            request.params["id"]))

    def asset(self, path):
        shell = {"/": "index.html", "/index.html": "index.html",
                 "/shell.js": "shell.js", "/shell.css": "shell.css"}
        if path in shell:
            root, relative = WEB_ROOT, shell[path]
        elif path.startswith("/assets/shell/"):
            root, relative = WEB_ROOT, path.removeprefix("/assets/shell/")
        elif path.startswith("/assets/"):
            parts = path.split("/", 3)
            if len(parts) != 4:
                raise FileNotFoundError("Asset not found.")
            feature = next((item for item in self.features if item.id == parts[2]), None)
            if feature is None or feature.frontend is None:
                raise FileNotFoundError("Asset not found.")
            root, relative = feature.frontend.asset_dir, parts[3]
        else:
            raise FileNotFoundError("Page not found.")
        parts = PurePosixPath(relative).parts
        if not parts or "\\" in relative or any(part.startswith(".") for part in parts):
            raise FileNotFoundError("Asset not found.")
        root = Path(root).resolve()
        target = (root / relative).resolve()
        if not target.is_relative_to(root) or target.suffix not in MIME_TYPES or not target.is_file():
            raise FileNotFoundError("Asset not found.")
        return Response(target.read_bytes(), content_type=MIME_TYPES[target.suffix])

    def close(self):
        self.context.jobs.close()
        for service in self.context.services.values():
            close = getattr(service, "close", None)
            if callable(close):
                close()


def create_server(initial_path, data_dir, port=8765, *, features=None):
    app = Application(initial_path, data_dir, features=features)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except Exception:
        app.close()
        raise
    server.daemon_threads = True
    server.app = app
    return server
