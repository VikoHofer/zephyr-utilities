"""Read-only, workspace-bound observations of existing Zephyr build outputs.

These observations are session references, not build provenance or source snapshots.
Only selected regular output files are read; metadata never supplies executable code.
"""

from copy import deepcopy
from datetime import datetime, timezone
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
from threading import RLock
import time
import uuid

import yaml


MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_YAML_BYTES = 2 * 1024 * 1024
MAX_DOMAINS = 16
ARTIFACTS = (
    ("CMakeCache.txt", "cmake-cache"), ("build_info.yml", "build-info"),
    ("zephyr/zephyr.dts", "resolved-dts"), ("zephyr/zephyr.dts.pre", "preprocessed-dts"),
    ("zephyr/.config", "kconfig"), ("zephyr/zephyr.elf", "elf"),
    ("zephyr/zephyr.map", "map"), ("zephyr/zephyr.bin", "binary"),
    ("zephyr/zephyr.hex", "hex"),
    ("zephyr/include/generated/zephyr/devicetree_generated.h", "devicetree-header"),
)
TEXT_KINDS = frozenset({"cmake-cache", "build-info", "resolved-dts", "preprocessed-dts", "kconfig", "map", "devicetree-header"})
METADATA_KINDS = frozenset({"cmake-cache", "build-info", "domains"})


def _path(value, base=None):
    if not isinstance(value, (str, Path)) or not str(value) or len(str(value)) > 4096:
        raise ValueError("Choose a valid build directory path.")
    if any(ord(char) < 32 for char in str(value)):
        raise ValueError("Build paths must not contain control characters.")
    path = Path(value)
    if ".." in path.parts:
        raise ValueError("Build paths must not contain parent traversal.")
    if not path.is_absolute():
        if base is None:
            raise ValueError("The workspace must be an absolute directory path.")
        path = base / path
    if base is not None and not path.is_relative_to(base):
        raise ValueError("Choose a build directory inside the selected workspace.")
    return path


