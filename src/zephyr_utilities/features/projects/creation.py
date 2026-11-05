"""Reviewed applications, published without replacing existing files."""

from copy import deepcopy
import ctypes
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid

from .probing import check_cancel
from .templates import render_template, select_template, template_paths, validate_template


SCHEMA_VERSION = 1
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_RESERVED = {".west", ".venv", ".git", ".features", "build", "node_modules"}
MAX_APPLICATION_FILES = 128
MAX_APPLICATION_FILE_BYTES = 1024 * 1024
MAX_APPLICATION_BYTES = 4 * 1024 * 1024
MAX_APPLICATION_DEPTH = 8
_FILE_PART = re.compile(r"[A-Za-z0-9_.+-]+\Z")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(content):
    return hashlib.sha256(content).hexdigest()


def _path(value, label):
    if (not isinstance(value, str) or not value or len(value) > 4096 or
            ";" in value or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError(f"{label} must be a local path without control characters or semicolons.")
    path = Path(value).expanduser()
    if ".." in path.parts:
        raise ValueError(f"{label} must not contain parent traversal.")
    return path


def _identity(info):
    return {"device": info.st_dev, "inode": info.st_ino}


def _directory(descriptor, name):
    try:
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=descriptor)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise ValueError("Application paths must contain only real directories, never symlinks.") from exc
        raise


def path_identity(path):
    """Capture an existing absolute directory and every ancestor without following links."""
    path = _path(str(path), "Directory")
    if not path.is_absolute():
        raise ValueError("Directory identity requires an absolute path.")
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    current = Path(path.anchor)
    entries = [{"path": str(current), **_identity(os.fstat(descriptor))}]
    try:
        for part in path.parts[1:]:
            child = _directory(descriptor, part)
            os.close(descriptor)
            descriptor = child
            current /= part
            entries.append({"path": str(current), **_identity(os.fstat(descriptor))})
    finally:
        os.close(descriptor)
    return entries


def validate_identity(path, identity):
    """Reject replacement or redirection of a previously selected directory."""
    if path_identity(path) != identity:
        raise ValueError("The selected directory or one of its ancestors changed. Select it again.")


