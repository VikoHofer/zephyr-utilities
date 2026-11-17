#!/usr/bin/env python3
"""Inspect the local TFLM sine source plan without configuring or copying a project.

This D0 development utility is independent of the planned application catalogue/API.
Exit 0 means the source layout is understood, even when its optional module is absent.
It does not establish configuration, compilation, inference accuracy or board support.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys

import yaml


SAMPLE = "samples/modules/tflite-micro/hello_world"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_SAMPLE_BYTES = 16 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_FILES = 128
MAX_MANIFESTS = 32
RUNTIME = (
    "src/assert.cpp", "src/constants.c", "src/constants.h", "src/main.c",
    "src/main_functions.cpp", "src/main_functions.h", "src/model.cpp", "src/model.hpp",
    "src/output_handler.cpp", "src/output_handler.hpp",
)
OMITTED = {
    "README.rst": "Replace sample/training instructions with the future generated integration guide.",
    "sample.yaml": "Upstream sample test metadata is evidence, not standalone application input.",
    "train/README.md": "Training is outside the runtime-only demo.",
    "train/train_hello_world_model.ipynb": "Training notebooks are outside the runtime-only demo.",
    "images/model_architecture.png": "Documentation image is unnecessary for runtime compilation.",
}
EXPECTED = set(RUNTIME) | set(OMITTED) | {"CMakeLists.txt", "prj.conf"}
REQUIRED = set(RUNTIME) | {"CMakeLists.txt", "prj.conf", "sample.yaml"}
CONFIG = {
    "CONFIG_CPP": "y", "CONFIG_STD_CPP17": "y", "CONFIG_TENSORFLOW_LITE_MICRO": "y",
    "CONFIG_MAIN_STACK_SIZE": "2048", "CONFIG_REQUIRES_FLOAT_PRINTF": "y",
}
CMAKE = """cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(tensorflow_hello_world)
set(NO_THREADSAFE_STATICS $<TARGET_PROPERTY:compiler-cpp,no_threadsafe_statics>)
zephyr_compile_options($<$<COMPILE_LANGUAGE:CXX>:${NO_THREADSAFE_STATICS}>)
file(GLOB app_sources src/*)
target_sources(app PRIVATE ${app_sources})"""
C_TOKENS = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.DOTALL)


class InspectionError(ValueError):
    """Unsafe, changing, excessive or unrecognised local source evidence."""


def _absolute(value):
    path = Path(value).expanduser()
    if ".." in path.parts or "\0" in str(path):
        raise InspectionError("Paths must not contain traversal or NUL characters.")
    return Path(os.path.abspath(path))


def _relative(value):
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value:
        raise InspectionError("Expected a bounded relative POSIX path.")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in value.split("/")):
        raise InspectionError("Relative paths must not contain traversal.")
    if any(ord(char) < 32 for char in value):
        raise InspectionError("Paths must not contain control characters.")
    return str(path)


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode


def _fingerprint(info):
    return _identity(info) + (info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _safe_open(path, *, directory=False):
    """Pin every ancestor; reject symlinks and replacements before and after reads."""
    path = _absolute(path)
    if len(path.parts) > 64:
        raise InspectionError("Path exceeds the bounded ancestor depth.")
    opened = []
    try:
        parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened.append((parent, None, None, os.fstat(parent)))
        for index, name in enumerate(path.parts[1:]):
            is_dir = index < len(path.parts) - 2 or directory
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if is_dir:
                flags |= os.O_DIRECTORY
            child = os.open(name, flags, dir_fd=parent)
            info = os.fstat(child)
            opened.append((child, parent, name, info))
            if not (stat.S_ISDIR(info.st_mode) if is_dir else stat.S_ISREG(info.st_mode)):
                raise InspectionError("Only regular files and directories are accepted.")
            parent = child
        yield parent
        for descriptor, ancestor, name, before in reversed(opened):
            current = os.fstat(descriptor)
            if _identity(current) != _identity(before):
                raise InspectionError("An inspected path changed identity.")
            if ancestor is not None:
                linked = os.stat(name, dir_fd=ancestor, follow_symlinks=False)
                if _identity(linked) != _identity(before):
                    raise InspectionError("An inspected path was replaced during inspection.")
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise
        raise InspectionError(f"Unsafe or unreadable path: {path}: {exc.strerror}") from exc
    finally:
        for descriptor, _, _, _ in reversed(opened):
            os.close(descriptor)


def _read(path, *, limit=MAX_METADATA_BYTES, retain=True):
    with _safe_open(path) as descriptor:
        before = os.fstat(descriptor)
        if before.st_size > limit:
            raise InspectionError(f"File exceeds the {limit}-byte inspection limit: {path}")
        digest = hashlib.sha256()
        size = 0
        chunks = []
        while True:
            chunk = os.read(descriptor, min(65536, limit - size + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise InspectionError(f"File grew beyond its inspection limit: {path}")
            digest.update(chunk)
            if retain:
                chunks.append(chunk)
        if size != before.st_size or _fingerprint(os.fstat(descriptor)) != _fingerprint(before):
            raise InspectionError(f"File changed during inspection: {path}")
    return {"bytes": size, "sha256": digest.hexdigest()}, b"".join(chunks)


def _text(data, path):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InspectionError(f"Expected UTF-8 text: {path}") from exc


def _yaml(data, path):
    try:
        value = yaml.safe_load(_text(data, path))
    except yaml.YAMLError as exc:
        raise InspectionError(f"Unsupported YAML metadata: {path}") from exc
    seen = set()
    remaining = 20000

    def check(item, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 24:
            raise InspectionError(f"YAML metadata exceeds structural limits: {path}")
        if isinstance(item, (dict, list)):
            if id(item) in seen:
                raise InspectionError(f"YAML aliases are not supported: {path}")
            seen.add(id(item))
            for child in (list(item.keys()) + list(item.values()) if isinstance(item, dict) else item):
                check(child, depth + 1)
        elif not isinstance(item, (str, int, float, bool, type(None))) or (
            isinstance(item, float) and not math.isfinite(item)
        ):
            raise InspectionError(f"Unsupported YAML scalar: {path}")
    check(value)
    if not isinstance(value, dict):
        raise InspectionError(f"Expected a YAML mapping: {path}")
    return value


def _inventory(base):
    files, directories, total = [], {}, 0

    def visit(relative="", depth=0):
        nonlocal total
        if depth > 4:
            raise InspectionError("Sample directory exceeds the supported depth.")
        with _safe_open(base / relative, directory=True) as descriptor:
            before = os.fstat(descriptor)
            directories[relative] = _fingerprint(before)
            names = []
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    if len(names) >= MAX_FILES:
                        raise InspectionError("Sample directory exceeds the entry limit.")
                    names.append(entry.name)
            for name in sorted(names):
                child = _relative(f"{relative}/{name}" if relative else name)
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    if child not in {"src", "train", "images"}:
                        raise InspectionError(f"Unsupported source directory: {child}")
                    visit(child, depth + 1)
                elif stat.S_ISREG(info.st_mode):
                    if len(files) >= MAX_FILES:
                        raise InspectionError("Sample exceeds the file count limit.")
                    total += info.st_size
                    if total > MAX_SAMPLE_BYTES:
                        raise InspectionError("Sample exceeds the total byte limit.")
                    files.append(child)
                    directories[child] = _fingerprint(info)
                else:
                    raise InspectionError(f"Symlinks and special files are unsupported: {child}")
            if _fingerprint(os.fstat(descriptor)) != _fingerprint(before):
                raise InspectionError("Sample directory changed during enumeration.")
    visit()
    return sorted(files), directories


def _compact_cmake(text):
    if "#[" in text:
        raise InspectionError("Bracket comments are outside the recognised CMake layout.")
    return re.sub(r"\s+", "", "\n".join(line.split("#", 1)[0] for line in text.splitlines()))


def _includes(path, text):
    if re.search(r"\\\r?\n|\?\?[=/]|(?m:^[ \t]*%:)", text):
        raise InspectionError(f"Unsupported preprocessor syntax in {path}.")
    clean = C_TOKENS.sub(lambda match: "\n" * match.group().count("\n")
                        if match.group().startswith(("/*", "//")) else match.group(), text)
    if re.search(r"\b(?:__has_include|__asm__|__asm|asm|_Pragma)\b|/incbin/", clean):
        raise InspectionError(f"Unsupported external-input syntax in {path}.")
    for line in clean.splitlines():
        directive = re.match(r"\s*#\s*(include\w*|import|embed|pragma)\b(.*)", line)
        if not directive:
            continue
        if directive[1] != "include":
            raise InspectionError(f"Unsupported preprocessor dependency in {path}: {directive[1]}")
        value = directive[2].strip()
        quoted = re.fullmatch(r'"([^"\\]+)"', value)
        angled = re.fullmatch(r"<([^<>]+)>", value)
        if not quoted and not angled:
            raise InspectionError(f"Dynamic or malformed include in {path}.")
        yield '"' if quoted else "<", _relative((quoted or angled)[1])


def _runtime_plan(texts):
    if _compact_cmake(texts["CMakeLists.txt"]) != _compact_cmake(CMAKE):
        raise InspectionError("Unsupported CMake layout; source CMake was not executed.")
    settings = {}
    for line in texts["prj.conf"].splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            if "=" not in line:
                raise InspectionError("Unsupported prj.conf statement.")
            key, value = line.split("=", 1)
            if key in settings:
                raise InspectionError(f"Duplicate configuration setting: {key}")
            settings[key] = value
    if settings != CONFIG:
        raise InspectionError("Unsupported runtime configuration; reference C++17 settings changed.")
    main = texts["src/main.c"]
    bridge = texts["src/main_functions.h"]
    inference = texts["src/main_functions.cpp"]
    if not all(re.search(pattern, main) for pattern in (r"\bint\s+main\s*\(", r"\bsetup\s*\(\s*\)\s*;",
                                                       r"\bloop\s*\(\s*\)\s*;")):
        raise InspectionError("Unsupported C entrypoint; expected the setup/loop bridge.")
    if 'extern "C"' not in bridge:
        raise InspectionError("C/C++ linkage bridge is missing.")
    required_tokens = ("tflite::GetModel(g_model)", "MicroMutableOpResolver", "AddFullyConnected()",
                       "static tflite::MicroInterpreter", "AllocateTensors()", "interpreter->Invoke()")
    if any(token not in inference for token in required_tokens):
        raise InspectionError("Unsupported TFLM interpreter/resolver layout.")
    model = re.search(r"alignas\((\d+)\)\s+const unsigned char g_model\[\]\s*=\s*\{([^}]+)\};",
                      texts["src/model.cpp"])
    length = re.search(r"const int g_model_len\s*=\s*(\d+)\s*;", texts["src/model.cpp"])
    if not model or not length or int(model[1]) != 8:
        raise InspectionError("Unsupported model declaration/alignment.")
    tokens = [part.strip() for part in model[2].split(",") if part.strip()]
    if any(not re.fullmatch(r"0x[0-9a-fA-F]{2}", token) for token in tokens):
        raise InspectionError("Model is not the recognised textual byte array.")
    if len(tokens) != int(length[1]) or [int(token, 16) for token in tokens[4:8]] != list(b"TFL3"):
        raise InspectionError("Model length or TFLite file identifier is inconsistent.")
    includes = []
    headers = {path.removeprefix("src/") for path in RUNTIME}
    for path in RUNTIME:
        for delimiter, name in _includes(path, texts[path]):
            if delimiter == '"' and name not in headers:
                raise InspectionError(f"Unresolved local include in {path}: {name}")
            if delimiter == "<" and not name.startswith("tensorflow/lite/"):
                raise InspectionError(f"Unrecognised external include in {path}: {name}")
            includes.append({"path": path, "include": name,
                             "provider": "sample" if delimiter == '"' else "tflite-micro"})
    sample = _yaml(texts["sample.yaml"].encode(), "sample.yaml")
    common = sample.get("common")
    tests = sample.get("tests", {})
    if not isinstance(common, dict) or not isinstance(tests, dict):
        raise InspectionError("Unsupported sample common/tests metadata mappings.")
    modules = common.get("modules")
    reference = tests.get("sample.tensorflow.helloworld")
    if (not isinstance(reference, dict) or not isinstance(modules, list)
            or any(not isinstance(module, str) for module in modules) or "tflite-micro" not in modules):
        raise InspectionError("Unsupported sample test metadata for the reference variant.")
    targets = reference.get("platform_allow", [])
    integration = reference.get("integration_platforms", [])
    if (not isinstance(targets, list) or any(not isinstance(target, str) for target in targets)
            or not isinstance(integration, list) or any(not isinstance(target, str) for target in integration)):
        raise InspectionError("Unsupported declared reference target list.")
    if reference.get("filter") is not None and not isinstance(reference["filter"], str):
        raise InspectionError("Unsupported declared reference filter.")
    return {
        "entrypoint": {"path": "src/main.c", "language": "C", "bridge": "src/main_functions.h",
                       "inference": "src/main_functions.cpp", "runtime_language": "C++17"},
        "model": {"path": "src/model.cpp", "header": "src/model.hpp", "bytes": len(tokens),
                  "alignment": 8, "format": "TFLite textual byte array", "validation": "identifier-and-length-only"},
        "cmake": {"action": "replace-glob-with-explicit-runtime-sources", "runtime_sources": list(RUNTIME),
                  "preserve_static_initialization_flags": True,
                  "static_initialization": "compiler-cpp/no_threadsafe_statics for CXX only",
                  "executed": False},
        "configuration": settings, "includes": includes,
        "declared_reference_targets": targets,
        "declared_integration_targets": integration,
        "declared_filter": reference.get("filter"),
        "validation": {"configuration": "not-checked", "build": "not-checked", "hardware": "not-checked"},
    }


def _license_evidence(base, texts):
    record, content = _read(base / "LICENSE")
    license_text = _text(content, "LICENSE")
    if "Apache License" not in license_text or "Version 2.0" not in license_text:
        raise InspectionError("Selected Zephyr LICENSE is not the recognised Apache-2.0 evidence.")
    evidence = [{"path": "LICENSE", **record, "identifier": "Apache-2.0", "action": "retain-license-text"}]
    for path in (*RUNTIME, "prj.conf"):
        text = texts[path]
        if "Licensed under the Apache License, Version 2.0" not in text and "SPDX-License-Identifier: Apache-2.0" not in text:
            raise InspectionError(f"Missing recognised license notice: {path}")
        evidence.append({"path": f"{SAMPLE}/{path}", "identifier": "Apache-2.0", "action": "retain-notice"})
    return evidence


def _manifest(base):
    pending, visited, evidence, matches = ["west.yml"], set(), [], []
    while pending:
        relative = _relative(pending.pop(0))
        if relative in visited:
            raise InspectionError("Repeated/cyclic local manifest import.")
        if len(visited) >= MAX_MANIFESTS:
            raise InspectionError("Manifest import count exceeds its limit.")
        visited.add(relative)
        record, data = _read(base / relative)
        evidence.append({"path": relative, **record})
        manifest = _yaml(data, relative).get("manifest")
        if not isinstance(manifest, dict):
            raise InspectionError(f"Unsupported manifest mapping: {relative}")
        projects = manifest.get("projects", [])
        if not isinstance(projects, list):
            raise InspectionError("Unsupported manifest projects list.")
        for project in projects:
            if isinstance(project, dict) and project.get("name") == "tflite-micro":
                path = _relative(project.get("path"))
                revision = project.get("revision")
                if not isinstance(revision, str) or not revision or len(revision) > 128:
                    raise InspectionError("TFLM revision must be explicitly declared.")
                matches.append({"path": path, "revision": revision, "groups": project.get("groups", []),
                                "manifest_path": relative})
        own = manifest.get("self", {})
        imports = own.get("import", []) if isinstance(own, dict) else []
        if isinstance(imports, str):
            imports = [imports]
        if not isinstance(imports, list) or any(not isinstance(item, str) for item in imports):
            raise InspectionError("Only bounded local string self-imports are supported.")
        for item in imports:
            item = _relative(item)
            try:
                with _safe_open(base / item, directory=True) as descriptor:
                    names = []
                    with os.scandir(descriptor) as entries:
                        for entry in entries:
                            if len(names) >= MAX_MANIFESTS:
                                raise InspectionError("Manifest import directory exceeds its limit.")
                            names.append(entry.name)
                    pending.extend(f"{item}/{name}" for name in sorted(names) if name.endswith((".yml", ".yaml")))
            except InspectionError:
                if Path(item).suffix not in (".yml", ".yaml"):
                    raise
                pending.append(item)
    if len(matches) > 1:
        raise InspectionError("Ambiguous TFLM declarations in local manifests.")
    return {"status": "declared" if matches else "not-declared", "evidence": evidence,
            **(matches[0] if matches else {})}


def _git(base):
    environment = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    prefix = ["git", "--no-pager", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
              "-C", str(base)]
    try:
        root = subprocess.run([*prefix, "rev-parse", "--show-toplevel"], cwd=base, env=environment, timeout=3,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        if root.returncode or root.stdout.decode().strip() != str(base):
            return {"status": "unavailable"}
        head = subprocess.run([*prefix, "rev-parse", "--verify", "HEAD"], cwd=base, env=environment, timeout=3,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
        revision = head.stdout.decode().strip()
        if head.returncode or not re.fullmatch(r"[a-f0-9]{40,64}", revision):
            return {"status": "unavailable"}
        dirty = subprocess.run([*prefix, "diff-index", "--quiet", "--no-ext-diff", "HEAD", "--"],
                               cwd=base, env=environment, timeout=3, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, check=False)
        return {"status": "observed", "head": revision,
                "tracked_changes": {0: False, 1: True}.get(dirty.returncode), "untracked_files": "not-checked"}
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return {"status": "unavailable"}


def _installation(workspace, manifest):
    if workspace is None or manifest.get("status") != "declared":
        return {"status": "unknown", "reason": "Workspace or declared module path is unavailable."}
    module = workspace / manifest["path"]
    try:
        with _safe_open(module, directory=True):
            pass
    except FileNotFoundError:
        return {"status": "missing", "path": manifest["path"], "metadata_evidence": []}
    evidence = []
    for name in ("zephyr/module.yml", "zephyr/module.yaml"):
        try:
            record, content = _read(module / name)
        except FileNotFoundError:
            continue
        metadata = _yaml(content, name)
        evidence.append({"path": f"{manifest['path']}/{name}", **record,
                         "declared_name": metadata.get("name"), "build": metadata.get("build")})
    return {"status": "present" if evidence else "metadata-missing", "path": manifest["path"],
            "metadata_evidence": evidence, "git": _git(module),
            "revision_matches_pin": None,
            "readiness": "Metadata presence alone does not establish configuration or build compatibility."}


def _sdk(path):
    if path is None:
        return {"status": "not-selected"}
    with _safe_open(path, directory=True):
        pass
    for name in ("sdk_version", "VERSION"):
        try:
            record, data = _read(path / name, limit=4096)
        except FileNotFoundError:
            continue
        return {"status": "observed", "version": _text(data, name).strip(), "evidence": {"path": name, **record},
                "toolchain_execution": "not-checked"}
    return {"status": "unknown", "reason": "No bounded SDK version metadata found."}


def qualify_sources(zephyr_base, *, workspace=None, target=None, sdk=None):
    """Return hash-only source evidence. No build scripts or module tools are executed."""
    base = _absolute(zephyr_base)
    workspace = _absolute(workspace) if workspace is not None else None
    sdk = _absolute(sdk) if sdk is not None else None
    if target is not None and (not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_.@/+\-]{1,256}", target)):
        raise InspectionError("Target must be a bounded exact target string.")
    result = {
        "schema_version": 1,
        "demo": {"demo_id": "tflm-sine", "variant_id": "reference", "framework_id": "tflm",
                 "adapter_id": "tflm-sine-v1", "locator_id": "zephyr-tflm-hello-world"},
        "qualification": {"status": "unavailable", "reasons": []},
        "source": {"sample_path": SAMPLE, "files": [], "license_evidence": [], "plan": None},
        "zephyr": {"version": None, "git": {"status": "unavailable"}},
        "dependencies": {"tflite_micro": {"manifest": {"status": "unknown"}, "installation": {"status": "unknown"}}},
        "selection": {"target": {"requested": target, "declared_reference_target": None}, "sdk": {"status": "not-selected"}},
        "diagnostics": [],
    }
    try:
        with _safe_open(base, directory=True):
            pass
        version_record, version_data = _read(base / "VERSION", limit=4096)
        version = dict(re.findall(r"^(VERSION_MAJOR|VERSION_MINOR|PATCHLEVEL|VERSION_TWEAK|EXTRAVERSION)\s*=\s*(.*?)\s*$",
                                  _text(version_data, "VERSION"), re.M))
        result["zephyr"] = {"version": version, "version_evidence": {"path": "VERSION", **version_record}, "git": _git(base)}
        files, directories = _inventory(base / SAMPLE)
        texts = {}
        for relative in files:
            retain = relative in REQUIRED
            record, data = _read(base / SAMPLE / relative, limit=MAX_FILE_BYTES, retain=retain)
            action = "include" if relative in RUNTIME or relative == "prj.conf" else "transform" if relative == "CMakeLists.txt" else "omit"
            role = "model-data" if relative.startswith("src/model.") else "runtime" if relative in RUNTIME else "configuration" if relative == "prj.conf" else "build-plan" if relative == "CMakeLists.txt" else "documentation-or-training"
            reason = OMITTED.get(relative, "Preserve upstream runtime and license notices." if action == "include"
                                 else "Replace GLOB with the explicit reviewed runtime file list." if action == "transform"
                                 else "Unrecognised path; source qualification is unavailable.")
            result["source"]["files"].append({"path": relative, **record, "action": action, "role": role, "reason": reason})
            if retain:
                texts[relative] = _text(data, relative)
        unknown, missing = set(files) - EXPECTED, REQUIRED - set(files)
        if unknown or missing:
            raise InspectionError(f"Unsupported source layout; unknown={sorted(unknown)}, missing={sorted(missing)}")
        result["source"]["plan"] = _runtime_plan(texts)
        result["source"]["license_evidence"] = _license_evidence(base, texts)
        again, after = _inventory(base / SAMPLE)
        if again != files or after != directories:
            raise InspectionError("Sample layout changed during inspection.")
        encoded_inventory = json.dumps(result["source"]["files"], sort_keys=True,
                                       separators=(",", ":")).encode("utf-8")
        result["source"]["inventory_sha256"] = hashlib.sha256(encoded_inventory).hexdigest()
        result["qualification"]["status"] = "source-qualified"
    except FileNotFoundError as exc:
        result["qualification"]["reasons"].append(f"Required local source is absent: {exc.filename}")
    except (InspectionError, RecursionError) as exc:
        result["qualification"] = {"status": "unsupported", "reasons": [str(exc)]}
    try:
        manifest = _manifest(base)
        installation = _installation(workspace, manifest)
        if installation.get("git", {}).get("head"):
            installation["revision_matches_pin"] = installation["git"]["head"] == manifest.get("revision")
        result["dependencies"]["tflite_micro"] = {"manifest": manifest, "installation": installation}
    except (FileNotFoundError, InspectionError, RecursionError) as exc:
        result["diagnostics"].append(f"Module evidence unavailable: {exc}")
    plan = result["source"]["plan"]
    if plan and target:
        result["selection"]["target"]["declared_reference_target"] = target in plan["declared_reference_targets"]
    try:
        result["selection"]["sdk"] = _sdk(sdk)
    except (FileNotFoundError, InspectionError) as exc:
        result["selection"]["sdk"] = {"status": "unavailable", "reason": str(exc)}
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zephyr-base", required=True, help="Selected local Zephyr checkout")
    parser.add_argument("--workspace", help="Explicit west workspace; module paths come only from its selected manifest")
    parser.add_argument("--target", help="Exact target to compare with upstream reference test declarations")
    parser.add_argument("--sdk", help="Optional selected SDK directory (version metadata only)")
    parser.add_argument("--output", help="Create a new JSON report; defaults to stdout")
    args = parser.parse_args(argv)
    try:
        result = qualify_sources(args.zephyr_base, workspace=args.workspace, target=args.target, sdk=args.sdk)
        payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if args.output:
            destination = _absolute(args.output)
            protected = [_absolute(args.zephyr_base)]
            manifest = result["dependencies"]["tflite_micro"]["manifest"]
            if args.workspace and manifest.get("path"):
                protected.append(_absolute(args.workspace) / manifest["path"])
            if any(destination == root or root in destination.parents for root in protected):
                raise InspectionError("Report output must be outside Zephyr and module source directories.")
            with _safe_open(destination.parent, directory=True) as parent:
                descriptor = os.open(destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=parent)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(payload)
        else:
            sys.stdout.write(payload)
        return 0 if result["qualification"]["status"] == "source-qualified" else 2
    except (OSError, InspectionError) as exc:
        print(f"Qualification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
