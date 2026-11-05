"""Versioned, feature-owned project and build-attempt records."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from threading import RLock
import uuid
import unicodedata

from .metadata import read_regular
from .fragments import profile_fragments


SCHEMA_VERSION = 1
MAX_RECORD = 4 * 1024 * 1024
MAX_CONFIGURATIONS = 32
MAX_CONFIGURATION_RECORDS = 16000


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid project, configuration or attempt identity.")
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


def configuration_name(value):
    if (not isinstance(value, str) or any(unicodedata.category(character).startswith("C") for character in value)
            or not 1 <= len(value.strip()) <= 64):
        raise ValueError("Configuration names need 1 to 64 readable characters without control characters.")
    name = value.strip()
    if name.casefold() == "original":
        raise ValueError("Original is reserved for the application's initial configuration.")
    return name


class ProjectStore:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = RLock()

    def _read(self, path):
        if any(p.is_symlink() for p in (self.root, path.parent, path)):
            raise ValueError("Project records must not be symlinks.")
        data = json.loads(read_regular(path, MAX_RECORD).decode("utf-8"))
        versions = (1, 2) if path.parent.name in {"projects", "configurations"} else (1,)
        if not isinstance(data, dict) or data.get("schema_version") not in versions:
            raise ValueError("Unsupported project record schema. Existing data was preserved.")
        if data.get("schema_version") == 2 and (type(data.get("revision")) is not int or data["revision"] < 0):
            raise ValueError("Unsupported saved record revision. Existing data was preserved.")
        return data

    def _write(self, path, data):
        versions = (1, 2) if path.parent.name in {"projects", "configurations"} else (1,)
        if data.get("schema_version") not in versions:
            raise ValueError("Unsupported project record schema.")
        if any(p.is_symlink() for p in (self.root, path.parent, path)):
            raise ValueError("Project records must not be symlinks.")
        # Future formats are never replaced by this writer.
        if path.exists():
            self._read(path)
        encoded = json.dumps(data, ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_RECORD:
            raise ValueError("Project record exceeds the size limit.")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(encoded)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def save_project(self, project):
        with self.lock:
            path = self.root / "projects" / f"{identifier(project['id'])}.json"
            if path.exists():
                if self._read(path) != project:
                    raise ValueError("A different project already uses this identity.")
                return
            self._write(path, project)

    def project(self, project_id, workspace):
        with self.lock:
            data = self._read(self.root / "projects" / f"{identifier(project_id)}.json")
            if data.get("workspace") != str(workspace) or data.get("id") != project_id:
                raise ValueError("The project does not belong to the selected workspace.")
            return data

    def update_project(self, updated, expected):
        """Replace a reviewed registration/profile without rewriting captured attempts."""
        with self.lock:
            current = self.project(expected["id"], expected["workspace"])
            if current != expected:
                raise ValueError("The saved application changed since review. Refresh and review again.")
            if (updated.get("id") != current["id"] or updated.get("workspace") != current["workspace"] or
                    updated.get("schema_version") != 2 or updated.get("revision") != current.get("revision", 0) + 1):
                raise ValueError("Invalid application update identity or revision.")
            self._validate_removal(current)
            self._validate_removal(updated)
            self._write(self.root / "projects" / f"{current['id']}.json", updated)

    def save_output(self, output):
        """Keep managed output ownership independent of removable attempt history."""
        with self.lock:
            self._validate_output(output)
            self.project(output["project_id"], output["workspace"])
            path = self.root / "outputs" / f"{identifier(output['id'])}.json"
            if path.exists():
                current = self._read(path)
                if any(current.get(key) != output.get(key) for key in ("id", "project_id", "workspace", "path", "source_dir")):
                    raise ValueError("Managed build output identity cannot be changed.")
            self._write(path, output)

    def outputs(self, project_id, workspace):
        with self.lock:
            self.project(project_id, workspace)
            values, errors = self._items("outputs", 4000)
            if errors:
                raise ValueError("Managed build output records could not be read completely. Existing outputs were preserved.")
            for value in values:
                self._validate_output(value)
            return [value for value in values if value.get("project_id") == project_id and value.get("workspace") == str(workspace)]

    @staticmethod
    def _validate_output(output):
        for key in ("id", "project_id", "latest_attempt_id"):
            identifier(output.get(key))
        if output.get("schema_version") != 1 or output.get("state") not in {"available", "cleaned"}:
            raise ValueError("Unsupported managed output state. Existing outputs were preserved.")
        for key in ("workspace", "path", "source_dir"):
            value = output.get(key)
            if not isinstance(value, str) or not Path(value).is_absolute() or ".." in Path(value).parts:
                raise ValueError("Invalid managed output path. Existing outputs were preserved.")

    def save_attempt(self, attempt):
        with self.lock:
            self._write(self.root / "attempts" / f"{identifier(attempt['id'])}.json", attempt)

    def attempt(self, attempt_id, project_id, workspace):
        with self.lock:
            data = self._read(self.root / "attempts" / f"{identifier(attempt_id)}.json")
            if (data.get("workspace") != str(workspace) or data.get("project_id") != project_id or
                    data.get("id") != attempt_id):
                raise ValueError("The build attempt does not belong to this application and workspace.")
            return data

    def _validate_removal(self, data):
        """Preserve records whose nested profile format this version cannot understand."""
        profile = data.get("profile")
        if profile is not None:
            if not isinstance(profile, dict) or profile.get("schema_version") not in (1, 2):
                raise ValueError("Unsupported saved profile. Existing data was preserved.")
            profile_fragments(profile)

    def remove_project(self, project_id, workspace):
        with self.lock:
            project = self.project(project_id, workspace)
            configurations, errors = self._configurations()
            attempts, more_errors = self._items("attempts", 2000)
            outputs, output_errors = self._items("outputs", 4000)
            if errors or more_errors or output_errors:
                raise ValueError("Saved application records could not be read completely. Existing data was preserved.")
            records = [("projects", project)]
            for kind, children in (("configurations", configurations), ("attempts", attempts), ("outputs", outputs)):
                for child in children:
                    if child.get("project_id") == project_id:
                        if child.get("workspace") != str(workspace):
                            raise ValueError("A saved child record belongs to another workspace. Existing data was preserved.")
                        records.append((kind, child))
            # Validate every candidate before removing any file. Source/output paths are never used.
            for _, record in records:
                self._validate_removal(record)
            for kind, record in reversed(records):
                (self.root / kind / f"{identifier(record['id'])}.json").unlink()

    def remove_configuration(self, configuration_id, project_id, workspace):
        with self.lock:
            self.project(project_id, workspace)
            configuration = self.configuration(configuration_id, project_id, workspace)
            self._validate_removal(configuration)
            (self.root / "configurations" / f"{configuration_id}.json").unlink()

    def remove_attempt(self, attempt_id, project_id, workspace):
        with self.lock:
            self.project(project_id, workspace)
            attempt = self.attempt(attempt_id, project_id, workspace)
            self._validate_removal(attempt)
            (self.root / "attempts" / f"{attempt_id}.json").unlink()

    def _validate_configuration(self, data):
        identifier(data.get("id"))
        identifier(data.get("project_id"))
        if data.get("schema_version") == 2 and (type(data.get("revision")) is not int or data["revision"] < 0):
            raise ValueError("Unsupported saved configuration revision.")
        if configuration_name(data.get("name")) != data["name"]:
            raise ValueError("Saved configuration names must be trimmed.")
        profile = data.get("profile")
        if (not isinstance(data.get("workspace"), str) or not isinstance(profile, dict) or
                profile.get("schema_version") not in (1, 2) or profile.get("workspace") != data["workspace"]):
            raise ValueError("The saved configuration has an unsupported or mismatched profile.")
        profile_fragments(profile)
        return data

    def configuration(self, configuration_id, project_id, workspace):
        with self.lock:
            path = self.root / "configurations" / f"{identifier(configuration_id)}.json"
            try:
                data = self._validate_configuration(self._read(path))
            except FileNotFoundError as exc:
                raise ValueError("The selected build configuration is no longer available. Refresh applications.") from exc
            if (data.get("workspace") != str(workspace) or data.get("project_id") != project_id or
                    data.get("id") != configuration_id):
                raise ValueError("The build configuration does not belong to this application and workspace.")
            return data

    def save_configuration(self, configuration):
        with self.lock:
            self._validate_configuration(configuration)
            self.project(configuration["project_id"], configuration["workspace"])
            path = self.root / "configurations" / f"{configuration['id']}.json"
            if path.exists():
                if self._read(path) != configuration:
                    raise ValueError("A different configuration already uses this identity.")
                return
            records, errors = self._configurations()
            if errors:
                raise ValueError("Saved configuration records could not be read completely. Existing data was preserved.")
            matching = [record for record in records if record["project_id"] == configuration["project_id"] and
                        record["workspace"] == configuration["workspace"]]
            if any(record["name"].casefold() == configuration["name"].casefold() for record in matching):
                raise ValueError("This application already has a configuration with that name.")
            if len(matching) >= MAX_CONFIGURATIONS or len(records) >= MAX_CONFIGURATION_RECORDS:
                raise ValueError("The saved build configuration limit was reached (32 per application).")
            self._write(path, configuration)

    def validate_configuration_change(self, updated, expected=None):
        """Check names, limits and the captured revision before a reviewed edit."""
        with self.lock:
            self._validate_configuration(updated)
            self.project(updated["project_id"], updated["workspace"])
            records, errors = self._configurations()
            if errors:
                raise ValueError("Saved configuration records could not be read completely. Existing data was preserved.")
            matching = [record for record in records if record["project_id"] == updated["project_id"] and
                        record["workspace"] == updated["workspace"]]
            if expected is not None:
                current = self.configuration(expected["id"], expected["project_id"], expected["workspace"])
                if current != expected:
                    raise ValueError("The saved configuration changed since review. Refresh and review again.")
                if (updated["id"] != expected["id"] or updated["project_id"] != expected["project_id"] or
                        updated["workspace"] != expected["workspace"] or updated.get("schema_version") != 2 or
                        updated.get("revision") != expected.get("revision", 0) + 1):
                    raise ValueError("Invalid configuration update identity or revision.")
            elif any(record["id"] == updated["id"] for record in records):
                raise ValueError("A different configuration already uses this identity.")
            elif len(matching) >= MAX_CONFIGURATIONS or len(records) >= MAX_CONFIGURATION_RECORDS:
                raise ValueError("The saved build configuration limit was reached (32 per application).")
            if any(record["id"] != updated["id"] and record["name"].casefold() == updated["name"].casefold() for record in matching):
                raise ValueError("This application already has a configuration with that name.")

    def update_configuration(self, updated, expected):
        with self.lock:
            self.validate_configuration_change(updated, expected)
            self._write(self.root / "configurations" / f"{updated['id']}.json", updated)

    def _configurations(self):
        records, errors = self._items("configurations", MAX_CONFIGURATION_RECORDS)
        result = []
        for record in records:
            try:
                result.append(self._validate_configuration(record))
            except ValueError as exc:
                errors.append(f"Cannot open configuration {record['id']}: {exc}")
        return result, errors

    def _items(self, kind, limit):
        directory = self.root / kind
        if directory.is_symlink() or self.root.is_symlink():
            raise ValueError("Project storage directories must not be symlinks.")
        if not directory.exists():
            return [], []
        values, diagnostics = [], []
        for index, path in enumerate(directory.iterdir()):
            if index >= limit:
                diagnostics.append(f"The {kind} record limit ({limit}) was reached.")
                break
            if path.suffix != ".json":
                continue
            try:
                identifier(path.stem)
                item = self._read(path)
                if item.get("id") != path.stem:
                    raise ValueError("Record identity does not match its filename.")
                values.append(item)
            except (OSError, ValueError, UnicodeError) as exc:
                diagnostics.append(f"Cannot open {path.name}: {exc}")
        return values, diagnostics

    def list(self, workspace, active_attempts=()):
        with self.lock:
            projects, errors = self._items("projects", 500)
            attempts, more_errors = self._items("attempts", 2000)
            configurations, configuration_errors = self._configurations()
            result = []
            for stored in projects:
                if stored.get("workspace") != str(workspace):
                    continue
                project = deepcopy(stored)
                project["configurations"] = [deepcopy(record) for record in configurations
                                             if record["workspace"] == str(workspace) and
                                             record["project_id"] == project["id"]]
                project["configurations"].sort(key=lambda item: (item["name"].casefold(), item["id"]))
                project["attempts"] = []
                for record in attempts:
                    if record.get("workspace") != str(workspace) or record.get("project_id") != project["id"]:
                        continue
                    attempt = deepcopy(record)
                    if attempt.get("status") == "running" and attempt["id"] not in active_attempts:
                        attempt.update(status="interrupted", error="The application stopped before this attempt recorded an outcome.")
                    project["attempts"].append(attempt)
                project["attempts"].sort(key=lambda item: item.get("started_at", ""), reverse=True)
                result.append(project)
            result.sort(key=lambda item: item.get("created_at", ""), reverse=True)
            return result, errors + more_errors + configuration_errors
