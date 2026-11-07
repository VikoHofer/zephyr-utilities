"""Projects-owned, reviewed inclusion of a newly saved application overlay."""

from copy import deepcopy
import os
from pathlib import Path
import re
import uuid

from ...core.source_files import WorkspaceSourceReader
from .creation import path_identity
from .editor_context import ProjectsEditorContextProvider
from .fragments import fragment_path
from .overlay_creation import inclusion
from .records import identifier


MAX_REGISTRATIONS = 64
_CATEGORY = "overlay_registrations"


def _revision(value):
    if type(value) is not int or value < 0:
        raise ValueError("The reviewed configuration revision is invalid.")
    return value


def _updated(before, profile, configuration_id):
    result = deepcopy(before)
    if profile != before["profile"]:
        result.update(schema_version=2, profile=deepcopy(profile), revision=_revision(before.get("revision", 0)) + 1)
        if configuration_id is None:
            result["profile_revision"] = _revision(before.get("profile_revision", 0)) + 1
    return result


def _guard(selection):
    return {key: deepcopy(selection.get(key)) for key in ("workspace", "workspace_identity", "application", "target",
                                                        "environment", "roots", "board", "default_overlay")}


class ProjectsOverlayProvider:
    """Publish selection metadata only after an independently saved file is verified."""

    def __init__(self, context, creator):
        self.context, self.creator, self.store = context, creator, creator.store
        self.provider = ProjectsEditorContextProvider(context, creator)

    def _path(self, record_id):
        return self.store.root / _CATEGORY / (identifier(record_id) + ".json")

    @staticmethod
    def _sync(path):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        descriptor = os.open(path.parent, flags | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write(self, record):
        self._validate(record)
        path = self._path(record["id"])
        self.store._write(path, record)
        self._sync(path)
        # The first journal also creates its category directory. Persist that
        # directory entry before a caller can publish the application file.
        descriptor = os.open(self.store.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _validate(self, record):
        try:
            if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
                    or record["schema_version"] != 1 or record.get("state") not in {"planned", "included"}
                    or type(record.get("required")) is not bool):
                raise ValueError("Unsupported overlay registration record. Existing data was preserved.")
            identifier(record.get("id"))
            identifier(record["project_id"])
            if record["configuration_id"] is not None:
                identifier(record["configuration_id"])
            relative = fragment_path(record["relative_path"], "overlays")
            selection = record["selection"]
            application = selection["application"]["path"]
            if (selection["workspace"] != record["workspace"] or selection["project"]["id"] != record["project_id"]
                    or selection["configuration"]["id"] != record["configuration_id"]
                    or str(Path(application) / relative) != record["path"]
                    or not Path(application).is_absolute() or ".." in Path(application).parts
                    or not Path(application).is_relative_to(record["workspace"]) or application == record["workspace"]):
                raise ValueError("The overlay registration belongs to another selection or destination.")
            project, before = record["before_project"], record["before_selected"]
            self.store._validate_removal(project)
            self.store._validate_removal(before)
            if (project["id"] != record["project_id"] or project["workspace"] != record["workspace"]
                    or project["path"] != application or before["workspace"] != record["workspace"]
                    or before["id"] != (record["configuration_id"] or project["id"])
                    or record["configuration_id"] is None and before != project
                    or record["configuration_id"] is not None and before["project_id"] != project["id"]):
                raise ValueError("The overlay registration's saved record identity is invalid.")
            profile, _, required = inclusion(before["profile"], application, record["source_files"],
                                             selection.get("default_overlay"), relative)
            after = _updated(before, profile, record["configuration_id"])
            expected_project = after if record["configuration_id"] is None else project
            if (required != record["required"] or after != record["after_selected"]
                    or expected_project != record["after_project"]):
                raise ValueError("The overlay registration contains changes outside its reviewed inclusion.")
            if record["state"] == "included" and not re.fullmatch(r"[a-f0-9]{64}", record.get("saved_sha256", "")):
                raise ValueError("The overlay registration receipt has no valid saved source hash.")
        except (KeyError, TypeError, AttributeError) as exc:
            raise ValueError("Malformed overlay registration record. Existing data was preserved.") from exc
        return record

    def _records(self):
        values, errors = self.store._items(_CATEGORY, MAX_REGISTRATIONS)
        if errors:
            raise ValueError("Overlay registration recovery could not be read completely. Existing records were preserved.")
        return [self._validate(value) for value in values]

    def _read(self, descriptor):
        if not isinstance(descriptor, dict):
            raise ValueError("Choose a reviewed overlay registration.")
        record = self._validate(self.store._read(self._path(descriptor.get("id"))))
        if record["id"] != descriptor["id"]:
            raise ValueError("The overlay registration identity changed.")
        return record

    @staticmethod
    def _descriptor(record):
        return {"id": record["id"], "state": "planned" if record["required"] else "already_included",
                "required": record["required"], "message": (
                    "Save will add this overlay to the selected configuration while preserving its other inputs."
                    if record["required"] else "The selected configuration already includes this overlay by its existing rules.")}

    def _current(self, record):
        project = self.store.project(record["project_id"], record["workspace"])
        selected = (project if record["configuration_id"] is None else
                    self.store.configuration(record["configuration_id"], record["project_id"], record["workspace"]))
        if (project, selected) == (record["before_project"], record["before_selected"]):
            return False
        if (project, selected) == (record["after_project"], record["after_selected"]):
            return True
        raise ValueError("The application or selected configuration changed after overlay review. The saved file was retained; resolve its inclusion in Projects before retrying.")

    def _sources(self, record, selection):
        if _guard(selection) != _guard(record["selection"]):
            raise ValueError("The selected application, target, environment or directory identity changed after review.")
        reader = WorkspaceSourceReader(record["workspace"])
        application = selection["application"]
        if (application["availability"]["state"] != "available"
                or path_identity(application["path"]) != record["selection"]["application"]["identity"]):
            raise ValueError("The application directory changed after overlay review.")
        files, _ = self.provider._current_sources(reader, application, [])
        if files is None:
            raise ValueError("The application's current sources cannot be verified for overlay inclusion.")
        def unchanged(values):
            return [item for item in values if item["path"] != record["relative_path"]]
        if unchanged(files) != unchanged(record["source_files"]):
            raise ValueError("Application CMake or other overlay inputs changed after review. The saved file was retained.")
        return reader

    def review(self, selection, relative_path, *, creating=False):
        if type(creating) is not bool:
            raise ValueError("Choose whether this Save creates an overlay.")
        relative = fragment_path(relative_path, "overlays")
        with self.creator.lock, self.store.lock:
            current = self.context.project_context.revalidate(selection)
            workspace, project_id, configuration_id = current["workspace"], current["project"]["id"], current["configuration"]["id"]
            records = self._records()
            pending = [record for record in records if record["state"] == "planned"
                       and (record["workspace"], record["project_id"], record["configuration_id"], record["relative_path"])
                       == (workspace, project_id, configuration_id, relative)]
            if len(pending) > 1:
                raise ValueError("Several pending overlay registrations need reconciliation; their records were preserved.")
            if pending:
                self._current(pending[0])
                self._sources(pending[0], current)
                return self._descriptor(pending[0])
            if not creating:
                return None
            proposal = current.get("overlay_creation")
            if not isinstance(proposal, dict) or proposal.get("path") != relative:
                raise ValueError("This file is not the selected project's proposed new overlay.")
            if len(records) >= MAX_REGISTRATIONS:
                raise ValueError("Overlay registration recovery reached its 64-record limit. Existing records were retained.")
            project = self.store.project(project_id, workspace)
            selected = project if configuration_id is None else self.store.configuration(configuration_id, project_id, workspace)
            reader = WorkspaceSourceReader(workspace)
            files, _ = self.provider._current_sources(reader, current["application"], [])
            if files is None:
                raise ValueError("The application's source selection is incomplete; overlay inclusion cannot be reviewed.")
            profile, _, required = inclusion(selected["profile"], current["application"]["path"], files,
                                             current.get("default_overlay"), relative)
            after = _updated(selected, profile, configuration_id)
            record = {"schema_version": 1, "id": uuid.uuid4().hex, "state": "planned", "workspace": workspace,
                      "project_id": project_id, "configuration_id": configuration_id, "relative_path": relative,
                      "path": str(Path(current["application"]["path"]) / relative), "selection": deepcopy(current),
                      "source_files": deepcopy(files), "before_project": deepcopy(project), "before_selected": deepcopy(selected),
                      "after_project": deepcopy(after if configuration_id is None else project),
                      "after_selected": after, "required": required}
            self.context.project_context.revalidate(selection)
            self._write(record)
            return self._descriptor(record)

    def register(self, registration, workspace, path, sha256):
        if workspace != self.context.workspace.get()["path"]:
            raise ValueError("The selected workspace changed before overlay inclusion.")
        if not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise ValueError("Overlay inclusion needs the exact saved source hash.")
        with self.creator.lock, self.store.lock:
            record = self._read(registration)
            if record["workspace"] != workspace or record["path"] != path:
                raise ValueError("The registration belongs to another workspace or overlay.")
            applied = self._current(record)
            if record["state"] == "included" and record["required"] and not applied:
                raise ValueError("This inclusion already completed and the configuration was later reverted. Review its current inputs in Projects.")
            current = self.context.project_context.capture(workspace, record["project_id"], record["configuration_id"])
            reader = self._sources(record, current)
            if reader.read_text(path, limit=256 * 1024)["sha256"] != sha256:
                raise ValueError("The saved overlay changed before configuration inclusion. Its file was retained.")
            if record["state"] == "included" and record["saved_sha256"] != sha256:
                raise ValueError("This registration receipt belongs to a different saved overlay revision.")
            if not applied and record["required"]:
                if record["configuration_id"] is None:
                    self.store.update_project(record["after_selected"], record["before_selected"])
                else:
                    self.store.update_configuration(record["after_selected"], record["before_selected"])
            if record["required"] and record["state"] == "planned":
                # A previous CAS may have committed before its fsync failed.
                # Retrying must finish durability without replaying the update.
                category = "configurations" if record["configuration_id"] else "projects"
                identity = record["configuration_id"] or record["project_id"]
                self._sync(self.store.root / category / (identity + ".json"))
            fresh = self.context.project_context.capture(workspace, record["project_id"], record["configuration_id"])
            self._sources(record, fresh)
            self._current(record)
            if WorkspaceSourceReader(workspace).read_text(path, limit=256 * 1024)["sha256"] != sha256:
                raise ValueError("The overlay changed while recording inclusion. The file and configuration were retained for reconciliation.")
            if record["state"] != "included":
                self._write({**record, "state": "included", "saved_sha256": sha256})
            return {"state": "included", "message": "The saved overlay is included in the selected configuration; Configure can verify its use.",
                    "selection": fresh}
