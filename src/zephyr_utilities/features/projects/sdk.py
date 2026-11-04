"""Bounded, offline inventory of installed Zephyr SDK files; never loads SDK code."""

from concurrent.futures import CancelledError
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .metadata import read_regular


SYSTEM_SEARCH_ROOTS = (Path("/usr"), Path("/usr/local"), Path("/opt"))
MAX_CHILDREN = 1024
MAX_CANDIDATES = 128
MAX_METADATA_BYTES = 64 * 1024
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?\Z")
TOOLCHAIN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*\Z")


class _Inventory:
    def __init__(self, cancel):
        self.cancel = cancel
        self.inputs = set()
        self.diagnostics = []

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise CancelledError()

    def diagnostic(self, code, message, path=None, severity="warning"):
        item = {"severity": severity, "code": code, "message": message}
        if path is not None:
            item["path"] = str(path)
        if item not in self.diagnostics:
            self.diagnostics.append(item)

    def track(self, path):
        self.check()
        self.inputs.add(str(path))

    def children(self, path):
        self.track(path)
        try:
            if not path.is_dir():
                return []
            result = []
            with os.scandir(path) as entries:
                for entry in entries:
                    self.check()
                    if len(result) >= MAX_CHILDREN:
                        self.diagnostic("sdk_scan_limit", "SDK directory listing was truncated.", path)
                        break
                    result.append(Path(entry.path))
            return sorted(result, key=lambda item: item.name)
        except (OSError, RuntimeError) as exc:
            self.diagnostic("sdk_unreadable", f"Cannot inspect SDK directory: {exc}", path)
            return []

    def text(self, path):
        self.track(path)
        try:
            if not path.exists():
                return None
            content = read_regular(path, MAX_METADATA_BYTES)
            return content.decode("utf-8").strip()
        except (OSError, ValueError, RuntimeError) as exc:
            code = "sdk_metadata_limit" if "exceeds the" in str(exc) else "sdk_metadata_invalid"
            self.diagnostic(code, f"Cannot read SDK metadata: {exc}", path)
            return None


def _absolute(value, workspace, home):
    value = os.fspath(value)
    if value == "~" or value.startswith("~/"):
        path = home / value[2:] if value != "~" else home
    else:
        path = Path(value).expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def _contained(path, root):
    try:
        path.resolve().relative_to(root)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _signature(path, inventory):
    """Track replacement/retargeting without reading multi-megabyte executable binaries."""
    inventory.track(path)
    try:
        value = path.stat()
        link = os.readlink(path) if path.is_symlink() else None
        return [str(path), value.st_size, value.st_mtime_ns, value.st_ctime_ns,
                value.st_ino, value.st_mode, link]
    except (OSError, RuntimeError):
        return [str(path), "unavailable"]


def _executable(path, root, inventory):
    inventory.track(path)
    if not _contained(path, root):
        inventory.diagnostic("sdk_external_link", "SDK component points outside the installation; skipped.", path)
        return False
    try:
        info = path.stat()
        return stat.S_ISREG(info.st_mode) and (path.suffix.lower() == ".exe" or os.access(path, os.X_OK))
    except (OSError, RuntimeError):
        return False


def _host_binaries(root, inventory):
    """Inspect known SDK layouts only, without recursively walking a toolchain sysroot."""
    directories = [root / "hosttools" / "bin", root / "hosttools" / "usr" / "bin"]
    for base in (root / "sysroots", root / "hosttools" / "sysroots"):
        if not _contained(base, root):
            inventory.diagnostic("sdk_external_link", "SDK host tools point outside the installation; skipped.", base)
            continue
        for target in inventory.children(base):
            if _contained(target, root) and target.is_dir():
                directories.extend((target / "usr" / "bin", target / "bin"))
    for name in ("qemu", "qemu-arc", "openocd"):
        directories.extend((root / "hosttools" / name, root / "hosttools" / name / "bin"))
    paths = []
    for directory in directories:
        if not _contained(directory, root):
            continue
        for path in inventory.children(directory):
            name = path.name.removesuffix(".exe")
            if (name in {"dtc", "openocd", "gperf", "bossac"} or name.startswith("qemu-system-")):
                if _executable(path, root, inventory):
                    paths.append(path)
    return sorted(set(paths))


