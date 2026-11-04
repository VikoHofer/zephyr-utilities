"""Footprint comparison with explicit uncertainty and independent RAM/ROM totals."""

from __future__ import annotations

from collections import defaultdict
import json


def _groups(report: dict) -> dict:
    groups: dict = defaultdict(list)
    for leaf in report.get("leaves", []):
        # Addresses move when preceding code/data changes; native identifiers
        # can be duplicated. Match by semantic source path, section and name.
        key = (leaf["path"], leaf.get("section") or "", leaf["name"])
        groups[key].append(leaf)
    return groups


def _display(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if not isinstance(value, str) else value


def _attribution_candidates(changes: list[dict], old_groups: dict, new_groups: dict) -> int:
    """Flag matching ranges without treating addresses as stable symbol IDs.

    Zephyr can select different alias names or DWARF source locations for the
    same storage. Coincident ranges alone are not enough to merge identities.
    """
    ranges = {"removed": defaultdict(list), "added": defaultdict(list)}
    for row in changes:
        if row["status"] not in ranges:
            continue
        key = (row["path"], row["section"] or "", row["name"])
        groups = old_groups if row["status"] == "removed" else new_groups
        for leaf in groups[key]:
            address = leaf.get("address")
            try:
                address = int(address, 0) if isinstance(address, str) else address
            except ValueError:
                continue
            if isinstance(address, int) and leaf["size"] > 0:
                ranges[row["status"]][(address, leaf.get("section"), leaf["size"])].append(row)
    common = ranges["removed"].keys() & ranges["added"].keys()
    message = (
        "An added and a removed report entry have the same address, section and size. "
        "This may reflect alias or source attribution differences; it does not prove "
        "symbol identity across builds. Entries remain separate."
    )
    for key in common:
        for row in ranges["removed"][key] + ranges["added"][key]:
            row["attribution_warning"] = message
    return len(common)


def compare_snapshots(baseline: dict, candidate: dict) -> dict:
    """Compare normalized snapshots, aggregating ambiguous duplicate symbols.

    A missing report is unknown, not a zero-size image. Percentage changes are
    deliberately undefined when the baseline is zero.
    """
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise ValueError("Both comparison inputs must be snapshots.")
    result = {
        "baseline": {"id": baseline.get("id"), "name": baseline.get("name")},
        "candidate": {"id": candidate.get("id"), "name": candidate.get("name")},
        "totals": {}, "changes": {}, "warnings": [], "config_changes": [],
    }
    warnings = result["warnings"]
    for side, snapshot in (("Baseline", baseline), ("Candidate", candidate)):
        for warning in snapshot.get("warnings", []):
            warnings.append(f"{side}: {warning}")

    for memory in ("ram", "rom"):
        left = (baseline.get("reports") or {}).get(memory)
        right = (candidate.get("reports") or {}).get(memory)
        before = left["total_size"] if left is not None else None
        after = right["total_size"] if right is not None else None
        available = before is not None and after is not None
        delta = after - before if available else None
        result["totals"][memory] = {
            "before": before, "after": after, "delta": delta,
            "percent": delta / before * 100 if available and before else None,
        }
        result["changes"][memory] = []
        for side, report in (("Baseline", left), ("Candidate", right)):
            if report is not None:
                warnings.extend(f"{side} {memory.upper()}: {warning}" for warning in report.get("warnings", []))
        if not available:
            warnings.append(f"{memory.upper()} comparison is unavailable because one or both snapshots have no report.")
            continue

        left_groups, right_groups = _groups(left), _groups(right)
        ambiguous_count = 0
        for key in left_groups.keys() | right_groups.keys():
            old, new = left_groups.get(key, []), right_groups.get(key, [])
            before_size, after_size = sum(n["size"] for n in old), sum(n["size"] for n in new)
            ambiguous = len(old) > 1 or len(new) > 1
            ambiguous_count += ambiguous
            status = (
                "added" if not old else "removed" if not new else
                "changed" if before_size != after_size else "unchanged"
            )
            path, section, name = key
            result["changes"][memory].append({
                "name": name, "path": path, "section": section or None,
                "before": before_size, "after": after_size, "delta": after_size - before_size,
                "status": status, "ambiguous": ambiguous,
                "baseline_count": len(old), "candidate_count": len(new),
            })
        result["changes"][memory].sort(key=lambda row: (-abs(row["delta"]), row["path"], row["section"] or "", row["name"]))
        if ambiguous_count:
            warnings.append(
                f"{memory.upper()}: {ambiguous_count} duplicate symbol group(s) were aggregated. "
                "Individual symbol correspondence is ambiguous."
            )
        attribution_count = _attribution_candidates(result["changes"][memory], left_groups, right_groups)
        if attribution_count:
            warnings.append(
                f"{memory.upper()}: added and removed entries share {attribution_count} address "
                "range(s) with equal section and size. Possible alias/source attribution changes "
                "are flagged; addresses alone do not establish identity, so entries remain separate."
            )

    before_build, after_build = baseline.get("build"), candidate.get("build")
    if not before_build or not after_build:
        warnings.append("Build metadata is unavailable for one or both snapshots; comparison conditions cannot be verified.")
    else:
        fields = {
            "board": "Board", "zephyr_version": "Zephyr version", "toolchain": "Toolchain",
            "domain": "Image/domain", "compiler": "Compiler", "model_hash": "Model hash",
        }
        unknown = []
        for field, label in fields.items():
            before, after = before_build.get(field), after_build.get(field)
            if before is None or after is None:
                if field in {"board", "zephyr_version", "toolchain"}:
                    unknown.append(label)
            elif before != after:
                warnings.append(f"{label} differs: {_display(before)} → {_display(after)}. Interpret the delta in that context.")
        if unknown:
            warnings.append("Comparison metadata is incomplete: " + ", ".join(unknown) + ".")

    before_config, after_config = baseline.get("config") or {}, candidate.get("config") or {}
    for key in sorted(before_config.keys() | after_config.keys()):
        before, after = before_config.get(key), after_config.get(key)
        if before != after:
            result["config_changes"].append({"key": key, "before": before, "after": after})
    critical = {
        "CONFIG_SIZE_OPTIMIZATIONS", "CONFIG_SIZE_OPTIMIZATIONS_AGGRESSIVE",
        "CONFIG_SPEED_OPTIMIZATIONS", "CONFIG_DEBUG_OPTIMIZATIONS", "CONFIG_NO_OPTIMIZATIONS",
        "CONFIG_LTO", "CONFIG_THREAD_ANALYZER", "CONFIG_THREAD_ANALYZER_AUTO",
        "CONFIG_SYS_HEAP_RUNTIME_STATS", "CONFIG_INIT_STACKS", "CONFIG_THREAD_RUNTIME_STATS",
    }
    changed_critical = [row["key"] for row in result["config_changes"] if row["key"] in critical]
    if changed_critical:
        warnings.append("Optimization or profiling settings differ: " + ", ".join(changed_critical) + ".")
    # Multiple paths can carry the same upstream diagnostic; keep output concise.
    result["warnings"] = list(dict.fromkeys(warnings))
    return result
