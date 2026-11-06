"""Bounded local dependency evidence and resource diagnostics, without preprocessing."""

from collections import defaultdict
import hashlib
import re

from .binding_service import BindingService, _literal


MAX_REFERENCES = 4096
MAX_DIAGNOSTICS = 256
MAX_PIN_STATE_CONSUMERS = 256


def location(document, node, prop=None):
    return {"node_id": node.id, "property_id": prop.id if prop else None,
            "path": document._path(node), "name": prop.name if prop else node.name,
            "start": prop.start if prop else node.start, "end": prop.end if prop else node.end}


def diagnostic(code, severity, message, locations=(), *, repair="source"):
    locations = list(locations)
    identity = repr((code, message, [(item.get("node_id"), item.get("property_id"), item.get("start")) for item in locations]))
    return {"id": hashlib.sha256(identity.encode()).hexdigest()[:24], "code": code, "severity": severity,
            "message": message, "locations": locations,
            "repair_choices": [{"kind": repair, "label": "Inspect " + ("source" if repair == "source" else repair),
                                "location": item} for item in locations]}


def _active(document, node):
    """Local status only: absent status is implicit; expressions never mean enabled."""
    unknown = False
    while node:
        props = [prop for prop in node.properties if prop.name == "status"]
        value = _literal(document, node, "status")
        if props:
            if value and value.kind == "string" and value.value == "disabled":
                return "disabled"
            if not value or value.kind != "string" or value.value not in {"okay", "ok"}:
                unknown = True
        if node.name.startswith("&"):
            unknown = True
        node = document.node(node.parent_id) if node.parent_id else None
    return "unknown" if unknown else "enabled"


def build_resource_graph(document, bindings):
    """Keep all literal consumers, including ambiguous and property-label references."""
    source_nodes = list(document.syntax.nodes)
    binding_rows = {row["node_id"]: row for row in bindings.get("nodes", [])}
    unique = {target["id"] for target in bindings.get("targets", [])}
    paths, labels, property_labels = defaultdict(list), defaultdict(list), defaultdict(list)
    for node in source_nodes:
        path = document._path(node)
        if path:
            paths[path].append(node.id)
        for label in node.labels:
            labels[label].append(node.id)
        for prop in node.properties:
            for label in prop.labels:
                property_labels[label].append((node.id, prop.id))
    pin_roots = [node for node in source_nodes if node.name == "&pinctrl" and node.parent_id is None]

    def unique_pin_declaration(node):
        if len(pin_roots) != 1 or node is pin_roots[0] or any(item.name.startswith("&{") for item in source_nodes):
            return False
        current = node
        while current and current is not pin_roots[0]:
            if current.name.startswith("&") or sum(item.parent_id == current.parent_id and item.name == current.name for item in source_nodes) != 1:
                return False
            if any(len(labels[label]) + len(property_labels[label]) != 1 for label in current.labels):
                return False
            if any(item.name in {"&" + label for label in current.labels} for item in source_nodes):
                return False
            current = document.node(current.parent_id) if current.parent_id else None
        return current is pin_roots[0] and document.can_edit_node(node.id)

    unique.update(node.id for node in source_nodes if unique_pin_declaration(node))
    nodes = [{"node_id": node.id, "key": "node:" + document._path(node) if node.id in unique and document._path(node) else None,
              "path": document._path(node), "name": node.name, "labels": list(node.labels),
              "start": node.start, "end": node.end, "parent_id": node.parent_id,
              "status": _active(document, node), "unique": node.id in unique} for node in source_nodes]
    references, seen, unknown_properties = [], set(), []

    def add(reference, start, end, kind, source_node=None, prop=None):
        if (start, end, reference) in seen:
            return
        seen.add((start, end, reference))
        if source_node is None:
            containing = [node for node in source_nodes if node.start <= start < node.end]
            source_node = min(containing, key=lambda node: node.end - node.start) if containing else None
            if source_node:
                prop = next((item for item in source_node.properties if item.start <= start < item.end), None)
                if prop is None:
                    kind = "declaration-reference"
        target_properties = []
        if reference.startswith("&{"):
            targets = list(paths.get(reference[2:-1], []))
        else:
            label = reference.removeprefix("&")
            targets = list(labels.get(label, []))
            for owner, target_property in property_labels.get(label, []):
                targets.append(owner)
                target_properties.append(target_property)
        count = len(targets)
        targets = list(dict.fromkeys(targets))
        references.append({"id": f"reference:{start}:{end}", "source_node_id": source_node.id if source_node else None,
                           "property_id": prop.id if prop else None, "target_node_ids": targets,
                           "target_property_ids": target_properties, "reference": reference, "kind": kind,
                           "resolution": "resolved" if count == 1 else "ambiguous" if count else "unresolved",
                           "start": start, "end": end,
                           "location": location(document, source_node, prop) if source_node else
                           {"node_id": None, "property_id": None, "path": None, "name": reference, "start": start, "end": end}})

    for item in document.reference_inventory()["references"]:
        add(item["reference"], item["start"], item["end"], item["kind"])
    for node in source_nodes:
        metadata = {prop["name"]: prop for prop in binding_rows.get(node.id, {}).get("properties", [])}
        for prop in node.properties:
            if prop.kind != "property":
                continue
            value = document.property_value(prop.id)
            if value is None:
                unknown_properties.append({**location(document, node, prop), "reason": "Opaque expression may contain dependencies not visible as literal references."})
            if value and value.kind in {"path", "string"} and isinstance(value.value, str) and value.value.startswith("/"):
                if metadata.get(prop.name, {}).get("type") == "path":
                    add("&{" + value.value + "}", prop.value_start, prop.value_end, "binding-path", node, prop)
                elif document._path(node) not in {"/aliases", "/chosen"}:
                    unknown_properties.append({**location(document, node, prop), "reason": "This path-like string has no established path binding."})
    truncated = len(references) > MAX_REFERENCES
    return {"nodes": nodes, "references": references[:MAX_REFERENCES], "pin_state_consumers": [], "coverage": "literal-local-source",
            "external_unknown": document.reference_inventory()["external_unknown"] or bool(unknown_properties),
            "unknown_properties": unknown_properties, "truncated": truncated}


