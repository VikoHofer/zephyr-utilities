"""Validate and normalize Zephyr footprint reports without losing unattributed bytes."""

from __future__ import annotations

from collections.abc import Mapping
import posixpath

MAX_NODES = 200_000
MAX_DEPTH = 128
MAX_TEXT_LENGTH = 16_384
MAX_BYTES = 2**53 - 1  # Exact integer representation in the browser as well as Python.


def _bytes(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_BYTES:
        raise ValueError(f"{field} must be a non-negative integer number of bytes.")
    return value


def _text(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH:
        raise ValueError(f"{field} must be text of at most {MAX_TEXT_LENGTH} characters.")
    return value


def normalize_report(raw: dict, prefixes: Mapping[str, str] | None = None) -> dict:
    """Return a detached, validated tree, flat leaves, and accounting diagnostics.

    ``prefixes`` maps physical roots to semantic names, for example
    ``{'/work/zephyr': 'ZEPHYR_BASE', '/work/app/build': 'OUTPUT_DIR'}``.
    Addresses and native identifiers are retained as evidence but are never IDs.
    Declared totals remain authoritative when symbol attribution overlaps.
    """
    if not isinstance(raw, dict):
        raise ValueError("A report must be a JSON object.")
    total_size = _bytes(raw.get("total_size"), "Report total_size")
    if not isinstance(raw.get("symbols"), dict):
        raise ValueError("A report must contain a symbols tree.")
    if prefixes is not None and not isinstance(prefixes, Mapping):
        raise ValueError("Path prefixes must map physical roots to labels.")

    roots = []
    for physical, logical in (prefixes or {}).items():
        if physical and logical:
            physical = _text(physical, "Physical path prefix").replace("\\", "/").rstrip("/")
            logical = _text(logical, "Logical path prefix").strip("/")
            if physical and logical:
                roots.append((physical, logical))
    roots.sort(key=lambda pair: len(pair[0]), reverse=True)

    def normalize_path(path: str) -> str:
        path = path.replace("\\", "/")
        # Native report trees already use these labels. Expand them before
        # reapplying the most specific root (e.g. APPLICATION within WORKSPACE).
        for physical, logical in roots:
            if path == logical or path.startswith(logical + "/"):
                path = physical + path[len(logical):]
                break
        for physical, logical in roots:
            if path == physical or path.startswith(physical + "/"):
                return logical + path[len(physical):]
        return path

    warnings: list[str] = []
    leaves: list[dict] = []
    count = 0

    def new_id() -> str:
        nonlocal count
        count += 1
        if count > MAX_NODES:
            raise ValueError(f"The report exceeds the limit of {MAX_NODES:,} nodes.")
        return f"n{count}"

    def add_leaf(node: dict) -> None:
        leaves.append({key: value for key, value in node.items() if key != "children"})

    def remainder_node(parent: dict, amount: int) -> dict:
        return {
            "id": new_id(), "name": "(unattributed)", "size": amount,
            "identifier": None, "original_identifier": None,
            "path": f"{parent['path']}/(unattributed)".lstrip("/"),
            "raw_path": f"{parent['raw_path']}/(unattributed)".lstrip("/"),
            "section": None, "address": None, "children": [],
            "unattributed": True,
        }

    def visit(source: dict, parent_path: str, depth: int) -> dict:
        if depth > MAX_DEPTH:
            raise ValueError(f"The report exceeds the maximum tree depth of {MAX_DEPTH}.")
        if not isinstance(source, dict):
            raise ValueError("Each report tree node must be an object.")
        node_id = new_id()
        name = _text(source.get("name"), "Node name")
        size = _bytes(source.get("size"), f"Size of {name!r}")
        identifier = _text(source.get("identifier"), "Node identifier", nullable=True)
        section = _text(source.get("section"), "Node section", nullable=True)
        address = source.get("address")
        if address is not None:
            if isinstance(address, bool) or not isinstance(address, (str, int)):
                raise ValueError("Node address must be an integer, text, or null.")
            if isinstance(address, int):
                if not 0 <= address < 2**64:
                    raise ValueError("Node address must fit an unsigned 64-bit address.")
                # Hex strings preserve 64-bit addresses in a JavaScript client.
                address = hex(address)
            else:
                address = _text(address, "Node address")
        children = source.get("children", [])
        if not isinstance(children, list):
            raise ValueError(f"Children of {name!r} must be an array.")
        if len(children) + count > MAX_NODES:
            raise ValueError(f"The report exceeds the limit of {MAX_NODES:,} nodes.")

        raw_path = posixpath.join(parent_path, name.replace("\\", "/")) if depth else ""
        path = normalize_path(raw_path)
        # Some old report versions emit absolute identifiers instead of a
        # consistent semantic hierarchy. Use those when a known prefix matches.
        if identifier:
            candidate = normalize_path(identifier)
            if candidate != identifier.replace("\\", "/"):
                path = candidate

        node = {
            "id": node_id, "name": name, "size": size,
            "identifier": normalize_path(identifier) if identifier else identifier,
            "original_identifier": identifier, "path": path, "raw_path": raw_path,
            "section": section, "address": address, "children": [],
            "unattributed": name in {"(hidden)", "(unattributed)"},
        }
        if depth == 0 and size != total_size:
            warnings.append(
                f"Root size ({size} B) differs from report total ({total_size} B); "
                "the report total is used for the overview."
            )
            node["reported_size"] = size
            node["size"] = total_size
        node["children"] = [visit(child, raw_path, depth + 1) for child in children]
        child_total = sum(child["size"] for child in node["children"])
        if children or depth == 0:
            difference = node["size"] - child_total
            if difference > 0:
                remainder = remainder_node(node, difference)
                node["children"].append(remainder)
                add_leaf(remainder)
                warnings.append(
                    f"{path or 'Root'} has {difference} B without child attribution; "
                    "shown as (unattributed)."
                )
            elif difference < 0:
                warnings.append(
                    f"Child attribution for {path or 'Root'} exceeds its reported size "
                    f"by {-difference} B; symbols may overlap. The total is not increased."
                )
        else:
            add_leaf(node)
        return node

    tree = visit(raw["symbols"], "", 0)
    attributed_sum = sum(leaf["size"] for leaf in leaves)
    if attributed_sum != total_size:
        warnings.append(
            f"Leaf attribution sums to {attributed_sum} B, while the report total is "
            f"{total_size} B. Do not sum these leaves as an independent memory total."
        )
    return {"total_size": total_size, "tree": tree, "leaves": leaves, "warnings": warnings}
