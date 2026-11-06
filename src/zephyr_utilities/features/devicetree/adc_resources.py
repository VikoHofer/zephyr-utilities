"""Bounded local ADC diagnostics and implicit channel-consumer evidence."""

from collections import defaultdict
import re

from .adc import adc_providers
from .binding_service import BindingService, _literal, _properties
from .resource_graph import MAX_DIAGNOSTICS, _active, diagnostic, location


def _channel(document, node):
    value = _literal(document, node, "reg")
    number = value.value[0] if value and value.kind == "cells" and len(value.value) == 1 else None
    suffix = node.name.removeprefix("channel@") if node.name.startswith("channel@") else ""
    unit = int(suffix, 16) if re.fullmatch(r"[0-9a-fA-F]{1,8}", suffix) else None
    return number, unit


def adc_diagnostics(document, projection, catalog=None):
    """Apply ADC API constraints only after the common ADC binding is established."""
    nodes = {node.id: node for node in document.syntax.nodes}
    rows = {row["node_id"]: row for row in projection.get("nodes", [])}
    unique = {target["id"] for target in projection.get("targets", [])}
    evidence = projection.get("adc_providers")
    providers = evidence if evidence is not None else adc_providers(document, projection, catalog)
    supported = [provider for provider in providers if provider["supported"]]
    result = []

    def add(code, severity, message, locations):
        if len(result) < MAX_DIAGNOSTICS - 1:
            result.append(diagnostic(code, severity, message, locations))
            return True
        if len(result) < MAX_DIAGNOSTICS:
            result.append(diagnostic("adc-diagnostics-truncated", "unknown",
                                     "ADC diagnostics exceed the 256-entry inspection limit; remaining channel evidence is unknown."))
        return False

    def loc(node, name=None):
        prop = next((prop for prop in node.properties if prop.name == name), None) if name else None
        return location(document, node, prop)

    def severity(*items):
        states = [_active(document, node) for node in items]
        return "warning" if "disabled" in states else "unknown" if "unknown" in states else "error"

    channels = {}
    for provider in supported:
        parent = nodes[provider["id"]]
        children = [node for node in nodes.values() if node.parent_id == parent.id]
        by_number = defaultdict(list)
        channels[parent.id] = []
        for child in children:
            number, unit = _channel(document, child)
            row = rows.get(child.id, {})
            field_names = {field["name"] for field in row.get("properties", [])}
            common = {"reg", "zephyr,gain", "zephyr,reference", "zephyr,acquisition-time"} <= field_names
            if (number is None or child.id not in unique or not common or _properties(child, "compatible")
                    or row.get("binding", {}).get("state") != "matched"):
                if not add("adc-channel-identity-unknown", "unknown",
                           "A unique literal ADC channel reg value and inherited channel binding are not established; expressions, explicit child bindings and repeated declarations require source review.", [loc(child, "reg")]):
                    return result
                continue
            channels[parent.id].append(child)
            by_number[number].append(child)
            if unit != number:
                if not add("adc-channel-unit-mismatch", "error",
                           "ADC API macros select a child by numeric unit-address, while channel configuration uses reg. This profile requires the common binding's channel@N name and matching reg.", [loc(child, "reg")]):
                    return result
            if not 0 <= number <= 31:
                if not add("adc-channel-id-range", severity(child, parent),
                           "This channel reg is outside the ADC API channel-ID range 0–31. This API bound does not establish which channels this hardware supports.", [loc(child, "reg")]):
                    return result
            acquisition = _literal(document, child, "zephyr,acquisition-time")
            if acquisition and acquisition.kind == "cells" and len(acquisition.value) == 1 and acquisition.value[0] > 65535:
                if not add("adc-acquisition-range", severity(child, parent),
                           "The literal acquisition-time value exceeds the ADC API's unsigned 16-bit encoding. Supported acquisition timing still depends on the driver.", [loc(child, "zephyr,acquisition-time")]):
                    return result
        for number, duplicates in by_number.items():
            if len(duplicates) > 1:
                if not add("adc-channel-duplicate", "error",
                           f"Multiple local children configure ADC channel {number}; the channel identity must be repaired explicitly.", [loc(child, "reg") for child in duplicates]):
                    return result
        if not add("adc-capability-unknown", "unknown",
                   "The ADC API admits channel IDs 0–31; this provider's usable channels, physical inputs, reference voltage, gain support and acquisition timing require target and driver evidence.", [loc(parent)]):
            return result

    for consumer in nodes.values():
        properties = _properties(consumer, "io-channels")
        if not properties:
            continue
        prop = properties[0]
        row = rows.get(consumer.id, {})
        field = next((item for item in row.get("properties", []) if item["name"] == "io-channels"), None)
        entries = (field.get("local") or {}).get("parsed") if field else None
        if (len(properties) != 1 or prop.kind != "property" or consumer.id not in unique
                or row.get("binding", {}).get("state") != "matched" or not field
                or field.get("type") != "phandle-array" or field.get("specifier_space") != "io-channel"
                or not isinstance(entries, list) or len(entries) != 1):
            if not add("adc-consumer-unknown", "unknown",
                       "This ADC form covers one literal io-channels specifier on a unique bound consumer. Multiple, opaque and unresolved declarations require generic binding or source review.", [location(document, consumer, prop)]):
                return result
            continue
        entry = entries[0]
        provider = BindingService._target(supported, entry.get("reference"), required=False)
        cells = entry.get("cells")
        if (not provider or not isinstance(cells, list) or len(cells) != 1
                or type(cells[0]) is not int or not field.get("editable")):
            if not add("adc-provider-unknown", "unknown",
                       "An ADC provider with the common channel binding, one input cell and a matching explicit count is not established. Other I/O providers and inherited hardware are not assigned ADC semantics.", [location(document, consumer, prop)]):
                return result
            continue
        parent = nodes[provider["id"]]
        if _active(document, parent) == "disabled":
            if not add("adc-provider-disabled", "warning",
                       "This consumer references a locally disabled ADC provider; usable enabled input is not established.", [location(document, consumer, prop), loc(parent, "status")]):
                return result
        if not 0 <= cells[0] <= 31:
            if not add("adc-input-range", severity(consumer, parent),
                       "The io-channels input is outside the ADC API channel-ID range 0–31. It is a channel identifier, not an inferred physical pin number.", [location(document, consumer, prop)]):
                return result
        matches = [child for child in channels[parent.id] if _channel(document, child) == (cells[0], cells[0])]
        if len(matches) != 1:
            if not add("adc-channel-config-unknown", "unknown",
                       "A unique local channel@N configuration matching this input is not established. Configuration may be supplied elsewhere; no default channel or pin configuration is inferred.", [location(document, consumer, prop), loc(parent)]):
                return result
    return result


