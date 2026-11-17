"""Bounded local sample cloning without executing imported build instructions."""

import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

import yaml

from .creation import open_directory, path_identity, validate_identity
from .default_overlay import ARCHIVED_CMAKE, add_default_overlay
from .probing import check_cancel
from .sample_adaptations import adapt_blinky


MAX_FILES = 128
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 8
_CATALOG = {
    "hello_world": ("Hello World", "Print a greeting using the local Zephyr sample."),
    "basic/blinky": ("Blinky", "Blink a GPIO LED using the local Zephyr sample."),
    "basic/button": ("Button", "Handle button input using the local Zephyr sample."),
}
_PART = re.compile(r"[A-Za-z0-9_+-][A-Za-z0-9_.+-]*\Z")
_FORBIDDEN = {"build", "out", "output", "dist", "target", "generated", "CMakeFiles", "node_modules",
              "__pycache__", "venv", "tools", "scripts", "sysbuild", "modules", "CMakeCache.txt"}
_SUFFIXES = {".c", ".h", ".conf", ".overlay", ".dtsi", ".rst", ".md", ".yaml", ".yml", ".txt"}
_RESERVED = {"LICENSE", "NOTICE", "REUSE.toml", "ZEPHYR-SAMPLE.md", "zephyr-sample.json", ".gitignore"}
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
_STANDARD_HEADERS = frozenset("assert.h complex.h ctype.h errno.h fenv.h float.h inttypes.h iso646.h limits.h "
                              "locale.h math.h setjmp.h signal.h stdalign.h stdarg.h stdatomic.h stdbool.h "
                              "stddef.h stdint.h stdio.h stdlib.h stdnoreturn.h string.h tgmath.h threads.h "
                              "time.h uchar.h wchar.h wctype.h".split())
_C_TOKENS = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.DOTALL)


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    return _hash(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _zephyr(snapshot):
    value = snapshot.get("zephyr") if isinstance(snapshot, dict) else None
    if not isinstance(value, dict) or not isinstance(value.get("path"), str):
        raise ValueError("Analyze a local Zephyr checkout before choosing a sample.")
    path = Path(value["path"])
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("The sample checkout must be an absolute path without parent traversal.")
    return {"path": str(path), "version": value.get("version"),
            "revision": value.get("commit", value.get("revision"))}


def _sample_id(value):
    if not isinstance(value, str) or value not in _CATALOG:
        raise ValueError("Choose one of the available local samples.")
    return value


def _require_reader():
    if (os.name != "posix" or any(not getattr(os, name, 0) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")) or
            os.open not in os.supports_dir_fd or os.stat not in os.supports_dir_fd):
        raise ValueError("Local sample cloning requires supported no-follow directory and file access on this host.")


def _safe_path(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or path.as_posix() != value or len(path.parts) > MAX_DEPTH or
            any(not _PART.fullmatch(part) or part in {".", ".."} for part in path.parts)):
        raise ValueError(f"Unsupported sample path: {value}")
    return path


def _identity(info):
    return {"device": info.st_dev, "inode": info.st_ino}


def _file_evidence(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read(descriptor, name):
    before = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"Sample inputs must be regular files, never links or special files: {name}")
    if before.st_size > MAX_FILE_BYTES:
        raise ValueError(f"Sample file exceeds the 1 MiB limit: {name}")
    opened = os.open(name, _READ_FLAGS, dir_fd=descriptor)
    try:
        actual = os.fstat(opened)
        if not stat.S_ISREG(actual.st_mode) or _identity(actual) != _identity(before):
            raise ValueError(f"Sample file changed while being read: {name}")
        with os.fdopen(opened, "rb", closefd=False) as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        after = os.fstat(opened)
        current = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        if _file_evidence(before) != _file_evidence(after) or _file_evidence(after) != _file_evidence(current):
            raise ValueError(f"Sample file changed while being read: {name}")
    finally:
        os.close(opened)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"Sample file exceeds the 1 MiB limit: {name}")
    try:
        content = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"Sample file is not UTF-8 text: {name}") from exc
    if any(ord(character) < 32 and character not in "\t\r\n" for character in content):
        raise ValueError(f"Sample file contains binary or control data: {name}")
    return content


