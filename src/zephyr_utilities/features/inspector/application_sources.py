"""Browse current application sources without configuring or selecting build outputs."""

from pathlib import Path
import re
from threading import RLock
import time

from ...core.source_files import WorkspaceSourceReader
from .sources import CurrentSourceScope, SourceNavigation


EXCLUDED_DIRECTORIES = frozenset({".git", ".hg", ".svn", ".west", ".venv", "venv", "__pycache__", "node_modules", "build"})
EXCLUDED_PREFIXES = ("build-", "build_")
BUILD_MARKERS = frozenset({"CMakeCache.txt", "build.ninja"})
CATALOG_SUFFIXES = frozenset({".dts", ".dtsi", ".overlay", ".conf", ".defconfig", ".config", ".yaml", ".yml"})
MAX_SCAN_FILES = 1000
MAX_SCAN_DIRS = 1000
MAX_SCAN_DEPTH = 16


class ApplicationDirectoryGuard:
    """Pin the source directory and its inert application marker for this catalogue."""

    def __init__(self, reader, application):
        self.reader, self.application = reader, application
        self.identity = reader.directory_identity(application)
        self.marker = reader.read_text(application / "CMakeLists.txt")
        self.verify()

    def verify(self):
        try:
            if self.reader.directory_identity(self.application) != self.identity:
                raise ValueError("The application directory was replaced.")
            marker = self.reader.read_text(self.application / "CMakeLists.txt")
            if marker["sha256"] != self.marker["sha256"]:
                raise ValueError("The application's CMakeLists.txt changed.")
            for name in BUILD_MARKERS:
                try:
                    self.reader.read_text(self.application / name, limit=1)
                except FileNotFoundError:
                    continue
                except (OSError, ValueError):
                    pass
                raise ValueError("Choose an application source directory, not a configured build directory.")
        except (OSError, ValueError) as exc:
            raise ValueError(f"The application source directory changed or is unavailable. Open its sources again. {exc}") from exc


class ApplicationSourceScope(CurrentSourceScope):
    def __init__(self, workspace, application_path):
        reader = WorkspaceSourceReader(workspace)
        application = reader.path(application_path)
        if application == reader.workspace:
            raise ValueError("Choose an application directory inside the workspace, not the workspace itself.")
        try:
            guard = ApplicationDirectoryGuard(reader, application)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Cannot open application sources. Choose an existing source directory with a readable CMakeLists.txt. {exc}") from exc
        super().__init__(workspace, application, guard, reader=reader)
        self.application_path = str(application)
        self.locations_description = "application and conventional workspace"
        self.include_dirs = self._directories((application, application / "include", reader.workspace / "zephyr/include",
                                                reader.workspace / "zephyr/dts"))
        self.binding_dirs = self._directories((application / "dts/bindings", reader.workspace / "zephyr/dts/bindings"))
        self.binding_scan_options = {"exclude_dirs": EXCLUDED_DIRECTORIES, "exclude_prefixes": EXCLUDED_PREFIXES,
                                     "stop_names": BUILD_MARKERS}
        self.diagnostics = [
            "These are current application source candidates; no configured inputs, effective target or resolved Device Tree are inferred.",
            "Literal includes use the current file directory, application, application/include, and existing conventional workspace/zephyr/include and workspace/zephyr/dts directories. Binding includes use application/dts/bindings and existing workspace/zephyr/dts/bindings. These locations do not identify a selected Zephyr checkout; absent or unsafe optional directories are skipped.",
            "Discovery excludes .git, .hg, .svn, .west, .venv, venv, __pycache__, node_modules, build, build-* and build_* directories, and directories containing CMakeCache.txt or build.ninja. Symbolic links are not followed.",
            "Discovery is limited to 1,000 candidate files, 1,000 directories, 2,000 entries and 16 directory levels. The catalogue admits at most 1,000 files and 16 MiB of text; each file is limited to 2 MiB. Headers are offered only through literal includes.",
        ]

    def _directories(self, paths):
        result = []
        for path in paths:
            try:
                self.reader.directory_identity(path)
            except (OSError, ValueError):
                continue
            result.append(str(path))
        return result

    def selection(self):
        return {"workspace": self.workspace, "application_path": self.application_path, "mode": "application"}


class ApplicationSourceNavigation:
    def __init__(self, *, max_catalogs=16, ttl=1800):
        self.max_catalogs, self.ttl = max_catalogs, ttl
        self.catalogs, self.lock = {}, RLock()

    def _expire(self):
        current = time.monotonic()
        self.catalogs = {key: entry for key, entry in self.catalogs.items() if entry["expires"] > current}

    def create(self, workspace, application_path):
        scope = ApplicationSourceScope(workspace, application_path)
        paths = scope.reader.list_files(scope.base_dir, CATALOG_SUFFIXES, names={".config"}, prefixes=("Kconfig",),
                                         endings=("_defconfig",), max_files=MAX_SCAN_FILES, max_dirs=MAX_SCAN_DIRS,
                                         max_depth=MAX_SCAN_DEPTH, exclude_dirs=EXCLUDED_DIRECTORIES,
                                         exclude_prefixes=EXCLUDED_PREFIXES, stop_names=BUILD_MARKERS)
        bindings = Path(scope.application_path) / "dts/bindings"
        for path in paths:
            if path.suffix in {".yaml", ".yml"}:
                if not path.is_relative_to(bindings):
                    continue
                kind = "binding"
            elif path.suffix in {".dts", ".dtsi", ".overlay"}:
                kind = "source"
            else:
                kind = "configuration"
            try:
                scope.add_file(path, kind, "Current application source candidate")
            except ValueError as exc:
                if "catalog exceeded" not in str(exc):
                    raise
                scope.diagnostics.append(str(exc))
                break
        scope.guard.verify()
        with self.lock:
            self._expire()
            while len(self.catalogs) >= self.max_catalogs:
                self.catalogs.pop(next(iter(self.catalogs)))
            self.catalogs[scope.catalog_id] = {"scope": scope, "expires": time.monotonic() + self.ttl}
        return scope.payload()

    def get(self, workspace, application_path, catalog_id):
        if not isinstance(catalog_id, str) or not re.fullmatch(r"[a-f0-9]{32}", catalog_id):
            raise ValueError("Choose a valid application source catalog.")
        with self.lock:
            self._expire()
            entry = self.catalogs.get(catalog_id)
            if entry is None:
                raise ValueError("This application source catalog expired. Refresh source navigation.")
            scope = entry["scope"]
        application = scope.reader.path(application_path)
        if (str(workspace), str(application)) != (scope.workspace, scope.application_path):
            raise ValueError("This source catalog belongs to another workspace or application.")
        scope.guard.verify()
        return scope

    document = staticmethod(SourceNavigation.document)