def add_pin_state_consumers(document, graph, pins):
    """Retain known state references even when their signal allocations are unknown."""
    if not pins.get("supported"):
        return False
    labels = defaultdict(list)
    for node in document.syntax.nodes:
        for label in node.labels:
            labels[label].append(node)
    consumers = graph["pin_state_consumers"]
    for controller in pins.get("controllers", []):
        names = {"&" + label for label in [controller["id"], *controller.get("aliases", [])]}
        sources = [node for node in document.syntax.nodes if node.name in names]
        source = sources[0] if len(sources) == 1 else None
        for state in controller.get("states", []):
            properties = [prop for prop in source.properties if prop.name == f"pinctrl-{state['index']}"] if source else []
            prop = properties[0] if len(properties) == 1 else None
            for label in state.get("refs", []):
                for target in labels.get(label, []):
                    if len(consumers) >= MAX_PIN_STATE_CONSUMERS:
                        graph["truncated"] = True
                        return True
                    identity = repr((controller["id"], state["name"], label, target.id))
                    consumers.append({"id": "pin-state:" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                                      "controller": controller["id"], "state": state["name"], "reference": "&" + label,
                                      "target_node_id": target.id, "source_node_id": source.id if prop else None,
                                      "property_id": prop.id if prop else None,
                                      "location": location(document, source, prop) if prop else None,
                                      "origin": "overlay" if prop else "verified-board",
                                      "evidence": "Current overlay controller property." if prop else
                                      "Verified board pin-state reference from the supported target adapter."})
    return False


