"""Bounded, read-only hardware model v2 metadata discovery.

This adapter reads the shapes used by Zephyr 3.7 and 4.4, including later
board/SoC extensions. It does not execute board CMake or validate a build.
"""

from concurrent.futures import CancelledError
import hashlib
import os
from pathlib import Path
import re

import yaml

from ...core.hardware_metadata import (MetadataError, UniqueHardwareLoader,
                                      board_qualifiers, parse_hardware_metadata,
                                      soc_entries as _soc_entries)
from ...core.board_revisions import parse_board_revisions as _revisions
from .metadata import read_regular


ADAPTER_VERSION = "hwm-v2-metadata/2"
MAX_DIRECTORIES = 16000
MAX_FILES = 12000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 24
MAX_RECORDS = 20000
MAX_QUALIFIERS = 2000
MAX_TARGETS = 40000
_IDENTIFIER = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\Z")
_SKIP = {".git", ".venv", "build", "__pycache__", "node_modules"}
# Retain the scaffold's existing local loader import while sharing parser semantics.
_UniqueLoader = UniqueHardwareLoader


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise CancelledError()


def _mapping(value, allowed, label):
    if not isinstance(value, dict):
        raise MetadataError(f"{label} must be a mapping.")
    unknown = set(value) - set(allowed)
    if unknown:
        raise MetadataError(f"Unsupported {label} fields: {', '.join(sorted(unknown))}.")
    return value


def _sequence(value, label):
    if not isinstance(value, list) or len(value) > MAX_RECORDS:
        raise MetadataError(f"{label} must be a bounded list.")
    return value


def _name(value, label="Name"):
    if not isinstance(value, str) or len(value) > 160 or not _IDENTIFIER.fullmatch(value):
        raise MetadataError(f"{label} must be a simple, nonempty metadata identifier.")
    return value


def _optional_text(value, label):
    if value is not None and (not isinstance(value, str) or len(value) > 1000):
        raise MetadataError(f"{label} must be text.")
    return value


def _read(path):
    try:
        text = read_regular(path, MAX_FILE_BYTES).decode("utf-8")
    except ValueError as exc:
        raise MetadataError(str(exc)) from exc
    return parse_hardware_metadata(text)


def _diagnostic(result, code, message, path=None, severity="warning"):
    item = {"severity": severity, "code": code, "message": message[:2000]}
    if path is not None:
        item["path"] = str(path)
    result["diagnostics"].append(item)


def _scan(roots, subdir, filename, result, cancel):
    """Include directories in inputs so added/deleted metadata invalidates a cache."""
    seen = set()
    directories = 0
    files = 0
    for entry in roots:
        _check_cancel(cancel)
        root = Path(entry["path"]).expanduser().resolve()
        if root in seen:
            continue
        seen.add(root)
        origin = str(entry.get("origin", root))
        start = root / subdir
        result["inputs"].append(str(start))
        if not start.exists():
            continue
        if start.is_symlink():
            _diagnostic(result, "symlink-root", f"Skipped symlinked {subdir} directory.", start)
            continue
        if not start.is_dir():
            _diagnostic(result, "invalid-root", f"Expected a {subdir} directory.", start)
            continue

        def walk_error(exc):
            _diagnostic(result, "unreadable-directory", str(exc), getattr(exc, "filename", start))

        for current, children, names in os.walk(start, followlinks=False, onerror=walk_error):
            _check_cancel(cancel)
            path = Path(current)
            directories += 1
            result["inputs"].append(str(path))
            if directories > MAX_DIRECTORIES:
                _diagnostic(result, "scan-limit", f"Stopped {subdir} scan at {MAX_DIRECTORIES} directories.", path)
                return
            depth = len(path.relative_to(start).parts)
            skipped = [name for name in children if (path / name).is_symlink()]
            for name in skipped:
                result["inputs"].append(str(path / name))
                _diagnostic(result, "symlink-directory", "Skipped symlinked hardware directory.", path / name)
            children[:] = sorted(name for name in children if name not in _SKIP
                                 and not name.startswith(".") and name not in skipped)
            if depth >= MAX_DEPTH and children:
                _diagnostic(result, "scan-depth", f"Stopped below depth {MAX_DEPTH}.", path)
                children[:] = []
            if filename in names:
                metadata = path / filename
                result["inputs"].append(str(metadata))
                files += 1
                if files > MAX_FILES:
                    _diagnostic(result, "scan-limit", f"Stopped after {MAX_FILES} metadata files.", path)
                    return
                if metadata.is_symlink():
                    _diagnostic(result, "symlink-metadata", "Skipped symlinked metadata file.", metadata)
                else:
                    yield metadata, origin
            elif subdir == "boards" and any(name.endswith("_defconfig") for name in names):
                # Older board roots have no board.yml. Never reinterpret those names as v2 targets.
                _diagnostic(result, "legacy-board", "Board directory has no board.yml; legacy hardware metadata is unsupported.", path)


