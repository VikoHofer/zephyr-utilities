"""Bundled MNIST application template with independently installed runtime assets.

Creation never reads another application. Trained assets and the framework live in
Projects-owned installation data; reviewed projects receive their own model bytes.
"""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

import yaml

from .creation import open_directory, path_identity, validate_identity
from .default_overlay import ARCHIVED_CMAKE, add_default_overlay
from .probing import check_cancel


SAMPLE_ID = "tinyml/mnist-shell"
SOURCE_PATH = "bundled:tinyml/mnist"
TEMPLATE_ROOT = Path(__file__).parent / "mnist_template"
TITLE = "MNIST digit recognition"
DESCRIPTION = "Recognize embedded handwritten digits using TensorFlow Lite Micro and the Zephyr shell."
MODULE_REVISION = "fcc760af130f3a595b5802cdebcc77461e54f382"
_TEXT = (
    "prj.conf", "README.rst", "NOTICE.rst", "LICENSE",
    "src/main.cpp", "src/inference.cpp", "src/inference.hpp", "src/assets.cpp", "src/assets.hpp",
    "boards/nucleo_h753zi.conf",
)
_ASSET_TEXT = ("model/model_metadata.hpp", "model/metadata.json", "data/test_samples.inc", "data/NOTICE.rst", "LICENSE")
_BINARY = {"model/mnist.tflite": "model/model.inc", "data/test_images.bin": "data/images.inc"}
_REQUIREMENTS = [
    "Sensor-free CPU inference: C++17, floating-point support and a working Zephyr serial shell/console.",
    "The Float32 model occupies about 200 KiB of ROM; allow a 16 KiB tensor arena, two 8 KiB stacks and runtime overhead.",
    "Uses the independently installed TensorFlow Lite Micro framework and MNIST asset pack; creation downloads nothing.",
    "Choose any discovered exact target and SDK, then Check compatibility and Build. Board listing alone does not establish support.",
    "Configure checks framework and console settings. Compilation, physical serial operation and predictions are separate checks.",
]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    return _sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _signature(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_at(directory, name, limit):
    before = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError(f"MNIST input must be a bounded regular file, never a link: {name}")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        if _signature(os.fstat(descriptor)) != _signature(before):
            raise ValueError("MNIST input changed during inspection. Refresh samples.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if (len(data) > limit or _signature(os.fstat(descriptor)) != _signature(before) or
                _signature(os.stat(name, dir_fd=directory, follow_symlinks=False)) != _signature(before)):
            raise ValueError("MNIST input changed during inspection. Refresh samples.")
        return data
    finally:
        os.close(descriptor)


def _capture_files(root, paths):
    identity = path_identity(root)
    contents = {}
    parents = {}
    for path in paths:
        parent = root / Path(path).parent
        parents.setdefault(parent, path_identity(parent))
        descriptor = open_directory(parent, parents[parent])
        try:
            contents[path] = _read_at(descriptor, Path(path).name, 1024 * 1024)
        finally:
            os.close(descriptor)
    validate_identity(root, identity)
    for parent, observed in parents.items():
        validate_identity(parent, observed)
    return contents, identity


def _module(root, cancel):
    """Bind actual local dependency bytes, including edits beneath a pinned HEAD."""
    identity = path_identity(root)
    revision_file = ".git/HEAD" if (root / ".git").exists() else "REVISION"
    selected, _ = _capture_files(root, ("zephyr/module.yml", "LICENSE", revision_file))
    if selected[revision_file].decode("ascii").strip() != MODULE_REVISION:
        raise ValueError("This MNIST adapter requires its reviewed pinned TensorFlow Lite Micro revision; the dependency was preserved.")
    try:
        metadata = yaml.safe_load(selected["zephyr/module.yml"])
        understood = metadata == {"name": "tflite-micro", "build": {"cmake-ext": True, "kconfig-ext": True}}
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        raise ValueError("The MNIST firmware dependency metadata is invalid.") from exc
    if not understood:
        raise ValueError("The local TensorFlow Lite Micro module metadata needs a newer MNIST adapter.")
    records, count, total = [], 0, 0

    def visit(directory, relative, depth):
        nonlocal count, total
        check_cancel(cancel)
        if depth > 24:
            raise ValueError("The MNIST firmware dependency exceeds the supported directory depth.")
        before = os.fstat(directory)
        names = sorted(os.listdir(directory))
        for name in names:
            if not relative and name == ".git":
                continue
            count += 1
            if count > 20000:
                raise ValueError("The MNIST firmware dependency exceeds the supported entry limit.")
            path = f"{relative}/{name}" if relative else name
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    if _signature(os.fstat(child)) != _signature(info):
                        raise ValueError("MNIST dependency changed during inspection.")
                    visit(child, path, depth + 1)
                finally:
                    os.close(child)
            else:
                data = _read_at(directory, name, 4 * 1024 * 1024)
                total += len(data)
                if total > 80 * 1024 * 1024:
                    raise ValueError("The MNIST firmware dependency exceeds the supported byte limit.")
                records.append({"path": path, "bytes": len(data), "sha256": _sha(data)})
        if _signature(os.fstat(directory)) != _signature(before) or sorted(os.listdir(directory)) != names:
            raise ValueError("MNIST dependency changed during inspection.")

    descriptor = open_directory(root, identity)
    try:
        visit(descriptor, "", 0)
        previous = records
        records, count, total = [], 0, 0
        visit(descriptor, "", 0)
        if previous != records:
            raise ValueError("MNIST dependency changed during inspection.")
    finally:
        os.close(descriptor)
    validate_identity(root, identity)
    if selected != _capture_files(root, selected)[0]:
        raise ValueError("MNIST dependency metadata changed during inspection.")
    return {"name": "tflite-micro", "path": str(root), "revision": MODULE_REVISION,
            "fingerprint": _digest(records), "file_count": len(records), "directory_identity": identity,
            "metadata_sha256": _sha(selected["zephyr/module.yml"]), "license_sha256": _sha(selected["LICENSE"])}


def validate_mnist_dependency(provenance, *, cancel=None):
    """Withhold original requirement claims after external runtime bytes change."""
    module = provenance.get("module") if isinstance(provenance, dict) else None
    if not isinstance(module, dict) or not isinstance(module.get("path"), str):
        raise ValueError("The saved MNIST firmware dependency evidence is unavailable.")
    path = Path(module["path"])
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("The saved MNIST dependency path is invalid.")
    if _module(path, cancel) != module:
        raise ValueError("The external MNIST firmware dependency changed; its original requirements are unverified.")


def _text(data, path):
    try:
        value = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"MNIST runtime text is not UTF-8: {path}") from exc
    if any(ord(character) < 32 and character not in "\t\r\n" for character in value):
        raise ValueError(f"MNIST runtime text contains control data: {path}")
    return value


