"""Bounded freshness evidence for cached sample configuration results.

Unlike the environment catalogue guard, this scans DTS, bindings, configuration
and build metadata, including transitive headers. Small metadata files are hashed;
headers and large files use device/inode/size/nanosecond mtime+ctime identities.
This is conservative local freshness evidence, not an archive of every possible
dynamic CMake input or proof against a host falsifying filesystem timestamps.
"""

import hashlib
import json
import os
from pathlib import Path
import stat

from .creation import path_identity
from .probing import check_cancel


MAX_ENTRIES = 250000
MAX_DIRECTORIES = 30000
MAX_DEPTH = 32
MAX_HASH_FILE_BYTES = 256 * 1024
MAX_HASH_BYTES = 32 * 1024 * 1024
_VERSION = 1
_SKIP = frozenset({".git", ".cache", ".venv", "venv", "__pycache__", "node_modules", "CMakeFiles",
                   "build", "out", "output", "dist", "target"})
_SOURCE_ONLY = frozenset({"samples", "tests", "test", "doc", "docs", "examples", "demos"})
_OUTPUT_MARKERS = frozenset({"CMakeCache.txt", "build.ninja", "compile_commands.json"})
_SUFFIXES = frozenset({".dts", ".dtsi", ".overlay", ".conf", ".h", ".cmake", ".yml", ".yaml", ".py"})


def _relevant(name):
    return (Path(name).suffix in _SUFFIXES or name.startswith("Kconfig") or
            name.endswith("defconfig") or name == "CMakeLists.txt")


def _path(value):
    if not isinstance(value, str) or not value or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise ValueError("Sample compatibility inputs need absolute source paths without parent traversal.")
    return Path(value)


