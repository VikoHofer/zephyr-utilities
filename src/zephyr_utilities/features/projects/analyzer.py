"""Versioned environment snapshots and refreshable local hardware catalogues."""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from .hardware import ADAPTER_VERSION as HARDWARE_ADAPTER_VERSION, discover_hardware
from .metadata import read_regular
from .probing import check_cancel
from .sdk import discover_sdks
from .workspace import diagnostic, inspect_workspace


SCHEMA_VERSION = 1
ADAPTER_VERSION = "metadata-v1"
MAX_INPUTS = 30000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def metadata_inputs(roots, *, cancel=None):
    """Track names as well as contents, so added/untracked metadata invalidates a cache."""
    paths, warnings = set(), []
    for root in sorted(set(map(str, roots))):
        base = Path(root)
        paths.add(str(base))
        if base.is_symlink():
            warnings.append(diagnostic("fingerprint-symlink", "Skipped symlinked hardware root.", base))
            continue
        if not base.is_dir():
            continue
        for current, directories, files in os.walk(base, followlinks=False):
            check_cancel(cancel)
            path = Path(current)
            paths.add(str(path))
            directories[:] = sorted(name for name in directories if not name.startswith(".") and
                                     not (path / name).is_symlink())
            if len(path.relative_to(base).parts) >= 24:
                if directories:
                    warnings.append(diagnostic("fingerprint-depth", "Metadata traversal stopped at twenty-four levels.", path))
                directories[:] = []
            for name in files:
                if name.endswith((".yml", ".yaml", ".cmake")) or name == "Kconfig":
                    paths.add(str(path / name))
            if len(paths) >= MAX_INPUTS:
                warnings.append(diagnostic("fingerprint-limit", "Metadata inspection reached the 30,000-input limit.", base))
                return sorted(paths)[:MAX_INPUTS], warnings
    return sorted(paths), warnings


def fingerprint(paths, *, cancel=None, started_ns=None):
    records, warnings = [], []
    changed = False
    names = sorted(set(map(str, paths)))
    if len(names) > MAX_INPUTS:
        warnings.append(diagnostic("fingerprint-limit", "Only the first 30,000 inputs could be fingerprinted."))
    for name in names[:MAX_INPUTS]:
        check_cancel(cancel)
        path = Path(name)
        try:
            stat = path.lstat()
            if started_ns is not None and max(stat.st_mtime_ns, stat.st_ctime_ns) >= started_ns:
                changed = True
            if path.is_symlink():
                # Discovery adapters decide whether a target is in scope; never read it here.
                identity = ["link", os.readlink(path), stat.st_mtime_ns, stat.st_ctime_ns]
            elif path.is_dir():
                children = []
                with os.scandir(path) as entries:
                    for entry in entries:
                        if len(children) >= MAX_INPUTS:
                            warnings.append(diagnostic("fingerprint-limit", "Directory contents exceed the input limit.", path))
                            break
                        children.append(entry.name)
                identity = ["directory", sorted(children)]
            elif path.is_file():
                identity = ["file", stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]
                if stat.st_size <= 2 * 1024 * 1024:
                    identity.append(hashlib.sha256(read_regular(path, 2 * 1024 * 1024)).hexdigest())
            else:
                identity = ["unsupported-file-type"]
        except FileNotFoundError:
            identity = ["missing"]
        except (OSError, RuntimeError, ValueError) as exc:
            identity = ["unreadable", str(exc)]
            warnings.append(diagnostic("fingerprint-unreadable", exc, path))
        records.append([name, identity])
    return digest(records), changed, warnings


def catalogue_changes(previous, current):
    changes = {"added": [], "removed": [], "changed": []}
    if previous is None:
        return changes
    for kind in ("boards", "socs", "sdks"):
        def entries(snapshot):
            return {str(item.get("id", item.get("name"))): item for item in snapshot.get(kind, [])}
        before, after = entries(previous), entries(current)
        changes["added"].extend(f"{kind}: {key}" for key in sorted(after.keys() - before.keys()))
        changes["removed"].extend(f"{kind}: {key}" for key in sorted(before.keys() - after.keys()))
        changes["changed"].extend(f"{kind}: {key}" for key in sorted(before.keys() & after.keys()) if before[key] != after[key])
    return changes


