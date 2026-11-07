"""Versioned recovery records; reading them never publishes firmware source."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
import uuid

from .source_syntax import MAX_BYTES


MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_STORE_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 64


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Choose a valid recovery record.")
    return value


def validate_record(value, workspace):
    """Validate every recovery field consumed by restoration before replacing data."""
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] not in {1, 2}:
        raise ValueError("Unsupported recovery schema. The record was preserved unchanged.")
    baseline_exists = value.get("baseline_exists", True) if value["schema_version"] == 1 else value.get("baseline_exists")
    if type(baseline_exists) is not bool or value["schema_version"] == 1 and not baseline_exists:
        raise ValueError("The recovery baseline identity is invalid; its record was preserved.")
    identifier(value.get("id"))

    def path_value(path):
        if (not isinstance(path, str) or not path or len(path) > 4096 or not Path(path).is_absolute()
                or ".." in Path(path).parts or any(ord(char) < 32 or ord(char) == 127 for char in path)):
            raise ValueError("The recovery path is malformed; its record was preserved.")
        return Path(path)

    workspace_path = path_value(workspace)
    if value.get("workspace") != workspace or not isinstance(value.get("context"), dict):
        raise ValueError("The recovery record belongs to another workspace or has no valid context.")
    if type(value.get("revision")) is not int or value["revision"] < 0:
        raise ValueError("The recovery revision is invalid; its record was preserved.")
    if not isinstance(value.get("updated_at"), str) or type(value.get("history_truncated")) is not bool:
        raise ValueError("The recovery metadata is malformed; its record was preserved.")

    def source_bytes(text):
        if not isinstance(text, str):
            raise ValueError("Recovery source must be bounded UTF-8 text.")
        source = text.encode("utf-8", errors="strict")
        if len(source) > MAX_BYTES or b"\x00" in source:
            raise ValueError("Recovery source exceeds its supported text limits.")
        return source

    base, source = source_bytes(value.get("base_text")), source_bytes(value.get("text"))
    if hashlib.sha256(base).hexdigest() != value.get("base_sha256") or hashlib.sha256(source).hexdigest() != value.get("sha256"):
        raise ValueError("The recovery source hashes do not match; its record was preserved.")
    observation, context = value.get("observation"), value["context"]
    if (not isinstance(observation, dict) or not isinstance(value.get("path"), str)
            or value["path"] != observation.get("path") or context.get("workspace") != workspace):
        raise ValueError("The recovery path or workspace identity is invalid; its record was preserved.")
    identifier(value.get("document_id"))
    identifier(context.get("id"))
    if (type(context.get("revision")) is not int or context["revision"] < 1
            or not isinstance(context.get("application"), dict) or not isinstance(context["application"].get("path"), str)
            or not isinstance(context.get("source_guard"), dict)
            or not isinstance(context.get("workspace_identity"), list)
            or not isinstance(context.get("fingerprint"), str)
            or not isinstance(context.get("documents"), list)
            or (context.get("project") is not None and (not isinstance(context["project"], dict)
                or "id" not in context["project"] or not isinstance(context.get("configuration"), dict)
                or "id" not in context["configuration"]))):
        raise ValueError("The recovery context is malformed; its record was preserved.")
    application_path = path_value(context["application"]["path"])
    destination = path_value(value["path"])
    if (application_path == workspace_path or not application_path.is_relative_to(workspace_path)
            or not destination.is_relative_to(application_path) or destination.suffix != ".overlay"):
        raise ValueError("The recovery destination is outside its original application; its record was preserved.")
    if context.get("project") is not None:
        identifier(context["project"]["id"])
        if context["configuration"]["id"] is not None:
            identifier(context["configuration"]["id"])
    if observation.get("workspace") != workspace or observation.get("sha256") != value["base_sha256"] or observation.get("text") != value["base_text"]:
        raise ValueError("The recovery observation differs from its original baseline; its record was preserved.")
    if type(observation.get("exists", True)) is not bool or observation.get("exists", True) != baseline_exists:
        raise ValueError("The recovery destination existence differs from its baseline; its record was preserved.")
    if not baseline_exists:
        selected = [entry for entry in context["documents"] if isinstance(entry, dict)
                    and entry.get("id") == value["document_id"] and entry.get("path") == value["path"]]
        ancestors = observation.get("ancestors")
        if (base or observation.get("identity") is not None or observation.get("attributes") != {}
                or len(selected) != 1 or selected[0].get("availability") != "absent" or selected[0].get("can_create") is not True
                or selected[0].get("sha256") != value["base_sha256"]
                or not isinstance(ancestors, list) or not ancestors):
            raise ValueError("The absent overlay recovery baseline is malformed; its record was preserved.")
        for ancestor in ancestors:
            if (not isinstance(ancestor, dict) or type(ancestor.get("device")) is not int or ancestor["device"] < 0
                    or type(ancestor.get("inode")) is not int or ancestor["inode"] < 0):
                raise ValueError("The absent overlay directory guard is malformed; its record was preserved.")
            path_value(ancestor.get("path"))
        missing = observation.get("missing_directories", [])
        if not isinstance(missing, list):
            raise ValueError("The absent overlay missing-directory guard is malformed; its record was preserved.")
        for path in missing:
            path_value(path)
        existing = [ancestor["path"] for ancestor in ancestors]
        expected_ancestors = [str(parent) for parent in reversed(destination.parents)]
        if existing + missing != expected_ancestors or str(application_path) not in existing:
            raise ValueError("The absent overlay directory chain does not match its destination; its record was preserved.")
    history = []
    for key in ("undo", "redo"):
        if not isinstance(value.get(key), list) or len(value[key]) > 32:
            raise ValueError("The recovery history is malformed; its record was preserved.")
        history.extend(source_bytes(text) for text in value[key])
    if len(history) > 32 or len(base) + len(source) + sum(map(len, history)) > 8 * 1024 * 1024:
        raise ValueError("The recovery history exceeds supported limits; its record was preserved.")
    return base, source


class DraftStore:
    """Atomic private JSON records, with explicit limits and unknown-version refusal."""

    def __init__(self, root):
        self.root = Path(root) / "drafts"
        self.lock = RLock()

    @contextmanager
    def _directory(self, *, create=False):
        if not self.root.is_absolute() or ".." in self.root.parts:
            raise ValueError("Recovery storage requires an absolute directory.")
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.root.parts[1:]:
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=descriptor)
                        os.fsync(descriptor)
                    except FileExistsError:
                        pass
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _read(directory, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_RECORD_BYTES:
                raise ValueError("Recovery record is not a supported bounded regular file.")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                content = stream.read(MAX_RECORD_BYTES + 1)
            after = os.fstat(descriptor)
            if len(content) > MAX_RECORD_BYTES or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("The recovery record changed while being read.")
            value = json.loads(content)
        finally:
            os.close(descriptor)
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] not in {1, 2}:
            raise ValueError("Unsupported recovery schema. The record was preserved unchanged.")
        if value.get("id") != name.removesuffix(".json"):
            raise ValueError("The recovery record identity is invalid; it was preserved.")
        validate_record(value, value.get("workspace"))
        return value

    def read(self, record_id):
        name = identifier(record_id) + ".json"
        with self.lock, self._directory() as directory:
            return self._read(directory, name)

    def records(self, workspace):
        records, diagnostics = [], []
        with self.lock:
            try:
                with self._directory() as directory:
                    names = sorted(name for name in os.listdir(directory) if name.endswith(".json"))
                    if len(names) > MAX_RECORDS:
                        diagnostics.append("Recovery storage exceeds its record limit; only the first 64 records are listed.")
                    for name in names[:MAX_RECORDS]:
                        try:
                            identifier(name.removesuffix(".json"))
                            value = self._read(directory, name)
                            if value.get("workspace") == workspace:
                                records.append(value)
                        except (OSError, ValueError, UnicodeError) as exc:
                            diagnostics.append(f"{name}: {exc}")
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as exc:
                diagnostics.append(f"Cannot read draft recovery: {exc}")
        return records, diagnostics

    def write(self, value):
        validate_record(value, value.get("workspace"))
        name = identifier(value["id"]) + ".json"
        content = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        if len(content) > MAX_RECORD_BYTES:
            raise ValueError("Draft recovery exceeds its record limit. The previous draft is retained; keep your pending text.")
        with self.lock, self._directory(create=True) as directory:
            names = [entry for entry in os.listdir(directory) if entry.endswith(".json")]
            total = 0
            for existing in names:
                info = os.stat(existing, dir_fd=directory, follow_symlinks=False)
                if existing != name:
                    total += info.st_size
            if (name not in names and len(names) >= MAX_RECORDS) or total + len(content) > MAX_STORE_BYTES:
                raise ValueError("Draft recovery storage is full. Existing records were retained; export or discard an old recovery record.")
            if name in names:
                self._read(directory, name)  # Never replace an unknown or malformed version.
            temporary = ".draft-" + uuid.uuid4().hex
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
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

    def remove(self, record_id, revision):
        name = identifier(record_id) + ".json"
        with self.lock, self._directory() as directory:
            value = self._read(directory, name)
            if type(revision) is not int or value.get("revision") != revision:
                raise ValueError("The recovery record changed. Review it again before discarding it.")
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
