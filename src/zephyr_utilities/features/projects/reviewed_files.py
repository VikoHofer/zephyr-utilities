"""Apply the creator's small, reviewed default-overlay patch with guarded rollback."""

import ctypes
import hashlib
import os
from pathlib import PurePosixPath
import stat
import uuid

from .creation import open_directory, validate_identity


def _read(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
            raise ValueError("Reviewed source changes require regular files of at most 1 MiB.")
        data = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("The source file changed while it was checked. Review again.")
    return data, (after.st_dev, after.st_ino), stat.S_IMODE(after.st_mode)


def _exchange(directory, left, right):
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise ValueError("Reviewed overlay updates need Linux atomic file exchange support.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(directory, os.fsencode(left), directory, os.fsencode(right), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _same_directory(parent, name, opened):
    expected = os.fstat(opened)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    return stat.S_ISDIR(current.st_mode) and (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)


def apply_reviewed_files(source, identity, changes, save):
    """Save metadata only after exact writes; preserve unrelated/concurrent file edits."""
    if not changes:
        validate_identity(source, identity)
        return save()
    directory = open_directory(source, identity)
    overlay_directory = None
    created_directory = False
    written = []
    exchanged = []
    try:
        # The creator currently emits precisely a new overlay and a root CMake patch.
        for change in changes:
            path = PurePosixPath(change["path"])
            if (path.as_posix() != change["path"] or path.is_absolute() or ".." in path.parts or
                    not (path.name == "CMakeLists.txt" and len(path.parts) == 1 or
                         len(path.parts) == 2 and path.parts[0] == "overlays" and path.suffix == ".overlay")):
                raise ValueError("Unsupported reviewed configuration file change.")
            data = change["content"].encode("utf-8")
            if len(data) > 1024 * 1024 or hashlib.sha256(data).hexdigest() != change["sha256"]:
                raise ValueError("The reviewed configuration contents changed.")
            if change["before"] is not None:
                existing, _, _ = _read(directory, path.name)
                if existing != change["before"].encode():
                    raise ValueError("Application files changed since review. Review configuration changes again.")
        for change in sorted(changes, key=lambda item: item["before"] is not None):
            path = PurePosixPath(change["path"])
            data = change["content"].encode("utf-8")
            parent = directory
            if len(path.parts) == 2:
                if overlay_directory is None:
                    try:
                        os.mkdir("overlays", mode=0o755, dir_fd=directory)
                        created_directory = True
                    except FileExistsError:
                        pass
                    overlay_directory = os.open("overlays", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                parent = overlay_directory
            if change["before"] is None:
                fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    info = os.fstat(stream.fileno())
                written.append((parent, path.name, data, (info.st_dev, info.st_ino)))
            else:
                old, old_identity, mode = _read(parent, path.name)
                if old != change["before"].encode():
                    raise ValueError("Application files changed since review. Review configuration changes again.")
                temporary = f".zephyr-review-{uuid.uuid4().hex}"
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    info = os.fstat(stream.fileno())
                try:
                    _exchange(parent, temporary, path.name)
                except BaseException:
                    os.unlink(temporary, dir_fd=parent)
                    raise
                exchanged.append((parent, path.name, temporary, data, (info.st_dev, info.st_ino)))
                actual, actual_identity, _ = _read(parent, temporary)
                if actual != old or actual_identity != old_identity:
                    raise ValueError("The source was replaced during application. Existing edits were preserved.")
        validate_identity(source, identity)
        if overlay_directory is not None and not _same_directory(directory, "overlays", overlay_directory):
            raise ValueError("The overlay directory changed during application. Existing edits were preserved.")
        for parent, name, data, expected in written:
            current, actual, _ = _read(parent, name)
            if current != data or actual != expected:
                raise ValueError("The new overlay changed before saving. Existing edits were preserved.")
        for parent, name, _, data, expected in exchanged:
            current, actual, _ = _read(parent, name)
            if current != data or actual != expected:
                raise ValueError("The updated CMake file changed before saving. Existing edits were preserved.")
        result = save()
    except BaseException:
        for parent, name, temporary, data, expected in reversed(exchanged):
            try:
                current, actual, _ = _read(parent, name)
                if current == data and actual == expected:
                    _exchange(parent, temporary, name)
                    os.unlink(temporary, dir_fd=parent)
            except (OSError, ValueError):
                pass  # Preserve the backup if a concurrent editor owns the current file.
        for parent, name, data, expected in reversed(written):
            try:
                current, actual, _ = _read(parent, name)
                if current == data and actual == expected:
                    os.unlink(name, dir_fd=parent)
            except (OSError, ValueError):
                pass
        if created_directory:
            try:
                if overlay_directory is not None and _same_directory(directory, "overlays", overlay_directory):
                    os.rmdir("overlays", dir_fd=directory)
            except OSError:
                pass
        raise
    else:
        for parent, _, temporary, _, _ in exchanged:
            os.unlink(temporary, dir_fd=parent)
        return result
    finally:
        if overlay_directory is not None:
            os.close(overlay_directory)
        os.close(directory)
