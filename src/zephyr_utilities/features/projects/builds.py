"""Explicit single-image CMake jobs with captured recipes and bounded evidence."""

from concurrent.futures import CancelledError
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import time

import yaml

from .probing import check_cancel
from .fragments import fragment_arguments


MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
BUILD_TIMEOUT = 30 * 60


def _path(value, label):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 or c == ";" for c in value):
        raise ValueError(f"{label} must be an absolute path without control characters or semicolons.")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be an absolute path without parent traversal.")
    return str(path)


def command_recipe(profile, source_dir, build_dir, *, environ=None):
    """Create a self-contained shell display and argv recipe without running tools."""
    environment = os.environ if environ is None else environ
    source = _path(str(source_dir), "Application directory")
    output = _path(str(build_dir), "Build directory")
    workspace = _path(profile.get("workspace"), "Workspace")
    zephyr = _path(profile.get("zephyr", {}).get("path"), "Zephyr checkout")
    sdk = _path(profile.get("sdk", {}).get("path"), "SDK installation")
    target = profile.get("target")
    if not isinstance(target, str) or len(target) > 1000 or not re.fullmatch(r"[A-Za-z0-9_.+@/-]+", target):
        raise ValueError("Select a declared board target before configuring.")
    tools = {item["name"]: item for item in profile.get("tools", []) if isinstance(item, dict) and "name" in item}

    def tool(name):
        item = tools.get(name, {})
        if item.get("status") != "discovered":
            raise ValueError(f"The selected environment needs an available {name}; analyze it again after setup.")
        return _path(item.get("path"), name)

    cmake, ninja, python = tool("cmake"), tool("ninja"), tool("Python")
    modules = []
    for module in profile.get("modules", []):
        if module.get("is_module"):
            if module.get("status") != "discovered" or module.get("active") is not True:
                raise ValueError("A module is unresolved or unsupported. Resolve environment diagnostics and analyze again.")
            modules.append(_path(module.get("path"), "Module"))
    if not modules:
        raise ValueError("This creator requires a resolved nonempty module list; an empty list would let Zephyr rediscover modules implicitly.")
    # Use only explicit low-risk environment values, never inherited compiler/configuration overrides.
    path_entries = list(dict.fromkeys([str(Path(python).parent), str(Path(cmake).parent), str(Path(ninja).parent),
                                      *environment.get("PATH", os.defpath).split(os.pathsep)]))
    selected = {"PATH": os.pathsep.join(path_entries), "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1", "GIT_OPTIONAL_LOCKS": "0", "CCACHE_DISABLE": "1",
                "ZEPHYR_BASE": zephyr, "ZEPHYR_TOOLCHAIN_VARIANT": "zephyr", "ZEPHYR_SDK_INSTALL_DIR": sdk}
    # HOME allows installed tooling to resolve its normal runtime files. No shell startup files are read.
    if environment.get("HOME"):
        selected["HOME"] = _path(environment["HOME"], "Home directory")
    configure = [cmake, "-S", source, "-B", output, "-G", "Ninja", f"-DBOARD={target}",
                 f"-DZephyr_DIR={zephyr}/share/zephyr-package/cmake", f"-DZEPHYR_BASE={zephyr}",
                 "-DZEPHYR_TOOLCHAIN_VARIANT=zephyr", f"-DZEPHYR_SDK_INSTALL_DIR={sdk}",
                 f"-DPython3_EXECUTABLE={python}", f"-DCMAKE_MAKE_PROGRAM={ninja}",
                 f"-DUSER_CACHE_DIR={output}/.cache", "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
                 "-DCMAKE_DISABLE_FIND_PACKAGE_ZephyrBuildConfiguration=TRUE",
                 "-DCMAKE_DISABLE_FIND_PACKAGE_ZephyrAppConfiguration=TRUE",
                 "-DZEPHYR_MODULES=" + ";".join(dict.fromkeys(modules))]
    for kind, name in (("boards", "BOARD_ROOT"), ("socs", "SOC_ROOT"), ("dts", "DTS_ROOT"), ("snippets", "SNIPPET_ROOT")):
        roots = [_path(item.get("path"), name) for item in profile.get("roots", {}).get(kind, [])]
        if roots:
            configure.append(f"-D{name}=" + ";".join(dict.fromkeys(roots)))
    if tools.get("dtc", {}).get("status") == "discovered":
        configure.append(f"-DDTC={_path(tools['dtc'].get('path'), 'dtc')}")
    configure.extend(fragment_arguments(profile, source))
    build = [cmake, "--build", output, "--parallel", "2"]
    prefix = ["env", "-i", *[f"{key}={value}" for key, value in sorted(selected.items())]]

    def display(argv):
        return f"cd {shlex.quote(workspace)} && " + shlex.join(prefix + argv)

    return {"cwd": workspace, "environment": selected, "configure": configure, "build": build,
            "display_configure": display(configure), "display_build": display(build)}


def _directory_identity(path):
    """Reject changed/symlinked directory paths before a child receives them."""
    path = Path(path)
    if str(path.resolve(strict=True)) != str(path):
        raise ValueError(f"Directory paths must not traverse symlinks: {path}")
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode):
        raise ValueError(f"Expected a directory: {path}")
    return value.st_dev, value.st_ino