def _hex(data):
    # Five characters per byte keeps this model below the existing 1 MiB file
    # publication limit, while retaining reviewable, lossless text-only writes.
    return "\n".join("".join(f"0x{value:02x}," for value in data[start:start + 32])
                     for start in range(0, len(data), 32)) + "\n"


def _runtime_inputs(contents):
    """Admit static C++/framework includes without relaxing the classic C adapter."""
    from .samples import _safe_path, _without_c_comments

    external = {"cerrno", "cmath", "cstring", "cstddef", "cstdint", "zephyr/kernel.h",
                "zephyr/shell/shell.h", "zephyr/sys/crc.h", "flatbuffers/verifier.h",
                "tensorflow/lite/micro/micro_interpreter.h", "tensorflow/lite/micro/micro_mutable_op_resolver.h",
                "tensorflow/lite/schema/schema_generated.h"}
    for path, content in contents.items():
        if Path(path).suffix in {".cpp", ".hpp", ".inc"}:
            if re.search(r"\\\r?\n|\?\?[=/]|(?m:^[ \t]*%:)", content):
                raise ValueError(f"Unsupported MNIST preprocessor syntax: {path}")
            clean = _without_c_comments(content)
            if re.search(r"\b(?:__has_include|__asm__|__asm|asm|_Pragma)\b|/incbin/", clean):
                raise ValueError(f"Unsupported MNIST external-input syntax: {path}")
            for line in clean.splitlines():
                directive = re.match(r"\s*#\s*(include\w*|import|embed|pragma)\b(.*)", line)
                if not directive:
                    continue
                kind, value = directive.groups()
                if kind == "pragma" and value.strip() == "once":
                    continue
                local = re.fullmatch(r'\s*"([^"\\]+)"\s*', value)
                angled = re.fullmatch(r"\s*<([^<>]+)>\s*", value)
                if kind != "include" or not (local or angled):
                    raise ValueError(f"MNIST needs literal, reviewed header includes: {path}")
                target = (local or angled).group(1)
                _safe_path(target)
                if angled and target not in external:
                    raise ValueError(f"Unsupported MNIST framework/header dependency: {target}")
                if local:
                    candidates = {(Path(path).parent / target).as_posix(),
                                  *((Path(directory) / target).as_posix() for directory in ("src", "model", "data"))}
                    if not candidates.intersection(contents):
                        raise ValueError(f"MNIST header is outside the reviewed runtime or missing: {target}")
        elif Path(path).suffix == ".conf":
            for line in content.splitlines():
                if line.strip() and not line.lstrip().startswith("#") and (re.search(r'\$(?![ \t"]|$)', line) or not re.fullmatch(
                        r'\s*CONFIG_[A-Z0-9_]+=(?:[ynm]|-?[0-9]+|0x[0-9A-Fa-f]+|"[^"\n]*")\s*', line)):
                    raise ValueError(f"Unsupported MNIST configuration input: {path}")