def local_diagnostics(document, payload, graph):
    result = []
    nodes = {node.id: node for node in document.syntax.nodes}
    states = {node["node_id"]: node["status"] for node in graph["nodes"]}
    rows = {row["node_id"]: row for row in payload["nodes"]}

    def loc(node_id, name=None):
        node = nodes[node_id]
        prop = next((prop for prop in node.properties if prop.name == name), None) if name else None
        return location(document, node, prop)

    def conflict_severity(node_ids):
        status = [states.get(node_id, "unknown") for node_id in node_ids]
        return "warning" if "disabled" in status else "unknown" if "unknown" in status else "error"

    for reference in graph["references"]:
        if reference["resolution"] != "resolved":
            result.append(diagnostic("ambiguous-reference" if reference["resolution"] == "ambiguous" else "unresolved-reference",
                                     "error" if reference["resolution"] == "ambiguous" else "unknown",
                                     f"{reference['reference']} has {reference['resolution']} local identity. Inherited targets remain unknown.",
                                     [reference["location"]]))
    for node in nodes.values():
        if states[node.id] == "unknown":
            result.append(diagnostic("unknown-status", "unknown", "Effective enabled status cannot be established from this declaration.", [loc(node.id, "status")]))
        metadata = rows[node.id]
        if metadata["binding"]["state"] in {"ambiguous", "unsupported"}:
            result.append(diagnostic("binding-coverage", "unknown", "A unique supported binding is unavailable; resource and property checks are incomplete.", [loc(node.id)], repair="bindings"))
        for prop in metadata["properties"]:
            local = prop.get("local")
            if not local:
                if prop.get("required") and not prop.get("deleted"):
                    result.append(diagnostic("required-property-unknown", "unknown", f"Required property {prop['name']} is not declared here; inherited presence is unknown.", [loc(node.id)], repair="bindings"))
                continue
            parsed = local.get("parsed")
            if parsed is None:
                continue
            if "const" in prop and (type(parsed) is not type(prop["const"]) or parsed != prop["const"]):
                result.append(diagnostic("binding-constant", "error", f"{prop['name']} differs from the selected binding constant.", [loc(node.id, prop["name"])], repair="bindings"))
            values = parsed if prop["type"] in {"array", "string-array"} else [parsed]
            if "enum" in prop and any(value not in prop["enum"] for value in values):
                result.append(diagnostic("binding-enum", "error", f"{prop['name']} contains a value outside the selected binding choices.", [loc(node.id, prop["name"])], repair="bindings"))
    for controller in payload.get("controllers", []):
        if controller["kind"] not in {"i2c", "spi"}:
            continue
        addresses = defaultdict(list)
        for child in controller["children"]:
            address = child["address"]
            if address is None:
                result.append(diagnostic("unknown-bus-address", "unknown", "This device's literal single-cell bus address is not established.", [loc(child["node_id"], "reg")], repair="peripherals"))
                continue
            addresses[address].append(child)
            suffix = child["name"].rpartition("@")[2]
            if not re.fullmatch(r"[0-9a-fA-F]{1,8}", suffix) or int(suffix, 16) != address:
                result.append(diagnostic("unit-address-mismatch", "error", "The node unit-address and reg value disagree.", [loc(child["node_id"], "reg")]))
            if controller["kind"] == "spi" and controller.get("chip_selects") is not None and address not in controller["chip_selects"]:
                result.append(diagnostic("spi-chip-select-range", "error", f"Chip select {address} is outside the explicitly declared local cs-gpios array.", [loc(child["node_id"], "reg"), loc(controller["node_id"], "cs-gpios")], repair="peripherals"))
            if controller["kind"] == "i2c" and not 0x08 <= address <= 0x77:
                result.append(diagnostic("i2c-address-range", "warning", f"Address {address:#x} is outside the editor's supported unreserved 7-bit range (0x08–0x77). Other addressing modes need source review and Configure.", [loc(child["node_id"], "reg")], repair="peripherals"))
        for address, consumers in addresses.items():
            if len(consumers) > 1:
                ids = [child["node_id"] for child in consumers]
                result.append(diagnostic("bus-address-conflict", conflict_severity(ids),
                                         f"{controller['kind'].upper()} address/chip select {address:#x} has multiple local consumers; disabled declarations remain visible but do not establish simultaneous use.",
                                         [loc(child["node_id"], "reg") for child in consumers], repair="peripherals"))
        if controller["kind"] == "spi" and controller.get("chip_selects") is None:
            result.append(diagnostic("spi-capacity-unknown", "unknown", "Native SPI chip-select capacity is not established by a local cs-gpios array.", [loc(controller["node_id"])], repair="peripherals"))

    allocations = defaultdict(list)
    unknown_ranges = set()
    for row in rows.values():
        for prop in row["properties"]:
            if prop.get("specifier_space") != "gpio" or not prop.get("local"):
                continue
            entries = prop["local"].get("parsed")
            if not isinstance(entries, list):
                result.append(diagnostic("gpio-allocation-unknown", "unknown", "GPIO allocation is opaque or has no resolved provider cells.", [loc(row["node_id"], prop["name"])], repair="pins"))
                continue
            for index, entry in enumerate(entries):
                provider = BindingService._target(prop.get("providers", []), entry["reference"], required=False)
                if not provider or "pin" not in provider["cells"] or len(entry["cells"]) != len(provider["cells"]):
                    result.append(diagnostic("gpio-provider-unknown", "unknown", "GPIO provider identity or cell layout is not uniquely established.", [loc(row["node_id"], prop["name"])], repair="bindings"))
                    continue
                pin = entry["cells"][provider["cells"].index("pin")]
                if type(pin) is not int:
                    continue
                allocations[(provider["id"], pin)].append((row["node_id"], prop["name"], index))
                provider_node = nodes[provider["id"]]
                count = _literal(document, provider_node, "ngpios")
                if count and count.kind == "cells" and len(count.value) == 1 and pin >= count.value[0]:
                    result.append(diagnostic("gpio-pin-range", "error", f"GPIO pin {pin} is outside the explicitly declared ngpios range.", [loc(row["node_id"], prop["name"]), loc(provider_node.id, "ngpios")], repair="pins"))
                elif not count or count.kind != "cells" or len(count.value) != 1:
                    unknown_ranges.add(provider_node.id)
                reserved = _literal(document, provider_node, "gpio-reserved-ranges")
                if reserved and reserved.kind == "cells" and len(reserved.value) % 2 == 0:
                    if any(start <= pin < start + count for start, count in zip(reserved.value[::2], reserved.value[1::2])):
                        result.append(diagnostic("gpio-reserved", "error", f"GPIO pin {pin} lies in an explicitly reserved provider range.", [loc(row["node_id"], prop["name"]), loc(provider_node.id, "gpio-reserved-ranges")], repair="pins"))
                elif any(prop.name == "gpio-reserved-ranges" for prop in provider_node.properties):
                    result.append(diagnostic("gpio-reservations-unknown", "unknown", "The declared GPIO reserved ranges are opaque or are not start/count pairs.", [loc(provider_node.id, "gpio-reserved-ranges")], repair="pins"))
    for provider_id in unknown_ranges:
        result.append(diagnostic("gpio-range-unknown", "unknown", "This local GPIO provider has no established literal ngpios count; pin range and board reservations require separate target evidence.", [loc(provider_id)], repair="pins"))
    for (_, pin), consumers in allocations.items():
        if len(consumers) > 1:
            ids = [item[0] for item in consumers]
            exclusive = any(name == "cs-gpios" or (nodes[node_id].parent_id and
                            rows[nodes[node_id].parent_id]["binding"].get("compatible") == "gpio-leds") for node_id, name, _ in consumers)
            severity = conflict_severity(ids)
            if not exclusive and severity == "error":
                severity = "unknown"
            result.append(diagnostic("gpio-pin-conflict", severity,
                                     f"GPIO pin {pin} has multiple local GPIO consumers. " + ("An established output owner requires exclusive configuration. " if exclusive else "Their sharing compatibility is not established. ") + "Sharing one controller's bus lines between its child devices does not create extra pin owners.",
                                     [loc(node_id, name) for node_id, name, _ in consumers], repair="pins"))
    if graph["external_unknown"]:
        result.append(diagnostic("external-coverage-unknown", "unknown", "Includes, opaque expressions or untyped path strings may contain additional dependencies. External and inherited consumers are not resolved."))
    if graph["truncated"]:
        result.append(diagnostic("references-truncated", "unknown", "The local reference graph exceeds its inspection limit; deletion effects cannot be fully listed."))
    return result


