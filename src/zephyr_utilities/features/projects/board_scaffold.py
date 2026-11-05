"""Reviewable custom-board copies from the selected checkout's reference ports.

The scaffold retains a reference port's hardware choices. It never infers wiring from
a SoC name or claims that a new PCB has been validated.
"""

from copy import deepcopy
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import yaml

from .analyzer import digest
from .creation import (_destination_identity, _path, _validate_destination, _validate_file_manifest,
                       open_directory, path_identity, publish_files, validate_identity)
from .hardware import _board_record, _UniqueLoader
from .samples import _licenses


_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_VENDOR = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_PART = re.compile(r"[A-Za-z0-9_.+-]+\Z")
_SKIP = {"doc", "docs", ".git", "build", "__pycache__"}
_LIMIT = 1024 * 1024
_MAX_TOTAL = 3 * 1024 * 1024
_WARNINGS = [
    "This is a copy of the reference hardware definition. Check its clocks, memory, console, "
    "pins, peripherals and runner settings against your schematic before using the board.",
    "All reference SoCs, variants and declared revisions are retained. Selecting a SoC "
    "filters the starting point; it does not remove the reference's other targets.",
    "Creation does not configure, compile or flash hardware. Analyze the environment, then "
    "create an application and explicitly Configure or Build to check the port.",
]


def _reason(board):
    if board.get("status") != "discovered" or not board.get("targets"):
        return "Resolve this board's environment diagnostics before using it as a reference."
    if len(board.get("directories", [])) != 1:
        return "Reference ports spread across board-extension directories are not supported."
    if board.get("revision_format") == "custom":
        return "Custom revision scripts need a manual port; choose a reference with declared revisions."
    if any(board.get("name", "").lower() in soc.lower() for soc in board.get("socs", [])):
        return "This reference shares its board identifier with a SoC identifier and needs manual renaming."
    return ""


def catalogue(snapshot):
    """Return candidates without executing or traversing their board build files."""
    if not snapshot:
        return {"references": [], "socs": []}
    references = [{key: deepcopy(board.get(key)) for key in ("id", "name", "full_name", "vendor", "socs", "targets")}
                  | {"eligible": not _reason(board), "reason": _reason(board)} for board in snapshot.get("boards", [])]
    return {"references": references,
            "socs": [{"name": soc["name"], "status": soc.get("status", "unknown")}
                     for soc in snapshot.get("socs", [])]}


def _read_file(directory, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _LIMIT:
            raise ValueError(f"Reference file is not bounded regular text: {name}")
        content = stream.read(_LIMIT + 1)
        if len(content) > _LIMIT or b"\0" in content:
            raise ValueError(f"Reference file is not bounded UTF-8 text: {name}")
        return content.decode("utf-8")


def _inventory(path):
    identity = path_identity(path)
    files, omitted = {}, []
    total = 0

    def walk(directory, prefix, depth):
        nonlocal total
        if depth > 5:
            raise ValueError("Reference board exceeds the supported directory depth.")
        entries = sorted(os.listdir(directory))
        if len(entries) > 256:
            raise ValueError("Reference board contains too many directory entries.")
        for name in entries:
            relative = f"{prefix}/{name}" if prefix else name
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode) and name in _SKIP:
                omitted.append(relative)
                continue
            if not _PART.fullmatch(name) or name.startswith("."):
                raise ValueError(f"Reference contains an unsupported source path: {relative}")
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    walk(child, relative, depth + 1)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                try:
                    content = _read_file(directory, name)
                except UnicodeDecodeError as exc:
                    raise ValueError(f"Reference runtime file is binary or not UTF-8: {relative}") from exc
                total += len(content.encode())
                if len(files) >= 110 or total > _MAX_TOTAL:
                    raise ValueError("Reference board exceeds the supported source size or file count.")
                files[relative] = content
            else:
                raise ValueError(f"Reference sources cannot contain symlinks or special files: {relative}")

    directory = open_directory(path, identity)
    try:
        walk(directory, "", 0)
    finally:
        os.close(directory)
    validate_identity(path, identity)
    return {"directory_identity": identity, "files": files, "omitted": omitted,
            "fingerprint": digest(files)}