def _tree(root, cancel=None):
    identity = path_identity(root)
    contents, directories = {}, []
    total = 0

    def visit(descriptor, relative):
        nonlocal total
        check_cancel(cancel)
        before = os.fstat(descriptor)
        names = sorted(os.listdir(descriptor))
        if not names:
            raise ValueError(f"Empty sample directories are unsupported: {relative or '.'}")
        if len(names) > MAX_FILES or len(directories) >= MAX_FILES:
            raise ValueError("Sample tree exceeds the supported entry limit.")
        directories.append({"path": relative or ".", **_identity(before)})
        for name in names:
            check_cancel(cancel)
            path = f"{relative}/{name}" if relative else name
            parsed = _safe_path(path)
            if name in _RESERVED or name.startswith(("build-", "build_", "cmake-build-")) or name in _FORBIDDEN:
                raise ValueError(f"Sample contains a generated, reserved or unsupported entry: {path}")
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                opened = os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
                try:
                    if _identity(os.fstat(opened)) != _identity(info):
                        raise ValueError("Sample directory changed during discovery.")
                    visit(opened, path)
                finally:
                    os.close(opened)
            elif stat.S_ISREG(info.st_mode):
                if (parsed.name == "CMakeLists.txt" and relative or
                        parsed.name != "CMakeLists.txt" and parsed.suffix not in _SUFFIXES):
                    raise ValueError(f"Sample contains an unsupported file: {path}")
                content = _read(descriptor, name)
                total += len(content.encode())
                if len(contents) >= MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise ValueError("Sample tree exceeds the supported file or byte limit.")
                contents[path] = content
            else:
                raise ValueError(f"Sample contains a symlink or special file: {path}")
        after = os.fstat(descriptor)
        if (_identity(before) != _identity(after) or before.st_mtime_ns != after.st_mtime_ns or
                before.st_ctime_ns != after.st_ctime_ns or sorted(os.listdir(descriptor)) != names):
            raise ValueError("Sample directory changed during discovery.")

    descriptor = open_directory(root, identity)
    try:
        visit(descriptor, "")
    finally:
        os.close(descriptor)
    validate_identity(root, identity)
    return contents, identity, directories


def _cmake(contents):
    content = contents.get("CMakeLists.txt", "")
    if "\\" in content or "#[" in content:
        raise ValueError("This sample uses unsupported CMake syntax; no build code was executed.")
    content = re.sub(r"(?m)#.*$", "", content)
    pattern = (r"\s*cmake_minimum_required\(\s*VERSION\s+\d+\.\d+(?:\.\d+)?\s*\)\s*"
               r"find_package\(\s*Zephyr\s+REQUIRED\s+HINTS\s+\$ENV\{ZEPHYR_BASE\}\s*\)\s*"
               r"project\(\s*[A-Za-z][A-Za-z0-9_-]*\s*\)\s*"
               r"target_sources\(\s*app\s+PRIVATE\s+([^()]*)\)\s*")
    match = re.fullmatch(pattern, content)
    if not match:
        raise ValueError("This sample's CMake dependencies are unsupported; only the simple local-source layout can be cloned.")
    sources = match.group(1).split()
    if not sources or len(sources) != len(set(sources)):
        raise ValueError("The sample must declare a distinct local source file list.")
    for source in sources:
        parsed = _safe_path(source)
        if parsed.suffix != ".c" or source not in contents:
            raise ValueError(f"The sample references a missing or unsupported source: {source}")


def _without_c_comments(content):
    return _C_TOKENS.sub(lambda match: "\n" * match.group().count("\n")
                         if match.group().startswith(("/*", "//")) else match.group(), content)


