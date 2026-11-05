"""Workspace-scoped custom board roots and live discovery identity."""

from copy import deepcopy
import json
from pathlib import Path
from threading import RLock
import uuid

from .analyzer import digest, fingerprint, metadata_inputs
from .creation import path_identity
from .metadata import read_regular
from .records import identifier


MAX_ROOTS = 32
MAX_RECORD = 4 * 1024 * 1024


class CustomBoardStore:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = RLock()

    def _path(self, workspace):
        return self.root / f"{digest(str(workspace))}.json"

    def read(self, workspace):
        with self.lock:
            path = self._path(workspace)
            if self.root.is_symlink() or path.is_symlink():
                raise ValueError("Custom board storage must not be a symlink.")
            if not path.exists():
                return {"schema_version": 1, "workspace": str(workspace), "revision": 0, "registrations": []}
            value = json.loads(read_regular(path, MAX_RECORD).decode("utf-8"))
            self.validate(value, workspace)
            return value

    @staticmethod
    def validate(value, workspace):
        if (not isinstance(value, dict) or value.get("schema_version") != 1 or
                value.get("workspace") != str(workspace) or type(value.get("revision")) is not int or value["revision"] < 0):
            raise ValueError("Unsupported custom board registry schema or workspace. Existing records were preserved.")
        entries = value.get("registrations")
        if not isinstance(entries, list) or len(entries) > MAX_ROOTS:
            raise ValueError("Invalid or excessive custom board registrations.")
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid custom board registration.")
            key = identifier(entry.get("id"))
            path = entry.get("path")
            if (key in seen or entry.get("workspace") != str(workspace) or not isinstance(path, str) or
                    not Path(path).is_absolute() or ".." in Path(path).parts or
                    not isinstance(entry.get("name"), str) or not entry["name"] or
                    not isinstance(entry.get("directory_identity"), list) or not entry["directory_identity"] or
                    not isinstance(entry.get("boards_identity"), list) or not entry["boards_identity"]):
                raise ValueError("Invalid custom board registration identity. Existing records were preserved.")
            seen.add(key)

    def write(self, value, expected):
        with self.lock:
            self.validate(value, expected["workspace"])
            if self.read(expected["workspace"]) != expected:
                raise ValueError("The custom board registrations changed. Review the operation again.")
            if value["revision"] != expected["revision"] + 1:
                raise ValueError("Invalid custom board registry revision.")
            encoded = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
            if len(encoded) > MAX_RECORD:
                raise ValueError("The custom board registry exceeds its storage limit.")
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(value["workspace"])
            temporary = self.root / f".{uuid.uuid4().hex}.tmp"
            try:
                with temporary.open("xb") as stream:
                    stream.write(encoded)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def availability(entry):
        try:
            if (path_identity(entry["path"]) != entry["directory_identity"] or
                    path_identity(Path(entry["path"]) / "boards") != entry["boards_identity"]):
                return "replaced", "The board root or its boards directory was replaced. Reconnect it to review the new files."
        except (FileNotFoundError, NotADirectoryError):
            return "missing", "The board root was moved or deleted. Reconnect it or remove this registration."
        except ValueError:
            return "replaced", "The board root is redirected or no longer identifies its original directory."
        except OSError as exc:
            return "unreadable", f"The board root cannot be read: {exc}"
        return "available", ""

    def observe(self, workspace):
        """Observe names/metadata without modifying the saved registration or firmware."""
        value = self.read(workspace)
        entries = []
        for saved in value["registrations"]:
            entry = deepcopy(saved)
            entry["availability"], entry["availability_reason"] = self.availability(entry)
            entry["input_fingerprint"] = None
            if entry["availability"] == "available":
                paths, warnings = metadata_inputs([Path(entry["path"]) / "boards", Path(entry["path"]) / "soc"])
                observed, _, more = fingerprint(paths)
                if warnings or more:
                    entry.update(availability="unreadable", availability_reason="Custom board inputs could not be inspected completely.")
                else:
                    entry["input_fingerprint"] = observed
            entries.append(entry)
        signature = None if value["revision"] == 0 and not entries else digest({
            "revision": value["revision"], "entries": [{key: entry.get(key) for key in
                ("id", "path", "availability", "input_fingerprint")} for entry in entries]})
        return value, entries, signature

    def signature(self, workspace):
        return self.observe(workspace)[2]

    def analysis_options(self, workspace, options):
        value = deepcopy(options)
        _, entries, _ = self.observe(workspace)
        for key, directory in (("board_roots", "boards"), ("soc_roots", "soc")):
            roots = [entry["path"] for entry in entries if entry["availability"] == "available" and
                     (Path(entry["path"]) / directory).is_dir()]
            value[key] = list(dict.fromkeys([*value.get(key, []), *roots]))
        return value
