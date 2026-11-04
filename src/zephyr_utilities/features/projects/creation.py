"""Reviewed minimal applications, published without replacing existing files."""

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


SCHEMA_VERSION = 1
TEMPLATE = {"id": "minimal-c", "version": 1}
_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_RESERVED = {".west", ".venv", ".git", ".features", "build", "node_modules"}


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


def _profile(workspace, body, snapshot):
    if snapshot.get("schema_version") != SCHEMA_VERSION or snapshot.get("status") not in {"complete", "partial"}:
        raise ValueError("Analyze the environment before creating an application; stale observations cannot be used.")
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
    if not isinstance(body, dict) or set(body) - {"name", "destination", "board_id", "target", "sdk_id"}:
        raise ValueError("Unknown project creation option.")
    name = body.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError("Application names need a leading letter and up to 64 letters, digits, underscores or hyphens.")
    if not isinstance(snapshot, dict):
        raise ValueError("Analyze the selected workspace before previewing an application.")
    for key in ("board_id", "target", "sdk_id"):
        if not isinstance(body.get(key), str) or not body[key] or len(body[key]) > 1000:
            raise ValueError("Select a board target and installed SDK from the environment catalogue.")
    profile = _profile(workspace, body, snapshot)
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
    readme = (f"# {name}\n\nA minimal Zephyr C application generated from the minimal-c template, version 1.\n\n"
              "The application sources do not depend on Zephyr Utilities. The selected board and SDK\n"
              "are arguments to the following commands, not hardcoded into the source.\n\n"
              f"Requested target: `{profile['target']}`.\n\n"
              "Run the commands from the workspace shown below. They configure and compile only;\n"
              "they do not flash hardware. Local CMake and toolchain code will run.\n\n"
              f"Working directory: `{recipe['cwd']}`\n\n"
              f"```sh\n{recipe['display_configure']}\n{recipe['display_build']}\n```\n\n"
              "The commands use build/manual. Choose a different build directory after changing\n"
              "the target, SDK or configuration; no automatic pristine deletion is performed.\n"
              "Zephyr Utilities uses a new build/<attempt-id> directory for every UI attempt.\n\n"
              "A successful build validates compilation only, not operation on hardware.\n")
    contents = {
        "CMakeLists.txt": ("cmake_minimum_required(VERSION 3.20.0)\n\n"
                           "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n"
                           f"project({name} LANGUAGES C)\n\n"
                           "target_sources(app PRIVATE src/main.c)\n"),
        "prj.conf": "# Application configuration, independent of board selection.\nCONFIG_PRINTK=y\n",
        "src/main.c": ("#include <zephyr/kernel.h>\n\nint main(void)\n{\n"
                       f'\tprintk("Hello from {name}!\\n");\n\treturn 0;\n}}\n'),
        ".gitignore": "/build/\n",
        "README.md": readme,
    }
    return {"schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex, "workspace": str(workspace),
            "name": name, "destination": str(destination), "profile": profile, "template": dict(TEMPLATE),
            "files": [{"path": path, "content": content, "sha256": _hash(content.encode())}
                      for path, content in contents.items()],
            "destination_identity": identity, "recipe": recipe, "created_at": _now()}


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
    except FileNotFoundError:
        return False


def _cleanup_files(directory, files, source_identity):
    """Remove unchanged operation-owned bytes only; foreign additions/edits survive."""
    source = None
    try:
        if source_identity and _same_directory(directory, "src", source_identity):
            source = _directory(directory, "src")
        for item in files:
            relative = Path(item["path"])
            parent = source if relative.parent == Path("src") else directory
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
    finally:
        if source is not None:
            os.close(source)
            try:
                if _same_directory(directory, "src", source_identity):
                    os.rmdir("src", dir_fd=directory)
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


def _validate_staging(directory, source, source_identity, files):
    if (not _same_directory(directory, "src", source_identity) or
            set(_directory_names(directory)) != {"CMakeLists.txt", "prj.conf", "README.md", ".gitignore", "src"} or
            _directory_names(source) != ["main.c"]):
        raise ValueError("The staged application changed during creation; foreign files were preserved.")
    for item in files:
        relative = Path(item["path"])
        parent = source if relative.parent == Path("src") else directory
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
    destination = _path(plan["destination"], "Destination")
    if _destination_identity(destination) != plan["destination_identity"]:
        raise ValueError("The destination ancestors changed after preview. Preview again.")
    files = plan["files"]
    expected_names = {"CMakeLists.txt", "prj.conf", "src/main.c", ".gitignore", "README.md"}
    if len(files) != len(expected_names) or {item["path"] for item in files} != expected_names:
        raise ValueError("The change plan contains unsupported generated files.")
    if any(_hash(item["content"].encode()) != item["sha256"] for item in files):
        raise ValueError("The reviewed file contents changed. Preview again.")
    check_cancel(cancel)
    owned = []
    parent = staging = source = None
    stage_name = f".zephyr-create-{uuid.uuid4().hex}"
    stage_identity = source_identity = None
    published = False
    written = []
    try:
        parent = _open_parent(destination, plan["destination_identity"], owned)
        os.mkdir(stage_name, mode=0o755, dir_fd=parent)
        staging = _directory(parent, stage_name)
        stage_identity = _identity(os.fstat(staging))
        os.mkdir("src", mode=0o755, dir_fd=staging)
        source = _directory(staging, "src")
        source_identity = _identity(os.fstat(source))
        for item in files:
            check_cancel(cancel)
            relative = Path(item["path"])
            fd = os.open(relative.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o644, dir_fd=source if relative.parent == Path("src") else staging)
            written.append({**item, "identity": _identity(os.fstat(fd)), "complete": False})
            with os.fdopen(fd, "wb") as stream:
                stream.write(item["content"].encode())
                stream.flush()
                os.fsync(stream.fileno())
            written[-1]["complete"] = True
        os.fsync(source)
        os.fsync(staging)
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
        _validate_staging(staging, source, source_identity, written)
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
        return {"schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex, "workspace": plan["workspace"],
                "name": plan["name"], "path": str(destination), "profile": deepcopy(plan["profile"]),
                "template": dict(TEMPLATE), "directory_identity": ancestry,
                "files": [{"path": item["path"], "sha256": item["sha256"]} for item in files],
                "diagnostics": diagnostics, "created_at": _now()}
    finally:
        if source is not None:
            os.close(source)
        if staging is not None:
            if not published:
                _cleanup_files(staging, written, source_identity)
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
