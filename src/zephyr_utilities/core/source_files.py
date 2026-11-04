"""Bounded current-source reads within one selected workspace, without symlinks."""

import os
from pathlib import Path
import stat

from .builds import _Budget, _directory_identity, _open_directory, _read_file


MAX_SOURCE_BYTES = 2 * 1024 * 1024


class _SourceBudget(_Budget):
    def __init__(self, reader):
        super().__init__()
        self.reader = reader

    def consume(self, size):
        self.reader.bytes_read += size
        super().consume(size)


class WorkspaceSourceReader:
    """Read data files; callers decide which source references users may open."""

    def __init__(self, workspace):
        self.workspace = Path(workspace)
        if not self.workspace.is_absolute() or ".." in self.workspace.parts:
            raise ValueError("Choose an absolute workspace without parent traversal.")
        self._identity = _directory_identity(self.workspace)
        self.bytes_read = 0

    def path(self, value, base=None):
        if not isinstance(value, (str, Path)) or not str(value) or len(str(value)) > 4096:
            raise ValueError("The source path is invalid.")
        if any(ord(character) < 32 or ord(character) == 127 for character in str(value)):
            raise ValueError("Source paths must not contain control characters.")
        path = Path(value)
        if ".." in path.parts:
            raise ValueError("Source paths must not contain parent traversal.")
        if not path.is_absolute():
            path = Path(base) / path if base is not None else self.workspace / path
        if ".." in path.parts or not path.is_relative_to(self.workspace):
            raise ValueError("This source is outside the selected workspace.")
        return path

    def _verify_workspace(self):
        if _directory_identity(self.workspace) != self._identity:
            raise ValueError("The workspace directory changed. Open the sources again.")

    def read_text(self, path, limit=MAX_SOURCE_BYTES):
        path = self.path(path)
        if not isinstance(limit, int) or not 0 < limit <= MAX_SOURCE_BYTES:
            raise ValueError("Choose a supported source reading limit.")
        self._verify_workspace()
        identity, content = _read_file(self.workspace, path, _SourceBudget(self), content=True, limit=limit)
        self._verify_workspace()
        try:
            text = content.decode("utf-8")
        except UnicodeError as exc:
            raise ValueError("This source is not UTF-8 text.") from exc
        if "\x00" in text:
            raise ValueError("This source contains binary data.")
        return {"path": str(path), "text": text, "sha256": identity["sha256"]}

    def list_files(self, root, suffixes, max_files=10000, max_dirs=2000):
        """List regular candidates without following links; fail on incomplete scans."""
        root = self.path(root)
        if not isinstance(max_files, int) or not 0 < max_files <= 10000 or not isinstance(max_dirs, int) or not 0 < max_dirs <= 2000:
            raise ValueError("Choose supported source directory limits.")
        suffixes = frozenset(suffixes)
        self._verify_workspace()
        files, directories, entries = [], 0, 0

        def visit(path, descriptor, depth):
            nonlocal directories, entries
            directories += 1
            if directories > max_dirs or depth > 32:
                raise ValueError("Source directory scan exceeded its directory limit.")
            before = os.fstat(descriptor)
            names = []
            with os.scandir(descriptor) as listing:
                for entry in listing:
                    entries += 1
                    if entries > max_files + max_dirs:
                        raise ValueError("Source directory scan exceeded its entry limit.")
                    names.append(entry.name)
            for name in sorted(names):
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                child = path / name
                if stat.S_ISDIR(info.st_mode):
                    opened = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                    try:
                        actual = os.fstat(opened)
                        if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                            raise ValueError("Source directory changed while being listed.")
                        visit(child, opened, depth + 1)
                    finally:
                        os.close(opened)
                elif stat.S_ISREG(info.st_mode) and child.suffix in suffixes:
                    files.append(child)
                    if len(files) > max_files:
                        raise ValueError("Source directory scan exceeded its file limit.")
            if _directory_identity(path) != (before.st_dev, before.st_ino):
                raise ValueError("Source directory changed while being listed.")

        descriptor = _open_directory(root)
        try:
            visit(root, descriptor, 0)
        finally:
            os.close(descriptor)
        self._verify_workspace()
        return files