def _runtime_metadata(contents, metadata):
    """Keep the displayed identity and fixed reference dimensions tied to the assets."""
    from .samples import _without_c_comments

    header = _without_c_comments(contents["model/model_metadata.hpp"])
    model_hash = re.search(r'\bkModelSha256\[\]\s*=\s*"([a-f0-9]{64})"\s*;', header)
    accuracy = re.search(r"\bkTestAccuracy\s*=\s*([0-9.eE+-]+)f\s*;", header)
    expected = metadata.get("test_accuracy")
    if (model_hash is None or model_hash.group(1) != metadata["model_sha256"] or accuracy is None or
            type(expected) not in {int, float} or not math.isfinite(expected) or not 0 <= expected <= 1 or
            not math.isclose(float(accuracy.group(1)), expected, rel_tol=0, abs_tol=1e-7)):
        raise ValueError("The MNIST runtime model identity or reported host accuracy does not match its metadata.")
    for name, value in (("kWidth", "28"), ("kClasses", "10"), ("kSampleCount", "30"),
                        ("kPixels", r"kWidth\s*\*\s*kWidth")):
        if not re.search(rf"\b{name}\s*=\s*{value}\s*;", _without_c_comments(contents["src/assets.hpp"])):
            raise ValueError("The MNIST runtime dimensions need a newer adapter.")
    if not re.search(r"\bkArenaSize\s*=\s*16\s*\*\s*1024\s*;", _without_c_comments(contents["src/inference.hpp"])):
        raise ValueError("The MNIST tensor arena needs a newer requirement adapter.")
    samples = _without_c_comments(contents["data/test_samples.inc"])
    pattern = r"\s*\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*\{([^{}]*)\}\s*\}\s*,"
    rows = list(re.finditer(pattern, samples))
    if len(rows) != 30 or re.sub(pattern, "", samples).strip():
        raise ValueError("The MNIST reference sample layout needs a newer adapter.")
    indices, correct = [], 0
    for row in rows:
        index, label, prediction = map(int, row.groups()[:3])
        scores = row.group(4).split(",")
        if (label > 9 or prediction > 9 or len(scores) != 10 or
                any(not re.fullmatch(r"\s*[0-9.eE+-]+f\s*", value) for value in scores)):
            raise ValueError("The MNIST reference sample scores or class identities are unsupported.")
        values = [float(value.strip()[:-1]) for value in scores]
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise ValueError("The MNIST reference scores must be finite probabilities.")
        indices.append(index)
        correct += label == prediction
    if indices != metadata["embedded_test_indices"] or metadata.get("embedded_correct") != correct:
        raise ValueError("The MNIST reference samples do not match the embedded model metadata.")


def _cmake(module_path, name):
    # Bracket arguments carry filesystem text literally, without CMake expansion.
    if any(ord(character) < 32 or character == ";" for character in module_path):
        raise ValueError("The MNIST module path cannot contain control characters or semicolons.")
    equals = "="
    while f"]{equals}]" in module_path:
        equals += "="
    literal = f"[{equals}[{module_path}]{equals}]"
    return (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "# Zephyr Utilities MNIST template with an independently installed framework.\n"
        "cmake_minimum_required(VERSION 3.20.0)\n"
        f"set(MNIST_TFLM_DIR {literal} CACHE PATH \"Reviewed local TensorFlow Lite Micro dependency\")\n"
        "if(NOT EXISTS \"${MNIST_TFLM_DIR}/zephyr/module.yml\")\n"
        "  message(FATAL_ERROR \"The reviewed local TensorFlow Lite Micro dependency is missing.\")\n"
        "endif()\n"
        "list(APPEND ZEPHYR_EXTRA_MODULES \"${MNIST_TFLM_DIR}\")\n"
        "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n"
        f"project({name})\n"
        "set(NO_THREADSAFE_STATICS $<TARGET_PROPERTY:compiler-cpp,no_threadsafe_statics>)\n"
        "zephyr_compile_options($<$<COMPILE_LANGUAGE:CXX>:${NO_THREADSAFE_STATICS}>)\n"
        "target_include_directories(app PRIVATE src model data)\n"
        "target_sources(app PRIVATE src/main.cpp src/inference.cpp src/assets.cpp)\n"
    )


