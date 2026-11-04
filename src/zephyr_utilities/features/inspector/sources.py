"""Current source navigation tied to verified, read-only build observations."""

from copy import deepcopy
from bisect import bisect_right
import json
from pathlib import Path
import re
from threading import RLock
import time
import uuid

from ...core.source_files import MAX_SOURCE_BYTES, WorkspaceSourceReader


MAX_FILES = 1000
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_INCLUDES = 128
SOURCE_SUFFIXES = frozenset({".dts", ".dtsi", ".overlay", ".h", ".hpp"})
OUTPUT_KINDS = frozenset({"resolved-dts", "preprocessed-dts", "devicetree-header"})
LINE_MARKER = re.compile(r'^\s*#(?:line)?\s+\d+\s+("(?:[^"\\]|\\.)*")', re.MULTILINE)
INCLUDE = re.compile(r'^\s*#\s*include\s*(?:"([^"\n]+)"|<([^>\n]+)>)|/include/\s*"([^"\n]+)"', re.MULTILINE)
COMMENTS_OR_STRING = re.compile(r'//[^\n]*|/\*[\s\S]*?(?:\*/|\Z)|"(?:\\.|[^"\\])*"')
STRING = re.compile(r'"(?:\\.|[^"\\])*"')


def _supported(path, kind):
    if kind == "source":
        return path.suffix in SOURCE_SUFFIXES
    if kind == "configuration":
        return path.suffix in {".conf", ".defconfig", ".config"} or path.name.endswith("_defconfig") or path.name == ".config" or path.name.startswith("Kconfig")
    return kind == "binding" and path.suffix in {".yaml", ".yml"}


class CurrentSourceScope:
    """A bounded catalog of current source identities, never a build-source snapshot."""

    def __init__(self, workspace, base_dir, guard, *, reader=None):
        self.workspace, self.base_dir, self.guard = str(workspace), base_dir, guard
        self.catalog_id = uuid.uuid4().hex
        self.reader = reader if reader is not None else WorkspaceSourceReader(self.workspace)
        self.include_dirs, self.binding_dirs = [], []
        self.binding_scan_options = {}
        self.locations_description = "recorded"
        self.files, self.diagnostics = {}, []
        self._paths, self._hashes, self._bytes = {}, {}, 0
        self.lock = RLock()

    def add_file(self, path, kind, origin, *, retain_missing=True):
        if kind not in {"source", "configuration", "binding"}:
            raise ValueError("Choose a supported source file kind.")
        raw = str(path)
        try:
            candidate = self.reader.path(path, self.base_dir)
            key = str(candidate)
            reason = None
        except (ValueError, TypeError) as exc:
            candidate, key, reason = Path(raw), raw, str(exc)
        with self.lock:
            if key in self._paths:
                item = self.files[self._paths[key]]
                if origin not in item["origins"]:
                    item["origins"].append(origin)
                return deepcopy(item)
            if len(self.files) >= MAX_FILES:
                raise ValueError("The source catalog exceeded its 1,000-file limit.")
            identifier = uuid.uuid4().hex
            item = {"id": identifier, "path": key, "name": candidate.name or raw, "kind": kind,
                    "origins": [origin], "availability": "blocked"}
            if reason:
                item["reason"] = reason
            elif not _supported(candidate, kind):
                item.update(availability="unsupported", reason="This file type is not supported by source navigation.")
            else:
                if self._bytes >= MAX_TOTAL_BYTES:
                    raise ValueError("The source catalog exceeded its 16 MiB text limit.")
                before_read = self.reader.bytes_read
                try:
                    document = self.reader.read_text(candidate, limit=min(MAX_SOURCE_BYTES, MAX_TOTAL_BYTES - self._bytes))
                    size = len(document["text"].encode("utf-8"))
                except FileNotFoundError:
                    item.update(availability="missing", reason="This source file is missing from the current workspace.")
                    if not retain_missing:
                        return item
                except (OSError, ValueError) as exc:
                    item["reason"] = str(exc)
                else:
                    self._hashes[identifier] = document["sha256"]
                    item.update(availability="available", sha256=document["sha256"], size=size)
                finally:
                    self._bytes += self.reader.bytes_read - before_read
            self._paths[key] = identifier
            self.files[identifier] = item
            return deepcopy(item)

    def read(self, file_id):
        with self.lock:
            item = deepcopy(self.files.get(file_id)) if isinstance(file_id, str) else None
            expected = self._hashes.get(file_id) if isinstance(file_id, str) else None
        if item is None:
            raise ValueError("Choose a file from this source catalog.")
        if item["availability"] != "available":
            raise ValueError(item.get("reason", "This source is unavailable."))
        self.guard.verify()
        try:
            document = self.reader.read_text(item["path"])
        except (OSError, ValueError) as exc:
            raise ValueError("This source changed or became unavailable. Refresh source navigation.") from exc
        if document["sha256"] != expected:
            raise ValueError("This source changed since the catalog was opened. Refresh source navigation.")
        self.guard.verify()
        return document

    def selection(self):
        return {"workspace": self.workspace}

    def payload(self):
        with self.lock:
            return {**self.selection(), "catalog_id": self.catalog_id,
                    "files": deepcopy(list(self.files.values())),
                    "diagnostics": list(self.diagnostics), "source_freshness": "unknown"}


