"""Explain captured or expected overlay selection without reading application files."""

import hashlib
from pathlib import PurePosixPath
import re

from .default_overlay import default_overlay as default_overlay_metadata
from .fragments import profile_fragments


def _relative(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or ".." in path.parts:
        return None
    return value


def _catalogue(files):
    paths, cmake = set(), None
    if not isinstance(files, list) or len(files) > 20000:
        return paths, cmake, False
    for item in files:
        value = item.get("path") if isinstance(item, dict) else item
        path = _relative(value)
        if path is None:
            return paths, None, False
        paths.add(path)
        if path == "CMakeLists.txt" and isinstance(item, dict) and isinstance(item.get("content"), str):
            cmake = item["content"] if len(item["content"]) <= 1024 * 1024 else None
    return paths, cmake, True


def _default(value, cmake):
    if not isinstance(value, dict):
        return None, cmake, False
    try:
        expected = default_overlay_metadata(value.get("target"))
    except ValueError:
        return None, cmake, False
    if any(value.get(key) != expected[key] for key in expected):
        return None, cmake, False
    if cmake is None:
        return expected, cmake, True
    block = re.search(r"\n# Zephyr Utilities: retain automatic overlays and add this application's editable overlay\.\n"
                      r".*?# End Zephyr Utilities default overlay\.\n\n", cmake, re.DOTALL)
    valid = block is not None and hashlib.sha256(block[0].encode()).hexdigest() == expected["cmake_block_sha256"]
    return expected, cmake[:block.start()] + cmake[block.end():] if valid else cmake, valid


def _simple_cmake(content):
    if content is None:
        return False
    clean = re.sub(r"(?m)^\s*#[^\n]*", "", content)
    commands = list(re.finditer(r"([a-z_]+)\s*\(([^()]*)\)", clean, re.IGNORECASE))
    rest, end = [], 0
    for match in commands:
        rest.append(clean[end:match.start()])
        end = match.end()
        name, args = match[1].lower(), match[2].strip()
        patterns = {"cmake_minimum_required": r"VERSION\s+[0-9.]+",
                    "find_package": r"Zephyr\s+REQUIRED\s+HINTS\s+\$ENV\{ZEPHYR_BASE\}",
                    "project": r"[A-Za-z_][A-Za-z0-9_]*(?:\s+(?:C|CXX|LANGUAGES))*",
                    "target_sources": r"app\s+PRIVATE\s+[A-Za-z0-9_./+\s-]+"}
        if name not in patterns or re.fullmatch(patterns[name], args) is None:
            return False
    rest.append(clean[end:])
    return not "".join(rest).strip() and any(match[1].lower() == "find_package" for match in commands)


def _automatic(target, paths):
    """Predict the standard application overlay search; shortened names remain uncertain."""
    if not isinstance(target, str) or re.fullmatch(r"[A-Za-z0-9_.+@/-]+", target) is None:
        return set(), set()
    head, *qualifiers = target.split("/")
    board, _, revision = head.partition("@")
    full = "_".join([board, *qualifiers])
    short = "_".join([board, *qualifiers[1:]]) if qualifiers else full
    names = [full, *([full + "_" + revision.replace(".", "_")] if revision else [])]
    short_names = [short, *([short + "_" + revision.replace(".", "_")] if revision else [])]
    board_files = {f"boards/{name}.overlay" for name in names} & paths
    ambiguous = ({f"boards/{name}.overlay" for name in short_names} -
                 {f"boards/{name}.overlay" for name in names}) & paths
    soc_files = ({f"socs/{'_'.join(qualifiers)}.overlay"} & paths) if qualifiers else set()
    if board_files or soc_files or ambiguous:
        return board_files | soc_files, ambiguous
    root_files = {f"{name}.overlay" for name in names} & paths
    ambiguous = ({f"{name}.overlay" for name in short_names} - {f"{name}.overlay" for name in names}) & paths
    if root_files or ambiguous:
        return root_files, ambiguous
    return {"app.overlay"} & paths, set()


def _captured(context, source, target):
    if not isinstance(context, dict) or context.get("configured") is not True:
        return None
    if context.get("source_dir") is not None and context["source_dir"] != str(source):
        return None
    if context.get("requested_target") is not None and context["requested_target"] != target:
        return None
    tree = context.get("devicetree")
    if not isinstance(tree, dict) or not isinstance(tree.get("files"), list) or len(tree["files"]) > 20000:
        return None
    # Older contexts substituted [] when build_info omitted this field. Only a
    # positive observation, or a nonempty legacy list, distinguishes that case.
    if tree.get("files_observed") is not True and ("files_observed" in tree or not tree["files"]):
        return None
    result, seen = [], set()
    for value in tree["files"]:
        if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
            return None
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts:
            return None
        if str(path) not in seen:
            result.append(str(path))
            seen.add(str(path))
    dependencies = tree.get("dependencies", [])
    valid_dependencies = (isinstance(dependencies, list) and len(dependencies) <= 20000 and
                          all(isinstance(path, str) and len(path) <= 4096 and PurePosixPath(path).is_absolute() and
                              ".." not in PurePosixPath(path).parts and not any(ord(c) < 32 for c in path)
                              for path in dependencies))
    if not valid_dependencies:
        dependencies = []
    complete = valid_dependencies and tree.get("dependency_coverage") == "complete" and set(result).issubset(dependencies)
    return result, dependencies, complete


def overlay_usage(profile, source_dir, *, files=None, context=None, default_overlay=None):
    """Return a pure report from an application catalogue and optional captured inputs."""
    target = profile.get("target") if isinstance(profile, dict) else None
    source = PurePosixPath(str(source_dir))
    paths, cmake, catalogue_valid = _catalogue(files)
    default, cmake, default_valid = _default(default_overlay, cmake)
    overlay_paths = {path for path in paths if path.endswith(".overlay")}
    default_path = default["path"] if default else None
    report = {"schema_version": 1, "target": target, "status": "unknown",
              "message": "Overlay use is unknown until configuration records its Device Tree inputs.",
              "files": [], "board_files": []}
    if default:
        report["default_overlay"] = default

    def role(path):
        if path == default_path:
            return "default"
        if path.startswith("upstream/"):
            return "preserved"
        if path.startswith(("boards/", "socs/")):
            return "board_specific"
        return "application"

    captured = _captured(context, source, target) if source.is_absolute() and ".." not in source.parts else None
    if captured is not None:
        direct, dependencies, complete = captured
        active = []
        included = [path for path in dependencies if path.endswith(".overlay") and path not in direct]
        for value in [*direct, *included]:
            path = PurePosixPath(value)
            if path.is_relative_to(source) and path.suffix == ".overlay":
                relative = path.relative_to(source).as_posix()
                overlay_paths.add(relative)
                active.append(relative)
            else:
                report["board_files"].append({"path": value, "state": "active", "role":
                    "external_overlay" if path.suffix == ".overlay" else "board_source",
                    "reason": "Recorded as a direct Device Tree input." if value in direct else
                    "Recorded as a transitive Device Tree include."})
        report.update(status="confirmed", scope="complete" if complete else "direct_inputs",
                      message="Overlay inputs confirmed by the captured configuration." if complete else
                      "Direct overlay inputs confirmed; transitive use is not fully recorded.")
        for path in [*active, *sorted(overlay_paths - set(active))]:
            used = path in active
            reason = ("Recorded as a direct Device Tree input." if str(source / path) in direct else
                      "Recorded as a transitive Device Tree include.") if used else (
                "Not selected directly; transitive use is unverified." if not complete else
                "Preserved upstream example; not used by this configuration." if role(path) == "preserved" else
                "Board-specific file; not selected by this configuration." if role(path) == "board_specific" else
                "Not recorded as an input of this configuration.")
            report["files"].append({"path": path, "state": "active" if used else "not_used" if complete else "unknown",
                                    "reason": reason, "role": role(path)})
        return report

    if context is not None:
        report["files"] = [{"path": path, "state": "unknown", "role": role(path),
                            "reason": "This attempt has no usable captured Device Tree input list for this application and target."}
                           for path in sorted(overlay_paths)]
        return report

    try:
        choice = profile_fragments(profile)["overlays"]
    except (ValueError, TypeError, AttributeError):
        choice = {"mode": "unknown", "files": []}
    simple = catalogue_valid and _simple_cmake(cmake)
    expected, ambiguous = _automatic(target, paths) if simple else (set(), set())
    if choice["mode"] == "replace":
        expected, ambiguous = set(choice["files"]), set()
    elif choice["mode"] == "append":
        expected.update(choice["files"])
    overlay_paths.update(choice["files"])
    if default_path in overlay_paths and default_valid and default["target"] == target and choice["mode"] in {"auto", "append"}:
        expected.add(default_path)
    known = catalogue_valid and simple and not ambiguous and choice["mode"] != "unknown"
    for path in sorted(overlay_paths):
        if path in expected:
            state, reason = "expected", "Selected for the next configuration; actual use is not confirmed yet."
        elif path in ambiguous:
            state, reason = "unknown", "Shortened board overlay names depend on the board's SoC metadata; configure to confirm."
        elif known or (path == default_path and default_valid and (default["target"] != target or choice["mode"] == "replace")):
            state = "unknown"
            reason = ("Preserved upstream example; outside automatic overlay selection." if role(path) == "preserved" else
                      "Default overlay belongs to a different exact target." if path == default_path and default["target"] != target else
                      "Replace mode does not automatically add this overlay." if choice["mode"] == "replace" else
                      "Board-specific file does not match the selected target." if role(path) == "board_specific" else
                      "Not selected by the standard automatic overlay search.") + " Transitive use needs configuration evidence."
        else:
            state, reason = "unknown", "Application CMake selection is not available or needs configuration to resolve."
        report["files"].append({"path": path, "state": state, "reason": reason, "role": role(path)})
    if known:
        report.update(status="expected", scope="direct_inputs",
                      message="Expected direct overlay selection; configure to confirm all actual inputs.")
    return report
