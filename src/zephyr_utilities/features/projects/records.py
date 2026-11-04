"""Versioned, feature-owned project and build-attempt records."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from threading import RLock
import uuid

from .metadata import read_regular


SCHEMA_VERSION = 1
MAX_RECORD = 4 * 1024 * 1024


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid project or attempt identity.")
    return value


def now():
    return datetime.now(timezone.utc).isoformat()


class ProjectStore:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = RLock()

    def _read(self, path):
        if any(p.is_symlink() for p in (self.root, path.parent, path)):
            raise ValueError("Project records must not be symlinks.")
        data = json.loads(read_regular(path, MAX_RECORD).decode("utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported project record schema. Existing data was preserved.")
        return data

    def _write(self, path, data):
        if data.get("schema_version") != SCHEMA_VERSION:
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

    def save_attempt(self, attempt):
        with self.lock:
            self._write(self.root / "attempts" / f"{identifier(attempt['id'])}.json", attempt)

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
            result = []
            for stored in projects:
                if stored.get("workspace") != str(workspace):
                    continue
                project = deepcopy(stored)
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
            return result, errors + more_errors