def _includes(contents, zephyr):
    for path, content in contents.items():
        suffix = PurePosixPath(path).suffix
        if suffix not in {".c", ".h", ".overlay", ".dtsi"}:
            continue
        if re.search(r"\\\r?\n|\?\?[=/]|(?m:^[ \t]*%:)", content):
            raise ValueError(f"Unsupported preprocessor syntax in {path}.")
        clean = _without_c_comments(content)
        if re.search(r"\b(?:__has_include|__asm__|__asm|asm|_Pragma)\b|/incbin/", clean):
            raise ValueError(f"Unsupported external-input syntax in {path}.")
        references = []
        for line in clean.splitlines():
            directive = re.match(r"\s*#\s*(include\w*|import|embed|pragma)\b(.*)", line)
            if directive:
                if directive.group(1) != "include":
                    raise ValueError(f"Unsupported preprocessor dependency in {path}.")
                value = directive.group(2).strip()
                quoted = re.fullmatch(r'"([^"\\]+)"', value)
                angled = re.fullmatch(r"<([^<>]+)>", value)
                if not quoted and not angled:
                    raise ValueError(f"Dynamic or malformed include in {path}.")
                references.append((quoted is not None, (quoted or angled).group(1)))
        for reference in re.finditer(r"/include/\s*(\S.*)", clean):
            match = re.fullmatch(r'"([^"\\]+)"\s*;?\s*', reference.group(1))
            if not match:
                raise ValueError(f"Unsupported devicetree include in {path}.")
            references.append((True, match.group(1)))
        for local, target in references:
            parsed = _safe_path(target)
            if local:
                resolved = (PurePosixPath(path).parent / parsed).as_posix()
                if resolved not in contents:
                    raise ValueError(f"Sample include is outside the copied source tree or missing: {path}: {target}")
            elif target not in _STANDARD_HEADERS:
                if not target.startswith("zephyr/"):
                    raise ValueError(f"Unsupported external header dependency: {path}: {target}")
                header = Path(zephyr["path"]) / "include" / target
                descriptor = open_directory(header.parent)
                try:
                    info = os.stat(header.name, dir_fd=descriptor, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError(f"Zephyr header must be a regular file: {target}")
                finally:
                    os.close(descriptor)
    for path, content in contents.items():
        if PurePosixPath(path).suffix == ".conf":
            for line in content.splitlines():
                if line.strip() and not line.lstrip().startswith("#"):
                    if "$" in line or not re.fullmatch(r'\s*CONFIG_[A-Z0-9_]+=(?:[ynm]|-?[0-9]+|0x[0-9A-Fa-f]+|"[^"\n]*")\s*', line):
                        raise ValueError(f"Unsupported configuration input in {path}.")


def _licenses(zephyr):
    root = Path(zephyr["path"])
    descriptor = open_directory(root)
    try:
        copied = {name: _read(descriptor, name) for name in ("LICENSE", "REUSE.toml")}
        try:
            os.stat("NOTICE", dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            copied["NOTICE"] = _read(descriptor, "NOTICE")
    finally:
        os.close(descriptor)
    if not re.search(r"Apache License\s+Version 2\.0, January 2004", copied["LICENSE"]):
        raise ValueError("This checkout's root license is not the supported Apache 2.0 license text.")
    reuse = re.sub(r"(?m)#.*$", "", copied["REUSE.toml"])
    match = re.fullmatch(r'\s*version\s*=\s*1\s*\[\[annotations\]\]\s*path\s*=\s*\[([^\]]*)\]\s*'
                         r'SPDX-License-Identifier\s*=\s*"Apache-2\.0"\s*'
                         r'SPDX-FileCopyrightText\s*=\s*"([^"\\\n]+)"\s*', reuse)
    if not match:
        raise ValueError("This checkout's REUSE license annotations need a newer sample adapter; no files were copied.")
    patterns = re.findall(r'"([A-Za-z0-9_./*?+-]+)"', match.group(1))
    if not patterns or not re.fullmatch(r'(?:\s*"[A-Za-z0-9_./*?+-]+"\s*,)*\s*(?:"[A-Za-z0-9_./*?+-]+"\s*,?)?\s*', match.group(1)):
        raise ValueError("Unsupported REUSE path annotations.")
    return copied, patterns, match.group(2)


def _requirements(sample_id, contents):
    if "prj.conf" not in contents or "src/main.c" not in contents or "README.rst" not in contents or "sample.yaml" not in contents:
        raise ValueError("The sample is missing its expected configuration, source, documentation or metadata.")
    try:
        metadata = yaml.safe_load(contents["sample.yaml"])
    except (yaml.YAMLError, ValueError, RecursionError) as exc:
        raise ValueError("The sample metadata is unsupported.") from exc
    if not isinstance(metadata, dict) or not isinstance(metadata.get("sample"), dict) or not isinstance(metadata.get("tests"), dict):
        raise ValueError("The sample metadata does not describe a supported sample.")
    if len(metadata["tests"]) > 128:
        raise ValueError("The sample metadata exceeds the supported test-entry limit.")
    for entry in [metadata.get("common", {}), *metadata["tests"].values()]:
        if not isinstance(entry, dict) or any(key in entry for key in (
                "extra_args", "extra_configs", "extra_conf_files", "extra_overlay_files", "required_modules", "sysbuild")):
            raise ValueError("The sample declares additional build inputs or module requirements that this adapter cannot clone.")
    if sample_id == "hello_world":
        return ["A board with console output. Configure and build to check the selected target and SDK."]
    source = _without_c_comments(contents["src/main.c"])
    if sample_id == "basic/blinky":
        if "DT_ALIAS(led0)" not in source or "GPIO_DT_SPEC_GET" not in source:
            raise ValueError("This Blinky implementation needs a newer requirement adapter.")
        return ["A GPIO LED with the led0 devicetree alias; see the copied README.rst.",
                "Board support is checked by explicit configuration and compilation, not by the sample catalogue."]
    if "INPUT_CALLBACK_DEFINE" in source and "zephyr/input/input.h" in source:
        return ["An input device generating KEY events, usually a gpio-keys button; see the copied README.rst.",
                "The led0 alias is optional for this local Button implementation."]
    if "DT_ALIAS(sw0)" in source and "gpio_pin_interrupt_configure" in source:
        return ["A GPIO button with the sw0 devicetree alias; see the copied README.rst.",
                "The local source and README define any optional LED behavior."]
    raise ValueError("This Button implementation needs a newer requirement adapter.")


def _capture(snapshot, sample_id, cancel=None):
    _require_reader()
    sample_id = _sample_id(sample_id)
    zephyr = _zephyr(snapshot)
    relative = "samples/" + sample_id
    contents, identity, directories = _tree(Path(zephyr["path"]) / relative, cancel)
    _cmake(contents)
    _includes(contents, zephyr)
    requirements = _requirements(sample_id, contents)
    license_files, patterns, copyright_text = _licenses(zephyr)
    attribution = []
    for path, content in contents.items():
        check_cancel(cancel)
        identifiers = re.findall(r"SPDX-License-Identifier:\s*([^\r\n*]+)", content)
        if identifiers and any(value.strip() != "Apache-2.0" for value in identifiers):
            raise ValueError(f"Unsupported license identifier in {path}; the file was not copied.")
        inherited = any(fnmatch.fnmatchcase(f"{relative}/{path}", pattern) for pattern in patterns)
        if not identifiers and not inherited:
            raise ValueError(f"No supported license attribution was found for {path}.")
        attribution.append({"path": path, "identifier": "Apache-2.0",
                            "source": "file-header" if identifiers else "REUSE.toml",
                            **({"copyright": copyright_text} if not identifiers else {})})
    contents.update(license_files)
    records = [{"path": path, "sha256": _hash(content.encode())} for path, content in sorted(contents.items())]
    provenance = {"schema_version": 1, "adapter_version": 1, "id": sample_id, "title": _CATALOG[sample_id][0],
                  "path": relative, "zephyr": zephyr, "files": records,
                  "licenses": [{"path": path, "sha256": _hash(content.encode())} for path, content in sorted(license_files.items())],
                  "attribution": attribution, "directory_identity": identity, "directories": directories,
                  "requirements": requirements, "checkout_metadata": "saved environment observation"}
    provenance["fingerprint"] = _digest(provenance)
    validate_identity(Path(zephyr["path"]) / relative, identity)
    return contents, provenance


def sample_catalog(snapshot, *, mnist_assets_root=None):
    """Inspect only the curated local paths; unavailable copies remain explainable."""
    try:
        zephyr = _zephyr(snapshot)
    except ValueError as exc:
        return {"zephyr": {"path": None, "version": None, "revision": None}, "samples": [], "diagnostics": [str(exc)]}
    samples = []
    for sample_id, (title, description) in _CATALOG.items():
        entry = {"id": sample_id, "title": title, "description": description, "path": "samples/" + sample_id,
                 "status": "available", "requirements": [], "diagnostics": [], "fingerprint": None, "file_count": 0}
        try:
            preview = preview_sample(snapshot, sample_id, {"cwd": "", "display_configure": "", "display_build": ""})
            provenance = preview["provenance"]
            entry.update(requirements=provenance["requirements"], fingerprint=provenance["fingerprint"], file_count=len(preview["files"]))
        except FileNotFoundError as exc:
            entry.update(status="missing", diagnostics=[f"A required local sample input is missing: {exc.filename or exc}"])
        except (OSError, ValueError) as exc:
            entry.update(status="unsupported", diagnostics=[str(exc)])
        samples.append(entry)
    from .mnist_sample import DESCRIPTION, SAMPLE_ID, SOURCE_PATH, TITLE
    entry = {"id": SAMPLE_ID, "title": TITLE, "description": DESCRIPTION, "path": SOURCE_PATH,
             "status": "available", "requirements": [], "diagnostics": [], "fingerprint": None, "file_count": 0}
    try:
        preview = preview_sample(snapshot, SAMPLE_ID, {"cwd": "", "display_configure": "", "display_build": ""},
                                 mnist_assets_root=mnist_assets_root)
        provenance = preview["provenance"]
        entry.update(requirements=provenance["requirements"], fingerprint=provenance["fingerprint"], file_count=len(preview["files"]))
    except FileNotFoundError as exc:
        entry.update(status="missing", diagnostics=[f"A required local MNIST runtime or firmware dependency is missing: {exc.filename or exc}"])
    except (OSError, ValueError, yaml.YAMLError, UnicodeError) as exc:
        entry.update(status="unsupported", diagnostics=[str(exc)])
    samples.append(entry)
    return {"zephyr": zephyr, "samples": samples, "diagnostics": []}


def _adaptation_input(zephyr, relative):
    """Read a fixed adapter input through the same bounded no-follow reader."""
    path = Path(zephyr["path"]) / relative
    identity = path_identity(path.parent)
    descriptor = open_directory(path.parent, identity)
    try:
        content = _read(descriptor, path.name)
    finally:
        os.close(descriptor)
    validate_identity(path.parent, identity)
    return content, {"directory_identity": identity}


def preview_sample(snapshot, sample_id, recipe, *, profile=None, cancel=None, provenance_version=None,
                   mnist_assets_root=None, name=None):
    """Return copied bytes and a standalone guide without running local tools."""
    if provenance_version is not None and (type(provenance_version) is not int or provenance_version not in {1, 2, 3}):
        raise ValueError("Unsupported local sample provenance schema. Preview again.")
    if provenance_version == 1:
        profile = None
    if provenance_version == 3 and not isinstance(profile, dict):
        raise ValueError("A default sample overlay requires the selected board profile.")
    if sample_id == "tinyml/mnist-shell":
        from .mnist_sample import preview_mnist
        if provenance_version == 2:
            raise ValueError("Unsupported MNIST provenance schema. Preview again.")
        return preview_mnist(snapshot, recipe, profile=profile, cancel=cancel, assets_root=mnist_assets_root, name=name)
    contents, provenance = _capture(snapshot, sample_id, cancel)
    contents, provenance = adapt_blinky(contents, provenance, profile,
                                       lambda path: _adaptation_input(provenance["zephyr"], path), cancel=cancel)
    adaptation = provenance.get("adaptation")
    if adaptation:
        _includes({path: content for path, content in contents.items() if not path.startswith("upstream/")},
                  provenance["zephyr"])
        provenance.pop("fingerprint")
        provenance["fingerprint"] = _digest(provenance)
    overlay = None
    if profile is not None and provenance_version not in {1, 2}:
        contents, overlay = add_default_overlay(contents, profile.get("target"), archive_cmake=True)
        provenance = {**provenance, "schema_version": 3,
                      "source_fingerprint": provenance.get("source_fingerprint", provenance["fingerprint"]),
                      "default_overlay": overlay,
                      "overlay_files": [{"path": path, "sha256": _hash(contents[path].encode())}
                                        for path in ("CMakeLists.txt", overlay["path"], ARCHIVED_CMAKE)]}
        provenance.pop("fingerprint")
        provenance["fingerprint"] = _digest(provenance)
    contents["zephyr-sample.json"] = json.dumps(provenance, sort_keys=True, indent=2) + "\n"
    requirements = "\n".join(f"- {item}" for item in provenance["requirements"])
    origin = (
        "This application uses the explicit ESP32-C3 RGB Blinky adaptation described in\n"
        "ADAPTATION.md. Generated root source/configuration are labeled; original Blinky\n"
        "files remain unchanged under upstream/basic-blinky/, and additional LED strip\n"
        "source evidence is retained under upstream/led-strip/.\n"
        if adaptation else
        "Upstream source files, README.rst, sample.yaml and board files are unchanged.\n")
    if overlay:
        if not adaptation:
            origin = ("Upstream source files, README.rst, sample.yaml and board files are unchanged.\n"
                      "The root CMakeLists.txt adds a labeled default-overlay activation block; its\n"
                      "original bytes are preserved in upstream/original/CMakeLists.txt.\n")
        else:
            origin += ("The root CMakeLists.txt also adds the labeled default-overlay activation block;\n"
                       "its pre-overlay adaptation is preserved in upstream/original/CMakeLists.txt.\n")
    contents["ZEPHYR-SAMPLE.md"] = (
        f"# {provenance['title']} — local sample copy\n\n"
        f"Copied from `{provenance['path']}` in `{provenance['zephyr']['path']}`.\n"
        f"Observed Zephyr version: `{provenance['zephyr']['version'] or 'unknown'}`.\n"
        f"Observed revision: `{provenance['zephyr']['revision'] or 'unknown'}`.\n\n"
        "The checkout version/revision came from the saved environment observation. The manifest\n"
        "records the exact current copied file hashes; it does not claim the checkout was clean.\n"
        f"{origin}LICENSE,\n"
        "REUSE.toml and any NOTICE retain the checkout's licensing evidence; REUSE paths refer\n"
        "to the original repository layout. See zephyr-sample.json for per-file attribution.\n\n"
        f"## Requirements\n\n{requirements}\n\n"
        "## Build independently\n\n"
        "The copied application does not depend on Zephyr Utilities. These commands configure\n"
        "and compile using the selected local tools; they do not flash or install anything.\n"
        f"Working directory: `{recipe['cwd']}`.\n\n"
        f"```sh\n{recipe['display_configure']}\n{recipe['display_build']}\n```\n\n"
        "Use a new build directory after changing the target, SDK or configuration. The\n"
        "sample's test metadata is preserved; Twister-specific options are not applied by\n"
        "these commands. A successful compile does not establish hardware operation.\n")
    if overlay:
        contents["ZEPHYR-SAMPLE.md"] += (
            f"\n## Default application overlay\n\nEdit `{overlay['path']}` for `{overlay['target']}`. "
            "This empty starting file makes no hardware changes. It is outside boards/ so it cannot "
            "shadow the sample's automatically selected overlays. Automatic selection preserves those "
            "overlays, followed by this default; Append adds the selected overlays after it.\n\n"
            "Use a fresh build directory when changing overlay selection. Replace selection omits "
            "this default unless explicitly selected. For an existing standalone build, "
            "`-DZEPHYR_UTILS_DEFAULT_OVERLAY=OFF` disables its automatic inclusion. The CMake block "
            "selects this file only for the exact target recorded above.\n")
    contents[".gitignore"] = "/build/\n"
    if len(contents) > MAX_FILES or sum(len(value.encode()) for value in contents.values()) > MAX_TOTAL_BYTES:
        raise ValueError("The complete sample copy exceeds the supported file or byte limit.")
    if any(len(value.encode()) > MAX_FILE_BYTES for value in contents.values()):
        raise ValueError("A generated sample document exceeds the supported per-file limit.")
    if any(len(PurePosixPath(path).parts) > MAX_DEPTH for path in contents):
        raise ValueError("The complete sample copy exceeds the supported path depth limit.")
    return {"files": [{"path": path, "content": content, "sha256": _hash(content.encode())}
                      for path, content in sorted(contents.items())], "provenance": provenance}


def validate_sample_plan(plan, cancel=None):
    """Reject changed upstream inputs or altered reviewed bytes before publication."""
    check_cancel(cancel)
    provenance = plan.get("sample")
    if not isinstance(provenance, dict):
        raise ValueError("The local sample provenance is missing. Preview again.")
    if provenance.get("schema_version") not in {1, 2, 3}:
        raise ValueError("Unsupported local sample provenance schema. Preview again.")
    mnist = provenance.get("id") == "tinyml/mnist-shell"
    if mnist and provenance.get("adapter_version") != 2:
        raise ValueError("This MNIST preview predates the independent template. Refresh samples and preview again.")
    actual = preview_sample({"zephyr": plan["profile"]["zephyr"]}, provenance.get("id"), plan["recipe"],
                            profile=plan["profile"] if provenance["schema_version"] in {2, 3} else None,
                            cancel=cancel, provenance_version=provenance["schema_version"],
                            mnist_assets_root=provenance.get("assets_root") if mnist else None,
                            name=provenance.get("application_name") if mnist else None)
    if actual["provenance"] != provenance or actual["files"] != plan.get("files"):
        raise ValueError("The local sample changed after preview. Refresh the sample list and preview again.")
    return provenance
