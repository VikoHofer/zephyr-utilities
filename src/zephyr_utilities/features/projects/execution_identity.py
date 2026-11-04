"""Freshness of the selected build environment, independent of SDK search listings."""

from pathlib import Path
import time

from .analyzer import digest, fingerprint, metadata_inputs
from .probing import check_cancel


GUARD_VERSION = 1


def execution_guard(snapshot, profile, *, cancel=None):
    """Hash selected metadata and tools without invalidating on unrelated workspace apps.

    This guards the observed environment and hardware metadata. It does not claim to
    archive or hash every source file or every transitive configure/build dependency.
    """
    started_ns = time.time_ns()
    if snapshot.get("status") not in {"complete", "partial"}:
        raise ValueError("Analyze a stable environment before creating or building an application.")
    workspace = snapshot.get("workspace", {})
    if workspace.get("path") != profile.get("workspace") or workspace.get("manifest_resolved") is not True:
        raise ValueError("The selected build environment needs a resolved manifest in the same workspace.")
    board = next((item for item in snapshot.get("boards", []) if item.get("id") == profile.get("board_id")), None)
    if board is None or board.get("status") != "discovered" or profile.get("target") not in board.get("targets", []):
        raise ValueError("The selected board target is no longer available. Analyze and select it again.")
    sdk = next((item for item in snapshot.get("sdks", []) if item.get("id") == profile.get("sdk", {}).get("id")), None)
    if (sdk is None or sdk.get("status") != "discovered" or not sdk.get("fingerprint") or
            not any(tool.get("family") == "GNU" for tool in sdk.get("toolchains", []))):
        raise ValueError("The selected installed GNU SDK is no longer available. Analyze and select it again.")
    zephyr = snapshot.get("zephyr", {})
    if not zephyr.get("path") or not workspace.get("west_topdir") or not workspace.get("manifest_path"):
        raise ValueError("The selected environment has incomplete Zephyr or workspace metadata.")
    roots = [Path(root["path"]) / directory for kind, directory in (("boards", "boards"), ("socs", "soc"))
             for root in snapshot.get("roots", {}).get(kind, [])]
    tracked = [str(Path(workspace["west_topdir"]) / ".west/config"), workspace["manifest_path"],
               str(Path(zephyr["path"]) / "VERSION"), str(Path(zephyr["path"]) / "SDK_VERSION")]
    modules = snapshot.get("modules", [])
    for module in modules:
        if module.get("source"):
            tracked.append(module["source"])
        if module.get("path"):
            # Missing files participate too: introducing module metadata changes identity.
            tracked.extend(str(Path(module["path"]) / name) for name in (
                "zephyr/module.yml", "zephyr/module.yaml", "zephyr/CMakeLists.txt", "zephyr/Kconfig"))
    tree, warnings = metadata_inputs(roots, cancel=cancel)
    before, changed, more_warnings = fingerprint(tracked + tree, cancel=cancel, started_ns=started_ns)
    after_tree, after_warnings = metadata_inputs(roots, cancel=cancel)
    after, changed_after, final_warnings = fingerprint(tracked + after_tree, cancel=cancel, started_ns=started_ns)
    if warnings or more_warnings or after_warnings or final_warnings:
        raise ValueError("Build environment inputs could not be inspected completely. Resolve discovery limits and rescan.")
    if changed or changed_after or before != after:
        raise ValueError("Build environment inputs changed during inspection. Preview the operation again.")
    check_cancel(cancel)
    return digest({"guard_version": GUARD_VERSION, "workspace": workspace,
                   "zephyr": zephyr, "tools": snapshot.get("tools", []), "modules": modules,
                   "roots": snapshot.get("roots", {}), "target": profile["target"], "board": board,
                   "sdk": {key: sdk.get(key) for key in ("id", "path", "version", "fingerprint")},
                   "metadata": after})