def preview_mnist(snapshot, recipe, *, profile=None, cancel=None, assets_root=None, name=None):
    """Render bundled source and installed assets without consulting applications."""
    from .samples import _require_reader, _zephyr

    _require_reader()
    check_cancel(cancel)
    zephyr = _zephyr(snapshot)
    root = TEMPLATE_ROOT
    if assets_root is None:
        raise ValueError("MNIST runtime assets are not installed. Install an MNIST asset pack and TensorFlow Lite Micro with zephyr-utils --install-mnist-assets PACK --tflm-source FRAMEWORK before creating this demo.")
    assets = Path(assets_root)
    if not assets.is_absolute() or ".." in assets.parts:
        raise ValueError("The installed MNIST asset location must be an absolute canonical directory.")
    application_name = "mnist_demo" if name is None else name
    if not isinstance(application_name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", application_name):
        raise ValueError("The MNIST application name is invalid.")
    raw, identity = _capture_files(root, _TEXT)
    asset_raw, asset_identity = _capture_files(assets, (*_ASSET_TEXT, *_BINARY))
    contents = {path: _text(raw[path], path) for path in _TEXT}
    contents.update({path: _text(asset_raw[path], path) for path in _ASSET_TEXT if path != "LICENSE"})
    contents["data/LICENSE-MODEL.txt"] = _text(asset_raw["LICENSE"], "LICENSE")
    try:
        metadata = json.loads(contents["model/metadata.json"])
    except (ValueError, RecursionError) as exc:
        raise ValueError("The MNIST model metadata is invalid.") from exc
    if not isinstance(metadata, dict):
        raise ValueError("The MNIST model metadata must be an object.")
    model, images = asset_raw["model/mnist.tflite"], asset_raw["data/test_images.bin"]
    if (len(model) < 8 or model[4:8] != b"TFL3" or metadata.get("model_sha256") != _sha(model) or
            metadata.get("model_bytes") != len(model) or metadata.get("images_sha256") != _sha(images) or
            len(images) != 30 * 28 * 28):
        raise ValueError("The MNIST model or image bytes do not match the reviewed metadata and runtime dimensions.")
    if (metadata.get("architecture") != [784, 64, 10] or metadata.get("dtype") != "float32" or
            not isinstance(metadata.get("embedded_test_indices"), list) or
            len(metadata["embedded_test_indices"]) != 30 or
            any(type(value) is not int or value < 0 for value in metadata["embedded_test_indices"]) or
            len(set(metadata["embedded_test_indices"])) != 30):
        raise ValueError("This MNIST model layout needs a newer runtime adapter.")
    _runtime_metadata(contents, metadata)
    if any(not re.search(r"Apache License\s+Version 2\.0", license_text)
           for license_text in (contents["LICENSE"], contents["data/LICENSE-MODEL.txt"])):
        raise ValueError("MNIST is missing the understood Apache 2.0 application or model license.")
    if "CC-BY-SA-3.0" not in contents["data/NOTICE.rst"] or "LeCun" not in contents["data/NOTICE.rst"]:
        raise ValueError("MNIST image attribution is missing; no dataset bytes were copied.")
    module = _module(assets / "framework/tflite-micro", cancel)
    for source, destination in _BINARY.items():
        contents[destination] = _hex(asset_raw[source])
    _runtime_inputs(contents)
    contents["CMakeLists.txt"] = _cmake(module["path"], application_name)
    records = [{"path": path, "bytes": len(data), "sha256": _sha(data)} for path, data in sorted(raw.items())]
    provenance = {"schema_version": 1, "adapter_version": 2, "adapter": "mnist-runtime-v2",
                  "id": SAMPLE_ID, "title": TITLE, "path": SOURCE_PATH, "zephyr": zephyr,
                  "source_root": str(root), "directory_identity": identity, "files": records,
                  "assets_root": str(assets), "assets_identity": asset_identity,
                  "asset_files": [{"path": path, "bytes": len(data), "sha256": _sha(data)}
                                  for path, data in sorted(asset_raw.items())],
                  "requirements": _REQUIREMENTS, "module": module,
                  "model": {"format": "tflite", "sha256": _sha(model), "bytes": len(model),
                            "metadata_sha256": _sha(asset_raw["model/metadata.json"]), "images_sha256": _sha(images)},
                  "licenses": [{"path": "LICENSE", "sha256": _sha(raw["LICENSE"])},
                               {"path": "data/NOTICE.rst", "sha256": _sha(asset_raw["data/NOTICE.rst"])},
                               {"path": "data/LICENSE-MODEL.txt", "sha256": _sha(asset_raw["LICENSE"])}],
                  "transformations": [{"source": source, "path": destination, "source_sha256": _sha(asset_raw[source]),
                                       "published_sha256": _sha(contents[destination].encode()),
                                       "reason": "Lossless binary to compact C initializer; no training or conversion."}
                                      for source, destination in _BINARY.items()],
                  "checkout_metadata": "bundled utility template and independently installed runtime assets"}
    provenance["fingerprint"] = _digest(provenance)
    source_fingerprint = provenance["fingerprint"]
    overlay = None
    if profile is not None:
        contents, overlay = add_default_overlay(contents, profile.get("target"), archive_cmake=True)
        provenance = {**provenance, "schema_version": 3, "default_overlay": overlay,
                      "overlay_files": [{"path": path, "sha256": _sha(contents[path].encode())}
                                        for path in ("CMakeLists.txt", overlay["path"], ARCHIVED_CMAKE)]}
    if profile is not None or name is not None:
        provenance.update(source_fingerprint=source_fingerprint, application_name=application_name)
        provenance.pop("fingerprint", None)
        provenance["fingerprint"] = _digest(provenance)
    contents["zephyr-sample.json"] = json.dumps(provenance, sort_keys=True, indent=2) + "\n"
    contents["ZEPHYR-SAMPLE.md"] = (
        f"# {application_name}: MNIST digit recognition\n\n"
        "Created from the MNIST template bundled with Zephyr Utilities. "
        "This application owns its runtime source, model and image bytes; no other application is read or linked.\n\n"
        "## Framework dependency\n\n"
        f"TensorFlow Lite Micro is installed separately at `{module['path']}`, revision `{module['revision']}`. "
        "This is a managed framework installation, independent of the source template and every application. "
        "Keep it available like the selected Zephyr checkout and SDK. "
        "For a different machine, install the same framework revision and set `MNIST_TFLM_DIR` to it.\n\n"
        "## Select a board\n\n"
        "Use Projects to add an exact-target build configuration and select its SDK. "
        "Each target must independently pass Configure and Build; neither step proves hardware operation.\n\n"
        + "\n".join(f"- {item}" for item in _REQUIREMENTS)
        + "\n\nThe model and embedded reference images are copied from the installed asset pack. "
        "Their hashes, license and attribution are retained. No training, downloads or model conversion run.\n\n"
        "## Configure and build\n\n"
        f"Working directory: `{recipe['cwd']}`.\n\n```sh\n{recipe['display_configure']}\n{recipe['display_build']}\n```\n\n"
        "Use the serial shell commands `ai info`, `ai list`, `ai show 0`, "
        "`ai infer 0` and `ai test` on a running target.\n"
    )
    if overlay:
        contents["ZEPHYR-SAMPLE.md"] += f"\nEditable default overlay for `{overlay['target']}`: `{overlay['path']}`.\n"
    contents[".gitignore"] = "/build/\n"
    if raw != _capture_files(root, raw)[0] or asset_raw != _capture_files(assets, asset_raw)[0]:
        raise ValueError("MNIST template or installed assets changed during inspection. Refresh samples.")
    check_cancel(cancel)
    files = [{"path": path, "content": content, "sha256": _sha(content.encode())} for path, content in sorted(contents.items())]
    if len(files) > 128 or any(len(item["content"].encode()) > 1024 * 1024 for item in files) or sum(
            len(item["content"].encode()) for item in files) > 4 * 1024 * 1024:
        raise ValueError("The MNIST runtime exceeds the existing reviewed publication bounds.")
    return {"files": files, "provenance": provenance}
