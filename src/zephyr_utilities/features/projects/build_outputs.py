"""Bounded review and no-follow removal of one managed build output directory."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .creation import open_directory, path_identity, validate_identity
from .probing import check_cancel


def managed_path(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or path.parent.name != "build" or not re.fullmatch(r"[a-f0-9]{32}", path.name):
        raise ValueError("Only an application's recorded build/<output-id> directory can be managed.")
    return path


def _entry(path, info):
    kind = "directory" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "symlink" if stat.S_ISLNK(info.st_mode) else None
    if kind is None:
        raise ValueError("Build output contains a special filesystem entry; automatic cleanup is unavailable.")
    return {"path": path, "type": kind, "device": info.st_dev, "inode": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns}


def inventory_output(path, *, identity=None, cancel=None):
    """Capture metadata only; symlink destinations and regular file bytes are never read."""
    path = managed_path(path)
    identity = identity or path_identity(path)
    check_cancel(cancel)
    descriptor = open_directory(path, identity)
    entries = []
    try:
        entries.append(_entry("", os.fstat(descriptor)))

        def visit(directory, relative, depth):
            if depth > 32:
                raise ValueError("Build output exceeds the cleanup directory depth limit.")
            with os.scandir(directory) as iterator:
                names = sorted(item.name for item in iterator)
            if len(entries) + len(names) > 100000:
                raise ValueError("Build output exceeds the 100,000-entry cleanup limit.")
            for name in names:
                check_cancel(cancel)
                if len(entries) >= 100000:
                    raise ValueError("Build output exceeds the 100,000-entry cleanup limit.")
                item = _entry(f"{relative}/{name}".lstrip("/"), os.stat(name, dir_fd=directory, follow_symlinks=False))
                entries.append(item)
                if item["type"] == "directory":
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (item["device"], item["inode"]):
                            raise ValueError("Build output changed while its cleanup inventory was read.")
                        visit(child, item["path"], depth + 1)
                    finally:
                        os.close(child)
        visit(descriptor, "", 0)
        if _entry("", os.fstat(descriptor)) != entries[0]:
            raise ValueError("Build output changed while its cleanup inventory was read.")
    finally:
        os.close(descriptor)
    validate_identity(path, identity)
    return {"identity": identity, "fingerprint": hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest(),
            "entries": entries, "bytes": sum(item["size"] for item in entries if item["type"] != "directory"),
            "file_count": sum(item["type"] == "file" for item in entries),
            "directory_count": sum(item["type"] == "directory" for item in entries),
            "symlink_count": sum(item["type"] == "symlink" for item in entries)}


def remove_output(path, inventory, *, cancel=None, emit=None):
    """Remove only reviewed entries, after a complete second inventory matches."""
    path = managed_path(path)
    current = inventory_output(path, identity=inventory["identity"], cancel=cancel)
    if current["fingerprint"] != inventory["fingerprint"]:
        raise ValueError("Build output changed since cleanup review. Preview cleanup again; nothing was removed.")
    check_cancel(cancel)
    parent = open_directory(path.parent, inventory["identity"][:-1])
    try:
        root = open_directory(path, inventory["identity"])
    except Exception:
        os.close(parent)
        raise
    reviewed = {item["path"]: item for item in current["entries"]}
    removed = 0
    try:
        # Once deletion starts, finish the reviewed tree. Cancellation is honored
        # during preflight; stopping halfway would unnecessarily leave partial output.
        if emit:
            emit("Removing the reviewed output entries; this final deletion step will finish before cancellation.")
        for item in sorted(current["entries"][1:], key=lambda entry: (entry["path"].count("/"), entry["path"]), reverse=True):
            relative = Path(item["path"])
            directory = os.dup(root)
            try:
                for index, name in enumerate(relative.parts[:-1]):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                    prefix = "/".join(relative.parts[:index + 1])
                    expected = reviewed[prefix]
                    opened = os.fstat(child)
                    if (opened.st_dev, opened.st_ino) != (expected["device"], expected["inode"]):
                        os.close(child)
                        raise ValueError("A reviewed output subdirectory was replaced during cleanup.")
                    os.close(directory)
                    directory = child
                observed = _entry(item["path"], os.stat(relative.name, dir_fd=directory, follow_symlinks=False))
                keys = ("type", "device", "inode") if item["type"] == "directory" else tuple(item)
                if any(observed[key] != item[key] for key in keys):
                    raise ValueError("A reviewed output entry changed during cleanup; unreviewed entries were preserved.")
                if item["type"] == "directory":
                    os.rmdir(relative.name, dir_fd=directory)
                else:
                    os.unlink(relative.name, dir_fd=directory)
                removed += 1
            finally:
                os.close(directory)
        info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (current["entries"][0]["device"], current["entries"][0]["inode"]):
            raise ValueError("Output directory moved during cleanup; its replacement was preserved.")
        os.rmdir(path.name, dir_fd=parent)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cleanup stopped after removing {removed} reviewed entries. Review remaining outputs again. {exc}") from exc
    finally:
        os.close(root)
        os.close(parent)
    return {key: inventory[key] for key in ("bytes", "file_count", "directory_count", "symlink_count")}
