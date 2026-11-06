"""Versioned presentation coordinates, independent of overlay source and history."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
from threading import RLock
import uuid

from .drafts import _GUARD_FIELDS


MAX_POSITIONS = 256
MAX_RECORD_BYTES = 256 * 1024
MAX_RECORDS = 256


def positions(value, keys=None):
    if not isinstance(value, dict) or len(value) > MAX_POSITIONS:
        raise ValueError("A module layout supports at most 256 positions.")
    for key, point in value.items():
        if (not isinstance(key, str) or not key.startswith("node:/") or len(key) > 4096
                or any(ord(char) < 32 for char in key) or (keys is not None and key not in keys)):
            raise ValueError("Choose nodes in the current module graph before saving layout.")
        if (not isinstance(point, dict) or set(point) != {"x", "y"}
                or any(type(point[axis]) is not int or not 0 <= point[axis] <= 31 for axis in ("x", "y"))):
            raise ValueError("Module coordinates must be whole numbers from 0 to 31.")
    return {key: dict(point) for key, point in value.items()}


class LayoutStore:
    """Atomic bounded local records with revision checks across application processes."""

    def __init__(self, root):
        self.root = Path(root) / "module-layouts"
        self.lock = RLock()

    @contextmanager
    def directory(self, *, create=False):
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise ValueError("Module layout storage requires an absolute directory.")
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in self.root.parts[1:]:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                        os.fsync(descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def identity(scope):
        return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _read(self, directory, scope):
        layout_id = self.identity(scope)
        try:
            descriptor = os.open(layout_id + ".json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return {"schema_version": 1, "layout_id": layout_id, "revision": 0, "positions": {}, "scope": scope}
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_RECORD_BYTES:
                raise ValueError("The module layout is not a supported bounded regular file.")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                content = stream.read(MAX_RECORD_BYTES + 1)
            after = os.fstat(descriptor)
            if len(content) > MAX_RECORD_BYTES or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("The module layout changed while being read. Load it again.")
            value = json.loads(content)
        finally:
            os.close(descriptor)
        if (not isinstance(value, dict) or set(value) != {"schema_version", "layout_id", "revision", "positions", "scope"}
                or type(value.get("schema_version")) is not int or value["schema_version"] != 1):
            raise ValueError("Unsupported module layout schema. The record was preserved unchanged.")
        if (value["layout_id"] != layout_id or value["scope"] != scope
                or type(value["revision"]) is not int or value["revision"] < 1):
            raise ValueError("The module layout identity or revision is invalid. The record was preserved.")
        positions(value["positions"])
        return value

    def read(self, scope):
        with self.lock:
            try:
                with self.directory() as directory:
                    return self._read(directory, scope)
            except FileNotFoundError:
                return {"schema_version": 1, "layout_id": self.identity(scope), "revision": 0, "positions": {}, "scope": scope}

    def save(self, scope, expected_revision, points):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Choose the observed module layout revision.")
        points = positions(points)
        with self.lock, self.directory(create=True) as directory:
            current = self._read(directory, scope)
            if current["revision"] != expected_revision:
                raise ValueError("The module layout changed. Reload layout before saving positions.")
            if current["positions"] == points:
                return current
            name = current["layout_id"] + ".json"
            names = [entry for entry in os.listdir(directory) if entry.endswith(".json")]
            if name not in names and len(names) >= MAX_RECORDS:
                raise ValueError("Module layout storage is full. Existing layouts were preserved.")
            value = {**current, "revision": current["revision"] + 1, "positions": points}
            content = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            if len(content) > MAX_RECORD_BYTES:
                raise ValueError("Module layout exceeds its storage limit. Existing layout was preserved.")
            temporary = ".layout-" + uuid.uuid4().hex
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            return value


class LayoutService:
    def __init__(self, drafts, modules, storage):
        self.drafts, self.modules = drafts, modules
        self.store = LayoutStore(storage)

    @staticmethod
    def _scope(context, draft):
        return {"workspace": context["workspace"], "workspace_identity": context["workspace_identity"],
                "application": context["application"]["path"], "path": draft.path,
                "project": (context.get("project") or {}).get("id"),
                "configuration": (context.get("configuration") or {}).get("id"),
                "target": (context.get("target") or {}).get("requested")}

    def _request(self, body, *, save):
        extra = {"positions", "expected_layout_revision"} if save else set()
        binding = self.drafts._body(body, _GUARD_FIELDS | {"schema_id"} | extra)
        with self.drafts.editor.lock, self.drafts.lock:
            context = self.drafts._context(binding)
            draft = self.drafts._scope(body, context)
            self.drafts._guard(body, draft)
            graph = self.modules.validate_observation({key: value for key, value in body.items() if key not in extra}, context, draft)
            keys = {node["key"] for node in graph["graph"]["nodes"] if node.get("key")}
            scope = self._scope(context, draft)
            if save:
                points = positions(body["positions"], keys)
                value = self.store.save(scope, body["expected_layout_revision"], points)
            else:
                value = self.store.read(scope)
            return {key: ({node: point for node, point in data.items() if node in keys} if key == "positions" else data)
                    for key, data in value.items() if key != "scope"}

    def read(self, body):
        return self._request(body, save=False)

    def save(self, body):
        return self._request(body, save=True)
