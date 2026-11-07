"""Pure plans for a missing application overlay and its selected recipe inclusion."""

from copy import deepcopy

from .default_overlay import default_overlay
from .fragments import fragment_path, normalize_fragments, profile_fragments
from .overlay_usage import overlay_usage


def inclusion(profile, application, files, default, relative, *, revision_resolution=None):
    """Retain existing inputs; add only this path when its use is not predicted."""
    relative = fragment_path(relative, "overlays")
    prospective = deepcopy(files) if isinstance(files, list) else []
    if not any(item.get("path") == relative for item in prospective):
        prospective.append({"path": relative})
    selected = deepcopy(profile)
    report = overlay_usage(selected, application, files=prospective, default_overlay=default,
                           revision_resolution=revision_resolution)
    expected = [item["path"] for item in report["files"] if item["state"] == "expected"]
    required = relative not in expected
    if required:
        fragments = profile_fragments(selected)
        choice = fragments["overlays"]
        fragments["overlays"] = {"mode": "append" if choice["mode"] == "auto" else choice["mode"],
                                  "files": list(dict.fromkeys([*choice["files"], relative]))}
        selected.update(schema_version=2, fragments=normalize_fragments(fragments))
        report = overlay_usage(selected, application, files=prospective, default_overlay=default,
                               revision_resolution=revision_resolution)
    return selected, report, required


def draft_destination(profile, application, files, default, *, revision_resolution=None):
    """Return proposed evidence, never claim that a missing file is already selected."""
    if not isinstance(files, list):
        return None
    if default is not None:
        try:
            if not isinstance(default, dict) or default != default_overlay(default.get("target")):
                return None
        except ValueError:
            return None
    paths = {item["path"] for item in files}
    choice = profile_fragments(profile)["overlays"]
    current = overlay_usage(profile, application, files=files, default_overlay=default,
                            revision_resolution=revision_resolution)
    active = {item["path"] for item in current["files"] if item["state"] == "expected"}
    missing = [path for path in choice["files"] if path not in paths]
    if len(choice["files"]) == 1 and len(missing) == 1:
        relative = missing[0]
    elif default and default.get("target") == profile["target"] and default["path"] not in paths and choice["mode"] != "replace":
        relative = default["path"]
    elif choice["mode"] == "auto" and not active:
        relative = default_overlay(profile["target"])["path"]
    else:
        return None
    if relative in paths or active & paths:
        return None
    try:
        fragment_path(relative, "overlays")
    except ValueError:
        return None
    _, report, required = inclusion(profile, application, files, default, relative,
                                    revision_resolution=revision_resolution)
    selected = [item["path"] for item in report["files"] if item["state"] == "expected"]
    missing_default = (default is not None and default["target"] == profile["target"]
                       and choice["mode"] != "replace" and default["path"] not in paths
                       and default["path"] != relative)
    supported = report["status"] == "expected" and selected == [relative] and not missing_default
    reason = ("" if supported else "The new overlay's complete input selection cannot be determined from this configuration. Source editing and Save remain available; pin activation requires a supported single-overlay selection.")
    if missing_default:
        reason = (f"This configuration also requires the missing default overlay {default['path']}. "
                  "Source editing and Save remain available; restore that overlay or adjust the selected configuration before pin activation.")
    return {"path": relative, "target": profile["target"], "supported": supported,
            "reason": reason,
            "registration_required": required, "input_evidence": report}
