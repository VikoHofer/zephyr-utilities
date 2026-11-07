"""Guarded Linux overlay publication, with durable recovery evidence.

Directory locks coordinate participating writers. Atomic exchange and verification do
not isolate writes through external descriptors; an uncertain exchange is never replayed
or rolled back automatically.
"""

from contextlib import contextmanager
import base64
from copy import deepcopy
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from threading import Lock, RLock
from weakref import WeakValueDictionary

try:
    import fcntl
except ImportError:  # Reading drafts remains available on hosts without Linux Save.
    fcntl = None

from .source_syntax import MAX_BYTES


MAX_PUBLICATIONS = 64
MAX_RECORD_BYTES = 4 * 1024 * 1024
_LOCKS = WeakValueDictionary()
_LOCK_GUARD = Lock()
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _sha(source):
    return hashlib.sha256(source).hexdigest()


def _path(value):
    if not isinstance(value, (str, Path)) or not str(value) or len(str(value)) > 4096:
        raise ValueError("Choose a supported absolute overlay path.")
    if any(ord(char) < 32 or ord(char) == 127 for char in str(value)):
        raise ValueError("Overlay paths must not contain control characters.")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Overlay publication requires absolute paths without parent traversal.")
    return path


def _open_directory(path, *, create=False):
    """Open each path component without links; return descriptor and identity chain."""
    path = _path(path)
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    current = Path(path.anchor)
    ancestors = []
    try:
        for part in (None, *path.parts[1:]):
            if part is not None:
                if create:
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=descriptor)
                        os.fsync(descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
                current /= part
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("Overlay ancestors must be real directories.")
            ancestors.append({"path": str(current), "device": info.st_dev, "inode": info.st_ino})
        return descriptor, ancestors
    except BaseException:
        os.close(descriptor)
        raise


def _identity(info):
    return {"device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid,
            "nlink": info.st_nlink}


def _existing_parent(path):
    """Observe the existing directory prefix without creating missing parents."""
    path = _path(path)
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    current = Path(path.anchor)
    ancestors = []
    missing = []
    try:
        for part in (None, *path.parts[1:]):
            if part is not None:
                current /= part
                if missing:
                    missing.append(str(current))
                    continue
                try:
                    child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
                except FileNotFoundError:
                    # NOFOLLOW rejects dangling links instead of treating them as
                    # an absent directory that may be replaced.
                    try:
                        os.stat(part, dir_fd=descriptor, follow_symlinks=False)
                    except FileNotFoundError:
                        missing.append(str(current))
                        continue
                    raise ValueError("A missing overlay parent changed during observation.")
                os.close(descriptor)
                descriptor = child
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("Overlay ancestors must be real directories.")
            ancestors.append({"path": str(current), "device": info.st_dev, "inode": info.st_ino})
        return descriptor, ancestors, missing
    except BaseException:
        os.close(descriptor)
        raise


def _attributes(fd):
    if not hasattr(os, "listxattr"):
        return {}
    names = os.listxattr(fd)
    if len(names) > 64:
        raise ValueError("This overlay has too many extended attributes to preserve safely.")
    result, size = {}, 0
    for name in sorted(names):
        value = os.getxattr(fd, name)
        size += len(value)
        if size > 128 * 1024:
            raise ValueError("This overlay's extended attributes exceed the preservation limit.")
        result[name] = base64.b64encode(value).decode("ascii")
    return result


def _read(parent, name, *, limit=MAX_BYTES, attributes=False):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError("Save requires a regular, singly linked overlay within the source size limit.")
        attrs = _attributes(stream.fileno()) if attributes else {}
        data = stream.read(limit + 1)
        if attributes and attrs != _attributes(stream.fileno()):
            raise ValueError("The overlay's attributes changed while reading. Compare the files again.")
        after = os.fstat(stream.fileno())
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (len(data) > limit or _identity(before) != _identity(after)
                or not stat.S_ISREG(named.st_mode) or _identity(named) != _identity(after)):
            raise ValueError("The overlay changed while it was being read. Compare the files again.")
    return data, _identity(after), attrs


def _chain(path, expected):
    descriptor, ancestors = _open_directory(path)
    os.close(descriptor)
    if ancestors != expected:
        raise ValueError("The overlay directory or one of its ancestors changed. Open the application again.")


def _directory_entries(directory):
    # A descriptor opened before children were created can retain a stale initial
    # directory view on some filesystems. Reopen the held inode, not its pathname.
    current = os.open(".", _DIRECTORY_FLAGS, dir_fd=directory)
    try:
        return os.listdir(current)
    finally:
        os.close(current)


@contextmanager
def _destination_lock(parent, name):
    info = os.fstat(parent)
    key = (info.st_dev, info.st_ino, name)
    with _LOCK_GUARD:
        lock = _LOCKS.setdefault(key, RLock())
    with lock:
        if fcntl is None:
            raise ValueError("Guarded overlay Save currently requires Linux file locking.")
        # Also coordinate different application processes/data directories. This
        # deliberately serializes all publications in this parent directory.
        try:
            fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Another participating writer is saving in this directory. Review again after it finishes.") from exc
        try:
            yield
        finally:
            fcntl.flock(parent, fcntl.LOCK_UN)


def _exchange(parent, left, right):
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if not sys.platform.startswith("linux") or rename is None:
        raise ValueError("Guarded overlay Save currently requires Linux atomic file exchange.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(left), parent, os.fsencode(right), 2):
        error = ctypes.get_errno()
        raise OSError(error, "The filesystem could not atomically exchange the reviewed overlay: " + os.strerror(error))


def _noreplace(parent, left, right):
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if not sys.platform.startswith("linux") or rename is None:
        raise ValueError("Guarded overlay creation currently requires Linux atomic no-replacement rename.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(left), parent, os.fsencode(right), 1):
        error = ctypes.get_errno()
        raise OSError(error, "The filesystem could not create the reviewed overlay without replacement: " + os.strerror(error))


class OverlayPublisher:
    """Reviewed replace/create writes; no automatic recovery or external tooling."""

    def __init__(self, data_dir):
        self.directory = _path(data_dir) / "publications"
        self.lock = RLock()
        self.completed = {}

    @staticmethod
    def observe(workspace, path, *, allow_absent=False):
        workspace, path = _path(workspace), _path(path)
        if not path.is_relative_to(workspace) or path == workspace or path.suffix != ".overlay":
            raise ValueError("Save only supports an overlay inside the selected workspace.")
        if allow_absent:
            workspace_fd, _ = _open_directory(workspace)
            os.close(workspace_fd)
            parent, ancestors, missing = _existing_parent(path.parent)
        else:
            parent, ancestors = _open_directory(path.parent)
            missing = []
        try:
            try:
                if missing:
                    raise FileNotFoundError("The overlay parent has not been created.")
                source, identity, attributes = _read(parent, path.name, attributes=True)
            except FileNotFoundError:
                if not allow_absent:
                    raise
                if not missing:
                    try:
                        os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise ValueError("The overlay destination appeared during observation.")
                info = os.fstat(parent)
                writable = bool(info.st_mode & 0o222) and os.access(".", os.W_OK | os.X_OK, dir_fd=parent, effective_ids=True)
                _chain(ancestors[-1]["path"], ancestors)
                check, check_ancestors, check_missing = _existing_parent(path.parent)
                os.close(check)
                if (check_ancestors, check_missing) != (ancestors, missing):
                    raise ValueError("The overlay parent changed during observation.")
                return {"schema_version": 1, "workspace": str(workspace), "path": str(path), "exists": False,
                        "ancestors": ancestors, "missing_directories": missing, "identity": None, "attributes": {},
                        "sha256": _sha(b""), "text": "", "writable": writable,
                        "write_reason": "" if writable else "The overlay parent directory is read only or unavailable for creation."}
            text = source.decode("utf-8", errors="strict")
            if "\x00" in text:
                raise ValueError("The overlay contains binary data.")
            writable = os.access(path.name, os.W_OK, dir_fd=parent, effective_ids=True, follow_symlinks=False)
            reason = ""
            if not identity["mode"] & 0o222 or not writable:
                reason = "The overlay is read only. Change its permissions outside the editor before saving."
            elif identity["uid"] != os.geteuid():
                reason = "Save currently supports overlays owned by the current user."
            elif identity["mode"] & 0o7000:
                reason = "Save does not support special permission bits. Export the draft instead."
            _chain(path.parent, ancestors)
            return {"schema_version": 1, "workspace": str(workspace), "path": str(path),
                    "ancestors": ancestors, "identity": identity, "attributes": attributes,
                    "sha256": _sha(source), "text": text,
                    "writable": not reason, "write_reason": reason}
        finally:
            os.close(parent)

    @staticmethod
    def check_writable(observation):
        if not sys.platform.startswith("linux") or fcntl is None:
            raise ValueError("Guarded overlay Save currently requires Linux.")
        if not observation.get("writable"):
            raise ValueError(observation.get("write_reason") or "The overlay is not writable.")

    def _load(self, operation_id):
        try:
            directory, _ = _open_directory(self.directory)
        except FileNotFoundError:
            return None
        try:
            try:
                raw, _, _ = _read(directory, operation_id + ".json", limit=MAX_RECORD_BYTES)
            except FileNotFoundError:
                return None
            record = json.loads(raw)
            if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
                    or record["schema_version"] not in {1, 2}):
                raise ValueError("This publication record has an unsupported version and was left untouched.")
            if record.get("id") != operation_id:
                raise ValueError("The publication recovery identity is invalid and was left untouched.")
            status = record.get("status")
            if (not isinstance(status, str) or status not in {"prepared", "saved", "unchanged", "conflict", "uncertain"}
                    or not isinstance(record.get("original"), dict)
                    or not isinstance(record.get("candidate_text"), str)
                    or not isinstance(record.get("recovery"), dict)
                    or any(not isinstance(record.get(key), str) for key in ("workspace", "path", "sha256", "original_sha256"))
                    or any(not re.fullmatch(r"[a-f0-9]{64}", record[key]) for key in ("sha256", "original_sha256"))
                    or any(not isinstance(record["recovery"].get(key), str) for key in ("journal", "staged_path"))):
                raise ValueError("The publication recovery record is malformed and was left untouched.")
            if record["schema_version"] == 2 and (record.get("operation") != "create" or record["original"].get("exists") is not False):
                raise ValueError("The publication creation record is malformed and was left untouched.")
            _path(record["workspace"])
            _path(record["path"])
            return record
        finally:
            os.close(directory)

    def _store(self, record, *, create=False):
        raw = json.dumps(record, ensure_ascii=True, allow_nan=False, sort_keys=True).encode("utf-8")
        if len(raw) > MAX_RECORD_BYTES:
            raise ValueError("Publication evidence exceeded its recovery size limit.")
        directory, _ = _open_directory(self.directory, create=True)
        temporary = "." + record["id"] + ".pending"
        try:
            if create:
                names = os.listdir(directory)
                if len(names) >= MAX_PUBLICATIONS:
                    raise ValueError("Publication recovery storage is full. Preserve or archive existing evidence before saving again.")
                # An existing operation can never be replaced with different intent.
                try:
                    os.stat(record["id"] + ".json", dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ValueError("This publication already has recovery evidence. Compare it before continuing.")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, record["id"] + ".json", src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            # Failed metadata writes remain as evidence and block a blind retry.
            os.close(directory)

    @staticmethod
    def _result(record):
        return {key: record[key] for key in ("schema_version", "id", "status", "path", "sha256",
                "original_sha256", "operation", "message", "recovery", "observation") if key in record}

    def _receipt(self, record):
        completed = self.completed.get(record["id"])
        if completed is not None:
            return deepcopy(completed)
        result = self._result(record)
        if record.get("status") in {"prepared", "saved", "unchanged"}:
            result.update(status="uncertain", message="This Save has retained evidence from an earlier or interrupted session. Compare the current file before continuing; no publication will be replayed.")
            result.pop("observation", None)
        return result

    def _remember(self, record):
        self.completed[record["id"]] = self._result(record)
        while len(self.completed) > MAX_PUBLICATIONS:
            del self.completed[next(iter(self.completed))]

    def receipts(self, workspace):
        workspace = str(_path(workspace))
        with self.lock:
            try:
                directory, _ = _open_directory(self.directory)
            except FileNotFoundError:
                return []
            try:
                names = sorted(os.listdir(directory))
            finally:
                os.close(directory)
            result = []
            for name in names[:MAX_PUBLICATIONS * 2]:
                if re.fullmatch(r"[a-f0-9]{32}\.json", name):
                    try:
                        record = self._load(name[:-5])
                        if record and record.get("workspace") == workspace:
                            summary = self._receipt(record)
                            summary.pop("observation", None)
                            if summary["status"] == "prepared":
                                summary.update(status="uncertain", message="An interrupted Save needs comparison. It will not be replayed automatically.")
                            result.append(summary)
                    except (OSError, ValueError) as exc:
                        result.append({"id": name[:-5], "status": "unavailable", "message": str(exc)})
            return result

    def _publish_create(self, workspace, path, expected, candidate, operation_id, revalidate_callback):
        parent, ancestors, missing = _existing_parent(path.parent)
        opened = []
        try:
            with _destination_lock(parent, Path(missing[0]).name if missing else path.name):
                if ancestors != expected.get("ancestors") or missing != expected.get("missing_directories", []):
                    raise ValueError("The reviewed overlay parent changed.")
                if self.observe(workspace, path, allow_absent=True) != expected:
                    raise ValueError("The overlay destination changed since review. No file was replaced.")
                self.check_writable(expected)
                revalidate_callback()
                temporary = f".zephyr-dte-{operation_id}.new"
                anchor = Path(ancestors[-1]["path"])
                record = {"schema_version": 2, "operation": "create", "id": operation_id, "status": "prepared",
                          "workspace": workspace, "path": str(path), "sha256": _sha(candidate),
                          "original_sha256": expected["sha256"], "original": deepcopy(expected),
                          "candidate_text": candidate.decode("utf-8"), "created_at": datetime.now(timezone.utc).isoformat(),
                          "recovery": {"journal": str(self.directory / (operation_id + ".json")),
                                       "staged_path": str(anchor / temporary)}}
                self._store(record, create=True)
                published = False
                try:
                    stage_parent = parent
                    created = []
                    for index, directory in enumerate(missing):
                        name = temporary if index == 0 else Path(directory).name
                        os.mkdir(name, mode=0o700, dir_fd=stage_parent)
                        child = os.open(name, _DIRECTORY_FLAGS, dir_fd=stage_parent)
                        opened.append(child)
                        info = os.fstat(child)
                        created.append((stage_parent, name, child, {"path": directory, "device": info.st_dev, "inode": info.st_ino}))
                        stage_parent = child
                    staged_name = path.name if missing else temporary
                    fd = os.open(staged_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=stage_parent)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(candidate)
                        stream.flush()
                        os.fsync(stream.fileno())
                        staged_identity = _identity(os.fstat(stream.fileno()))
                    for directory in reversed(opened):
                        os.fsync(directory)
                    os.fsync(parent)
                    revalidate_callback()
                    if self.observe(workspace, path, allow_absent=True) != expected:
                        raise ValueError("The destination appeared or changed while Save was prepared. No file was replaced.")
                    staged, identity, _ = _read(stage_parent, staged_name)
                    if staged != candidate or identity != staged_identity:
                        raise ValueError("The staged overlay changed before creation.")
                    for index, (owner, name, child, identity) in enumerate(created):
                        named, held = os.stat(name, dir_fd=owner, follow_symlinks=False), os.fstat(child)
                        if (not stat.S_ISDIR(named.st_mode) or (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino)
                                or (held.st_dev, held.st_ino) != (identity["device"], identity["inode"])
                                or stat.S_IMODE(held.st_mode) != 0o700 or held.st_uid != os.geteuid()
                                or _directory_entries(child) != [Path(missing[index + 1]).name if index + 1 < len(missing) else path.name]):
                            raise ValueError("A staged overlay directory changed before creation.")
                    _chain(anchor, ancestors)
                    _noreplace(parent, temporary, Path(missing[0]).name if missing else path.name)
                    published = True
                    os.fsync(parent)
                    observation = self.observe(workspace, path)
                    unchanged_keys = ("device", "inode", "size", "mtime_ns", "mode", "uid", "gid", "nlink")
                    if (observation["sha256"] != record["sha256"]
                            or observation["ancestors"] != ancestors + [item[3] for item in created]
                            or any(observation["identity"][key] != staged_identity[key] for key in unchanged_keys)):
                        raise ValueError("The destination changed during overlay creation.")
                    if any(_directory_entries(item[2]) != [Path(missing[index + 1]).name if index + 1 < len(missing) else path.name]
                           for index, item in enumerate(created)):
                        raise ValueError("A created overlay directory acquired unexpected contents during publication.")
                    self.check_writable(observation)
                    record.update(status="saved", observation=observation,
                                  message="Created the reviewed overlay without replacing any existing file.")
                    self._store(record)
                    self._remember(record)
                    return self._result(record)
                except (OSError, ValueError) as exc:
                    record.pop("observation", None)
                    record.update(status="uncertain" if published else "conflict",
                                  message=("Overlay creation may already be visible. Compare the destination and retained recovery evidence. "
                                           "No automatic rollback or retry was performed. " if published else
                                           "No existing file was replaced. The draft and creation evidence were retained. ") + str(exc))
                    try:
                        self._store(record)
                    except (OSError, ValueError):
                        pass
                    return self._result(record)
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)
            os.close(parent)

    def publish(self, workspace, path, expected, candidate, operation_id, revalidate_callback):
        workspace, path = str(_path(workspace)), _path(path)
        if not isinstance(operation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", operation_id):
            raise ValueError("Choose a valid reviewed publication identity.")
        if not isinstance(candidate, bytes) or len(candidate) > MAX_BYTES or b"\x00" in candidate:
            raise ValueError("The reviewed overlay must be bounded UTF-8 source.")
        candidate_text = candidate.decode("utf-8", errors="strict")
        if (not isinstance(expected, dict) or expected.get("schema_version") != 1
                or expected.get("workspace") != workspace or expected.get("path") != str(path)):
            raise ValueError("The reviewed destination changed.")
        digest = _sha(candidate)
        operation = "create" if expected.get("exists") is False else "replace"
        with self.lock:
            completed = self.completed.get(operation_id)
            if completed is not None:
                if (completed["path"] != str(path) or completed["sha256"] != digest
                        or completed["original_sha256"] != expected.get("sha256")
                        or completed.get("operation", "replace") != operation):
                    raise ValueError("This publication identity was already used for a different change.")
                return deepcopy(completed)
            previous = self._load(operation_id)
            if previous is not None:
                if (previous.get("workspace") != workspace or previous.get("path") != str(path)
                        or previous.get("sha256") != digest or previous.get("original_sha256") != expected.get("sha256")
                        or previous.get("operation", "replace") != operation):
                    raise ValueError("This publication identity was already used for a different change.")
                return self._receipt(previous)
            if operation == "create":
                return self._publish_create(workspace, path, expected, candidate, operation_id, revalidate_callback)
            parent, ancestors = _open_directory(path.parent)
            try:
                with _destination_lock(parent, path.name):
                    if ancestors != expected.get("ancestors"):
                        raise ValueError("The reviewed overlay directory changed.")
                    current = self.observe(workspace, path)
                    if current != expected:
                        raise ValueError("The overlay changed since review. Compare it before saving.")
                    self.check_writable(current)
                    revalidate_callback()
                    temporary = f".zephyr-dte-{operation_id}.old"
                    record = {"schema_version": 1, "id": operation_id, "status": "prepared",
                              "workspace": workspace, "path": str(path), "sha256": digest,
                              "original_sha256": current["sha256"], "original": current,
                              "candidate_text": candidate_text, "created_at": datetime.now(timezone.utc).isoformat(),
                              "recovery": {"journal": str(self.directory / (operation_id + ".json")),
                                           "staged_path": str(path.parent / temporary)}}
                    if candidate == current["text"].encode("utf-8"):
                        record.pop("recovery")
                        record.update(status="unchanged", observation=current, message="The overlay already contains these bytes.")
                        self._remember(record)
                        return self._result(record)
                    self._store(record, create=True)
                    exchanged = False
                    try:
                        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                        with os.fdopen(fd, "wb") as stream:
                            identity = current["identity"]
                            if os.fstat(stream.fileno()).st_gid != identity["gid"]:
                                os.fchown(stream.fileno(), -1, identity["gid"])
                            os.fchmod(stream.fileno(), identity["mode"])
                            stream.write(candidate)
                            stream.flush()
                            existing_attrs = _attributes(stream.fileno())
                            for name in existing_attrs.keys() - current["attributes"].keys():
                                os.removexattr(stream.fileno(), name)
                            for name, value in current["attributes"].items():
                                if existing_attrs.get(name) != value:
                                    os.setxattr(stream.fileno(), name, base64.b64decode(value, validate=True))
                            if (_attributes(stream.fileno()) != current["attributes"]
                                    or stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != identity["mode"]):
                                raise ValueError("The candidate could not preserve the overlay's permissions and attributes.")
                            os.fsync(stream.fileno())
                            staged_identity = _identity(os.fstat(stream.fileno()))
                        os.fsync(parent)
                        revalidate_callback()
                        if self.observe(workspace, path) != expected:
                            raise ValueError("The overlay changed while Save was prepared. No replacement was made.")
                        staged, actual, staged_attributes = _read(parent, temporary, attributes=True)
                        if staged != candidate or actual != staged_identity or staged_attributes != current["attributes"]:
                            raise ValueError("The staged candidate changed before publication. No replacement was made.")
                        _chain(path.parent, ancestors)
                        _exchange(parent, temporary, path.name)
                        exchanged = True
                        os.fsync(parent)
                        displaced, displaced_identity, displaced_attributes = _read(parent, temporary, attributes=True)
                        unchanged_keys = ("device", "inode", "size", "mtime_ns", "mode", "uid", "gid", "nlink")
                        if displaced_attributes != current["attributes"] or displaced != current["text"].encode("utf-8") or any(
                                displaced_identity[key] != identity[key] for key in unchanged_keys):
                            raise ValueError("The displaced overlay differs from the reviewed original.")
                        observation = self.observe(workspace, path)
                        self.check_writable(observation)
                        if observation["sha256"] != digest or observation["attributes"] != current["attributes"] or any(
                                observation["identity"][key] != staged_identity[key] for key in unchanged_keys):
                            raise ValueError("The destination changed during publication.")
                        _chain(path.parent, ancestors)
                        record.update(status="saved", observation=observation,
                                      message="Saved the reviewed overlay. Recovery evidence was retained.")
                        self._store(record)
                        self._remember(record)
                        return self._result(record)
                    except (OSError, ValueError) as exc:
                        record.pop("observation", None)
                        record.update(status="uncertain" if exchanged else "conflict",
                                      message=("Publication may already be visible. Compare the destination and retained recovery files. "
                                               "No automatic rollback or retry was performed. " if exchanged else
                                               "No overlay replacement was made. The draft and recovery evidence were retained. ") + str(exc))
                        try:
                            self._store(record)
                        except (OSError, ValueError):
                            # Either the prepared or terminal record still identifies
                            # both candidates; failed metadata durability is uncertain.
                            pass
                        return self._result(record)
            finally:
                os.close(parent)