def open_directory(path, identity=None):
    """Return a caller-owned nofollow descriptor, optionally checking every previewed inode."""
    path = _path(str(path), "Directory")
    if not path.is_absolute():
        raise ValueError("Directory access requires an absolute path.")
    expected = {item["path"]: {key: item[key] for key in ("device", "inode")} for item in identity or []}
    descriptor = os.open(path.anchor, _DIRECTORY_FLAGS)
    current = Path(path.anchor)
    try:
        for part in [None, *path.parts[1:]]:
            if part is not None:
                child = _directory(descriptor, part)
                os.close(descriptor)
                descriptor = child
                current /= part
            if identity is not None and _identity(os.fstat(descriptor)) != expected.get(str(current)):
                raise ValueError("The selected directory or one of its ancestors changed. Select it again.")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _destination_identity(destination):
    """Read each existing ancestor through directory descriptors, without following links."""
    descriptor = os.open(destination.anchor, _DIRECTORY_FLAGS)
    current = Path(destination.anchor)
    ancestors = [{"path": str(current), **_identity(os.fstat(descriptor))}]
    missing = []
    try:
        parts = destination.parent.parts[1:]
        for index, part in enumerate(parts):
            current /= part
            try:
                child = _directory(descriptor, part)
            except FileNotFoundError:
                missing = [str(current.joinpath(*parts[index + 1:end])) for end in range(index + 1, len(parts) + 1)]
                break
            os.close(descriptor)
            descriptor = child
            ancestors.append({"path": str(current), **_identity(os.fstat(descriptor))})
        if not missing:
            try:
                os.stat(destination.name, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError("The application destination already exists. Choose a new directory.")
    finally:
        os.close(descriptor)
    value = {"ancestors": ancestors, "missing": missing, "destination": str(destination)}
    value["hash"] = _hash(json.dumps(value, sort_keys=True).encode())
    return value


def _rename_noreplace(parent, source, destination):
    """Linux atomic publication; ordinary rename can silently replace an empty directory."""
    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if not sys.platform.startswith("linux") or rename is None:
        raise ValueError("Safe application publication is currently supported on Linux hosts only.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), parent, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        if code in {errno.EEXIST, errno.ENOTEMPTY}:
            raise ValueError("The destination appeared after preview. No existing files were replaced.")
        if code in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
            raise ValueError("This filesystem cannot publish applications without replacement.")
        raise OSError(code, os.strerror(code), destination)


def _validate_destination(workspace, destination, snapshot):
    selected = _path(str(workspace), "Workspace")
    if not selected.is_absolute() or selected != selected.resolve():
        raise ValueError("Select an existing canonical workspace directory.")
    info = snapshot.get("workspace", {})
    if info.get("path") != str(selected) or info.get("manifest_resolved") is not True:
        raise ValueError("Analyze this workspace with a resolved local west manifest before creating an application.")
    top = _path(info.get("west_topdir"), "West workspace")
    if not top.is_absolute() or not selected.is_relative_to(top) or not (top / ".west").is_dir():
        raise ValueError("Project creation requires an existing local west workspace.")
    if not destination.is_absolute():
        destination = selected / destination
    if any(character.isspace() for character in str(destination)):
        raise ValueError("The selected Zephyr build system does not support application paths containing whitespace.")
    if destination == selected or not destination.is_relative_to(selected):
        raise ValueError("The application destination must be inside the selected workspace.")
    if any(part in _RESERVED or part.startswith(".") for part in destination.relative_to(top).parts):
        raise ValueError("Choose an application directory outside hidden, build and tool directories.")
    utilities = Path(__file__).resolve().parents[3]
    utilities = utilities.parent if utilities.name == "src" else Path(__file__).resolve().parents[2]
    forbidden = [utilities, Path(snapshot["zephyr"]["path"])]
    forbidden += [Path(item["path"]) for item in snapshot.get("modules", []) if item.get("path")]
    forbidden += [Path(item["path"]) for item in snapshot.get("sdks", []) if item.get("path")]
    manifest = info.get("manifest_path")
    if manifest and Path(manifest).parent != top:
        forbidden.append(Path(manifest).parent)
    if any(destination == path or destination.is_relative_to(path) for path in forbidden):
        raise ValueError("Create applications outside Zephyr, dependency, SDK and utilities directories.")
    return destination


def select_profile(workspace, body, snapshot):
    """Capture an exact discovered target and installed SDK for an application."""
    if (not isinstance(snapshot, dict) or snapshot.get("schema_version") != SCHEMA_VERSION or
            snapshot.get("status") not in {"complete", "partial"}):
        raise ValueError("Analyze the environment before selecting a build configuration; stale observations cannot be used.")
    for key in ("board_id", "target", "sdk_id"):
        if not isinstance(body.get(key), str) or not body[key] or len(body[key]) > 1000:
            raise ValueError("Select a board target and installed SDK from the environment catalogue.")
    board = next((item for item in snapshot.get("boards", []) if item.get("id") == body.get("board_id")), None)
    if board is None or board.get("status") != "discovered" or body.get("target") not in board.get("targets", []):
        raise ValueError("Select an exact discovered board target, including its revision and qualifiers.")
    sdk = next((item for item in snapshot.get("sdks", []) if item.get("id") == body.get("sdk_id")), None)
    if (sdk is None or sdk.get("status") != "discovered" or
            not any(tool.get("family") == "GNU" for tool in sdk.get("toolchains", []))):
        raise ValueError("Select a discovered Zephyr SDK with an installed GNU compiler.")
    zephyr = snapshot.get("zephyr", {})
    _path(zephyr.get("path"), "Zephyr checkout")
    for name in ("Python", "cmake", "ninja"):
        tool = next((item for item in snapshot.get("tools", []) if item.get("name") == name), {})
        if tool.get("status") != "discovered" or not tool.get("path"):
            raise ValueError(f"The selected environment needs an available {name} tool.")
        _path(tool["path"], f"{name} tool")
    _path(sdk.get("path"), "SDK installation")
    for entries in snapshot.get("roots", {}).values():
        for entry in entries:
            _path(entry.get("path"), "Discovery root")
    for module in snapshot.get("modules", []):
        _path(module.get("path"), "Module directory")
        if module.get("is_module") and module.get("status") != "discovered":
            raise ValueError("Resolve unsupported or missing module metadata before creating a project.")
    return {"schema_version": 1, "workspace": str(workspace), "target": body["target"], "board_id": board["id"],
            "sdk": deepcopy(sdk), "zephyr": deepcopy(zephyr), "tools": deepcopy(snapshot["tools"]),
            "roots": deepcopy(snapshot.get("roots", {})), "modules": deepcopy(snapshot.get("modules", [])),
            "environment_id": snapshot["id"], "environment_fingerprint": snapshot["fingerprint"],
            "analysis_options": deepcopy(snapshot.get("request", {}))}


def preview_project(workspace, body, snapshot, *, recipe_factory=None):
    """Prepare all source bytes and identities without writing or configuring anything."""
    if not isinstance(body, dict) or set(body) - {
            "name", "destination", "board_id", "target", "sdk_id", "template_id", "sample_id", "sample_fingerprint"}:
        raise ValueError("Unknown project creation option.")
    template = select_template(body.get("template_id", "minimal-c"))
    sample = template["id"] == "local-sample"
    if not sample and {"sample_id", "sample_fingerprint"} & set(body):
        raise ValueError("Sample selection is only supported for the local sample template.")
    if sample and (not isinstance(body.get("sample_id"), str) or not body["sample_id"] or
                   not isinstance(body.get("sample_fingerprint"), str) or
                   not re.fullmatch(r"[a-f0-9]{64}", body["sample_fingerprint"])):
        raise ValueError("Choose a current local sample before previewing.")
    name = body.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("Application names need a leading letter and up to 64 letters, digits, underscores or hyphens.")
    if not isinstance(snapshot, dict):
        raise ValueError("Analyze the selected workspace before previewing an application.")
    profile = select_profile(workspace, body, snapshot)
    selected_destination = body.get("destination", "")
    if not isinstance(selected_destination, str):
        raise ValueError("The application destination must be a local path.")
    destination = _validate_destination(workspace, _path(selected_destination or f"applications/{name}", "Destination"), snapshot)
    identity = _destination_identity(destination)
    if not sys.platform.startswith("linux") or not hasattr(ctypes.CDLL(None), "renameat2"):
        raise ValueError("Safe application publication is currently supported on Linux hosts only.")
    if recipe_factory is None:
        from .builds import command_recipe
        recipe_factory = command_recipe
    recipe = recipe_factory(profile, destination, destination / "build" / "manual")
    provenance = None
    if sample:
        from .samples import preview_sample
        selected_sample = preview_sample(snapshot, body["sample_id"], recipe, profile=profile)
        provenance = selected_sample["provenance"]
        if provenance.get("source_fingerprint", provenance["fingerprint"]) != body["sample_fingerprint"]:
            raise ValueError("The local sample changed after selection. Refresh the sample list and select it again.")
        files = selected_sample["files"]
    else:
        contents = render_template(template, name, profile, recipe)
        files = [{"path": path, "content": content, "sha256": _hash(content.encode())}
                 for path, content in contents.items()]
    _validate_file_manifest(files)
    plan = {"schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex, "workspace": str(workspace),
            "name": name, "destination": str(destination), "profile": profile, "template": template,
            "files": files,
            "destination_identity": identity, "recipe": recipe, "created_at": _now()}
    if sample:
        plan["sample"] = deepcopy(provenance)
    return plan


def _open_parent(destination, expected, owned):
    """Pin the previewed path and create only previously absent parent directories."""
    descriptor = os.open(destination.anchor, _DIRECTORY_FLAGS)
    current = Path(destination.anchor)
    identities = {entry["path"]: {key: entry[key] for key in ("device", "inode")}
                  for entry in expected["ancestors"]}
    missing = set(expected["missing"])
    if _identity(os.fstat(descriptor)) != identities.get(str(current)):
        os.close(descriptor)
        raise ValueError("The destination ancestors changed after preview. Preview again.")
    try:
        for part in destination.parent.parts[1:]:
            current /= part
            created = str(current) in missing
            if created:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                except FileExistsError as exc:
                    raise ValueError("A destination parent appeared after preview. Preview again.") from exc
            child = _directory(descriptor, part)
            observed = _identity(os.fstat(child))
            if created:
                owned.append((os.dup(descriptor), part, observed))
            elif observed != identities.get(str(current)):
                os.close(child)
                raise ValueError("The destination ancestors changed after preview. Preview again.")
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _same_directory(parent, name, identity):
    try:
        observed = os.stat(name, dir_fd=parent, follow_symlinks=False)
        return stat.S_ISDIR(observed.st_mode) and _identity(observed) == identity
    except OSError:
        return False


def _validate_file_manifest(files):
    """Bound every reviewed text file before creating any destination directory."""
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_APPLICATION_FILES:
        raise ValueError("The change plan contains an unsupported generated file count.")
    names = set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise ValueError("The change plan contains unsupported generated files.")
        name = item["path"]
        parts = name.split("/")
        if (not 1 <= len(parts) <= MAX_APPLICATION_DEPTH or len(name) > 1024 or
                any(not _FILE_PART.fullmatch(part) or len(part) > 255 or part in {".", ".."} or
                    part in _RESERVED or (part.startswith(".") and name != ".gitignore") for part in parts) or
                name in names):
            raise ValueError("The change plan contains unsupported generated files or conflicting paths.")
        names.add(name)
        if not isinstance(item.get("content"), str) or "\0" in item["content"]:
            raise ValueError("The reviewed files must contain UTF-8 text without null bytes.")
        content = item["content"].encode("utf-8")
        total += len(content)
        if len(content) > MAX_APPLICATION_FILE_BYTES or total > MAX_APPLICATION_BYTES:
            raise ValueError("The reviewed files exceed the application publication size limit.")
        if _hash(content) != item.get("sha256"):
            raise ValueError("The reviewed file contents changed. Preview again.")
    if any(parent.as_posix() in names for name in names for parent in Path(name).parents if parent != Path(".")):
        raise ValueError("The change plan contains unsupported generated files or conflicting paths.")
    return names


def _attached_directory(directory, relative, directories):
    """Return a pinned descriptor only while its complete directory chain is intact."""
    descriptor = directory
    current = Path(".")
    for part in relative.parts:
        current /= part
        entry = directories.get(current)
        if entry is None or not _same_directory(descriptor, part, entry["identity"]):
            return None
        descriptor = entry["descriptor"]
    return descriptor


def _ensure_directory(directory, relative, directories):
    """Create and retain every nested parent inside the private staging directory."""
    descriptor = directory
    current = Path(".")
    for part in relative.parts:
        current /= part
        if current not in directories:
            os.mkdir(part, mode=0o755, dir_fd=descriptor)
            child = _directory(descriptor, part)
            directories[current] = {"descriptor": child, "identity": _identity(os.fstat(child))}
        entry = directories[current]
        if not _same_directory(descriptor, part, entry["identity"]):
            raise ValueError("The staged application directory changed during creation.")
        descriptor = entry["descriptor"]
    return descriptor


def _cleanup_files(directory, files, directories):
    """Remove unchanged operation-owned bytes only; foreign additions/edits survive."""
    for item in files:
        relative = Path(item["path"])
        parent = _attached_directory(directory, relative.parent, directories)
        if parent is None:
            continue
        try:
            fd = os.open(relative.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                expected = item["content"].encode()
                if (not stat.S_ISREG(info.st_mode) or _identity(info) != item["identity"] or
                        info.st_size > len(expected) or (item["complete"] and info.st_size != len(expected))):
                    continue
                if stream.read(len(expected) + 1) != expected[:info.st_size]:
                    continue
                current = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
                if _identity(current) == _identity(info):
                    os.unlink(relative.name, dir_fd=parent)
        except OSError:
            continue
    for relative, entry in reversed(list(directories.items())):
        parent = _attached_directory(directory, relative.parent, directories)
        try:
            if parent is not None and _same_directory(parent, relative.name, entry["identity"]):
                os.rmdir(relative.name, dir_fd=parent)
        except OSError:
            pass


def _directory_names(descriptor):
    # Some local filesystems retain the initial empty enumeration on a directory
    # opened before its children were created. Reopen the pinned inode, not its path.
    fresh = _directory(descriptor, ".")
    try:
        return os.listdir(fresh)
    finally:
        os.close(fresh)


def _validate_staging(directory, directories, files):
    expected = {Path("."): set()}
    for item in files:
        relative = Path(item["path"])
        expected.setdefault(relative.parent, set()).add(relative.name)
        for parent in relative.parents:
            if parent != Path("."):
                expected.setdefault(parent.parent, set()).add(parent.name)
    if set(expected) != {Path("."), *directories}:
        raise ValueError("The staged application directory structure changed during creation.")
    for relative, names in expected.items():
        descriptor = _attached_directory(directory, relative, directories)
        if descriptor is None or set(_directory_names(descriptor)) != names:
            raise ValueError("The staged application changed during creation; foreign files were preserved.")
    for item in files:
        relative = Path(item["path"])
        parent = _attached_directory(directory, relative.parent, directories)
        if parent is None:
            raise ValueError("The staged application directory changed during creation.")
        fd = os.open(relative.name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            expected = item["content"].encode()
            if (not stat.S_ISREG(info.st_mode) or _identity(info) != item["identity"] or
                    info.st_size != len(expected) or stream.read(len(expected) + 1) != expected):
                raise ValueError("The staged application changed during creation; foreign edits were preserved.")


def apply_project(plan, *, cancel=None):
    """Publish exactly the retained preview. The caller rechecks environment freshness."""
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported project change-plan schema.")
    template = validate_template(plan.get("template"))
    sample = template["id"] == "local-sample"
    if not sample and "sample" in plan:
        raise ValueError("Sample provenance is only supported for the local sample template.")
    destination = _path(plan["destination"], "Destination")
    if _destination_identity(destination) != plan["destination_identity"]:
        raise ValueError("The destination ancestors changed after preview. Preview again.")
    files = plan["files"]
    names = _validate_file_manifest(files)
    if sample:
        from .samples import validate_sample_plan
        validate_sample_plan(plan, cancel=cancel)
    elif names != template_paths(template):
        raise ValueError("The change plan contains unsupported generated files.")
    check_cancel(cancel)
    owned = []
    parent = staging = None
    stage_name = f".zephyr-create-{uuid.uuid4().hex}"
    stage_identity = None
    directories = {}
    published = False
    written = []
    try:
        parent = _open_parent(destination, plan["destination_identity"], owned)
        os.mkdir(stage_name, mode=0o755, dir_fd=parent)
        staging = _directory(parent, stage_name)
        stage_identity = _identity(os.fstat(staging))
        for item in files:
            check_cancel(cancel)
            relative = Path(item["path"])
            directory = _ensure_directory(staging, relative.parent, directories)
            fd = os.open(relative.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o644, dir_fd=directory)
            written.append({**item, "identity": _identity(os.fstat(fd)), "complete": False})
            with os.fdopen(fd, "wb") as stream:
                stream.write(item["content"].encode())
                stream.flush()
                os.fsync(stream.fileno())
            written[-1]["complete"] = True
        for entry in reversed(list(directories.values())):
            os.fsync(entry["descriptor"])
        os.fsync(staging)
        if sample:
            validate_sample_plan(plan, cancel=cancel)
        # The descriptors pinned the parent; rechecking paths catches renamed ancestors.
        expected = deepcopy(plan["destination_identity"])
        expected["ancestors"].extend({"path": str(Path(plan["destination_identity"]["missing"][index])), **identity}
                                     for index, (_, _, identity) in enumerate(owned))
        expected["missing"] = []
        expected.pop("hash", None)
        expected["hash"] = _hash(json.dumps(expected, sort_keys=True).encode())
        if _destination_identity(destination) != expected:
            raise ValueError("The destination ancestors changed during creation. Preview again.")
        if not _same_directory(parent, stage_name, stage_identity):
            raise ValueError("The staged application directory changed during creation.")
        _validate_staging(staging, directories, written)
        check_cancel(cancel)
        _rename_noreplace(parent, stage_name, destination.name)
        published = True
        diagnostics = []
        try:
            os.fsync(parent)
        except OSError as exc:
            # Publication already succeeded. Report uncertain durability without pretending
            # creation failed or attempting to remove the now-visible application.
            diagnostics.append({"severity": "warning", "code": "directory-sync",
                                "message": f"Application created, but directory synchronization failed: {exc}"})
        ancestry = [{key: entry[key] for key in ("path", "device", "inode")} for entry in expected["ancestors"]]
        ancestry.append({"path": str(destination), **stage_identity})
        project = {"schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex, "workspace": plan["workspace"],
                "name": plan["name"], "path": str(destination), "profile": deepcopy(plan["profile"]),
                "template": template, "directory_identity": ancestry,
                "files": [{"path": item["path"], "sha256": item["sha256"]} for item in files],
                "diagnostics": diagnostics, "created_at": _now()}
        if sample:
            project["sample"] = deepcopy(plan["sample"])
        return project
    finally:
        if staging is not None:
            if not published:
                _cleanup_files(staging, written, directories)
            for entry in directories.values():
                os.close(entry["descriptor"])
            os.close(staging)
        if parent is not None:
            if not published and stage_identity and _same_directory(parent, stage_name, stage_identity):
                try:
                    os.rmdir(stage_name, dir_fd=parent)
                except OSError:
                    pass
            os.close(parent)
        for directory, name, identity in reversed(owned):
            try:
                if not published and _same_directory(directory, name, identity):
                    os.rmdir(name, dir_fd=directory)
            except OSError:
                pass
            finally:
                os.close(directory)