def _provenance(record, path, origin):
    record["source_files"] = list(dict.fromkeys([*record.get("source_files", []), str(path)]))
    record["origins"] = list(dict.fromkeys([*record.get("origins", []), origin]))


def _read_socs(roots, result, cancel):
    records = {}
    extensions = []
    for path, origin in _scan(roots, "soc", "soc.yml", result, cancel):
        try:
            entries = _soc_entries(_read(path))
            for record in entries:
                _provenance(record, path, origin)
                record.update(evidence="declared", status="discovered")
                if record.pop("extension"):
                    extensions.append(record)
                elif record["name"] in records:
                    previous = records[record["name"]]
                    previous["status"] = "conflict"
                    _provenance(previous, path, origin)
                    _diagnostic(result, "duplicate-soc", f"SoC {record['name']} has multiple definitions.", path)
                else:
                    records[record["name"]] = record
        except (MetadataError, yaml.YAMLError, OSError, UnicodeError, RecursionError) as exc:
            _diagnostic(result, "unsupported-soc-metadata", f"Cannot interpret SoC metadata: {exc}", path)
        if len(records) > MAX_RECORDS:
            _diagnostic(result, "record-limit", "Stopped at the SoC record limit.", path)
            break
    for extension in extensions:
        _check_cancel(cancel)
        target = records.get(extension["name"])
        if target is None:
            _diagnostic(result, "missing-soc-extension-base", f"SoC extension base {extension['name']} was not found.", extension["source_files"][0])
            continue
        for path in extension["source_files"]:
            _provenance(target, path, extension["origins"][0])
        if set(extension["cpuclusters"]) & set(target["cpuclusters"]):
            target["status"] = "conflict"
            _diagnostic(result, "duplicate-cpucluster", f"SoC {target['name']} repeats CPU clusters in an extension.", extension["source_files"][0])
        target["cpuclusters"] = list(dict.fromkeys(target["cpuclusters"] + extension["cpuclusters"]))
    return records


def _board_record(data, path, origin, systems):
    _mapping(data, {"name", "full_name", "vendor", "revision", "socs"}, "Board")
    name = _name(data.get("name"), "Board")
    record = {"id": hashlib.sha256(f"{path}:{name}".encode()).hexdigest()[:20],
              "name": name, "full_name": _optional_text(data.get("full_name"), "Board full name") or name,
              "vendor": _optional_text(data.get("vendor"), "Vendor"), "socs": [], "qualifiers": [],
              "directories": [str(path.parent)], "evidence": "declared", "status": "discovered"}
    _provenance(record, path, origin)
    record["revisions"], record["default_revision"], record["revision_format"], record["revision_exact"] = _revisions(data.get("revision"))
    record["qualifiers"] = board_qualifiers(data, systems)
    for soc in data["socs"]:
        soc_name = soc["name"]
        record["socs"].append(soc_name)
        system = systems[soc_name]
        record["source_files"].extend(source for source in system["source_files"] if source not in record["source_files"])
    return record


