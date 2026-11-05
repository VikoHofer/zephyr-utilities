"""Detached project selections for the read-only Device Tree Editor workspace."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from ...core.source_files import WorkspaceSourceReader
from .creation import path_identity
from .default_overlay import default_overlay, project_default_overlay
from .fragments import BUILD_MARKERS, EXCLUDED_DIRS, EXCLUDED_PREFIXES, fragment_path, profile_fragments
from .overlay_usage import overlay_usage
from .records import identifier


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _revision(value):
    if type(value) is not int or value < 0:
        raise ValueError("The saved project/configuration has an invalid revision.")
    return value


def _diagnostic(code, message):
    return {"code": code, "severity": "warning", "message": str(message)}


def _availability(path, expected=None):
    try:
        actual = path_identity(path)
        if expected is not None and actual != expected:
            return actual, {"state": "replaced", "reason": "The saved directory or one of its ancestors changed."}
        return actual, {"state": "available", "reason": ""}
    except (FileNotFoundError, NotADirectoryError):
        return None, {"state": "missing", "reason": "The selected directory was moved or deleted."}
    except (OSError, ValueError) as exc:
        return None, {"state": "unavailable", "reason": str(exc)}


class ProjectsEditorContextProvider:
    """Observe saved selections; never execute tools or alter creator state."""

    def __init__(self, context, creator):
        self.context = context
        self.creator = creator

    def _workspace(self, workspace):
        if not isinstance(workspace, str) or workspace != self.context.workspace.get()["path"]:
            raise ValueError("The selected workspace changed. Open the editor context again.")
        return WorkspaceSourceReader(workspace), path_identity(workspace)

    @staticmethod
    def _application(reader, project):
        path = reader.path(project.get("path"))
        if path == reader.workspace:
            raise ValueError("Choose an application inside the workspace, not the workspace itself.")
        name = project.get("name", path.name)
        if not isinstance(name, str) or not name or len(name) > 256:
            raise ValueError("The saved application has an invalid name.")
        expected = project.get("directory_identity")
        if not isinstance(expected, list) or not expected:
            return {"path": str(path), "name": name, "identity": None,
                    "availability": {"state": "unavailable", "reason": "The saved application has no directory identity."}}
        actual, availability = _availability(path, expected)
        return {"path": str(path), "name": name, "identity": actual,
                "availability": availability}

    def catalog(self, workspace):
        reader, identity = self._workspace(workspace)
        with self.creator.lock, self.creator.store.lock:
            projects, errors = self.creator.store._items("projects", 500)
            configurations, configuration_errors = self.creator.store._configurations()
            errors.extend(configuration_errors)
            result = []
            for project in projects:
                if project.get("workspace") != workspace:
                    continue
                try:
                    application = self._application(reader, project)
                    profile_fragments(project["profile"])
                    choices = [{"id": None, "name": "Original", "revision": _revision(project.get("profile_revision", 0))}]
                    choices.extend({"id": item["id"], "name": item["name"], "revision": _revision(item.get("revision", 0))}
                                   for item in configurations if item["workspace"] == workspace and item["project_id"] == project["id"])
                    choices[1:] = sorted(choices[1:], key=lambda item: (item["name"].casefold(), item["id"]))
                    result.append({"id": project["id"], "name": application["name"], "path": application["path"],
                                   "availability": application["availability"], "configurations": choices})
                except (KeyError, TypeError, ValueError, OSError) as exc:
                    errors.append(f"Cannot open application {project.get('id', '')}: {exc}")
        if path_identity(workspace) != identity or self.context.workspace.get()["path"] != workspace:
            raise ValueError("The selected workspace changed during context loading.")
        return {"workspace": workspace, "projects": sorted(result, key=lambda item: (item["name"].casefold(), item["id"])),
                "diagnostics": [_diagnostic("project-catalog", message) for message in errors]}

    def _environment(self, workspace, profile, diagnostics):
        # Use the captured profile, not EnvironmentStore.latest(): a newer observation
        # must not silently retarget an existing project or require installed tools.
        result = {"id": profile.get("environment_id"), "fingerprint": profile.get("environment_fingerprint"),
                  "zephyr": deepcopy(profile.get("zephyr")), "sdk": deepcopy(profile.get("sdk")),
                  "tools": deepcopy(profile.get("tools", [])), "freshness": "unknown",
                  "observation_status": "unavailable", "availability": {"state": "unknown", "reason": ""}}
        try:
            observation_id = identifier(result["id"])
            store = self.creator.environments
            with store.lock:
                saved = store._read(store.root / "snapshots" / f"{observation_id}.json")
            saved_workspace = saved.get("workspace")
            if (saved.get("id") != observation_id or not isinstance(saved_workspace, dict) or
                    saved_workspace.get("path") != workspace or
                    saved.get("fingerprint") != result["fingerprint"]):
                raise ValueError("The saved environment observation does not match this profile.")
            result["observation_status"] = saved.get("status", "unknown")
            result["freshness"] = "stale" if saved.get("status") == "stale" else "saved-observation"
            try:
                current_registry = store.custom_boards.signature(workspace)
                result["custom_board_registry"] = {"captured": saved.get("custom_board_registry"), "current": current_registry}
                if current_registry != saved.get("custom_board_registry"):
                    result["freshness"] = "stale"
                    diagnostics.append(_diagnostic("custom-boards-changed",
                        "Custom board registrations or metadata changed since the selected observation."))
            except (OSError, ValueError) as exc:
                result["freshness"] = "unknown"
                diagnostics.append(_diagnostic("custom-boards-unavailable", f"Cannot check selected custom board evidence: {exc}"))
        except (OSError, ValueError, TypeError) as exc:
            diagnostics.append(_diagnostic("environment-observation", f"Saved environment evidence is unavailable: {exc}"))
        missing = []
        identities = []
        for kind in ("zephyr", "sdk"):
            selected = result.get(kind)
            if isinstance(selected, dict) and isinstance(selected.get("path"), str):
                identity, availability = _availability(selected["path"])
                identities.append({"kind": kind, "path": selected["path"], "identity": identity, "availability": availability})
                if availability["state"] != "available":
                    missing.append(kind)
        result["directories"] = identities
        result["availability"] = {"state": "unavailable" if missing else "available" if len(identities) == 2 else "unknown",
                                  "reason": "Unavailable selected directories: " + ", ".join(missing) if missing else
                                  "Directory availability does not establish toolchain readiness or current source freshness."}
        if missing:
            result["freshness"] = "stale"
            diagnostics.append(_diagnostic("environment-unavailable", result["availability"]["reason"]))
        return result

    @staticmethod
    def _roots(profile, diagnostics):
        result = []
        roots = profile.get("roots", {})
        if not isinstance(roots, dict):
            raise ValueError("The selected profile has invalid discovery roots.")
        for kind, entries in roots.items():
            if not isinstance(entries, list) or len(entries) > 256:
                raise ValueError("The selected profile has unsupported discovery roots.")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise ValueError("The selected profile contains an invalid root.")
                identity, availability = _availability(entry["path"])
                result.append({"kind": kind, "path": entry["path"], "origin": deepcopy(entry.get("origin", entry.get("source", "saved-profile"))),
                               "identity": identity, "availability": availability})
                if availability["state"] != "available":
                    diagnostics.append(_diagnostic("root-unavailable", f"Selected {kind} root is unavailable: {entry['path']}"))
        return result

    @staticmethod
    def _current_sources(reader, application, diagnostics):
        if application["availability"]["state"] != "available":
            return None, []
        source = Path(application["path"])
        try:
            files = reader.list_files(source, {".overlay"}, max_files=1000, max_dirs=1000, max_depth=16,
                                      exclude_dirs=EXCLUDED_DIRS, exclude_prefixes=EXCLUDED_PREFIXES, stop_names=BUILD_MARKERS)
            cmake = reader.read_text(source / "CMakeLists.txt", limit=256 * 1024)
            records = [{"path": "CMakeLists.txt", "content": cmake["text"], "sha256": cmake["sha256"]}]
            fingerprints = [{"path": "CMakeLists.txt", "sha256": cmake["sha256"]}]
            for path in files:
                relative = path.relative_to(source).as_posix()
                try:
                    fragment_path(relative, "overlays")
                except ValueError:
                    continue
                document = reader.read_text(path, limit=256 * 1024)
                if reader.bytes_read > 8 * 1024 * 1024:
                    raise ValueError("Overlay context exceeds its 8 MiB source-read limit.")
                records.append({"path": relative, "sha256": document["sha256"]})
                fingerprints.append({"path": relative, "sha256": document["sha256"]})
            return records, fingerprints
        except (OSError, ValueError) as exc:
            diagnostics.append(_diagnostic("source-evidence-unavailable", f"Current overlay prediction is incomplete: {exc}"))
            return None, []

    def capture(self, workspace, project_id, configuration_id=None):
        reader, workspace_identity = self._workspace(workspace)
        identifier(project_id)
        if configuration_id is not None:
            identifier(configuration_id)
        with self.creator.lock, self.creator.store.lock:
            project = self.creator.store.project(project_id, workspace)
            selected = project if configuration_id is None else self.creator.store.configuration(configuration_id, project_id, workspace)
            profile = deepcopy(selected.get("profile"))
            if not isinstance(profile, dict) or profile.get("workspace") != workspace:
                raise ValueError("The saved profile does not belong to this workspace.")
            fragments = profile_fragments(profile)
            revision = _revision(selected.get("profile_revision" if configuration_id is None else "revision", 0))
            application = self._application(reader, project)
            target = profile.get("target")
            if not isinstance(target, str) or not target or len(target) > 1000:
                raise ValueError("The saved profile has no supported exact target.")
            default_overlay(target)  # Validate supported target spelling without writing a file.
            base, *qualifiers = target.split("/")
            board, _, board_revision = base.partition("@")
            diagnostics = []
            if application["availability"]["state"] != "available":
                diagnostics.append(_diagnostic("application-unavailable", application["availability"]["reason"]))
            default = deepcopy(project_default_overlay(project, profile))
            files, fingerprints = self._current_sources(reader, application, diagnostics)
            predicted = overlay_usage(profile, application["path"], files=files, default_overlay=default)
            provenance = {"configuration_id": configuration_id, "configuration_revision": revision}
            predicted["provenance"] = {**provenance, "kind": "current-source-prediction"}
            attempts, errors = self.creator.store._items("attempts", 2000)
            diagnostics.extend(_diagnostic("attempt-evidence", message) for message in errors)
            captured = []
            for attempt in sorted(attempts, key=lambda item: str(item.get("finished_at") or item.get("started_at") or ""), reverse=True):
                configuration = attempt.get("configuration", {})
                if (attempt.get("workspace") != workspace or attempt.get("project_id") != project_id or
                        not isinstance(configuration, dict) or configuration.get("id") != configuration_id or
                        attempt.get("profile") != profile or
                        attempt.get("configuration_revision", 0) != revision):
                    continue
                sources = attempt.get("sources")
                report = overlay_usage(profile, application["path"], files=sources.get("files") if isinstance(sources, dict) else None,
                                       context=attempt.get("context"), default_overlay=attempt.get("default_overlay") or default)
                if report["status"] != "confirmed":
                    continue
                report["provenance"] = {**provenance, "kind": "historical-attempt", "attempt_id": attempt["id"],
                                        "captured_at": attempt.get("finished_at") or attempt.get("started_at"),
                                        "source_freshness": "unknown"}
                captured.append(report)
                if len(captured) == 8:
                    break
            result = {"schema_version": 1, "workspace": workspace,
                      "workspace_identity": list(reader.directory_identity(workspace)),
                      "application": application, "project": {"id": project_id, "revision": _revision(project.get("revision", 0))},
                      "configuration": {"id": configuration_id, "name": "Original" if configuration_id is None else selected["name"],
                                        "revision": revision, "profile_version": profile.get("schema_version", 1),
                                        "profile_fingerprint": _fingerprint(profile)},
                      "target": {"requested": target, "board_id": profile.get("board_id"), "board": board,
                                 "revision": board_revision or None, "qualifiers": qualifiers},
                      "environment": self._environment(workspace, profile, diagnostics), "fragments": fragments,
                      "default_overlay": default, "roots": self._roots(profile, diagnostics),
                      "input_evidence": {"predicted": [predicted], "captured": captured, "coverage": "partial",
                                         "source_fingerprints": fingerprints}, "diagnostics": diagnostics}
            if path_identity(workspace) != workspace_identity or self.context.workspace.get()["path"] != workspace:
                raise ValueError("The selected workspace changed during context loading.")
            if self._application(reader, project) != application:
                raise ValueError("The application directory changed during context loading. Open it again.")
            return deepcopy(result)

    def revalidate(self, selection):
        if not isinstance(selection, dict) or type(selection.get("schema_version")) is not int or selection["schema_version"] != 1:
            raise ValueError("Unsupported project selection context. Open the editor context again.")
        try:
            current = self.capture(selection["workspace"], selection["project"]["id"], selection["configuration"]["id"])
        except (KeyError, TypeError) as exc:
            raise ValueError("Invalid project selection context. Open it again.") from exc
        if current != selection:
            raise ValueError("The project selection or its inputs changed. Open the editor context again.")
        return current
