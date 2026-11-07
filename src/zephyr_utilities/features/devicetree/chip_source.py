"""Small source-preserving edits for validated chip activation plans."""

import re

from .source import SourceDocument, SourceError


_LABEL = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")
_PROPERTY = re.compile(r"[A-Za-z_#][A-Za-z_0-9,#.+?-]*")
_HEADERS = {"zephyr/dt-bindings/gpio/gpio.h", "zephyr/dt-bindings/pinctrl/nrf-pinctrl.h",
            "zephyr/dt-bindings/pinctrl/stm32-pinctrl.h"}


def _apply(document, edit):
    return document._plan("chip-source-step", [edit]).apply(document)


def reference_paths(observed):
    return {"&" + label: row["path"] for row in observed["payload"]["nodes"] for label in row["labels"]}


def target_node(document, reference, paths):
    """Reject ambiguous contributors instead of guessing which override wins."""
    target_path = paths.get(reference, reference[2:-1] if reference.startswith("&{/") else
                            reference if reference.startswith("/") else None)
    resolved_paths = {}

    def source_path(node):
        if node.id not in resolved_paths:
            if node.name.startswith("&"):
                path = paths.get(node.name, node.name[2:-1] if node.name.startswith("&{/") else None)
            elif node.parent_id is not None:
                parent = source_path(document.node(node.parent_id))
                path = parent.rstrip("/") + "/" + node.name if parent else None
            else:
                path = "/" if node.name == "/" else None
            resolved_paths[node.id] = path
        return resolved_paths[node.id]

    matches = []
    for node in document.syntax.nodes:
        resolved = source_path(node)
        if (node.name == reference or reference.startswith("&") and reference[1:] in node.labels
                or target_path and resolved == target_path):
            matches.append(node)
    if len(matches) > 1:
        raise SourceError(f"{reference} has several source contributors. Combine its overrides before assigning pins.")
    return matches[0] if matches else None


def _ensure(document, reference, paths):
    selected = target_node(document, reference, paths)
    if selected:
        return document, selected
    if reference == "/zephyr,user":
        statement = b"/ {\n\tzephyr,user {\n\t};\n};"
    elif reference.startswith("&") and _LABEL.fullmatch(reference[1:]):
        statement = (reference + " {\n};").encode()
    else:
        raise SourceError("The activation target is not an understood node reference.")
    newline = document._newline(len(document.source))
    document = _apply(document, document._insert(None, statement.replace(b"\n", newline)))
    return document, target_node(document, reference, paths)


def _set(document, reference, name, value, paths):
    if not _PROPERTY.fullmatch(name) or value is not None and not isinstance(value, str):
        raise SourceError("Invalid generated property.")
    document, node = _ensure(document, reference, paths)
    found = [prop for prop in node.properties if prop.name == name]
    if len(found) > 1:
        raise SourceError(f"{reference} repeats {name}; edit that source explicitly first.")
    prop = found[0] if found else None
    statement = ("/delete-property/ " + name + ";" if value is None else
                 name + (" = " + value if value else "") + ";").encode()
    if prop is None:
        return _apply(document, document._insert(node, statement, property=True))
    if value is None and prop.kind == "delete-property":
        return document
    if value is not None and prop.kind == "property" and prop.value_start is not None and value:
        raw = document.source[prop.value_start:prop.value_end]
        if re.sub(rb"\s+", b"", raw) == re.sub(rb"\s+", b"", value.encode()):
            return document
        if any(token.kind == "comment" for token in document._tokens(prop.value_start, prop.value_end)):
            raise SourceError(f"Changing {name} would remove an embedded comment; edit that expression in source.")
        return _apply(document, document._edit(prop.value_start, prop.value_end, value.encode()))
    if any(token.kind == "comment" for token in document._tokens(prop.start, prop.end)) or prop.labels:
        raise SourceError(f"Changing {name} would remove comments or labels; edit that property in source.")
    return _apply(document, document._edit(prop.start, prop.end, statement))


def unique_prefix(document, observed, controller):
    base = "zu_" + re.sub(r"[^A-Za-z_0-9]", "_", controller.lstrip("&"))
    labels = {label for node in document.syntax.nodes for label in node.labels}
    labels.update(label for row in observed["payload"]["nodes"] for label in row["labels"])
    for index in range(1000):
        prefix = base + (f"_{index}" if index else "")
        if not any(label == prefix or label.startswith(prefix + "_") for label in labels):
            return prefix
    raise SourceError("Too many local pin-state labels. Simplify this overlay before assigning another route.")


def apply_activation(document, observed, plan):
    """Apply internal adapter output; the caller owns guards and one history entry."""
    if document.syntax.blocked or document.syntax.deletions:
        raise SourceError("Repair unsupported source or node deletions before assigning chip pins.")
    paths = reference_paths(observed)
    current = document
    for header in plan.get("includes", []):
        if header not in _HEADERS:
            raise SourceError("The route requested an unsupported pin header.")
        directive = f"#include <{header}>".encode()
        if not any(token.kind == "directive" and current.source[token.start:token.end].strip() == directive
                   for token in current.syntax.tokens):
            position = 3 if current.source.startswith(b"\xef\xbb\xbf") else 0
            current = _apply(current, current._edit(position, position, directive + current._newline(position)))
    for block in plan.get("pinctrl_nodes", []):
        current, parent = _ensure(current, "&pinctrl", paths)
        parsed = SourceDocument.parse(("&pinctrl {\n" + block["source"] + "\n};").encode())
        roots = [node for node in parsed.syntax.nodes if node.parent_id is None]
        outer = roots[0] if len(roots) == 1 else None
        child = parsed.node(outer.children[0]) if outer and len(outer.children) == 1 else None
        if (parsed.syntax.blocked or outer is None or outer.name != "&pinctrl" or child is None
                or outer.properties or parsed.syntax.deletions
                or any(token.kind == "directive" for token in parsed.syntax.tokens)
                or child.labels != (block["label"],) or child.name != block["name"]
                or any(block["label"] in node.labels or node.parent_id == parent.id and node.name == block["name"]
                       for node in current.syntax.nodes)
                or "&" + block["label"] in paths):
            raise SourceError("The generated pin state has an invalid or already used identity.")
        newline = current._newline(parent.close_brace)
        raw = block["source"].encode().replace(b"\r\n", b"\n").replace(b"\n", newline)
        current = _apply(current, current._insert(parent, raw))
    for update in plan.get("updates", []):
        for name, value in update["properties"].items():
            current = _set(current, update["reference"], name, value, paths)
    # Intermediate lexical edits are collapsed to one public draft revision.
    return SourceDocument.parse(current.source, document_id=document.id, revision=document.revision + 1)