def pin_diagnostics(document, payload):
    """Merge adapter-established allocations without treating a bus child as an owner."""
    if not payload.get("supported"):
        return [diagnostic("pin-coverage-unknown", "unknown", payload.get("reason") or "No verified pin adapter is available for this context.")]
    nodes = list(document.syntax.nodes)

    def loc(use):
        node = next((node for node in nodes if node.id == use.get("node_id") or
                     node.name == "&" + str(use.get("owner")) or use.get("owner") in node.labels), None)
        if node:
            prop = next((prop for prop in node.properties if prop.name == use.get("property")), None)
            return location(document, node, prop)
        return {"node_id": None, "property_id": None, "path": use.get("path"), "name": str(use.get("owner", "Inherited consumer")), "start": None, "end": None}

    def exclusive(use):
        if use["kind"] == "pinctrl" or use.get("property") == "cs-gpios":
            return True
        node = next((node for node in nodes if node.id == use.get("node_id")), None)
        parent = document.node(node.parent_id) if node and node.parent_id else None
        compatible = _literal(document, parent, "compatible") if parent else None
        return bool(compatible and compatible.kind == "string" and compatible.value == "gpio-leds")

    result, allocations = [], payload.get("allocations", [])
    controllers = {row["id"]: row for row in payload.get("controllers", [])}
    for index, left in enumerate(controllers.values()):
        if left.get("status") not in {"okay", "ok"} or left.get("instance") is None:
            continue
        for right in list(controllers.values())[index + 1:]:
            if right.get("status") in {"okay", "ok"} and left["instance"] == right.get("instance"):
                result.append(diagnostic("controller-instance-conflict", "error", f"{left['id']} and {right['id']} are enabled on shared hardware instance {left['instance']}.", [loc({"owner": left["id"]}), loc({"owner": right["id"]})], repair="peripherals"))
    pins = defaultdict(list)
    for use in allocations:
        pins[(use["port"], use["pin"])].append(use)
    for group in pins.values():
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                if left["kind"] == right["kind"] == "pinctrl" and left["owner"] == right["owner"] and left["state"] != right["state"]:
                    continue
                known = any(exclusive(use) for use in (left, right)) and all(controllers.get(use["owner"], {}).get("status", "okay") in {"okay", "ok"} for use in (left, right))
                result.append(diagnostic("pin-conflict", "error" if known else "unknown", f"P{left['port']}.{left['pin']:02d} is allocated to {left['owner']} {left['role']} and {right['owner']} {right['role']}.", [loc(left), loc(right)], repair="pins"))
                if len(result) >= MAX_DIAGNOSTICS:
                    return [*result[:MAX_DIAGNOSTICS - 1], diagnostic("pin-conflicts-truncated", "unknown", "Pin conflicts exceed the 256-entry inspection limit; remaining pin checks are not listed.")]
    for use in allocations:
        if not any(pin["port"] == use["port"] and pin["pin"] == use["pin"] for pin in payload.get("pins", [])):
            result.append(diagnostic("pin-range", "error", f"P{use['port']}.{use['pin']:02d} is outside the verified target pin table.", [loc(use)], repair="pins"))
        for reserved in payload.get("reservations", []):
            if (use["port"], use["pin"]) == (reserved["port"], reserved["pin"]) and reserved.get("owner") != use.get("owner"):
                result.append(diagnostic("pin-reserved", "error", f"P{use['port']}.{use['pin']:02d} is reserved: {reserved.get('reason', reserved.get('owner', 'board reservation'))}.", [loc(use)], repair="pins"))
    result.extend(diagnostic("pin-allocation-unknown", "unknown", message) for message in payload.get("allocation_unknown", []))
    return result