def _validate_dependencies(files, source):
    """Reject local dependencies which a self-contained directory copy would lose."""
    for relative, content in files.items():
        if "../" in content or "..\\" in content:
            raise ValueError(f"Reference {relative} uses a parent-directory dependency; this port needs manual adaptation.")
        if str(source) in content:
            raise ValueError(f"Reference {relative} embeds its source path; this port needs manual adaptation.")
        if re.search(r"boards/[^\s\"<>)]*" + re.escape(source.name), content):
            raise ValueError(f"Reference {relative} embeds its original board-directory location and needs manual adaptation.")
        if relative.endswith((".dts", ".dtsi")):
            for include in re.findall(r'^\s*#\s*include\s+"([^"]+)"', content, flags=re.MULTILINE):
                candidate = (Path(relative).parent / include).as_posix()
                if candidate not in files:
                    raise ValueError(f"Reference {relative} includes {include}, which is outside the copied board files.")
        if Path(relative).name.startswith("Kconfig"):
            for include in re.findall(r'^\s*(?:rsource|orsource)\s+"([^"]+)"', content, flags=re.MULTILINE):
                pattern = (Path(relative).parent / include).as_posix()
                if "$" in pattern or not any(fnmatch.fnmatch(name, pattern) for name in files):
                    raise ValueError(f"Reference {relative} has an unresolved local Kconfig dependency: {include}")
        if relative.endswith(".cmake") or Path(relative).name == "CMakeLists.txt":
            for variable, suffix in re.findall(r"\$\{(BOARD_DIR|CMAKE_CURRENT_LIST_DIR)\}/([A-Za-z0-9_./+-]+)", content):
                candidate = (Path(relative).parent / suffix).as_posix() if variable == "CMAKE_CURRENT_LIST_DIR" else suffix
                if candidate not in files and not any(name.startswith(candidate.rstrip("/") + "/") for name in files):
                    raise ValueError(f"Reference {relative} has a missing local CMake dependency: {suffix}")
            for include in re.findall(r'\binclude\s*\(\s*"?([^\s"\)]+)', content, flags=re.IGNORECASE):
                if "$" not in include and include.endswith(".cmake") and (Path(relative).parent / include).as_posix() not in files:
                    raise ValueError(f"Reference {relative} includes a CMake file outside the copied board: {include}")


def _token(value, expression, label):
    if not isinstance(value, str) or not expression.fullmatch(value):
        raise ValueError(f"{label} must start with a lowercase letter and use at most 64 lowercase letters, digits or underscores.")
    return value