def _open_directory(path):
    """Resolve each component without following symlinks, including ancestors."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _directory_identity(path):
    descriptor = _open_directory(path)
    try:
        info = os.fstat(descriptor)
        return info.st_dev, info.st_ino
    finally:
        os.close(descriptor)


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class _Budget:
    def __init__(self):
        self.used = 0

    def consume(self, size):
        self.used += size
        if self.used > MAX_TOTAL_BYTES:
            raise ValueError("Build inspection exceeded its total file-reading limit.")


def _read_file(workspace, path, budget, *, content=False, limit=MAX_ARTIFACT_BYTES):
    if not path.is_relative_to(workspace):
        raise ValueError("Build output is outside the selected workspace.")
    parent = _open_directory(path.parent)
    try:
        parent_info = os.fstat(parent)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError("Build output is not a regular file or exceeds its size limit.")
            digest, size, chunks = hashlib.sha256(), 0, []
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                budget.consume(len(chunk))
                if size > limit:
                    raise ValueError("Build output grew beyond its size limit.")
                digest.update(chunk)
                if content:
                    chunks.append(chunk)
            after = os.fstat(stream.fileno())
            current = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if _signature(before) != _signature(after) or _signature(after) != _signature(current):
                raise ValueError("Build output changed while it was being read. Inspect the build again.")
        if _directory_identity(path.parent) != (parent_info.st_dev, parent_info.st_ino):
            raise ValueError("Build directory changed while it was being read. Inspect the build again.")
        return {"sha256": digest.hexdigest(), "size": size}, b"".join(chunks) if content else None
    finally:
        os.close(parent)


def _yaml_mapping(content):
    if len(content) > MAX_YAML_BYTES:
        raise ValueError("Build YAML exceeds the metadata size limit.")
    text = content.decode("utf-8")
    depth = 0
    for count, event in enumerate(yaml.parse(text, Loader=yaml.SafeLoader)):
        if count >= 60000 or isinstance(event, yaml.AliasEvent):
            raise ValueError("Build YAML uses aliases or exceeds its parsing limit.")
        if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
            depth += 1
        elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
            depth -= 1
        if depth > 32:
            raise ValueError("Build YAML exceeds its nesting limit.")
    result = yaml.safe_load(text)
    if not isinstance(result, dict):
        raise ValueError("Build YAML must contain a mapping.")
    return result


def _yaml(content):
    try:
        return _yaml_mapping(content)
    except (UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"Cannot parse build YAML: {exc}") from exc


def _cache(content):
    result = {}
    for line in content.decode("utf-8").splitlines():
        if not line or line.startswith(("#", "//")):
            continue
        match = re.match(r"([^:=]+):[^=]*=(.*)", line)
        if match:
            result[match[1]] = match[2]
    return result


def _text(value):
    return value if isinstance(value, str) and value and not value.endswith("-NOTFOUND") else None


def _strings(value):
    return [entry for entry in value if isinstance(entry, str)] if isinstance(value, list) else []


def _context(cache, info):
    def mapping(key):
        value = info.get(key)
        return value if isinstance(value, dict) else {}

    board, application, toolchain = mapping("board"), mapping("application"), mapping("toolchain")
    name, revision, qualifiers = (_text(board.get(key)) for key in ("name", "revision", "qualifiers"))
    target = name + (f"@{revision}" if revision else "") + (f"/{qualifiers}" if qualifiers else "") if name else None
    return {
        "effective_target": target, "board": name, "revision": revision, "qualifiers": qualifiers,
        "cached_board": _text(cache.get("BOARD") or cache.get("CACHED_BOARD")),
        "application_source_dir": _text(application.get("source-dir")) or _text(cache.get("APPLICATION_SOURCE_DIR") or cache.get("CMAKE_HOME_DIRECTORY")),
        "zephyr_base": _text(mapping("zephyr").get("zephyr-base")) or _text(cache.get("ZEPHYR_BASE")),
        "zephyr_version": _text(mapping("zephyr").get("version")),
        "compiler": _text(cache.get("CMAKE_C_COMPILER")),
        "sdk_path": _text(toolchain.get("path")) or _text(cache.get("ZEPHYR_SDK_INSTALL_DIR")),
        "toolchain": _text(toolchain.get("name")) or _text(cache.get("ZEPHYR_TOOLCHAIN_VARIANT")),
        "configuration": {"files": _strings(mapping("kconfig").get("files")), "user_files": _strings(mapping("kconfig").get("user-files"))},
        "devicetree": {"files": _strings(mapping("devicetree").get("files")),
                       "user_files": _strings(mapping("devicetree").get("user-files")),
                       "extra_user_files": _strings(mapping("devicetree").get("extra-user-files")),
                       "include_dirs": _strings(mapping("devicetree").get("include-dirs")),
                       "binding_dirs": _strings(mapping("devicetree").get("bindings-dirs"))},
        "source_freshness": "unknown", "input_coverage": "partial", "hardware_validated": False,
    }


def _diagnostic(code, message, path=None):
    return {"severity": "warning", "code": code, "message": str(message), **({"path": str(path)} if path else {})}


def _verify_metadata(entry, budget, paths=None):
    workspace = Path(entry["observation"]["workspace"])
    for path, expected in entry["watches"].items():
        if paths is not None and path not in paths:
            continue
        try:
            current, _ = _read_file(workspace, Path(path), budget, limit=MAX_TEXT_BYTES)
        except FileNotFoundError:
            if expected is None:
                continue
            raise ValueError("Build metadata changed during inspection. Open the build again.") from None
        except OSError:
            if expected is None:
                continue  # Preserve the original partial observation of unreadable metadata.
            raise ValueError("Build metadata became unavailable. Open the build again.") from None
        if current["sha256"] != expected:
            raise ValueError("Build metadata changed during inspection. Open the build again.")


class BuildArtifactSelection:
    """Detached output identity for a submitted task, independent of session retention."""

    def __init__(self, entry, domain, artifact, related_kinds=()):
        observation = entry["observation"]
        self.workspace = observation["workspace"]
        self.inspection_id = observation["id"]
        self.root_build_dir = observation["root_build_dir"]
        self.domain = domain["name"]
        self.build_dir = domain["build_dir"]
        self._context = deepcopy(domain["context"])
        self._artifact = deepcopy(artifact)
        observed = {item["kind"]: item for item in domain["artifacts"]}
        self._artifact_hashes = {artifact["path"]: artifact["sha256"]}
        for relative, kind in ARTIFACTS:
            if kind in related_kinds:
                self._artifact_hashes[str(Path(self.build_dir) / relative)] = observed.get(kind, {}).get("sha256")
        paths = {str(Path(self.build_dir) / name) for name in ("CMakeCache.txt", "build_info.yml")}
        paths.add(str(Path(self.root_build_dir) / "domains.yaml"))
        self._metadata_hashes = {path: digest for path, digest in entry["watches"].items() if path in paths}
        self._identities = {path: identity for path, identity in entry["identities"].items()
                            if Path(self.build_dir).is_relative_to(path)}

    @property
    def context(self):
        return deepcopy(self._context)

    @property
    def artifact(self):
        return deepcopy(self._artifact)

    @property
    def metadata_hashes(self):
        return dict(self._metadata_hashes)

    @property
    def artifact_hashes(self):
        return dict(self._artifact_hashes)

    def _verify_directories(self):
        for path, expected in self._identities.items():
            if _directory_identity(Path(path)) != expected:
                raise ValueError("The selected build directory changed. Inspect the build again.")

    def _verify_context(self, budget):
        for path, expected in self._metadata_hashes.items():
            try:
                actual, _ = _read_file(Path(self.workspace), Path(path), budget, limit=MAX_TEXT_BYTES)
            except FileNotFoundError:
                if expected is None:
                    continue
                raise ValueError("The selected build context changed. Inspect the build again.") from None
            if actual["sha256"] != expected:
                raise ValueError("The selected build context changed. Inspect the build again.")

    def verify(self):
        """Require the original context and selected bytes; unrelated outputs may change."""
        try:
            self._verify_directories()
            budget = _Budget()
            self._verify_context(budget)
            for path, expected in self._artifact_hashes.items():
                try:
                    actual, _ = _read_file(Path(self.workspace), Path(path), budget)
                except FileNotFoundError:
                    if expected is None:
                        continue
                    raise ValueError("The selected build output is unavailable. Inspect the build again.") from None
                if actual["sha256"] != expected:
                    raise ValueError("The selected build output changed. Inspect the build again.")
            self._verify_context(budget)
            self._verify_directories()
        except OSError as exc:
            raise ValueError("The selected build output or context is unavailable. Inspect the build again.") from exc


class BuildInspectionService:
    """Share bounded build observations across Projects, Device Tree and Memory."""

    def __init__(self, *, max_observations=16, ttl=1800):
        self.max_observations = max_observations
        self.ttl = ttl
        self.lock = RLock()
        self.observations = {}

    def _capture(self, workspace, path, kind, budget):
        limit = MAX_YAML_BYTES if kind in {"build-info", "domains"} else MAX_TEXT_BYTES if kind == "cmake-cache" else MAX_ARTIFACT_BYTES
        identity, content = _read_file(workspace, path, budget, content=kind in METADATA_KINDS, limit=limit)
        return {"kind": kind, "path": str(path), **identity, "availability": "live"}, content

    def _domain(self, workspace, path, name, budget, watches):
        directory_identity = _directory_identity(path)
        artifacts, diagnostics, cache, info = [], [], {}, {}
        for relative, kind in ARTIFACTS:
            target = path / relative
            if kind in METADATA_KINDS:
                watches[str(target)] = None
            try:
                artifact, content = self._capture(workspace, target, kind, budget)
                artifacts.append(artifact)
                if kind in METADATA_KINDS:
                    watches[str(target)] = artifact["sha256"]
                if kind == "cmake-cache":
                    cache = _cache(content)
                elif kind == "build-info":
                    raw = _yaml(content).get("cmake", {})
                    if not isinstance(raw, dict):
                        raise ValueError("Build information has no supported cmake mapping.")
                    info = raw
            except FileNotFoundError:
                continue
            except (OSError, ValueError, yaml.YAMLError, UnicodeError) as exc:
                diagnostics.append(_diagnostic("output-unavailable", exc, target))
        context = _context(cache, info)
        recognized = context["zephyr_base"] or any(item["kind"] in {"resolved-dts", "preprocessed-dts", "devicetree-header"} for item in artifacts)
        if not recognized:
            raise ValueError(f"No readable Zephyr build metadata or Device Tree output was found in {path}.")
        if not context["effective_target"] or not context["compiler"]:
            diagnostics.append(_diagnostic("partial-context", "Effective target or compiler information is incomplete."))
        if _directory_identity(path) != directory_identity:
            raise ValueError("Build directory changed during inspection. Inspect the build again.")
        return {"name": name, "build_dir": str(path), "context": context, "artifacts": artifacts,
                "diagnostics": diagnostics, "freshness": "unchanged"}, directory_identity

    def inspect(self, workspace, build_dir):
        workspace = _path(workspace)
        root = _path(build_dir, workspace)
        try:
            identities = {str(workspace): _directory_identity(workspace), str(root): _directory_identity(root)}
        except OSError as exc:
            if exc.errno in {errno.ENOTDIR, errno.ELOOP}:
                raise ValueError("Choose an existing build directory without symlinked path components.") from exc
            raise
        budget = _Budget()
        watches = {str(root / "domains.yaml"): None}
        root_artifacts, diagnostics = [], []
        try:
            manifest, content = self._capture(workspace, root / "domains.yaml", "domains", budget)
        except FileNotFoundError:
            entries, default = [("application", root)], "application"
        else:
            root_artifacts.append(manifest)
            watches[manifest["path"]] = manifest["sha256"]
            data = _yaml(content)
            raw_domains = data.get("domains")
            if not isinstance(raw_domains, list) or not raw_domains or len(raw_domains) > MAX_DOMAINS:
                raise ValueError(f"Sysbuild must declare between 1 and {MAX_DOMAINS} domains.")
            entries, names, paths = [], set(), set()
            for item in raw_domains:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"] or len(item["name"]) > 128 or any(ord(char) < 32 for char in item["name"]):
                    raise ValueError("Sysbuild contains an invalid domain name.")
                name = item["name"]
                path = _path(item.get("build_dir"), root)
                if path == root or name in names or path in paths:
                    raise ValueError("Sysbuild domains must have unique names and distinct child build directories.")
                names.add(name)
                paths.add(path)
                entries.append((name, path))
            default = _text(data.get("default"))
            if default not in names:
                default = None
        domains = []
        for name, path in entries:
            try:
                domain, identity = self._domain(workspace, path, name, budget, watches)
                domains.append(domain)
                identities[str(path)] = identity
            except (OSError, ValueError) as exc:
                if len(entries) == 1:
                    raise ValueError(str(exc)) from exc
                diagnostics.append(_diagnostic("domain-unavailable", f"Cannot inspect domain {name}: {exc}", path))
        if not domains:
            raise ValueError("No readable Zephyr image was found in the selected build.")
        if default not in {domain["name"] for domain in domains}:
            default = domains[0]["name"]
        observation = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": str(workspace),
                       "root_build_dir": str(root), "created_at": datetime.now(timezone.utc).isoformat(),
                       "default_domain": default, "domains": domains, "artifacts": root_artifacts, "diagnostics": diagnostics}
        entry = {"observation": observation, "identities": identities, "watches": watches, "expires": time.monotonic() + self.ttl}
        _verify_metadata(entry, budget)
        # Reject a moving build instead of combining metadata from different directories.
        for path, identity in identities.items():
            if _directory_identity(Path(path)) != identity:
                raise ValueError("A build directory changed during inspection. Inspect the build again.")
        with self.lock:
            self._expire()
            while len(self.observations) >= self.max_observations:
                self.observations.pop(next(iter(self.observations)))
            self.observations[observation["id"]] = entry
        return deepcopy(observation)

    def _expire(self):
        current = time.monotonic()
        self.observations = {key: entry for key, entry in self.observations.items() if entry["expires"] > current}

    def _entry(self, workspace, observation_id):
        if not isinstance(observation_id, str) or not re.fullmatch(r"[a-f0-9]{32}", observation_id):
            raise ValueError("Invalid build inspection identity.")
        with self.lock:
            self._expire()
            entry = self.observations.get(observation_id)
            if entry is None:
                raise ValueError("This build inspection expired. Open the build again.")
            if entry["observation"]["workspace"] != str(_path(workspace)):
                raise ValueError("This build inspection belongs to another workspace.")
            return deepcopy(entry)

    def _revalidate(self, entry):
        observation = entry["observation"]
        workspace = Path(observation["workspace"])
        budget = _Budget()
        changed_metadata = set()
        invalid_directories = set()
        for path, identity in entry["identities"].items():
            try:
                if _directory_identity(Path(path)) != identity:
                    invalid_directories.add(path)
            except OSError:
                invalid_directories.add(path)
        seen = {}
        for container in [observation, *observation["domains"]]:
            for artifact in container["artifacts"]:
                path = Path(artifact["path"])
                try:
                    if any(path.is_relative_to(directory) for directory in invalid_directories):
                        raise ValueError("Build directory was replaced or is unavailable.")
                    actual, _ = self._capture(workspace, path, artifact["kind"], budget)
                    artifact["availability"] = "live" if actual["sha256"] == artifact["sha256"] else "changed"
                except FileNotFoundError:
                    artifact["availability"] = "missing"
                except (OSError, ValueError):
                    artifact["availability"] = "unavailable"
                seen[str(path)] = artifact["availability"]
                if artifact["kind"] in METADATA_KINDS and artifact["availability"] != "live":
                    changed_metadata.add(str(path))
        for path, expected in entry["watches"].items():
            if path in seen:
                continue
            try:
                current, _ = _read_file(workspace, Path(path), budget, limit=MAX_TEXT_BYTES)
                if expected != current["sha256"]:
                    changed_metadata.add(path)
            except FileNotFoundError:
                if expected is not None:
                    changed_metadata.add(path)
            except (OSError, ValueError):
                changed_metadata.add(path)
        root_changed = str(Path(observation["root_build_dir"]) / "domains.yaml") in changed_metadata
        for domain in observation["domains"]:
            statuses = {artifact["availability"] for artifact in domain["artifacts"]}
            metadata_changed = root_changed or any(str(Path(domain["build_dir"]) / filename) in changed_metadata for filename in ("CMakeCache.txt", "build_info.yml"))
            if metadata_changed or "changed" in statuses:
                domain["freshness"] = "changed"
            elif statuses - {"live"} or any(Path(domain["build_dir"]).is_relative_to(path) for path in invalid_directories):
                domain["freshness"] = "unavailable"
            if domain["freshness"] != "unchanged":
                domain["diagnostics"].append(_diagnostic("outputs-changed", "Saved outputs changed or became unavailable. Open the build again to capture its current state."))
        return observation, changed_metadata, invalid_directories

    def get(self, workspace, observation_id):
        """Return original metadata and hashes with current output availability."""
        return self._revalidate(self._entry(workspace, observation_id))[0]

    def select_artifact(self, workspace, observation_id, domain, kind, *, related_kinds=()):
        """Pin one existing output and its context for an explicitly submitted task."""
        if not set(related_kinds).issubset({item_kind for _, item_kind in ARTIFACTS}):
            raise ValueError("Choose supported related build outputs.")
        entry = self._entry(workspace, observation_id)
        selected = next((item for item in entry["observation"]["domains"] if item["name"] == domain), None)
        if selected is None:
            raise ValueError("Choose an image from this build inspection.")
        artifact = next((item for item in selected["artifacts"] if item["kind"] == kind), None)
        if artifact is None:
            raise ValueError("The selected output was not available when the build was inspected. Inspect a completed build again.")
        selection = BuildArtifactSelection(entry, selected, artifact, related_kinds)
        selection.verify()
        return selection

    def read_text(self, workspace, observation_id, domain, kind):
        if not isinstance(kind, str) or kind not in TEXT_KINDS:
            raise ValueError("Choose a supported text output from this build inspection.")
        entry = self._entry(workspace, observation_id)
        observation, changed_metadata, invalid_directories = self._revalidate(entry)
        selected = next((item for item in observation["domains"] if item["name"] == domain), None)
        if selected is None:
            raise ValueError("Choose an image from this build inspection.")
        artifact = next((item for item in selected["artifacts"] if item["kind"] == kind), None)
        if artifact is None:
            raise ValueError("This text output was not available when the build was inspected.")
        metadata_paths = {str(Path(selected["build_dir"]) / name) for name in ("CMakeCache.txt", "build_info.yml")}
        metadata_paths.add(str(Path(observation["root_build_dir"]) / "domains.yaml"))
        if artifact["availability"] != "live" or changed_metadata & metadata_paths or any(Path(artifact["path"]).is_relative_to(path) for path in invalid_directories):
            raise ValueError("Build outputs changed or are unavailable. Open the build again before reading this file.")
        identity, content = _read_file(Path(observation["workspace"]), Path(artifact["path"]), _Budget(), content=True, limit=MAX_TEXT_BYTES)
        if identity["sha256"] != artifact["sha256"]:
            raise ValueError("Build output changed during the read. Open the build again.")
        _verify_metadata(entry, _Budget(), metadata_paths)
        return {"path": artifact["path"], "text": content.decode("utf-8"), "sha256": artifact["sha256"]}