def _unsupported_board(data, path, origin):
    """Keep identifiable unsupported boards visible, without synthesizing targets."""
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return None
    name = data["name"][:160]
    return {"id": hashlib.sha256(f"{path}:{name}".encode()).hexdigest()[:20],
            "name": name, "full_name": name, "vendor": None, "socs": [],
            "qualifiers": [], "revisions": [], "default_revision": None,
            "targets": [], "directories": [str(path.parent)], "origins": [origin],
            "source_files": [str(path)], "evidence": "declared", "status": "unsupported"}


def _board_entries(data):
    _mapping(data, {"board", "boards", "runners"}, "Board document")
    if ("board" in data) == ("boards" in data):
        raise MetadataError("Board document needs either board or boards.")
    if "runners" in data and not isinstance(data["runners"], dict):
        raise MetadataError("Runner metadata must be a mapping.")
    return [data["board"]] if "board" in data else _sequence(data["boards"], "Boards")


def _platforms(board, result, cancel):
    """Read declared architecture/type hints without using them to invent targets."""
    board["architectures"] = []
    board["platforms"] = []
    for directory in board["directories"]:
        try:
            candidates = sorted(Path(directory).glob("*.yaml"))
        except OSError as exc:
            _diagnostic(result, "unreadable-platform-directory", str(exc), directory)
            continue
        if len(candidates) > 1000:
            _diagnostic(result, "platform-limit", "Platform metadata listing exceeds the supported limit.", directory)
            candidates = candidates[:1000]
        for path in candidates:
            _check_cancel(cancel)
            result["inputs"].append(str(path))
            if path.is_symlink():
                _diagnostic(result, "symlink-metadata", "Skipped symlinked platform metadata.", path)
                continue
            try:
                value = _read(path)
                if not isinstance(value, dict):
                    continue
                target = value.get("identifier")
                if not isinstance(target, str) or len(target) > 1000:
                    continue
                if target != board["name"] and not target.startswith((board["name"] + "/", board["name"] + "@")):
                    continue
                architecture = value.get("arch")
                if architecture is not None:
                    _name(architecture, "Architecture")
                kind = _optional_text(value.get("type"), "Platform type")
                board["platforms"].append({"target": target, "architecture": architecture,
                                           "type": kind, "source_file": str(path), "evidence": "declared"})
                if architecture and architecture not in board["architectures"]:
                    board["architectures"].append(architecture)
                if str(path) not in board["source_files"]:
                    board["source_files"].append(str(path))
            except (MetadataError, yaml.YAMLError, OSError, UnicodeError, RecursionError) as exc:
                _diagnostic(result, "unsupported-platform-metadata", f"Cannot read declared platform hints: {exc}", path)


def _extend_boards(records, extensions, result, cancel):
    for extension, path, origin in extensions:
        _check_cancel(cancel)
        target_name = extension.get("extend")
        board = records.get(target_name) if isinstance(target_name, str) else None
        try:
            _mapping(extension, {"extend", "variants"}, "Board extension")
            name = _name(extension.get("extend"), "Extended board")
            if board is None:
                raise MetadataError(f"Extension base {name} was not found.")
            if board["status"] != "discovered":
                raise MetadataError(f"Extension base {name} is not supported unambiguously.")
            qualifiers = list(board["qualifiers"])
            for variant in _sequence(extension.get("variants", []), "Extension variants"):
                _mapping(variant, {"name", "qualifier"}, "Extension variant")
                name = _name(variant.get("name"), "Extension variant")
                base = variant.get("qualifier")
                if not isinstance(base, str) or base not in qualifiers:
                    raise MetadataError(f"Extension qualifier {base!r} was not found.")
                qualifier = f"{base}/{name}"
                if qualifier in qualifiers:
                    raise MetadataError(f"Extension qualifier {qualifier} is duplicated.")
                if len(qualifiers) >= MAX_QUALIFIERS:
                    raise MetadataError("Board extension exceeds the supported qualifier limit.")
                qualifiers.append(qualifier)
            board["qualifiers"] = qualifiers
            board["directories"] = list(dict.fromkeys(board["directories"] + [str(path.parent)]))
            _provenance(board, path, origin)
        except MetadataError as exc:
            if board is not None:
                board["status"] = "unsupported"
                _provenance(board, path, origin)
            _diagnostic(result, "unsupported-board-extension", str(exc), path)


