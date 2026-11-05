"""A named, editable overlay without replacing upstream automatic overlays."""

import hashlib
from pathlib import PurePosixPath
import re


ARCHIVED_CMAKE = "upstream/original/CMakeLists.txt"
_OPTION = "ZEPHYR_UTILS_DEFAULT_OVERLAY"
_TARGET = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*(?:@[A-Za-z0-9_.+-]+)?(?:/[A-Za-z0-9_][A-Za-z0-9_.+-]*)*\Z")


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _path(target):
    if (not isinstance(target, str) or len(target) > 1000 or not _TARGET.fullmatch(target) or
            any(part in {".", ".."} for part in target.replace("@", "/").split("/"))):
        raise ValueError("A default overlay needs an exact supported board target, including its revision and qualifiers.")
    name = target.replace("@", "_rev_").replace("/", "_")
    if len(name) > 200:
        name = name[:180] + "-" + _sha(target)[:16]
    return f"overlays/{name}.overlay"


def _content(target):
    return ("/*\n * SPDX-License-Identifier: Apache-2.0\n *\n"
            f" * Application overlay for {target}.\n"
            " * Add application-specific Device Tree changes here.\n"
            " * This starting file is intentionally empty; no hardware mapping is guessed.\n"
            " * Automatic and Append overlay selection include it for this exact target.\n"
            " * Replace selection starts without it; select it explicitly if needed.\n */\n")


def _cmake_block(target, path):
    return (
        "\n# Zephyr Utilities: retain automatic overlays and add this application's editable overlay.\n"
        f"if(NOT DEFINED {_OPTION})\n"
        "  if(DEFINED DTC_OVERLAY_FILE)\n"
        f'    set({_OPTION} OFF CACHE BOOL "Include the generated default application overlay")\n'
        "  else()\n"
        f'    set({_OPTION} ON CACHE BOOL "Include the generated default application overlay")\n'
        "  endif()\n"
        "endif()\n"
        f'if({_OPTION} AND "${{BOARD}}" STREQUAL "{target}")\n'
        f'  list(PREPEND EXTRA_DTC_OVERLAY_FILE "${{CMAKE_CURRENT_SOURCE_DIR}}/{path}")\n'
        "endif()\n"
        "# End Zephyr Utilities default overlay.\n\n")


def default_overlay(target):
    """Return deterministic metadata for the generated overlay and activation block."""
    path = _path(target)
    return {"schema_version": 1, "path": path, "target": target, "sha256": _sha(_content(target)),
            "activation": "automatic-or-append", "cmake_option": _OPTION,
            "cmake_block_sha256": _sha(_cmake_block(target, path))}


def add_default_overlay(contents, target, *, archive_cmake=False):
    """Add the no-op overlay and a bounded CMake block; never replace existing paths."""
    metadata = default_overlay(target)
    cmake = contents.get("CMakeLists.txt")
    if not isinstance(cmake, str):
        raise ValueError("The application needs a readable root CMakeLists.txt before adding its default overlay.")
    matches = list(re.finditer(r"(?m)^[ \t]*cmake_minimum_required\(\s*VERSION\s+\d+\.\d+(?:\.\d+)?\s*\)"
                              r"[ \t]*(?:\#[^\r\n]*)?(?:\r?\n|$)", cmake))
    if len(matches) != 1 or _OPTION in cmake:
        raise ValueError("The application's CMake layout needs a newer default-overlay adapter.")
    additions = {metadata["path"]: _content(target)}
    if archive_cmake:
        additions[ARCHIVED_CMAKE] = cmake
    for path in additions:
        if (path in contents or any(str(parent) in contents for parent in PurePosixPath(path).parents) or
                any(PurePosixPath(existing).is_relative_to(path) for existing in contents)):
            raise ValueError(f"The generated default overlay would replace an existing application path: {path}")
    position = matches[0].end()
    return {**contents, **additions,
            "CMakeLists.txt": cmake[:position] + _cmake_block(target, metadata["path"]) + cmake[position:]}, metadata
