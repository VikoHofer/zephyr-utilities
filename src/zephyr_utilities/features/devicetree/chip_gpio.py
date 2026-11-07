"""Resolve observed GPIO specifiers, including one verified connector mapping."""


_UINT32 = 0xffffffff


def _cells(value, label):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(cell) is not int or not 0 <= cell <= _UINT32 for cell in value)):
        raise ValueError(f"{label} needs two unsigned pin/flags cells.")
    return tuple(value)


def _references(row):
    result = {"&" + label for label in row.get("labels", []) if isinstance(label, str)}
    if isinstance(row.get("reference"), str):
        result.add(row["reference"])
    if isinstance(row.get("path"), str) and row["path"].startswith("/"):
        result.add("&{" + row["path"] + "}")
    return result


def _provider(payload, identifier, path=None):
    found = [row for row in payload.get("gpio_providers", []) if row.get("id") == identifier]
    if len(found) != 1 or path is not None and found[0].get("path") != path:
        raise ValueError("The GPIO mapping needs one verified final GPIO provider; chained mappings are unsupported.")
    row = found[0]
    if (row.get("binding", {}).get("state") != "matched" or row.get("supported") is not True
            or row.get("cells") != ["pin", "flags"] or type(row.get("pin_count")) is not int
            or not 0 < row["pin_count"] <= _UINT32):
        raise ValueError("The GPIO provider needs a matched pin/flags binding and a known pin count.")
    if any(mapping.get("id") == row.get("id") or mapping.get("path") == row.get("path")
           for mapping in payload.get("connector_maps", [])):
        raise ValueError("Chained or cyclic GPIO connector mappings are unsupported.")
    return row


def _checked(provider, cells):
    if cells[0] >= provider["pin_count"]:
        raise ValueError("The resolved GPIO pin is outside its provider's observed range.")
    return provider, cells


def resolve_gpio(observed_or_payload, reference, cells):
    """Return ``(provider_row, (pin, flags))`` or fail on unverified evidence.

    A connector must have a matched two-cell binding and exactly one matching
    map entry. Masking applies only to the input, as in Zephyr edtlib ``_map``;
    pass-through bits replace the corresponding parent specifier bits. Chained
    connectors remain unavailable rather than being interpreted approximately.
    """
    if not isinstance(observed_or_payload, dict) or not isinstance(reference, str):
        raise ValueError("Choose an observed GPIO reference and pin/flags specifier.")
    payload = observed_or_payload.get("payload", observed_or_payload)
    if not isinstance(payload, dict):
        raise ValueError("The GPIO topology is unavailable.")
    selected = _cells(cells, "The GPIO specifier")
    targets = {(row.get("id"), row.get("path")): row
               for row in [*payload.get("nodes", []), *payload.get("gpio_providers", [])]
               if reference in _references(row)}
    if len(targets) != 1:
        raise ValueError("The GPIO reference is unknown or ambiguous in the observed topology.")
    target = next(iter(targets.values()))
    maps = [mapping for mapping in payload.get("connector_maps", [])
            if mapping.get("path") == target.get("path") or mapping.get("id") == target.get("id")]
    if not maps:
        return _checked(_provider(payload, target.get("id"), target.get("path")), selected)
    if len(maps) != 1:
        raise ValueError("The GPIO connector has ambiguous mapping evidence.")
    mapping = maps[0]
    if (target.get("binding", {}).get("state") != "matched"
            or type(target.get("properties", {}).get("#gpio-cells")) is not int
            or target["properties"]["#gpio-cells"] != 2
            or mapping.get("path") != target.get("path")
            or mapping.get("id", target.get("id")) != target.get("id") or mapping.get("reason")):
        raise ValueError("The connector needs a matched two-cell binding and decoded GPIO map.")
    mask = _cells(mapping["mask"], "The GPIO map mask") if mapping.get("mask") is not None else (_UINT32, _UINT32)
    pass_thru = _cells(mapping["pass_thru"], "The GPIO map pass-through") if mapping.get("pass_thru") is not None else (0, 0)
    masked = tuple(cell & field for cell, field in zip(selected, mask))
    entries = mapping.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("The GPIO connector has no decoded mapping entries.")
    matches = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("The GPIO connector contains an undecoded mapping entry.")
        child = _cells(entry.get("child_cells"), "The GPIO map child")
        parent = _cells(entry.get("parent_cells"), "The GPIO map parent")
        provider = _provider(payload, entry.get("provider_id"), entry.get("provider_path"))
        if child == masked:
            resolved = tuple((value & field) | (original & (_UINT32 ^ field))
                             for value, original, field in zip(selected, parent, pass_thru))
            matches.append(_checked(provider, resolved))
    if len(matches) != 1:
        raise ValueError("The GPIO connector specifier has no unique matching map entry.")
    return matches[0]