def _full_name(value, name):
    value = value if value is not None else name
    if (not isinstance(value, str) or not value.strip() or len(value) > 64 or
            any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError("Board display name must contain 1–64 printable characters.")
    return value.strip()


def _rename(text, previous, name):
    # Kconfig symbols normalize punctuation; lowercase filenames preserve board names.
    old_symbol = re.sub(r"[^A-Z0-9_]", "_", previous.upper())
    new_symbol = name.upper()
    def rename_line(line):
        if re.search(r"copyright|SPDX", line, flags=re.IGNORECASE):
            return line
        line = re.sub(r"(?<![A-Z0-9_])((?:CONFIG_)?BOARD_)" + re.escape(old_symbol) + r"(?=_|\b)",
                      lambda match: match[1] + new_symbol, line)
        return line.replace(previous, name)
    return "".join(rename_line(line) for line in text.splitlines(keepends=True))


def _board_metadata(text):
    depth = 0
    for count, event in enumerate(yaml.parse(text, Loader=_UniqueLoader), 1):
        if count > 40000 or isinstance(event, yaml.events.AliasEvent):
            raise ValueError("Reference board metadata contains unsupported YAML aliases or too many entries.")
        if isinstance(event, yaml.events.CollectionStartEvent):
            depth += 1
        elif isinstance(event, yaml.events.CollectionEndEvent):
            depth -= 1
        if depth > 24:
            raise ValueError("Reference board metadata is too deeply nested.")
    return yaml.load(text, Loader=_UniqueLoader)


def _observed_files(paths):
    observations = []
    for value in sorted(set(paths)):
        path = Path(value)
        directory = open_directory(path.parent)
        try:
            text = _read_file(directory, path.name)
        finally:
            os.close(directory)
        observations.append({"path": str(path), "sha256": hashlib.sha256(text.encode()).hexdigest()})
    return observations


def _license_evidence(snapshot, source, files):
    """Retain the existing bounded Apache reference adapter's license evidence."""
    zephyr = Path(snapshot["zephyr"]["path"])
    # Only declared board roots may supply fallback annotations. A generated origin
    # record is provenance, never authority to assign a license to current files.
    owners = [Path(root["path"]) for root in snapshot.get("roots", {}).get("boards", [])
              if source.is_relative_to(Path(root["path"]) / "boards")]
    owner = zephyr if source.is_relative_to(zephyr) else max(owners, key=lambda path: len(path.parts), default=zephyr)
    license_root = owner if (owner / "LICENSE").exists() and (owner / "REUSE.toml").exists() else zephyr
    licenses, patterns, copyright_text = _licenses({"path": str(license_root)})
    attribution = []
    for path, content in files.items():
        identifiers = re.findall(r"SPDX-License-Identifier:\s*([^\r\n*]+)", content)
        if identifiers and any(identifier.strip() != "Apache-2.0" for identifier in identifiers):
            raise ValueError(f"Reference {path} uses a license not supported by the board-copy adapter; port it manually.")
        inherited = source.is_relative_to(license_root) and any(
            fnmatch.fnmatchcase((source.relative_to(license_root) / path).as_posix(), pattern) for pattern in patterns)
        if not identifiers and not inherited:
            raise ValueError(f"Reference {path} has no supported license attribution; port it manually.")
        attribution.append({"path": path, "identifier": "Apache-2.0", "source": "file-header" if identifiers else "REUSE.toml",
                            **({"copyright": copyright_text} if not identifiers else {})})
    return licenses, attribution, [str(license_root / name) for name in licenses]


def _generated_licenses(licenses, attribution, prefix, previous, name):
    """Keep source annotations as evidence and map inherited attribution to new paths."""
    generated = {key: content for key, content in licenses.items() if key != "REUSE.toml"}
    generated["licenses/reference-REUSE.toml"] = licenses["REUSE.toml"]
    inherited = [item for item in attribution if item["source"] == "REUSE.toml"]
    if inherited:
        paths = [f"{prefix}/{_rename(item['path'], previous, name)}" for item in inherited]
        generated["REUSE.toml"] = ('version = 1\n\n[[annotations]]\npath = [\n'
            + "".join(f"    {json.dumps(path)},\n" for path in sorted(paths))
            + ']\nSPDX-License-Identifier = "Apache-2.0"\nSPDX-FileCopyrightText = '
            + json.dumps(inherited[0]["copyright"], ensure_ascii=False) + "\n")
    return generated


def preview_scaffold(workspace, body, snapshot):
    """Prepare a complete renamed reference port without writing or running its code."""
    if not isinstance(snapshot, dict) or snapshot.get("status") not in {"complete", "partial"}:
        raise ValueError("Analyze the environment before creating a custom board.")
    name = _token(body.get("name"), _IDENTIFIER, "Board identifier")
    vendor = _token(body.get("vendor", "custom"), _VENDOR, "Vendor identifier")
    full_name = _full_name(body.get("full_name"), name)
    if any(board.get("name", "").casefold() == name.casefold() for board in snapshot.get("boards", [])):
        raise ValueError("This board identifier is already present in the environment. Choose a unique identifier.")
    reference = next((board for board in snapshot.get("boards", []) if board.get("id") == body.get("reference_id")), None)
    if not reference:
        raise ValueError("Select a reference board from the analyzed environment.")
    reason = _reason(reference)
    if reason:
        raise ValueError(reason)
    soc = body.get("soc", "")
    if not isinstance(soc, str) or soc and soc not in reference.get("socs", []):
        raise ValueError("The selected reference does not use the chosen supported SoC.")
    source = Path(reference["directories"][0])
    inventory = _inventory(source)
    metadata = _board_metadata(inventory["files"].get("board.yml", ""))
    board = metadata.get("board") if isinstance(metadata, dict) else None
    if (not isinstance(board, dict) or set(metadata) != {"board"} or board.get("name") != reference["name"] or
            not isinstance(board.get("socs"), list) or [item.get("name") for item in board["socs"] if isinstance(item, dict)] != reference["socs"]):
        raise ValueError("The reference must define one unchanged board. Analyze the environment again or choose another reference.")
    checked = _board_record(board, source / "board.yml", "reference", {item["name"]: item for item in snapshot.get("socs", [])})
    if any(checked[key] != reference.get(key) for key in ("qualifiers", "revisions", "default_revision", "revision_format", "revision_exact")):
        raise ValueError("Reference targets changed since analysis. Analyze the environment again before creating the board.")
    if ("board.yml" not in inventory["files"] or f"Kconfig.{reference['name']}" not in inventory["files"] or
            not any(path.endswith(".dts") for path in inventory["files"])):
        raise ValueError("The reference needs board.yml, its board Kconfig file and a local DTS hardware definition.")
    _validate_dependencies(inventory["files"], source)
    licenses, attribution, license_paths = _license_evidence(snapshot, source, inventory["files"])
    destination = _path(body.get("path") or str(Path(workspace) / "hardware" / name), "Board destination")
    if not destination.is_absolute():
        destination = Path(workspace) / destination
    destination = _validate_destination(workspace, destination, snapshot)
    if any(character.isspace() for character in str(destination)):
        raise ValueError("Choose a board destination without whitespace for the selected build system.")
    destination_identity = _destination_identity(destination)
    prefix = f"boards/{vendor}/{name}"
    contents = {}
    for relative, content in inventory["files"].items():
        renamed = _rename(relative, reference["name"], name)
        path = f"{prefix}/{renamed}"
        if path in contents:
            raise ValueError("Renaming the reference would produce conflicting source paths.")
        contents[path] = _rename(content, reference["name"], name)
    board = deepcopy(board)
    board.update(name=name, vendor=vendor, full_name=full_name)
    original = inventory["files"]["board.yml"]
    comments = "\n".join(line for line in original.splitlines() if line.lstrip().startswith("#"))
    contents[f"{prefix}/board.yml"] = (comments + "\n" if comments else "") + yaml.safe_dump({"board": board}, sort_keys=False)
    observed = _observed_files([*reference.get("source_files", []), *license_paths])
    provenance = {"schema_version": 1, "kind": "reference-board-copy", "reference_id": reference["id"],
                  "reference_name": reference["name"], "reference_path": str(source), "selected_soc": soc or None,
                  "zephyr": deepcopy(snapshot.get("zephyr", {})),
                  "source_files": [{"path": relative, "sha256": hashlib.sha256(content.encode()).hexdigest()}
                                   for relative, content in inventory["files"].items()],
                  "omitted_directories": inventory["omitted"], "attribution": attribution,
                  "licenses": [{"path": "licenses/reference-REUSE.toml" if path == "REUSE.toml" else path,
                                "source_path": path, "sha256": hashlib.sha256(content.encode()).hexdigest()}
                               for path, content in licenses.items()]}
    targets = [name + target[len(reference["name"]):] for target in reference["targets"]]
    contents["zephyr-board-origin.json"] = json.dumps(provenance, indent=2, ensure_ascii=True) + "\n"
    contents.update(_generated_licenses(licenses, attribution, prefix, reference["name"], name))
    contents["README.md"] = (f"# {full_name}\n\nCustom board `{name}`, based on `{reference['name']}` from the selected Zephyr environment.\n\n"
        "This is a starting port, not hardware validation. The reference clocks, memory, console, pin routing, "
        "peripherals and runner settings are retained. Review them against your schematic before using the board.\n\n"
        f"Board sources: `{prefix}/`. All reference SoCs, variants and declared revisions are retained.\n\n"
        "Register this directory as a board root and analyze the workspace. Create a minimal application for one "
        "of the targets below, then explicitly Configure or Build it. Build success does not establish hardware operation.\n\n"
        + "\n".join(f"- `{target}`" for target in targets) + "\n\n"
        "For builds outside Utilities, pass this directory as `BOARD_ROOT`. Keep original copyright and SPDX notices; "
        "the original file hashes and checkout identity are recorded in `zephyr-board-origin.json`.\n")
    files = [{"path": path, "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}
             for path, content in sorted(contents.items())]
    _validate_file_manifest(files)
    if _inventory(source) != inventory:
        raise ValueError("Reference board files changed while preparing the review. Review again.")
    return {"schema_version": 1, "workspace": str(workspace), "path": str(destination), "name": name,
            "vendor": vendor, "full_name": full_name, "files": files, "targets": targets,
            "warnings": [*_WARNINGS, *(["Reference documentation and build directories are excluded from the copy."] if inventory["omitted"] else [])],
            "provenance": provenance, "reference_path": str(source), "reference_inventory": inventory,
            "observed_files": observed, "destination_identity": destination_identity}


def revalidate_scaffold(plan):
    if plan.get("schema_version") != 1:
        raise ValueError("Unsupported custom-board preview schema.")
    if _inventory(plan["reference_path"]) != plan["reference_inventory"]:
        raise ValueError("Reference board files changed since review. Review again.")
    if _observed_files([item["path"] for item in plan["observed_files"]]) != plan["observed_files"]:
        raise ValueError("Reference hardware metadata changed since review. Analyze and review again.")


def apply_scaffold(plan, *, cancel=None):
    """Publish the reviewed fresh root; never overwrite or modify a reference port."""
    revalidate_scaffold(plan)
    result = publish_files(Path(plan["path"]), plan["destination_identity"], plan["files"],
                           revalidate=lambda: revalidate_scaffold(plan), cancel=cancel)
    return {**result, "provenance": deepcopy(plan["provenance"]), "targets": list(plan["targets"])}
