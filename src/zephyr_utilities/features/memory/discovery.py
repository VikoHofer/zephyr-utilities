"""Read-only discovery of existing Zephyr application builds."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path

from ...core.workspace import list_folders as list_folders
import re
import sys

import yaml


SKIP_DIRECTORIES = {".git", ".venv", "venv", "node_modules", "CMakeFiles", "__pycache__", ".cache"}


def read_cache(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line or line.startswith(("//", "#")):
                continue
            match = re.match(r"([^:=]+):[^=]*=(.*)", line)
            if match:
                values[match[1]] = match[2]
    return values


def read_config(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("CONFIG_") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
            else:
                match = re.fullmatch(r"# (CONFIG_\w+) is not set", line)
                if match:
                    values[match[1]] = "n"
    return values


def _yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError(f"Build metadata is unexpectedly large: {path}")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeError) as exc:
        raise ValueError(f"Cannot read build metadata {path.name}: {exc}") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Expected a mapping in {path.name}.")
    return value


def _get(value: dict, *keys):
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, (str, int, float)) else None


def _path(value, relative_to: Path) -> str | None:
    if not isinstance(value, str) or not value or value.endswith("-NOTFOUND"):
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = relative_to / path
    return str(path.resolve())


def _python_path(value, relative_to: Path) -> str | None:
    # Resolving symlinks would turn a venv interpreter into the system interpreter.
    if not isinstance(value, str) or not value or value.endswith("-NOTFOUND"):
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = relative_to / path
    return os.path.abspath(path)


def inspect_build(path) -> dict:
    directory = Path(path).expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Build directory does not exist: {directory}")
    cache = read_cache(directory / "CMakeCache.txt")
    info = _yaml(directory / "build_info.yml")
    config = read_config(directory / "zephyr" / ".config")
    zephyr = _path(cache.get("ZEPHYR_BASE") or _get(info, "cmake", "zephyr", "zephyr-base"), directory)
    if not zephyr and not (directory / "domains.yaml").exists():
        raise ValueError(f"This directory is not a recognized Zephyr build: {directory}")
    application = _path(cache.get("APPLICATION_SOURCE_DIR") or _get(info, "cmake", "application", "source-dir"), directory)
    workspace = _path(_get(info, "west", "topdir") or cache.get("WEST_TOPDIR") or cache.get("ZEPHYR_WORKSPACE"), directory)
    board = cache.get("BOARD") or _get(info, "cmake", "board", "name")
    if not cache.get("BOARD") and board:
        qualifier = _get(info, "cmake", "board", "qualifiers")
        if qualifier:
            board = f"{board}/{qualifier}"
    binary_name = config.get("CONFIG_KERNEL_BIN_NAME", cache.get("CONFIG_KERNEL_BIN_NAME", "zephyr")).strip('"')
    elf_name = cache.get("KERNEL_ELF_NAME") or f"{binary_name}.elf"
    elf_dir = _path(cache.get("ZEPHYR_BINARY_DIR"), directory) or str(directory / "zephyr")
    elf = Path(elf_name) if Path(elf_name).is_absolute() else Path(elf_dir) / elf_name
    interpreter = None
    python_source = None
    for key in ("PYTHON_EXECUTABLE", "Python3_EXECUTABLE", "Python_EXECUTABLE", "WEST_PYTHON", "_Python3_EXECUTABLE"):
        if cache.get(key) and not cache[key].endswith("-NOTFOUND"):
            interpreter = _python_path(cache[key], directory)
            python_source = f"CMakeCache.txt:{key}"
            break
    if interpreter is None:
        fallback = Path(workspace) / ".venv" / "bin" / "python" if workspace else None
        if fallback and fallback.is_file():
            interpreter = str(fallback)
            python_source = "workspace .venv fallback"
        else:
            interpreter = sys.executable
            python_source = "application interpreter fallback"
    error = None
    timestamp = None
    if not elf.is_file():
        error = "No final ELF found. Select a completed application build or a sysbuild image."
    else:
        with elf.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                error = "The selected final artifact is not an ELF file."
        timestamp = datetime.fromtimestamp(elf.stat().st_mtime, timezone.utc).isoformat()
    if not zephyr or not (Path(zephyr) / "scripts" / "footprint" / "size_report").is_file():
        error = "The build's Zephyr report generator is unavailable. Restore its Zephyr checkout or import reports."
    if not Path(interpreter).is_file():
        error = "The Python interpreter recorded by this build is unavailable. Restore its environment or import reports."
    domain_name = None
    sysbuild_path = None
    for ancestor in list(directory.parents)[:4]:
        if not (ancestor / "domains.yaml").is_file():
            continue
        domains = _yaml(ancestor / "domains.yaml").get("domains", [])
        if not isinstance(domains, list):
            continue
        for domain in domains:
            if not isinstance(domain, dict) or not isinstance(domain.get("build_dir"), str):
                continue
            target = Path(domain["build_dir"])
            if not target.is_absolute():
                target = ancestor / target
            if target.resolve() == directory:
                domain_name = str(domain.get("name", directory.name))
                sysbuild_path = str(ancestor)
                break
        if domain_name is not None:
            break
    return {
        "path": str(directory), "application": application, "board": board,
        "domain": domain_name, "sysbuild_path": sysbuild_path,
        "elf_path": str(elf.resolve()), "zephyr_base": zephyr,
        "zephyr_version": _get(info, "cmake", "zephyr", "version"),
        "workspace": workspace, "python": interpreter, "python_source": python_source,
        "toolchain": cache.get("CMAKE_C_COMPILER") or _get(info, "cmake", "toolchain", "name"),
        "build_command": _get(info, "west", "command"),
        "artifact_timestamp": timestamp, "ready": error is None, "error": error,
    }


def _domains(directory: Path) -> list[dict]:
    data = _yaml(directory / "domains.yaml")
    domains = data.get("domains", [])
    if not isinstance(domains, list):
        raise ValueError(f"Expected a domain list in {directory / 'domains.yaml'}.")
    result = []
    for domain in domains:
        if not isinstance(domain, dict) or not isinstance(domain.get("build_dir"), str):
            raise ValueError("Each sysbuild domain must have a build_dir path.")
        child = Path(domain["build_dir"])
        if not child.is_absolute():
            child = directory / child
        build = inspect_build(child)
        build["domain"] = str(domain.get("name", child.name))
        build["sysbuild_path"] = str(directory)
        result.append(build)
    return result


def discover_builds(root, max_depth: int = 6, max_results: int = 100) -> dict:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project directory does not exist: {root}")
    builds = {}
    warnings = []
    visited = 0
    for current, directories, files in os.walk(root, followlinks=False):
        directory = Path(current)
        visited += 1
        if visited > 6000:
            warnings.append("Search stopped after 6,000 folders. Choose a more specific project folder to find additional builds.")
            break
        depth = len(directory.relative_to(root).parts)
        directories[:] = sorted(name for name in directories if name not in SKIP_DIRECTORIES and not name.startswith(".") and not (directory / name).is_symlink())
        if depth >= max_depth:
            directories[:] = []
        try:
            if "domains.yaml" in files:
                for build in _domains(directory):
                    builds[build["path"]] = build
            elif "CMakeCache.txt" in files or "build_info.yml" in files:
                try:
                    build = inspect_build(directory)
                except ValueError as exc:
                    if "not a recognized Zephyr build" not in str(exc):
                        raise
                else:
                    builds.setdefault(build["path"], build)
                # Keep sibling/nested application build directories discoverable.
                directories[:] = [name for name in directories if name not in {"zephyr", "modules", "app"}]
        except (ValueError, OSError) as exc:
            warnings.append(str(exc))
        if len(builds) >= max_results:
            warnings.append(f"Showing the first {max_results} builds. Choose a more specific folder for additional results.")
            break
    return {"builds": sorted(builds.values(), key=lambda build: (not build["ready"], build["path"]))[:max_results], "warnings": warnings}