def adc_channel_dependants(document, projection, graph, removed_ids):
    """Find numeric io-channels dependencies that have no direct child phandle."""
    nodes = {node.id: node for node in document.syntax.nodes}
    providers = {provider["id"]: provider for provider in projection.get("adc_providers", [])}
    rows = {row["node_id"]: row for row in projection.get("nodes", [])}
    targets = projection.get("targets", [])
    result = []
    for node_id in removed_ids:
        child = nodes[node_id]
        if child.parent_id in removed_ids or child.parent_id not in nodes:
            continue
        parent = nodes[child.parent_id]
        provider = providers.get(parent.id)
        parent_fields = {field["name"] for field in rows.get(parent.id, {}).get("properties", [])}
        # The ADC API selects children by numeric unit-address, including a
        # child outside this editor's channel@N/common-binding form profile.
        # Unsupported I/O providers may have implicit child dependencies too;
        # only established ADC identity can exclude a different numeric input.
        if not (provider or "#io-channel-cells" in parent_fields or _properties(parent, "#io-channel-cells")):
            continue
        number, unit = _channel(document, child)
        known_identity = bool(provider and provider["supported"] and number == unit and number is not None)
        for consumer in nodes.values():
            if consumer.id in removed_ids:
                continue
            properties = _properties(consumer, "io-channels")
            for prop in properties:
                references = [item for item in graph["references"] if item.get("property_id") == prop.id]
                to_parent = [item for item in references if parent.id in item.get("target_node_ids", [])]
                value = document.property_value(prop.id) if prop.kind == "property" else None
                literal = value is not None and value.kind == "phandle-array" and len(properties) == 1
                matches = []
                if literal:
                    for reference, cells in value.value:
                        target = BindingService._target(targets, reference, required=False)
                        if target and target["id"] == parent.id:
                            matches.append(cells)
                unknown = (not known_identity or not literal or any(item["resolution"] != "resolved" for item in to_parent)
                           or any(len(cells) != 1 for cells in matches))
                if not to_parent and not matches:
                    # Even with a visible reference to another provider, an
                    # opaque expression may hide this channel's dependency.
                    if literal or prop.kind != "property":
                        continue
                    unknown = True
                elif not unknown and matches and all(len(cells) == 1 and cells[0] != unit for cells in matches):
                    continue
                elif not unknown and not matches:
                    unknown = True
                result.append({"id": f"adc-channel:{child.id}:{prop.id}",
                               "reference": (provider or {}).get("reference") or document._path(parent) or parent.name,
                               "source_node_id": consumer.id, "property_id": prop.id,
                               "kind": "adc-channel-consumer", "location": location(document, consumer, prop),
                               "message": (f"{document._path(consumer) or consumer.name} may select the channel configuration {child.name}; its provider/input identity is not fully resolved."
                                           if unknown else f"{document._path(consumer) or consumer.name} selects {child.name} through io-channels, without a direct reference to the child node.")})
    return result
