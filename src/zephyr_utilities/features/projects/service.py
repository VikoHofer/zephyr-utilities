"""Feature-owned storage and captured-input analysis jobs."""

from copy import deepcopy
import json
import os
from pathlib import Path
import re
from threading import RLock
import uuid

from .analyzer import SCHEMA_VERSION, analyze_environment, digest
from .metadata import read_regular
from .probing import check_cancel


MAX_SNAPSHOT = 32 * 1024 * 1024


def validate_options(body, workspace):
    if set(body) - {"zephyr_base", "board_roots", "soc_roots", "sdk_paths", "force"}:
        raise ValueError("Unknown environment analysis option.")
    options = {}
    for key in ("board_roots", "soc_roots", "sdk_paths"):
        values = body.get(key, [])
        if not isinstance(values, list) or len(values) > 16:
            raise ValueError(f"{key} must be a list of at most sixteen local paths.")
        normalized = []
        for value in values:
            if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\0" in value:
                raise ValueError(f"{key} contains an invalid local path.")
            path = Path(value.strip()).expanduser()
            normalized.append(str((path if path.is_absolute() else Path(workspace) / path).resolve()))
        options[key] = list(dict.fromkeys(normalized))
    base = body.get("zephyr_base", "")
    if not isinstance(base, str) or len(base) > 4096 or "\0" in base:
        raise ValueError("Zephyr base must be a local path.")
    if base.strip():
        path = Path(base.strip()).expanduser()
        options["zephyr_base"] = str((path if path.is_absolute() else Path(workspace) / path).resolve())
    force = body.get("force", False)
    if not isinstance(force, bool):
        raise ValueError("force must be true or false.")
    options["force"] = force
    return options


class EnvironmentStore:
    """Schema v1 stores immutable observations and an atomic per-workspace pointer."""

    def __init__(self, root):
        self.root = Path(root)
        self.lock = RLock()

    def _read(self, path):
        if self.root.is_symlink() or path.parent.is_symlink():
            raise ValueError("Environment storage directories must not be symlinks.")
        if path.is_symlink() or path.stat().st_size > MAX_SNAPSHOT:
            raise ValueError("Environment storage is a symlink or exceeds its size limit.")
        data = json.loads(read_regular(path, MAX_SNAPSHOT).decode("utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported environment storage schema. Existing data was preserved.")
        return data

    def _index_path(self, workspace):
        return self.root / "workspaces" / f"{digest(str(workspace))}.json"

    def latest(self, workspace):
        with self.lock:
            index_path = self._index_path(workspace)
            if not index_path.exists():
                return None
            index = self._read(index_path)
            identifier = index.get("snapshot_id", "")
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise ValueError("Invalid saved environment identity.")
            data = self._read(self.root / "snapshots" / f"{identifier}.json")
            if not isinstance(data.get("workspace"), dict):
                raise ValueError("Invalid saved environment workspace record.")
            if data["workspace"].get("path") != str(workspace):
                raise ValueError("Saved environment belongs to another workspace.")
            return data

    def _write(self, path, value):
        # Storage only; firmware and toolchain directories are never destinations.
        for parent in (self.root, path.parent):
            if parent.is_symlink():
                raise ValueError("Environment storage directories must not be symlinks.")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=True, allow_nan=False), encoding="utf-8")
            if temporary.stat().st_size > MAX_SNAPSHOT:
                raise ValueError("Environment snapshot exceeds its storage limit.")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, snapshot):
        with self.lock:
            if snapshot.get("schema_version") != SCHEMA_VERSION:
                raise ValueError("Unsupported environment storage schema. Existing data was preserved.")
            workspace = snapshot["workspace"]["path"]
            # Refuse to overwrite future formats, even when the caller forces a rescan.
            self.latest(workspace)
            identifier = snapshot["id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
                raise ValueError("Invalid saved environment identity.")
            path = self.root / "snapshots" / f"{identifier}.json"
            if not path.exists():
                self._write(path, snapshot)
            else:
                if self._read(path) != snapshot:
                    raise ValueError("Environment identity already belongs to another observation.")
            self._write(self._index_path(workspace), {"schema_version": SCHEMA_VERSION, "snapshot_id": identifier})


class ProjectsService:
    def __init__(self, context):
        self.context = context
        self.store = EnvironmentStore(context.feature_data("projects") / "environments")

    def current(self):
        workspace = self.context.workspace.get()["path"]
        jobs = [job for job in self.context.jobs.list("projects") if job["metadata"].get("workspace") == workspace
                and job["metadata"].get("operation") == "analyze"]
        snapshot = self.store.latest(workspace)
        if snapshot:
            snapshot = deepcopy(snapshot)
            snapshot["freshness"] = "saved-observation"
        return {"workspace": workspace, "snapshot": snapshot, "job": jobs[0] if jobs else None}

    def start(self, body):
        workspace = self.context.workspace.get()["path"]
        options = validate_options(body, workspace)
        environment = dict(os.environ)
        previous = self.store.latest(workspace)

        def analyze(emit, cancel):
            result = analyze_environment(workspace, options, environ=environment, emit=emit, cancel=cancel, previous=previous)
            check_cancel(cancel)
            if not result["cache"]["hit"]:
                self.store.save(result)
            return {"snapshot": result}

        return self.context.jobs.submit("projects", analyze, resource_key=f"environment:{workspace}",
                                        metadata={"workspace": workspace, "operation": "analyze", "options": options})