class SourceScope(CurrentSourceScope):
    """Current sources selected by a verified configured-image observation."""

    def __init__(self, builds, guard):
        super().__init__(guard.workspace, guard.build_dir, guard)
        self.builds, self.inspection_id, self.domain = builds, guard.inspection_id, guard.domain
        self.context, self.build_dir = guard.context, guard.build_dir
        self.include_dirs = self.context.get("devicetree", {}).get("include_dirs", [])
        self.binding_dirs = self.context.get("devicetree", {}).get("binding_dirs", [])

    def artifact_text(self, kind):
        if kind not in OUTPUT_KINDS:
            raise ValueError("Choose a supported Device Tree output.")
        self.guard.verify()
        value = self.builds.read_text(self.workspace, self.inspection_id, self.domain, kind)
        self.guard.verify()
        return value

    def selection(self):
        return {"workspace": self.workspace, "inspection_id": self.inspection_id, "domain": self.domain}


class SourceNavigation:
    def __init__(self, builds, *, max_catalogs=16, ttl=1800):
        self.builds, self.max_catalogs, self.ttl = builds, max_catalogs, ttl
        self.catalogs = {}
        self.lock = RLock()

    def _expire(self):
        now = time.monotonic()
        self.catalogs = {key: entry for key, entry in self.catalogs.items() if entry["expires"] > now}

    def create(self, workspace, inspection_id, domain):
        guard = self.builds.select_artifact(workspace, inspection_id, domain, "resolved-dts",
                                           related_kinds=("preprocessed-dts", "devicetree-header"))
        scope = SourceScope(self.builds, guard)
        contexts = (("devicetree", "files", "Recorded Device Tree input", "source"),
                    ("devicetree", "user_files", "Explicit overlay", "source"),
                    ("devicetree", "extra_user_files", "Extra overlay", "source"),
                    ("configuration", "files", "Recorded configuration input", "configuration"),
                    ("configuration", "user_files", "Explicit configuration input", "configuration"))
        try:
            for category, key, origin, kind in contexts:
                for path in scope.context.get(category, {}).get(key, []):
                    scope.add_file(path, kind, origin)
            if any(path.endswith("/zephyr.dts.pre") and digest is not None for path, digest in guard.artifact_hashes.items()):
                preprocessed = scope.artifact_text("preprocessed-dts")["text"]
                for match in LINE_MARKER.finditer(preprocessed):
                    try:
                        path = json.loads(match[1])
                    except ValueError:
                        continue
                    if path.startswith("<") and path.endswith(">"):
                        continue
                    if Path(path).suffix in SOURCE_SUFFIXES:
                        scope.add_file(path, "source", "Recorded preprocessor input")
        except ValueError as exc:
            if "catalog exceeded" not in str(exc):
                raise
            scope.diagnostics.append(str(exc))
        guard.verify()
        with self.lock:
            self._expire()
            while len(self.catalogs) >= self.max_catalogs:
                self.catalogs.pop(next(iter(self.catalogs)))
            self.catalogs[scope.catalog_id] = {"scope": scope, "expires": time.monotonic() + self.ttl}
        return scope.payload()

    def get(self, workspace, inspection_id, domain, catalog_id):
        if not isinstance(catalog_id, str) or not re.fullmatch(r"[a-f0-9]{32}", catalog_id):
            raise ValueError("Choose a valid source catalog.")
        with self.lock:
            self._expire()
            entry = self.catalogs.get(catalog_id)
            if entry is None:
                raise ValueError("This source catalog expired. Refresh source navigation.")
            scope = entry["scope"]
        if (str(workspace), inspection_id, domain) != (scope.workspace, scope.inspection_id, scope.domain):
            raise ValueError("This source catalog belongs to another workspace or image.")
        # A detached read guard must not extend a browser inspection's session lifetime.
        self.builds.select_artifact(workspace, inspection_id, domain, "resolved-dts",
                                   related_kinds=("preprocessed-dts", "devicetree-header"))
        scope.guard.verify()
        return scope

    @staticmethod
    def document(scope, file_id):
        document = scope.read(file_id)
        item = deepcopy(scope.files[file_id])
        includes, diagnostics, seen = [], [], set()
        text = COMMENTS_OR_STRING.sub(lambda match: re.sub(r"[^\n]", " ", match[0]) if match[0].startswith("/") else match[0], document["text"])
        strings = [(match.start(), match.end()) for match in STRING.finditer(text)]
        starts = [start for start, _ in strings]
        if re.search(r'^\s*#\s*include\s+(?!["<\s])\S', text, re.MULTILINE):
            diagnostics.append("Macro-based include references are not expanded.")
        checks = 0
        exhausted = False
        for match in INCLUDE.finditer(text):
            index = bisect_right(starts, match.start()) - 1
            if index >= 0 and strings[index][0] <= match.start() < strings[index][1]:
                continue
            name = next(value for value in match.groups() if value is not None)
            if name in seen:
                continue
            seen.add(name)
            if len(includes) >= MAX_INCLUDES:
                diagnostics.append("Source include navigation stopped at its 128-reference limit.")
                break
            candidates, failures = [], []
            roots = [str(Path(document["path"]).parent), *scope.include_dirs]
            paths = set()
            for root in roots:
                checks += 1
                if checks > 512:
                    diagnostics.append("Source include navigation stopped at its lookup limit.")
                    exhausted = True
                    break
                try:
                    parent = scope.reader.path(root, scope.base_dir)
                    path = scope.reader.path(name, parent)
                except (ValueError, OSError) as exc:
                    failures.append(str(exc))
                    continue
                if str(path) in paths:
                    continue
                paths.add(str(path))
                try:
                    ref = scope.add_file(path, "source", f"Include from {item['name']}", retain_missing=False)
                except ValueError as exc:
                    diagnostics.append(str(exc))
                    exhausted = True
                    break
                if ref["availability"] != "missing":
                    candidates.append(ref)
            note = "Multiple current files match; no preprocessor selection is inferred." if len(candidates) > 1 else "Literal reference in the current source; preprocessor conditions and macros are not evaluated."
            if not candidates:
                note = failures[0] if failures else f"No supported current file was found in the {scope.locations_description} include locations."
            includes.append({"name": name, "files": candidates, **({"note": note} if note else {})})
            if exhausted:
                break
        scope.guard.verify()
        return {"file": item, "document": document, "includes": includes, "properties": [], "diagnostics": diagnostics}