def _inspect_sdk(root, sources, inventory):
    metadata = root / "sdk_version"
    if not _contained(metadata, root):
        inventory.diagnostic("sdk_external_link", "SDK version metadata points outside the installation; skipped.", metadata)
        return None
    text = inventory.text(metadata)
    if text is None:
        inventory.diagnostic("sdk_missing_version", "SDK installation has no readable sdk_version metadata.", root)
        return None
    version = text if VERSION_PATTERN.fullmatch(text) else None
    status = "discovered"
    if version is None:
        status = "unsupported"
        inventory.diagnostic("sdk_version_invalid", "SDK version metadata is not a supported version string.", metadata)
    elif int(version.split(".", 1)[0]) > 1:
        status = "unsupported"
        inventory.diagnostic("sdk_version_unsupported", "This SDK release family is not covered by the inventory adapter.", metadata)

    source_files = [str(metadata)]
    signatures = [_signature(root, inventory), _signature(metadata, inventory), [str(metadata), text]]
    package_files = []
    for name in ("Zephyr-sdkConfig.cmake", "Zephyr-sdkConfigVersion.cmake", "zephyr-sdk-config.cmake"):
        path = root / "cmake" / name
        signatures.append(_signature(path, inventory))
        if not _contained(path, root):
            inventory.diagnostic("sdk_external_link", "SDK CMake package metadata points outside the installation; skipped.", path)
            continue
        contents = inventory.text(path)
        if contents is not None:
            source_files.append(str(path))
            signatures.append([str(path), contents])
            package_files.append(str(path))
    if not any(Path(path).name in {"Zephyr-sdkConfig.cmake", "zephyr-sdk-config.cmake"} for path in package_files):
        inventory.diagnostic("sdk_package_missing", "No supported SDK CMake package file was found; setup may be incomplete.", root / "cmake")
        if status == "discovered":
            status = "incomplete"
    available = []
    for name in ("sdk_gnu_toolchains", "sdk_toolchains"):
        path = root / name
        if not _contained(path, root):
            inventory.diagnostic("sdk_external_link", "SDK toolchain metadata points outside the installation; skipped.", path)
            continue
        contents = inventory.text(path)
        signatures.append(_signature(path, inventory))
        if contents is not None:
            source_files.append(str(path))
            signatures.append([str(path), contents])
            for line in contents.splitlines():
                if TOOLCHAIN_PATTERN.fullmatch(line):
                    available.append(line)
                elif line:
                    inventory.diagnostic("sdk_toolchain_metadata_invalid", "Ignored malformed available-toolchain entry.", path)

    # Layout is detected from files, so compatible backports need not use a fixed version threshold.
    gnu = root / "gnu" if (root / "gnu").is_dir() else root
    signatures.append(_signature(gnu, inventory))
    toolchains = []
    if _contained(gnu, root):
        for candidate in inventory.children(gnu):
            if not _contained(candidate, root):
                if candidate.is_symlink():
                    inventory.diagnostic("sdk_external_link", "SDK toolchain points outside the installation; skipped.", candidate)
                continue
            if not candidate.is_dir() or not TOOLCHAIN_PATTERN.fullmatch(candidate.name):
                continue
            directory = candidate / "bin"
            signatures.extend((_signature(candidate, inventory), _signature(directory, inventory)))
            for suffix in ("-gcc", "-gcc.exe"):
                compiler = directory / (candidate.name + suffix)
                if _executable(compiler, root, inventory):
                    signatures.append(_signature(compiler, inventory))
                    toolchains.append({"name": candidate.name, "compiler": str(compiler),
                                       "path": str(candidate), "family": "GNU"})
                    break
    else:
        inventory.diagnostic("sdk_external_link", "SDK GNU toolchain root points outside the installation; skipped.", gnu)

    for compiler in (root / "llvm" / "bin" / "clang", root / "llvm" / "bin" / "clang.exe"):
        if _executable(compiler, root, inventory):
            toolchains.append({"name": "llvm", "compiler": str(compiler),
                               "path": str(compiler.parent.parent), "family": "LLVM"})
            signatures.append(_signature(compiler, inventory))
            break
    signatures.extend(_signature(root / name, inventory) for name in ("llvm", "llvm/bin", "hosttools", "sysroots"))
    hosts = _host_binaries(root, inventory)
    signatures.extend(_signature(path, inventory) for path in hosts)
    for path in (root / "hosttools", root / "sysroots"):
        if path.is_dir() and not hosts:
            inventory.diagnostic("sdk_host_tools_unverified", "Host-tools directory exists, but no recognized executable was found.", path)
    if not toolchains:
        if status == "discovered":
            status = "incomplete"
        inventory.diagnostic("sdk_no_compiler", "No installed executable GNU or LLVM compiler was found in a supported layout.", root)
    installed = {entry["name"] for entry in toolchains}
    return {
        "id": hashlib.sha256(str(root).encode()).hexdigest()[:20],
        "path": str(root), "version": version, "toolchains": sorted(toolchains, key=lambda item: item["name"]),
        "available_toolchains": sorted(set(available)),
        "uninstalled_toolchains": sorted(set(available) - installed),
        "host_tools": sorted({path.name.removesuffix(".exe") for path in hosts}),
        "host_tools_paths": [str(path) for path in hosts],
        "source": sources[0], "sources": sources, "source_files": source_files,
        "cmake_package_files": package_files,
        "fingerprint": hashlib.sha256(json.dumps(signatures, sort_keys=True).encode()).hexdigest(),
        "fingerprint_kind": "metadata-content-and-component-stat", "evidence": "discovered", "status": status,
        "compatibility": "unknown",
    }


