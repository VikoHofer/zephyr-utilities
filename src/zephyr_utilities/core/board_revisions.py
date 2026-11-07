"""Pure, bounded Zephyr board revision metadata and selection semantics."""

import re

from .hardware_metadata import MAX_RECORDS, MetadataError


_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.+-]{0,159}\Z")
_SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]+\.[0-9]+\Z")
_SEMVER_REQUEST = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+){0,2}\Z")
_PATTERNS = {"major.minor.patch": _SEMVER, "letter": re.compile(r"[A-Z]\Z"),
             "number": re.compile(r"[0-9]+\Z")}


def _name(value, *, custom=False):
    if custom and value == "":
        return value
    if not isinstance(value, str) or not _NAME.fullmatch(value):
        raise MetadataError("Revision must be a simple, bounded text identifier.")
    return value


def parse_board_revisions(value):
    """Return declared revisions, default, format and exact-match requirement.

    Custom declarations remain useful catalogue evidence. Resolving their active
    revision requires checkout code and is deliberately a separate operation.
    """
    if value is None:
        return [], None, None, False
    if not isinstance(value, dict) or set(value) - {"format", "default", "exact", "revisions"}:
        raise MetadataError("Revision must be a mapping with supported metadata fields.")
    fmt = value.get("format")
    if not isinstance(fmt, str) or fmt not in {*_PATTERNS, "custom"}:
        raise MetadataError("Unsupported revision format.")
    exact = value.get("exact", False)
    if type(exact) is not bool:
        raise MetadataError("Revision exact must be a boolean.")
    entries = value.get("revisions", [])
    if not isinstance(entries, list) or len(entries) > MAX_RECORDS:
        raise MetadataError("Revisions must be a bounded list.")
    revisions = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"name"}:
            raise MetadataError("Revision name must contain only its name field.")
        revisions.append(_name(entry["name"], custom=fmt == "custom"))
    default = value.get("default")
    if default is not None:
        _name(default, custom=fmt == "custom")
    if fmt != "custom" and (not revisions or default not in revisions):
        raise MetadataError("Revision metadata needs a declared default and revision list.")
    if fmt in _PATTERNS and any(not _PATTERNS[fmt].fullmatch(name) for name in revisions):
        raise MetadataError("Revision names do not match the declared format.")
    if len(set(revisions)) != len(revisions):
        raise MetadataError("Revision names are duplicated.")
    return revisions, default, fmt, exact


def _normalized(value, fmt):
    _name(value)
    pattern = _SEMVER_REQUEST if fmt == "major.minor.patch" else _PATTERNS[fmt]
    if not pattern.fullmatch(value):
        raise MetadataError(f"Requested board revision does not match the {fmt} format.")
    if fmt == "major.minor.patch":
        value += ".0" * (2 - value.count("."))
    return value


def _key(value, fmt):
    return tuple(int(part) for part in value.split(".")) if fmt == "major.minor.patch" else value


def resolve_board_revision(value, requested=None):
    """Resolve supported declarative revision metadata without executing CMake.

    Preserve the user's spelling separately from the effective declared revision.
    Standard defaults and explicit selections follow Zephyr's board_check_revision;
    source discovery can therefore use the same revision suffix as Configure.
    """
    revisions, default, fmt, exact = parse_board_revisions(value)
    if requested is not None:
        _name(requested)
    result = {"requested": requested, "effective": None, "default": default,
              "format": fmt, "match": "none", "exact": exact}
    if fmt is None:
        if requested is not None:
            raise MetadataError("This board does not declare revisions; an explicit revision is unavailable.")
        return result
    if fmt == "custom":
        raise MetadataError("Custom board revision resolution requires revision.cmake; automatic discovery does not execute that script.")
    selected = _normalized(default if requested is None else requested, fmt)
    if selected in revisions:
        result.update(effective=selected, match="default" if requested is None else "exact")
        return result
    if exact:
        raise MetadataError("The requested board revision is not declared; this board requires an exact revision match.")
    if fmt == "number":
        # Zephyr's numeric fallback compares against an unset active revision.
        # Unlike VERSION/STR comparisons this cannot establish a predecessor in
        # the inspected board_check_revision implementation. Do not silently
        # select a different source tree than its Configure command accepts.
        raise MetadataError("Compatible numeric board revision selection is not established by the supported Zephyr revision rules; select a declared numeric revision.")
    candidates = [revision for revision in revisions if _key(revision, fmt) <= _key(selected, fmt)]
    if not candidates:
        raise MetadataError("The requested board revision is older than every declared revision; no compatible revision is available.")
    # VERSION_GREATER_EQUAL replaces an equal candidate in declaration order.
    # Keep that behavior for differently spelled but numerically equal versions.
    effective = max(enumerate(candidates), key=lambda item: (_key(item[1], fmt), item[0]))[1]
    result.update(effective=effective, match="default" if requested is None else "compatible")
    return result