def _roots(profile):
    # Whole checkouts/modules contain configuration files outside conventional
    # include directories. Their demonstration/test trees are not build inputs.
    trees = {_path(profile.get("zephyr", {}).get("path")): True}
    for module in profile.get("modules", []):
        if module.get("is_module"):
            if module.get("status") != "discovered" or module.get("active") is not True:
                raise ValueError("Sample compatibility needs discovered active modules.")
            trees[_path(module.get("path"))] = True
    for kind, subdirectories in (("boards", ("boards",)), ("socs", ("soc",)),
                                ("dts", ("dts", "include")), ("snippets", ("snippets",))):
        for entry in profile.get("roots", {}).get(kind, []):
            base = _path(entry.get("path"))
            for subdirectory in subdirectories:
                trees.setdefault(base / subdirectory, False)
    # A whole source root already covers nested hardware/include roots.
    selected = []
    for path, whole in sorted(trees.items(), key=lambda item: (len(item[0].parts), str(item[0]))):
        if any(path.is_relative_to(parent) and not (
                parent_whole and path != parent and path.relative_to(parent).parts[0] in _SOURCE_ONLY)
                for parent, parent_whole in selected):
            continue
        selected.append((path, whole))
    return selected


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _scan(roots, cancel):
    records, visited = [], set()
    entries_count = directory_count = hashed_bytes = 0
    boundaries = [root for root, _ in roots]

    def record_file(path, before):
        nonlocal hashed_bytes
        signature = _identity(before)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"Unsupported sample compatibility input: {path}")
        digest = None
        if (path.suffix != ".h" and before.st_size <= MAX_HASH_FILE_BYTES and
                hashed_bytes + before.st_size <= MAX_HASH_BYTES):
            hashed_bytes += before.st_size
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if _identity(os.fstat(descriptor)) != signature:
                    raise ValueError("Sample compatibility inputs changed during inspection. Check again.")
                with os.fdopen(descriptor, "rb", closefd=False) as stream:
                    data = stream.read(MAX_HASH_FILE_BYTES + 1)
                if len(data) != before.st_size or _identity(os.fstat(descriptor)) != signature:
                    raise ValueError("Sample compatibility inputs changed during inspection. Check again.")
                digest = hashlib.sha256(data).hexdigest()
            finally:
                os.close(descriptor)
        if _identity(path.lstat()) != signature:
            raise ValueError("Sample compatibility inputs changed during inspection. Check again.")
        records.append([str(path), "file", signature, digest])

    def link(path, depth):
        before = path.lstat()
        target = path.resolve(strict=True)
        if not any(target.is_relative_to(root) for root in boundaries):
            raise ValueError(f"Sample compatibility input link leaves the selected source roots: {path}")
        records.append([str(path), "link", _identity(before), os.readlink(path), str(target)])
        actual = target.lstat()
        if stat.S_ISDIR(actual.st_mode):
            visit(target, depth + 1, False)
        elif _relevant(path.name):
            record_file(target, actual)
        if _identity(path.lstat()) != _identity(before):
            raise ValueError("Sample compatibility input link changed during inspection. Check again.")

    def visit(path, depth, whole):
        nonlocal entries_count, directory_count
        check_cancel(cancel)
        if depth > MAX_DEPTH:
            raise ValueError("Sample compatibility source tree exceeds the supported depth.")
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode):
            link(path, depth)
            return
        if not stat.S_ISDIR(before.st_mode):
            raise ValueError(f"Sample compatibility expected a source directory: {path}")
        if path in visited:
            return
        visited.add(path)
        directory_count += 1
        if directory_count > MAX_DIRECTORIES:
            raise ValueError("Sample compatibility source tree exceeds the directory limit.")
        with os.scandir(path) as iterator:
            children = []
            for entry in iterator:
                entries_count += 1
                if entries_count > MAX_ENTRIES:
                    raise ValueError("Sample compatibility source tree exceeds the entry limit.")
                children.append(entry)
        names = {entry.name for entry in children}
        if names & _OUTPUT_MARKERS:
            if depth == 0:
                raise ValueError(f"Sample compatibility source root contains build outputs: {path}")
            return
        # Directory mtime changes for irrelevant build/code files must not make
        # cached hardware facts stale. Relevant entries have their own records.
        records.append([str(path), "directory", before.st_dev, before.st_ino])
        for entry in sorted(children, key=lambda item: item.name):
            check_cancel(cancel)
            name, child = entry.name, path / entry.name
            if (name.startswith(".") or name in _SKIP or name.startswith(("build-", "build_", "cmake-build-")) or
                    whole and name in _SOURCE_ONLY):
                continue
            if entry.is_symlink():
                if _relevant(name) or entry.is_dir():
                    link(child, depth)
            elif entry.is_dir(follow_symlinks=False):
                visit(child, depth + 1, False)
            elif _relevant(name):
                record_file(child, entry.stat(follow_symlinks=False))
        after = path.lstat()
        if _identity(before) != _identity(after):
            raise ValueError("Sample compatibility source directory changed during inspection. Check again.")

    for root, whole in roots:
        check_cancel(cancel)
        if not root.exists() and not root.is_symlink():
            existing = root.parent
            while not existing.exists():
                existing = existing.parent
            path_identity(existing)
            records.append([str(root), "missing"])
            continue
        path_identity(root)
        visit(root, 0, whole)
    return sorted(records, key=lambda item: json.dumps(item, separators=(",", ":")))


def sample_input_guard(profile, *, cancel=None):
    """Return stable evidence for static configuration inputs, or reject partial scans.

    Outputs, source-root demonstrations/tests/docs and hidden/cache directories are
    excluded. Application/sample bytes are guarded separately by their own hashes.
    After the bounded content-hashing budget, remaining files retain stat identities.
    Arbitrary dynamic reads outside these selected source trees remain unverified.
    """
    roots = _roots(profile)
    before = _scan(roots, cancel)
    after = _scan(roots, cancel)
    if before != after:
        raise ValueError("Sample compatibility inputs changed during inspection. Check again.")
    check_cancel(cancel)
    return hashlib.sha256(json.dumps([_VERSION, after], separators=(",", ":")).encode()).hexdigest()