def _reserve_output(path):
    path = Path(path)
    parent = _directory_identity(path.parent)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(descriptor)
        if parent != (opened.st_dev, opened.st_ino):
            raise ValueError("Build parent changed before output reservation.")
        os.mkdir(path.name, mode=0o755, dir_fd=descriptor)
        if _directory_identity(path.parent) != parent:
            raise ValueError("Build parent changed during output reservation; no build was started.")
        return _directory_identity(path)
    finally:
        os.close(descriptor)


def _run(argv, recipe, *, emit, cancel, deadline):
    check_cancel(cancel)
    process = subprocess.Popen(argv, cwd=recipe["cwd"], env=recipe["environment"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    total, pending = 0, bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise ValueError("Build exceeded the 30-minute execution limit; partial outputs were retained.")
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > MAX_LOG_BYTES:
                        raise ValueError("Build output exceeded the 16 MiB log limit; partial outputs were retained.")
                    pending.extend(chunk)
                    while b"\n" in pending or len(pending) >= 2000:
                        end = pending.find(b"\n")
                        end = end if 0 <= end < 2000 else min(2000, len(pending))
                        line = bytes(pending[:end])
                        del pending[:end + (1 if pending[end:end + 1] == b"\n" else 0)]
                        emit(line.decode("utf-8", errors="replace"))
                if not selector.get_map() and process.poll() is None:
                    try:
                        process.wait(timeout=0.05)
                    except subprocess.TimeoutExpired:
                        pass
        if pending:
            emit(pending.decode("utf-8", errors="replace"))
        if process.returncode:
            raise ValueError(f"{Path(argv[0]).name} exited with status {process.returncode}; see the build log.")
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()


def _diagnostic(code, message, path=None):
    return {"severity": "warning", "code": code, "message": str(message), **({"path": str(path)} if path else {})}


def _artifact(path, base, kind):
    # Walk with directory descriptors: a parent swapped for a symlink cannot redirect reads.
    relative = path.relative_to(base)
    directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            nested = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = nested
        descriptor = os.open(relative.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_ARTIFACT_BYTES:
            raise ValueError("Artifact is not a regular file or exceeds the 256 MiB hashing limit.")
        digest, size, content = hashlib.sha256(), 0, bytearray()
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise ValueError("Artifact grew beyond the hashing limit.")
            digest.update(chunk)
            if kind in {"cmake-cache", "build-info"} and len(content) <= 8 * 1024 * 1024:
                content.extend(chunk)
        after = os.fstat(stream.fileno())
        def identity(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        if identity(before) != identity(after) or identity(after) != identity(path.stat()):
            raise ValueError("Artifact changed while it was captured.")
    return {"kind": kind, "path": str(path), "sha256": digest.hexdigest(), "size": size,
            "availability": "live", "evidence": "generated"}, content


def _yaml_mapping(content):
    if len(content) > 2 * 1024 * 1024:
        raise ValueError("Build information exceeds the 2 MiB metadata limit.")
    text = content.decode("utf-8")
    depth = 0
    for count, event in enumerate(yaml.parse(text, Loader=yaml.SafeLoader)):
        if count >= 60000 or isinstance(event, yaml.AliasEvent):
            raise ValueError("Build information uses unsupported aliases or exceeds its parsing limit.")
        if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
            depth += 1
        elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
            depth -= 1
        if depth > 32:
            raise ValueError("Build information exceeds its nesting limit.")
    data = yaml.safe_load(text)
    if not isinstance(data, dict) or not isinstance(data.get("cmake", {}), dict):
        raise ValueError("Build information has no supported cmake mapping.")
    return data.get("cmake", {})


def capture_context(profile, source_dir, build_dir, *, configured=False):
    """Capture bounded text metadata and live artifact identities; never unpickle build data."""
    output = Path(build_dir)
    diagnostics, artifacts, cache, info = [], [], {}, {}
    files = [("CMakeCache.txt", "cmake-cache"), ("build_info.yml", "build-info"),
             ("zephyr_modules.txt", "modules"), ("compile_commands.json", "compile-commands"),
             ("zephyr/zephyr.dts.pre", "preprocessed-dts"), ("zephyr/zephyr.dts", "resolved-dts"),
             ("zephyr/.config", "kconfig"), ("zephyr/zephyr.elf", "elf"),
             ("zephyr/zephyr.bin", "binary"), ("zephyr/zephyr.hex", "hex"), ("zephyr/zephyr.map", "map"),
             ("zephyr/include/generated/zephyr/devicetree_generated.h", "devicetree-header")]
    for relative, kind in files:
        path = output / relative
        try:
            artifact, content = _artifact(path, output, kind)
            artifacts.append(artifact)
            if kind == "cmake-cache":
                if len(content) > 8 * 1024 * 1024:
                    raise ValueError("CMake cache exceeds the 8 MiB metadata limit.")
                for line in content.decode("utf-8").splitlines():
                    if line and not line.startswith(("#", "//")) and "=" in line and ":" in line.split("=", 1)[0]:
                        name_type, value = line.split("=", 1)
                        cache[name_type.split(":", 1)[0]] = value
            elif kind == "build-info":
                info = _yaml_mapping(content)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, RuntimeError, yaml.YAMLError) as exc:
            diagnostics.append(_diagnostic("artifact-unavailable", exc, path))
    def mapping(key):
        value = info.get(key, {})
        return value if isinstance(value, dict) else {}
    board, application, toolchain = mapping("board"), mapping("application"), mapping("toolchain")
    board_name = board.get("name") if isinstance(board.get("name"), str) else None
    qualifiers = board.get("qualifiers") if isinstance(board.get("qualifiers"), str) else None
    revision = board.get("revision") if isinstance(board.get("revision"), str) else None
    target = None
    if board_name:
        target = board_name + (f"@{revision}" if revision else "") + (f"/{qualifiers}" if qualifiers else "")
    compiler = cache.get("CMAKE_C_COMPILER")
    context = {"schema_version": 1, "source_dir": str(source_dir), "build_dir": str(output), "image": "application",
               "requested_target": profile.get("target"), "effective_target": target,
               "board": board_name, "revision": revision, "qualifiers": qualifiers,
               "cached_board": cache.get("BOARD") or cache.get("CACHED_BOARD"),
               "application_source_dir": application.get("source-dir") or cache.get("APPLICATION_SOURCE_DIR") or cache.get("CMAKE_HOME_DIRECTORY"),
               "zephyr_base": mapping("zephyr").get("zephyr-base") or cache.get("ZEPHYR_BASE"),
               "compiler": compiler, "cxx_compiler": cache.get("CMAKE_CXX_COMPILER"),
               "sdk_path": toolchain.get("path") or cache.get("ZEPHYR_SDK_INSTALL_DIR"),
               "toolchain": toolchain.get("name") or cache.get("ZEPHYR_TOOLCHAIN_VARIANT"),
               "configuration": {"mode": "automatic", "files": mapping("kconfig").get("files", []),
                                 "user_files": mapping("kconfig").get("user-files", [])},
               "devicetree": {"mode": "automatic", "files": mapping("devicetree").get("files", []),
                              "include_dirs": mapping("devicetree").get("include-dirs", []),
                              "binding_dirs": mapping("devicetree").get("bindings-dirs", [])},
               "freshness": "captured-at-attempt", "input_coverage": "partial", "hardware_validated": False,
               "configured": bool(configured)}
    context["status"] = "captured" if configured and target and compiler else "partial"
    if context["status"] == "partial":
        diagnostics.append(_diagnostic("partial-build-context", "Effective target/compiler information is incomplete; inspect retained outputs and logs."))
    return context, artifacts, diagnostics


def execute_build(profile, source_dir, build_dir, operation, *, environ=None, emit=None, cancel=None):
    """Reserve a fresh output and configure, optionally compile; preserve failed attempt evidence."""
    if os.name != "posix":
        raise ValueError("Project build execution is currently supported on Linux/POSIX hosts only.")
    if operation not in {"configure", "build"}:
        raise ValueError("Operation must be configure or build.")
    recipe = command_recipe(profile, source_dir, build_dir, environ=environ)
    identities = {path: _directory_identity(path) for path in (str(source_dir), recipe["cwd"])}
    check_cancel(cancel)
    identities[str(build_dir)] = _reserve_output(build_dir)
    emit = emit or (lambda message: None)
    result = {"schema_version": 1, "operation": operation, "recipe": recipe, "status": "failed",
              "started_at": datetime.now(timezone.utc).isoformat(), "context": {}, "artifacts": [], "diagnostics": []}
    configured = False
    deadline = time.monotonic() + BUILD_TIMEOUT
    try:
        for step in (["configure", "build"] if operation == "build" else ["configure"]):
            for path, identity in identities.items():
                if _directory_identity(path) != identity:
                    raise ValueError("An application, workspace or build directory changed during the attempt.")
            emit(f"Starting {step}: {shlex.join(recipe[step])}")
            _run(recipe[step], recipe, emit=emit, cancel=cancel, deadline=deadline)
            if step == "configure":
                configured = True
                result["status"] = "configured"
            else:
                result["status"] = "built"
        check_cancel(cancel)
    except CancelledError:
        result.update(status="cancelled", error="Build cancelled; partial outputs were retained.")
    except (OSError, ValueError, RuntimeError) as exc:
        result.update(status="failed", error=str(exc))
        emit(str(exc))
    try:
        if _directory_identity(build_dir) != identities[str(build_dir)]:
            raise ValueError("Build output directory changed; its artifacts were not attributed to this attempt.")
        result["context"], result["artifacts"], result["diagnostics"] = capture_context(
            profile, source_dir, build_dir, configured=configured)
    except (OSError, ValueError, RuntimeError) as exc:
        result["diagnostics"].append(_diagnostic("build-context-unavailable", exc))
        result["status"] = "failed" if result["status"] != "cancelled" else "cancelled"
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result