def discover_sdks(workspace, zephyr_base, *, sdk_paths=(), environ=None, home=None, cancel=None):
    """Return installed candidates, not the compiler CMake would ultimately select.

    Explicit SDK roots and CMake registry entries may name a root or its cmake directory.
    Search is limited to well-known prefixes and one level of named SDK directories.
    No executable, setup script, CMake package, or shell configuration is evaluated.
    """
    inventory = _Inventory(cancel)
    inventory.check()
    environment = dict(os.environ if environ is None else environ)
    home = Path(home or environment.get("HOME") or Path.home()).expanduser().resolve()
    workspace = Path(workspace).expanduser().resolve()
    if not isinstance(sdk_paths, (list, tuple)) or len(sdk_paths) > 32:
        raise ValueError("Use at most 32 explicit SDK installation paths.")
    candidates = {}

    def add(value, source, required=False):
        if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
            if required:
                raise ValueError("SDK installation paths must be nonempty paths.")
            return
        inventory.check()
        try:
            root = _absolute(value, workspace, home)
            inventory.track(root)
            if root.name in {"cmake", "zephyr", "Zephyr-sdk"}:
                for ancestor in list(root.parents)[:3]:
                    inventory.track(ancestor / "sdk_version")
                    if (ancestor / "sdk_version").is_file():
                        root = ancestor
                        break
            if not root.is_dir() or not (root / "sdk_version").is_file():
                if required:
                    inventory.diagnostic("sdk_path_invalid", "Selected path is not a readable SDK installation with sdk_version metadata.", root)
                return
            if root not in candidates:
                if len(candidates) >= MAX_CANDIDATES:
                    inventory.diagnostic("sdk_candidate_limit", "SDK candidate limit reached; add a specific installation path.", root)
                    return
                candidates[root] = []
            if source not in candidates[root]:
                candidates[root].append(source)
        except (OSError, RuntimeError, ValueError) as exc:
            inventory.diagnostic("sdk_path_invalid", f"Cannot resolve SDK installation path: {exc}", value)

    for value in sdk_paths:
        add(value, "explicit path", required=True)
    requested = environment.get("ZEPHYR_SDK_INSTALL_DIR") or None
    if requested:
        add(requested, "ZEPHYR_SDK_INSTALL_DIR", required=True)
    for name in ("Zephyr-sdk_DIR", "Zephyr_sdk_DIR"):
        if environment.get(name):
            add(environment[name], name, required=True)

    roots = [*SYSTEM_SEARCH_ROOTS, home, home / ".local", home / ".local/opt", home / "bin",
             workspace, workspace.parent]
    prefixes = environment.get("CMAKE_PREFIX_PATH", "")
    for value in re.split(r"[;" + re.escape(os.pathsep) + r"]", prefixes):
        if value:
            try:
                roots.append(_absolute(value, workspace, home))
            except (OSError, RuntimeError, ValueError) as exc:
                inventory.diagnostic("sdk_path_invalid", f"Cannot resolve CMake SDK search prefix: {exc}", value)
    for root in dict.fromkeys(roots):
        inventory.check()
        add(root, "search prefix")
        for child in inventory.children(root):
            if child.name.lower().startswith("zephyr-sdk"):
                add(child, f"search directory: {root}")

    registry = home / ".cmake" / "packages" / "Zephyr-sdk"
    for entry in inventory.children(registry):
        if entry.is_symlink():
            inventory.diagnostic("sdk_registry_link", "Ignored symlink in the CMake package registry.", entry)
            continue
        value = inventory.text(entry)
        if value:
            if "\n" in value or not Path(value).is_absolute():
                inventory.diagnostic("sdk_registry_invalid", "CMake SDK registry entry must contain one absolute directory path.", entry)
                continue
            add(value, "CMake package registry", required=True)

    sdks = []
    for root, sources in candidates.items():
        inventory.check()
        item = _inspect_sdk(root, sources, inventory)
        if item is not None:
            sdks.append(item)
    recommended_path = Path(zephyr_base) / "SDK_VERSION" if zephyr_base else None
    recommended = inventory.text(recommended_path) if recommended_path else None
    if recommended and not VERSION_PATTERN.fullmatch(recommended):
        inventory.diagnostic("sdk_recommendation_invalid", "Selected Zephyr SDK_VERSION is not a supported version string.", recommended_path)
        recommended = None
    if not sdks:
        inventory.diagnostic("sdk_not_found", "No Zephyr SDK installation was found. Add an installed SDK path; board browsing is still available.", severity="info")
    variant = environment.get("ZEPHYR_TOOLCHAIN_VARIANT") or None
    if variant and variant not in {"zephyr", "zephyr/gnu", "zephyr/llvm"}:
        inventory.diagnostic("sdk_other_toolchain", "A non-SDK toolchain variant is requested; this inventory does not validate that toolchain.", severity="info")
    inventory.check()
    return {
        "sdks": sorted(sdks, key=lambda item: (item["version"] or "", item["path"])),
        "diagnostics": inventory.diagnostics,
        "inputs": sorted(inventory.inputs),
        "selection": {"requested_variant": variant, "requested_sdk_path": requested,
                      "recommended_version": recommended,
                      "recommended_source": str(recommended_path) if recommended_path else None,
                      "effective_compiler": None, "status": "not-configured"},
    }