def discover_hardware(board_roots, soc_roots, *, cancel=None, emit=None):
    """Discover declared board targets and chips without executing workspace code.

    Roots are ordered mappings with ``path`` and a human-readable ``origin``.
    All paths are root directories containing ``boards/`` or ``soc/``.
    """
    result = {"boards": [], "socs": [], "diagnostics": [], "inputs": [],
              "capabilities": {"adapter": ADAPTER_VERSION, "hardware_model": "v2",
                               "board_extensions": True, "soc_extensions": True,
                               "legacy_hardware": False, "build_validation": False,
                               "runner_validation": False}}
    _check_cancel(cancel)
    if emit:
        emit("Reading chip and CPU-cluster metadata.")
    systems = _read_socs(soc_roots, result, cancel)
    if emit:
        emit("Reading board targets, revisions and extensions.")
    records = {}
    extensions = []
    for path, origin in _scan(board_roots, "boards", "board.yml", result, cancel):
        try:
            entries = _board_entries(_read(path))
            for data in entries:
                if isinstance(data, dict) and "extend" in data:
                    extensions.append((data, path, origin))
                    continue
                try:
                    record = _board_record(data, path, origin, systems)
                except MetadataError as exc:
                    _diagnostic(result, "unsupported-board-metadata", str(exc), path)
                    record = _unsupported_board(data, path, origin)
                    if record is None:
                        continue
                if record["name"] in records:
                    previous = records[record["name"]]
                    previous["status"] = "conflict"
                    _provenance(previous, path, origin)
                    previous["directories"] = list(dict.fromkeys(previous["directories"] + record["directories"]))
                    _diagnostic(result, "duplicate-board", f"Board {record['name']} has multiple base definitions.", path)
                else:
                    records[record["name"]] = record
        except (MetadataError, yaml.YAMLError, OSError, UnicodeError, RecursionError) as exc:
            _diagnostic(result, "unsupported-board-metadata", f"Cannot interpret board metadata: {exc}", path)
        if len(records) > MAX_RECORDS:
            _diagnostic(result, "record-limit", "Stopped at the board record limit.", path)
            break
    _extend_boards(records, extensions, result, cancel)
    target_count = 0
    for board in records.values():
        _check_cancel(cancel)
        _platforms(board, result, cancel)
        board["targets"] = []
        if board["status"] == "discovered":
            expansion = len(board["qualifiers"]) * (1 + sum(bool(revision) for revision in board["revisions"]))
            if target_count + expansion > MAX_TARGETS:
                board["status"] = "unsupported"
                _diagnostic(result, "target-limit", "Target expansion exceeds the catalogue limit.", board["source_files"][0])
                continue
            target_count += expansion
            board["targets"] = [f"{board['name']}/{qualifier}" for qualifier in board["qualifiers"]]
            board["targets"].extend(f"{board['name']}@{revision}/{qualifier}"
                                    for revision in board["revisions"] if revision for qualifier in board["qualifiers"])
            if board.get("revision_format") == "custom":
                _diagnostic(result, "custom-revision", f"Board {board['name']} uses custom revision resolution; listed targets are declarations only.", board["source_files"][0], "info")
    result["boards"] = sorted(records.values(), key=lambda value: value["name"].casefold())
    result["socs"] = sorted(systems.values(), key=lambda value: value["name"].casefold())
    result["inputs"] = list(dict.fromkeys(result["inputs"]))
    result["capabilities"]["complete"] = not any(item["severity"] != "info" for item in result["diagnostics"])
    return result
