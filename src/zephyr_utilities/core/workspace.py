"""Shared workspace selection; changing it never changes already submitted jobs."""

from pathlib import Path
from threading import RLock


class WorkspaceService:
    def __init__(self, path):
        self._lock = RLock()
        self.set(path)

    def get(self):
        with self._lock:
            return {"path": self._path}

    def set(self, path):
        if not isinstance(path, (str, Path)) or not str(path).strip():
            raise ValueError("Select an existing workspace directory.")
        directory = Path(path).expanduser().resolve()
        if not directory.is_dir():
            raise FileNotFoundError(f"Workspace directory does not exist: {directory}")
        with self._lock:
            self._path = str(directory)
            return self.get()


def list_folders(path):
    directory = Path(path).expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Folder does not exist: {directory}")
    folders = [
        {"name": child.name, "path": str(child.resolve())}
        for child in directory.iterdir()
        if not child.name.startswith(".") and child.is_dir()
    ]
    folders.sort(key=lambda item: item["name"].casefold())
    return {"path": str(directory), "parent": str(directory.parent) if directory.parent != directory else None,
            "folders": folders}
