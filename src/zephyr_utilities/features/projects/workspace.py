"""Read workspace and module metadata without configuring applications."""

import configparser
import json
import os
from pathlib import Path
import shutil
import sys

import yaml

from .metadata import read_regular
from .probing import ProbeError, check_cancel, run_probe


MAX_METADATA = 2 * 1024 * 1024
MAX_PROJECTS = 512
WEST_QUERY = r'''
import json, sys
from west.manifest import Manifest
manifest = Manifest.from_topdir(topdir=sys.argv[1])
print(json.dumps({"manifest_path": str(manifest.path), "projects": [
    {"name": p.name, "path": p.abspath, "revision": p.revision,
     "active": manifest.is_active(p), "cloned": p.is_cloned()}
    for p in manifest.projects]}))
'''


def diagnostic(code, message, path=None, severity="warning"):
    result = {"severity": severity, "code": code, "message": str(message)}
    if path is not None:
        result["path"] = str(path)
    return result


def read_text(path):
    return read_regular(path, MAX_METADATA).decode("utf-8")


def read_yaml(path):
    text = read_text(path)
    depth = 0
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    for count, event in enumerate(yaml.parse(text, Loader=loader), 1):
        if count > 60000 or isinstance(event, yaml.events.AliasEvent):
            raise ValueError(f"Unsupported YAML alias or excessive metadata entries: {path}")
        if isinstance(event, yaml.events.CollectionStartEvent):
            depth += 1
        elif isinstance(event, yaml.events.CollectionEndEvent):
            depth -= 1
        if depth > 32:
            raise ValueError(f"Metadata nesting exceeds the supported limit: {path}")
    value = yaml.load(text, Loader=loader)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a metadata mapping: {path}")
    return value


def split_paths(value):
    return [part.strip() for part in value.split(";") if part.strip()] if value else []


def resolve_path(value, base):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(base) / path).resolve()


def _version(path):
    fields = {}
    for line in read_text(path).splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    parts = [fields.get(key, "") for key in ("VERSION_MAJOR", "VERSION_MINOR", "PATCHLEVEL")]
    if not all(part.isdecimal() for part in parts):
        raise ValueError("Zephyr VERSION does not contain a supported version declaration.")
    return ".".join(parts) + (f"-{fields['EXTRAVERSION']}" if fields.get("EXTRAVERSION") else "")


