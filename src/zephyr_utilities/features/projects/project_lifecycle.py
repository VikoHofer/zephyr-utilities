"""Reviewed, metadata-only import and reconnection of existing applications."""

from copy import deepcopy
import os
import unicodedata
import uuid

from .application_inputs import source_inputs
from .builds import command_recipe
from .creation import _path, _validate_destination, path_identity, select_profile, validate_identity
from .execution_identity import execution_guard
from .metadata import read_regular
from .records import identifier, now


MAX_RECONNECTIONS = 128


def _application_name(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 64 or
            any(unicodedata.category(character).startswith("C") for character in value)):
        raise ValueError("Application names need 1 to 64 readable characters without control characters.")
    return value.strip()


def _location(workspace, value, snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("status") not in {"complete", "partial"}:
        raise ValueError("Analyze the workspace before importing or reconnecting an application.")
    path = _validate_destination(workspace, _path(value, "Application directory"), snapshot)
    identity = path_identity(path)
    # Existing application CMake is retained as data. Import never interprets it.
    try:
        read_regular(path / "CMakeLists.txt", 1024 * 1024).decode("utf-8")
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("Choose an existing application with a readable root CMakeLists.txt.") from exc
    before = source_inputs(path)
    after = source_inputs(path)
    validate_identity(path, identity)
    if before != after:
        raise ValueError("Application files changed during inspection. Review the location again.")
    return path, identity, after


def _leaf(identity):
    if not isinstance(identity, list) or not identity or not isinstance(identity[-1], dict):
        return None
    return identity[-1].get("device"), identity[-1].get("inode")


def _difference(previous, current):
    old = {item["path"]: item.get("sha256") for item in previous if isinstance(item, dict) and "path" in item}
    new = {item["path"]: item["sha256"] for item in current}
    return {"added": sorted(new.keys() - old.keys()), "removed": sorted(old.keys() - new.keys()),
            "changed": sorted(path for path in old.keys() & new.keys() if old[path] != new[path])}


def _registration(project):
    saved = project.get("registration")
    if saved is None:
        return {"schema_version": 1, "origin": "created", "initial_path": project["path"],
                "initial_directory_identity": deepcopy(project["directory_identity"]), "reconnections": []}
    if (not isinstance(saved, dict) or saved.get("schema_version") != 1 or
            not isinstance(saved.get("reconnections", []), list) or
            "sources" in saved and (not isinstance(saved["sources"], dict) or
                                    not isinstance(saved["sources"].get("files"), list))):
        raise ValueError("Unsupported application registration metadata. Existing data was preserved.")
    return deepcopy(saved)


class ProjectLifecycle:
    """Mixin using CreatorService's lock, retained plans and project store."""

    def _registration_available(self, workspace, path, identity, *, excluding=None):
        projects, diagnostics = self.store.list(workspace)
        if diagnostics:
            raise ValueError("Saved application records could not be checked completely. Resolve their diagnostics before registering a location.")
        for existing in projects:
            if existing["id"] == excluding:
                continue
            if existing["path"] == str(path) or _leaf(existing.get("directory_identity")) == _leaf(identity):
                raise ValueError("This application location is already registered. Reconnect its saved entry or remove that entry first.")
        self._require_idle({"id": excluding or "unregistered", "path": str(path)}, workspace)

    def import_preview(self, body):
        """Review an existing source tree and selected profile without changing files."""
        with self.lock:
            workspace = self._workspace(body, {"name", "path", "board_id", "target", "sdk_id"})
            name = _application_name(body.get("name"))
            snapshot = self.environments.latest(workspace)
            profile = select_profile(workspace, body, snapshot)
            path, identity, sources = _location(workspace, body.get("path"), snapshot)
            self._registration_available(workspace, path, identity)
            profile["execution_fingerprint"] = execution_guard(snapshot, profile)
            environment = dict(os.environ)
            recipe = command_recipe(profile, path, path / "build/manual", environ=environment)
            plan = {"schema_version": 1, "id": uuid.uuid4().hex, "operation": "import",
                    "workspace": workspace, "name": name, "path": str(path), "profile": profile,
                    "directory_identity": identity, "sources": sources, "files": deepcopy(sources["files"]),
                    "recipe": recipe, "created_at": now(),
                    "registration": {"schema_version": 1, "origin": "imported"},
                    "warnings": ["Import saves a registration only. Source files, CMake and configuration are not changed.",
                                 "Build compatibility is unverified. Configure or Build runs application CMake only after a separate review.",
                                 "Existing build outputs and Git bookkeeping are excluded from the source observation."]}
            return self._remember("import", plan, environment)

    def import_project(self, body):
        """Register exactly the reviewed existing application; never execute its code."""
        with self.lock:
            workspace = self._workspace(body, {"plan_id"})
            saved = self._consume("import", body.get("plan_id"), workspace)
            plan = saved["plan"]
            snapshot = self.environments.latest(workspace)
            path, identity, sources = _location(workspace, plan["path"], snapshot)
            if identity != plan["directory_identity"] or sources != plan["sources"]:
                raise ValueError("Application directory or files changed after preview. Review the import again.")
            self._registration_available(workspace, path, identity)
            if execution_guard(snapshot, plan["profile"]) != plan["profile"]["execution_fingerprint"]:
                raise ValueError("The selected environment changed after preview. Analyze and review the import again.")
            validate_identity(path, identity)
            captured = now()
            project = {"schema_version": 2, "revision": 0, "id": uuid.uuid4().hex, "workspace": workspace,
                       "name": plan["name"], "path": str(path), "profile": deepcopy(plan["profile"]),
                       "initial_profile": deepcopy(plan["profile"]), "template": {"id": "imported", "version": 1},
                       "directory_identity": identity, "files": deepcopy(sources["files"]),
                       "registered_sources": deepcopy(sources), "created_at": captured,
                       "diagnostics": [], "registration": {"schema_version": 1, "origin": "imported",
                           "imported_at": captured, "initial_path": str(path), "initial_directory_identity": deepcopy(identity),
                           "sources": deepcopy(sources), "reconnections": []}}
            self.store.save_project(project)
            return {"project": deepcopy(project)}

    def reconnect_preview(self, body):
        """Review a replacement location while preserving profiles and historical paths."""
        with self.lock:
            workspace = self._workspace(body, {"project_id", "path"})
            project = self.store.project(identifier(body.get("project_id")), workspace)
            self._require_idle(project, workspace)
            registration = _registration(project)
            if len(registration.get("reconnections", [])) >= MAX_RECONNECTIONS:
                raise ValueError("The application reconnection history limit was reached. Existing records were preserved.")
            snapshot = self.environments.latest(workspace)
            path, identity, sources = _location(workspace, body.get("path"), snapshot)
            if str(path) == project["path"] and identity == project.get("directory_identity"):
                raise ValueError("This application is already connected to that directory.")
            self._registration_available(workspace, path, identity, excluding=project["id"])
            previous_sources = project.get("registered_sources", registration.get("sources", {})).get(
                "files", project.get("files", []))
            plan = {"schema_version": 1, "id": uuid.uuid4().hex, "operation": "reconnect",
                    "workspace": workspace, "project_id": project["id"], "name": project["name"],
                    "previous_path": project["path"], "old_path": project["path"],
                    "path": str(path), "profile": deepcopy(project["profile"]),
                    "directory_identity": identity, "sources": sources, "files": deepcopy(sources["files"]),
                    "expected_project": project, "created_at": now(),
                    "same_directory": _leaf(identity) == _leaf(project.get("directory_identity")),
                    "file_changes": _difference(previous_sources, sources["files"]),
                    "warnings": ["Reconnect updates the saved location only. It does not move, copy or modify source files.",
                                 "Existing configurations, creation provenance and build history remain unchanged; earlier attempts keep their original paths.",
                                 "Build profiles are preserved. Review their environment and file selections before the next build."]}
            if not plan["same_directory"]:
                plan["warnings"].append("This is a different directory. Confirm that its files are the application intended for this saved history.")
            return self._remember("reconnect", plan, dict(os.environ))

    def reconnect_project(self, body):
        """Apply the reviewed path update with a full-record compare-and-swap."""
        with self.lock:
            workspace = self._workspace(body, {"plan_id"})
            plan = self._consume("reconnect", body.get("plan_id"), workspace)["plan"]
            expected = plan["expected_project"]
            project = self.store.project(plan["project_id"], workspace)
            if project != expected:
                raise ValueError("The saved application changed after preview. Review the reconnection again.")
            self._require_idle(project, workspace)
            snapshot = self.environments.latest(workspace)
            path, identity, sources = _location(workspace, plan["path"], snapshot)
            if identity != plan["directory_identity"] or sources != plan["sources"]:
                raise ValueError("Application directory or files changed after preview. Review the reconnection again.")
            self._registration_available(workspace, path, identity, excluding=project["id"])
            registration = _registration(project)
            if len(registration.get("reconnections", [])) >= MAX_RECONNECTIONS:
                raise ValueError("The application reconnection history limit was reached. Existing records were preserved.")
            timestamp = now()
            registration.setdefault("reconnections", []).append({
                "at": timestamp, "from_path": project["path"], "to_path": str(path),
                "previous_directory_identity": deepcopy(project["directory_identity"]),
                "directory_identity": deepcopy(identity), "source_fingerprint": sources["fingerprint"],
                "same_directory": plan["same_directory"]})
            registration["sources"] = deepcopy(sources)
            updated = {**deepcopy(project), "schema_version": 2, "revision": project.get("revision", 0) + 1,
                       "path": str(path), "directory_identity": identity, "registration": registration,
                       "registered_sources": deepcopy(sources),
                       "updated_at": timestamp}
            validate_identity(path, identity)
            self.store.update_project(updated, expected)
            self._discard_previews(project["id"])
            return {"project": deepcopy(updated)}
