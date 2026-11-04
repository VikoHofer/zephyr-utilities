"""Ordered application fragment choices and bounded, read-only file discovery."""

from pathlib import Path, PurePosixPath
import unicodedata

from ...core.source_files import WorkspaceSourceReader


MAX_FRAGMENT_BYTES = 256 * 1024
MAX_FRAGMENT_TOTAL = 8 * 1024 * 1024
MAX_SELECTED_FILES = 32
EXCLUDED_DIRS = frozenset({"build", "out", "output", "dist", "target", "node_modules", "venv",
                           "__pycache__", "CMakeFiles", "generated", "cache", "tools"})
EXCLUDED_PREFIXES = (".", "build-", "build_", "cmake-build-")
BUILD_MARKERS = ("CMakeCache.txt", "build.ninja")
KINDS = {"kconfig": (".conf", "CONF_FILE", "EXTRA_CONF_FILE"),
         "overlays": (".overlay", "DTC_OVERLAY_FILE", "EXTRA_DTC_OVERLAY_FILE")}


def automatic_fragments():
    return {"schema_version": 1, **{kind: {"mode": "auto", "files": []} for kind in KINDS}}


def _safe_characters(value):
    return not any(character.isspace() or unicodedata.category(character).startswith("C") or
                   character in ';$\\@"[]' for character in value)


def fragment_path(value, kind):
    if not isinstance(value, str) or not value or len(value) > 1000 or not _safe_characters(value):
        raise ValueError("Fragment paths must not contain whitespace, control characters or CMake expansions/list separators.")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {".", ".."} for part in path.parts):
        raise ValueError("Select a normalized application-relative fragment path without parent traversal.")
    if path.suffix != KINDS[kind][0]:
        raise ValueError(f"Select a {KINDS[kind][0]} file for {kind}.")
    if any(part.startswith(EXCLUDED_PREFIXES) or part in EXCLUDED_DIRS for part in path.parts[:-1]) or path.name.startswith("."):
        raise ValueError("Select fragments outside hidden, build, generated and tool directories.")
    return value


def normalize_fragments(value):
    if (not isinstance(value, dict) or set(value) != {"schema_version", *KINDS} or
            type(value.get("schema_version")) is not int or value["schema_version"] != 1):
        raise ValueError("Unsupported fragment selection schema. Existing data was preserved.")
    result = {"schema_version": 1}
    for kind in KINDS:
        choice = value[kind]
        if (not isinstance(choice, dict) or set(choice) != {"mode", "files"} or
                choice.get("mode") not in ("auto", "append", "replace") or not isinstance(choice.get("files"), list)):
            raise ValueError("Choose automatic, append or replace with an ordered fragment file list.")
        files = choice["files"]
        if len(files) > MAX_SELECTED_FILES or (choice["mode"] == "auto" and files) or (choice["mode"] != "auto" and not files):
            raise ValueError("Automatic selection needs no files; append and replace need 1 to 32 files. Empty replacement is unsupported.")
        selected = [fragment_path(path, kind) for path in files]
        if len(set(selected)) != len(selected):
            raise ValueError("A fragment may appear only once in each ordered list.")
        result[kind] = {"mode": choice["mode"], "files": selected}
    return result


def profile_fragments(profile):
    version = profile.get("schema_version")
    if "schema_version" in profile and (type(version) is not int or version not in (1, 2)):
        raise ValueError("Unsupported build profile schema for fragment selection. Existing data was preserved.")
    if version == 2:
        return normalize_fragments(profile.get("fragments"))
    if version not in (None, 1) or "fragments" in profile:
        raise ValueError("Unsupported build profile schema for fragment selection. Existing data was preserved.")
    return automatic_fragments()


def fragment_arguments(profile, source):
    """Render validated choices only; command recipe generation never reads files."""
    arguments = []
    for kind, choice in profile_fragments(profile).items():
        if kind == "schema_version" or choice["mode"] == "auto":
            continue
        files = [str(Path(source) / path) for path in choice["files"]]
        if any(not _safe_characters(path) for path in files):
            raise ValueError("The application path cannot be represented safely in a CMake fragment list.")
        variable = KINDS[kind][2 if choice["mode"] == "append" else 1]
        arguments.append(f"-D{variable}=" + ";".join(files))
    return arguments


def _read_fragment(reader, path):
    parent = path.parent
    while True:
        reader.directory_identity(parent)
        if any((parent / marker).exists() or (parent / marker).is_symlink() for marker in BUILD_MARKERS):
            raise ValueError("Fragments inside a generated build directory cannot be selected.")
        if parent == reader.workspace:
            break
        parent = parent.parent
    result = reader.read_text(path, limit=MAX_FRAGMENT_BYTES)
    if reader.bytes_read > MAX_FRAGMENT_TOTAL:
        raise ValueError("Fragment inputs exceed the 8 MiB total read limit.")
    return result


def validate_fragment_files(source, selection):
    selection = normalize_fragments(selection)
    reader = WorkspaceSourceReader(source)
    for kind in KINDS:
        for relative in selection[kind]["files"]:
            _read_fragment(reader, reader.path(relative))
    return selection


def discover_fragment_files(source):
    reader = WorkspaceSourceReader(source)
    paths = reader.list_files(source, {".conf", ".overlay"}, max_files=2000, max_dirs=1000,
                              exclude_dirs=EXCLUDED_DIRS, exclude_prefixes=EXCLUDED_PREFIXES,
                              stop_names=BUILD_MARKERS, max_depth=16)
    files = {kind: [] for kind in KINDS}
    diagnostics = []
    for path in paths:
        kind = "kconfig" if path.suffix == ".conf" else "overlays"
        relative = path.relative_to(reader.workspace).as_posix()
        try:
            fragment_path(relative, kind)
            _read_fragment(reader, path)
            files[kind].append(relative)
        except (OSError, ValueError) as exc:
            diagnostics.append(f"Skipped {relative}: {exc}")
        if reader.bytes_read > MAX_FRAGMENT_TOTAL:
            raise ValueError("Fragment discovery exceeds the 8 MiB read limit; narrow the application inputs.")
    return {"files": {kind: sorted(paths) for kind, paths in files.items()}, "diagnostics": diagnostics}