def inspect_workspace(workspace, options, *, environ=None, cancel=None, emit=None, probe=run_probe):
    environment = dict(os.environ if environ is None else environ)
    selected = Path(workspace).resolve()
    messages, inputs = [], []
    emit = emit or (lambda message: None)
    topdir = next((p for p in (selected, *selected.parents) if (p / ".west").is_dir()), None)
    config = configparser.ConfigParser(interpolation=None)
    manifest_path = None
    candidates = []
    if topdir:
        config_path = topdir / ".west/config"
        inputs.append(str(config_path))
        try:
            config.read_string(read_text(config_path))
            manifest_dir = resolve_path(config.get("manifest", "path"), topdir)
            manifest_path = manifest_dir / config.get("manifest", "file", fallback="west.yml")
            inputs.extend([str(manifest_path), str(manifest_dir)])
            configured = config.get("zephyr", "base", fallback="")
            if configured:
                candidates.append(resolve_path(configured, topdir))
            candidates.extend([manifest_dir, topdir / "zephyr"])
        except (OSError, ValueError, configparser.Error) as exc:
            messages.append(diagnostic("workspace-config", exc, config_path))
    else:
        messages.append(diagnostic("no-west-workspace", "No .west workspace found; only selected local roots can be inspected.", selected))
    candidates.extend([selected, selected / "zephyr"])
    requested = options.get("zephyr_base") or environment.get("ZEPHYR_BASE")
    if requested:
        zephyr = resolve_path(requested, selected)
        if not (zephyr / "VERSION").is_file() or not (zephyr / "scripts").is_dir():
            messages.append(diagnostic("zephyr-unavailable", "The selected Zephyr base has no VERSION/scripts. Check the override.", zephyr, "error"))
            zephyr = None
    else:
        zephyr = next((p.resolve() for p in candidates if (p / "VERSION").is_file() and (p / "scripts").is_dir()), None)
    if zephyr is None and not requested:
        messages.append(diagnostic("zephyr-unavailable", "No Zephyr checkout found. Select a workspace or provide its Zephyr base.", selected, "error"))

    interpreters = [root / ".venv/bin/python" for root in (topdir, selected) if root is not None]
    python = next((str(path) for path in interpreters if path.is_file()), sys.executable)
    # Deliberately preserve the venv symlink instead of resolving to the system Python.
    environment["PATH"] = str(Path(python).parent) + os.pathsep + environment.get("PATH", os.defpath)
    tools = []
    emit("Inspecting the workspace and local tool versions…")
    for name, argv in [("Python", [python, "-I", "-B", "--version"]),
                       ("west", [python, "-I", "-B", "-m", "west", "--version"]),
                       *[(name, [shutil.which(name, path=environment["PATH"]) or name, "--version"])
                         for name in ("cmake", "ninja", "dtc", "git")]]:
        check_cancel(cancel)
        path = argv[0] if name in {"Python", "west"} else shutil.which(name, path=environment["PATH"])
        entry = {"name": name, "path": path, "version": None, "status": "unavailable"}
        if path:
            try:
                entry.update(version=probe(argv, cwd=selected, environ=environment, cancel=cancel).splitlines()[0], status="discovered")
            except (ProbeError, IndexError) as exc:
                messages.append(diagnostic("tool-unavailable", f"{name}: {exc}", path))
        else:
            messages.append(diagnostic("tool-unavailable", f"{name} is not on the selected environment's PATH."))
        tools.append(entry)

    projects = []
    manifest_resolved = False
    if topdir:
        try:
            data = json.loads(probe([python, "-I", "-B", "-c", WEST_QUERY, str(topdir)],
                                    cwd=selected, environ=environment, cancel=cancel))
            projects = data["projects"]
            if not isinstance(projects, list) or len(projects) > MAX_PROJECTS:
                raise ValueError("Workspace exceeds the 512-project inspection limit.")
            manifest_path = Path(data["manifest_path"])
            manifest_resolved = True
        except (ProbeError, ValueError, KeyError, TypeError) as exc:
            messages.append(diagnostic("manifest-unresolved", f"West manifest resolution is incomplete: {exc}", manifest_path))
            # A bounded fallback shows direct entries without claiming imported resolution.
            if manifest_path:
                try:
                    manifest = read_yaml(manifest_path).get("manifest", {})
                    direct = manifest.get("projects", [])
                    if not isinstance(direct, list) or len(direct) > MAX_PROJECTS:
                        raise ValueError("Invalid or excessive direct project list.")
                    projects = [{"name": p["name"], "path": str(resolve_path(p.get("path", p["name"]), topdir)),
                                 "revision": str(p.get("revision", "unknown")), "active": None}
                                for p in direct if isinstance(p, dict) and isinstance(p.get("name"), str)]
                except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as error:
                    messages.append(diagnostic("manifest-invalid", error, manifest_path))

    root_sets = {"boards": [], "socs": [], "dts": [], "snippets": []}

    def add_root(kind, value, origin):
        path = str(resolve_path(value, selected))
        if not any(item["path"] == path for item in root_sets[kind]):
            root_sets[kind].append({"path": path, "origin": origin})

    for kind, option, variable in (("boards", "board_roots", "BOARD_ROOT"), ("socs", "soc_roots", "SOC_ROOT")):
        for value in options.get(option, []):
            add_root(kind, value, "explicit")
        for value in split_paths(environment.get(variable)):
            add_root(kind, value, f"environment:{variable}")
        if (selected / ("boards" if kind == "boards" else "soc")).is_dir() and selected != zephyr:
            add_root(kind, selected, "selected directory")
    module_override = split_paths(environment.get("ZEPHYR_MODULES"))
    module_projects = ([{"name": Path(p).name, "path": str(resolve_path(p, selected)), "active": True,
                         "revision": None} for p in module_override] if module_override else list(projects))
    for value in split_paths(environment.get("EXTRA_ZEPHYR_MODULES")):
        module_projects.append({"name": Path(value).name, "path": str(resolve_path(value, selected)), "active": True, "revision": None})
    if len(module_projects) > MAX_PROJECTS:
        messages.append(diagnostic("module-limit", f"Only the first {MAX_PROJECTS} module candidates can be inspected."))
    modules = []
    seen = set()
    for project in module_projects[:MAX_PROJECTS]:
        check_cancel(cancel)
        path = Path(project["path"])
        if str(path) in seen or project.get("active") is False:
            continue
        seen.add(str(path))
        if not path.is_dir():
            modules.append({**project, "status": "missing", "is_module": False})
            messages.append(diagnostic("project-missing", f"Workspace project {project['name']} is not present locally.", path))
            continue
        inputs.extend([str(path / "zephyr"), str(path / "zephyr/module.yml"), str(path / "zephyr/module.yaml")])
        meta_path = next((p for p in (path / "zephyr/module.yml", path / "zephyr/module.yaml") if p.is_file()), None)
        meta = {}
        dependencies = []
        is_module = bool(meta_path or ((path / "zephyr/CMakeLists.txt").is_file() and (path / "zephyr/Kconfig").is_file()))
        status = "discovered" if project.get("active") is not None else "unresolved"
        if meta_path:
            try:
                meta = read_yaml(meta_path)
                build = meta.get("build", {})
                if not isinstance(meta.get("name", project["name"]), str):
                    raise ValueError("Module name must be text.")
                dependencies = build.get("depends", [])
                if not isinstance(dependencies, list) or any(not isinstance(value, str) for value in dependencies):
                    raise ValueError("Module build.depends must be a list of module names.")
                settings = build.get("settings", {})
                if not isinstance(settings, dict):
                    raise ValueError("Module build.settings must be a mapping.")
                module_roots = []
                for key, kind in (("board_root", "boards"), ("soc_root", "socs"), ("dts_root", "dts"), ("snippet_root", "snippets")):
                    value = settings.get(key)
                    if value is not None:
                        if not isinstance(value, str):
                            raise ValueError(f"Module {key} must be a path string.")
                        if project.get("active") is not None:
                            module_roots.append((kind, resolve_path(value, path)))
                for kind, root in module_roots:
                    add_root(kind, root, f"module:{project['name']}")
            except (OSError, ValueError, TypeError, AttributeError, yaml.YAMLError) as exc:
                status = "unsupported"
                dependencies = []
                messages.append(diagnostic("module-metadata", exc, meta_path))
        revision = None
        if (path / ".git").exists():
            try:
                revision = probe(["git", "-C", str(path), "rev-parse", "HEAD"], cwd=selected, environ=environment, cancel=cancel)
            except ProbeError as exc:
                messages.append(diagnostic("project-revision", exc, path))
        modules.append({"name": str(meta.get("name", project["name"])), "path": str(path), "status": status,
                        "is_module": is_module, "active": project.get("active"), "requested_revision": project.get("revision"),
                        "commit": revision, "source": str(meta_path) if meta_path else None, "dependencies": dependencies})
    by_name = {module["name"]: module for module in modules if module.get("is_module")}
    visiting, visited = set(), set()

    def check_dependencies(name):
        if name in visiting:
            messages.append(diagnostic("module-dependency-cycle", f"Module dependency cycle includes {name}.", by_name[name]["path"]))
            return
        if name in visited:
            return
        visiting.add(name)
        for dependency in by_name[name].get("dependencies", []):
            if dependency not in by_name:
                messages.append(diagnostic("module-dependency-missing", f"Module {name} requires unavailable module {dependency}.", by_name[name]["path"]))
            else:
                check_dependencies(dependency)
        visiting.remove(name)
        visited.add(name)

    for name in by_name:
        check_cancel(cancel)
        check_dependencies(name)
    for kind in root_sets:
        if zephyr:
            add_root(kind, zephyr, "Zephyr checkout")
    info = {"path": str(zephyr) if zephyr else None, "version": None, "commit": None, "dirty": None, "recommended_sdk": None}
    if zephyr:
        inputs.extend([str(zephyr / "VERSION"), str(zephyr / "SDK_VERSION"), str(zephyr / "scripts/schemas")])
        try:
            info["version"] = _version(zephyr / "VERSION")
        except (OSError, ValueError) as exc:
            messages.append(diagnostic("zephyr-version", exc, zephyr / "VERSION"))
        try:
            info["recommended_sdk"] = read_text(zephyr / "SDK_VERSION").strip()
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            messages.append(diagnostic("sdk-reference-invalid", exc, zephyr / "SDK_VERSION"))
        if (zephyr / ".git").exists():
            try:
                info["commit"] = probe(["git", "-C", str(zephyr), "rev-parse", "HEAD"], cwd=selected, environ=environment, cancel=cancel)
                info["dirty"] = bool(probe(["git", "-C", str(zephyr), "status", "--porcelain", "--untracked-files=normal"],
                                          cwd=selected, environ=environment, cancel=cancel))
            except ProbeError as exc:
                messages.append(diagnostic("zephyr-revision", exc, zephyr))
    return {"workspace": {"path": str(selected), "west_topdir": str(topdir) if topdir else None,
                          "manifest_path": str(manifest_path) if manifest_path else None, "manifest_resolved": manifest_resolved},
            "zephyr": info, "tools": tools, "modules": modules, "roots": root_sets,
            "diagnostics": messages, "inputs": inputs, "python": python,
            "environment": environment}