def analyze_environment(workspace, options, *, environ=None, emit=None, cancel=None, previous=None):
    started_ns = time.time_ns()
    emit = emit or (lambda message: None)
    info = inspect_workspace(workspace, options, environ=environ, cancel=cancel, emit=emit)
    check_cancel(cancel)
    emit("Inspecting installed SDK metadata and compiler components…")
    sdk = discover_sdks(Path(workspace), Path(info["zephyr"]["path"]) if info["zephyr"]["path"] else None,
                        sdk_paths=options.get("sdk_paths", []), environ=info["environment"], cancel=cancel)
    inputs = info.pop("inputs") + sdk.get("inputs", [])
    environment = info.pop("environment")
    info.pop("python")
    roots = [Path(root["path"]) / directory for kind, directory in (("boards", "boards"), ("socs", "soc"))
             for root in info["roots"][kind]]
    tree_inputs, tree_warnings = metadata_inputs(roots, cancel=cancel)
    inputs += tree_inputs
    before, changed_during_setup, warnings = fingerprint(inputs, cancel=cancel, started_ns=started_ns)
    selections = {key: environment.get(key) for key in ("ZEPHYR_BASE", "ZEPHYR_TOOLCHAIN_VARIANT", "ZEPHYR_SDK_INSTALL_DIR",
                                                        "ZEPHYR_MODULES", "EXTRA_ZEPHYR_MODULES", "BOARD_ROOT", "SOC_ROOT")}
    basis = {"schema": SCHEMA_VERSION, "adapter": ADAPTER_VERSION,
             "hardware_adapter": HARDWARE_ADAPTER_VERSION, "context": info,
             "options": {key: value for key, value in options.items() if key != "force"},
             "sdk": {key: value for key, value in sdk.items() if key != "inputs"}, "selection": selections,
             "inputs": before}
    cache_key = digest(basis)
    if (previous and not options.get("force") and not changed_during_setup and not tree_warnings and not warnings and
            previous.get("cache_key") == cache_key and previous.get("status") != "stale"):
        emit("Input fingerprints are unchanged; reusing the saved hardware catalogue.")
        result = deepcopy(previous)
        result["cache"] = {"hit": True}
        result["checked_at"] = datetime.now(timezone.utc).isoformat()
        return result
    emit("Reading board and chip metadata…")
    hardware = discover_hardware(info["roots"]["boards"], info["roots"]["socs"], cancel=cancel, emit=emit)
    # Repeat enumeration as well as hashing to detect additions/deletions during discovery.
    after_tree, after_warnings = metadata_inputs(roots, cancel=cancel)
    tree_set = set(tree_inputs)
    after, changed_after, more_warnings = fingerprint(
        [p for p in inputs if p not in tree_set] + after_tree,
        cancel=cancel, started_ns=started_ns)
    stale = before != after or changed_during_setup or changed_after
    diagnostics = info.pop("diagnostics") + sdk.get("diagnostics", []) + hardware.get("diagnostics", [])
    diagnostics += tree_warnings + warnings + after_warnings + more_warnings
    if stale:
        diagnostics.append(diagnostic("inputs-changed", "Inputs changed during analysis. Rescan before using this catalogue."))
    check_cancel(cancel)
    snapshot = {
        **info, "schema_version": SCHEMA_VERSION, "adapter_version": ADAPTER_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(), "request": basis["options"],
        "boards": hardware.get("boards", []), "socs": hardware.get("socs", []), "sdks": sdk.get("sdks", []),
        "selection": sdk.get("selection", {}), "diagnostics": diagnostics,
        "capabilities": {**hardware.get("capabilities", {}), "analysis_only": True,
                         "configure": False, "create_project": False, "hardware_validation": False},
        "fingerprint": digest({"context": cache_key, "inputs": after}), "cache_key": cache_key,
        "status": "stale" if stale else "partial" if any(d.get("severity") in {"warning", "error"} for d in diagnostics) else "complete",
        "cache": {"hit": False}, "freshness": "checked-at-scan",
    }
    # Different observations can share fingerprints (e.g. a stale scan followed by a settled one).
    snapshot["id"] = uuid.uuid4().hex
    snapshot["changes"] = catalogue_changes(previous, snapshot)
    emit(f"Discovered {len(snapshot['boards'])} boards, {len(snapshot['socs'])} chips and {len(snapshot['sdks'])} SDK installations.")
    return snapshot
