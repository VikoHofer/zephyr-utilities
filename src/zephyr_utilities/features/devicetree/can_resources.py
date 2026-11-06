"""Bounded CAN source observations and conservative implicit removal dependencies."""

from collections import defaultdict
import re

from .binding_service import BindingService, _literal, _properties, _references
from .can import can_evidence
from .can_profiles import CAN_CONTROLLER_PROFILES, CAN_TRANSCEIVER_PROFILES
from .resource_graph import MAX_DIAGNOSTICS, _active, diagnostic, location


def _number(document, node, name):
    value = _literal(document, node, name)
    return value.value[0] if value and value.kind == "cells" and len(value.value) == 1 else None


def _known_compatible(document, node, row, compatibles):
    """Ambiguous known declarations still need conservative removal protection."""
    if row.get("binding", {}).get("compatible") in compatibles:
        return True
    for prop in _properties(node, "compatible"):
        value = document.property_value(prop.id) if prop.kind == "property" else None
        if value and value.kind == "string" and value.value in compatibles:
            return True
        if value and value.kind == "string-array" and compatibles.intersection(value.value):
            return True
    return False


def _passive_children(document, parent):
    # DT_CHILD uses a normalized identifier. Unexpected spellings cannot prove
    # that a child is unrelated to the common can-transceiver dependency.
    return [node for node in document.syntax.nodes if node.parent_id == parent.id
            and re.sub(r"[-,.@/+]", "_", node.name.lower()) == "can_transceiver"]


def can_diagnostics(document, projection):
    """Separate nominal API bounds and declared PHY limits from hardware timing."""
    evidence = projection.get("can")
    if evidence is None:
        evidence = can_evidence(document, projection)
    nodes = {node.id: node for node in document.syntax.nodes}
    rows = {row["node_id"]: row for row in projection.get("nodes", [])}
    transceivers = {item["node_id"]: item for item in evidence["transceivers"]}
    users = defaultdict(list)
    result = []

    def loc(node, name=None):
        prop = next(iter(_properties(node, name)), None) if name else None
        return location(document, node, prop)

    def add(code, severity, message, locations=()):
        if len(result) < MAX_DIAGNOSTICS - 1:
            result.append(diagnostic(code, severity, message, locations))
            return True
        if len(result) < MAX_DIAGNOSTICS:
            result.append(diagnostic("can-diagnostics-truncated", "unknown",
                                     "CAN diagnostics exceed the 256-entry inspection limit; remaining controller evidence is unknown."))
        return False

    def severity(*items):
        states = [_active(document, node) for node in items]
        return "warning" if "disabled" in states else "unknown" if "unknown" in states else "error"

    for controller in evidence["controllers"]:
        node = nodes[controller["node_id"]]
        if not controller.get("identity_supported"):
            if not add("can-controller-unknown", "unknown",
                       "CAN inspection needs a unique explicit local compatible and the supported profile binding. Repeated, inherited or unsupported declarations remain source-only.", [loc(node, "compatible")]):
                return result
            continue
        rate_name = "bitrate" if _properties(node, "bitrate") else "bus-speed"
        rate = _number(document, node, rate_name)
        if rate is None:
            if not add("can-bitrate-unknown", "unknown",
                       "The nominal bitrate is not a single local literal. Inherited values, expressions and Kconfig defaults are not resolved.", [loc(node, rate_name)]):
                return result
        elif not 1 <= rate <= 1_000_000:
            if not add("can-bitrate-range", severity(node),
                       "The nominal bitrate is outside the CAN timing API range 1–1000000 bit/s. This does not establish the narrower rates achievable by this controller.", [loc(node, rate_name)]):
                return result
        if _properties(node, "sample-point"):
            sample = _number(document, node, "sample-point")
            if sample is None:
                if not add("can-sample-point-unknown", "unknown",
                           "The sample point is not a single local literal. Its effective value remains unknown.", [loc(node, "sample-point")]):
                    return result
            elif not 0 <= sample < 1000:
                if not add("can-sample-point-range", severity(node),
                           "The CAN timing API requires a sample point below 1000 per mille; 0 requests its bitrate-dependent default policy. Exact achievable timing remains unknown.", [loc(node, "sample-point")]):
                    return result

        limits = controller.get("limits") or {}
        limit_node = nodes.get(limits.get("node_id"))
        lower, upper = limits.get("min_bitrate"), limits.get("max_bitrate")
        known = (limits.get("source") in {"phys", "passive"} and not limits.get("reason")
                 and limit_node is not None and type(upper) is int and upper > 0
                 and (lower is None or type(lower) is int and 0 <= lower <= upper))
        if not known:
            if not add("can-transceiver-range-unknown", "unknown",
                       "A unique effective local CAN transceiver range is not established. A phys property takes precedence over the passive can-transceiver child; absent local evidence does not establish inherited limits.",
                       [loc(node, "phys"), *([loc(limit_node)] if limit_node else [])]):
                return result
        else:
            if lower in {None, 0} and not add("can-transceiver-minimum-unknown", "unknown",
                                            "No positive local transceiver minimum bitrate is established. The maximum is a declared upper bound; it does not establish the lower rates this controller can achieve.", [loc(limit_node, "min-bitrate")]):
                return result
            if rate is not None and (lower is not None and rate < lower or rate > upper):
                bound = "min-bitrate" if lower is not None and rate < lower else "max-bitrate"
                if not add("can-transceiver-bitrate-range", severity(node, limit_node),
                           "The nominal bitrate is outside this effective transceiver's explicitly declared bitrate limits. Passing these limits would still not prove achievable timing or electrical suitability.",
                           [loc(node, rate_name), loc(limit_node, bound)]):
                    return result
            data_name = "bitrate-data" if _properties(node, "bitrate-data") else "bus-speed-data"
            data_field = next((field for field in rows.get(node.id, {}).get("properties", []) if field["name"] == data_name), {})
            data_rate = _number(document, node, data_name) if data_field.get("type") == "int" else None
            if data_rate is not None and (lower is not None and data_rate < lower or data_rate > upper):
                bound = "min-bitrate" if lower is not None and data_rate < lower else "max-bitrate"
                if not add("can-transceiver-data-bitrate-range", severity(node, limit_node),
                           "The preserved explicit CAN FD data-phase bitrate is outside the selected transceiver's declared limits. This form does not configure data-phase settings or establish CAN FD operation.",
                           [loc(node, data_name), loc(limit_node, bound)]):
                    return result

        references = _references(_literal(document, node, "phys"))
        target = BindingService._target(projection.get("targets", []), references[0], required=False) if references and len(references) == 1 else None
        phy = transceivers.get(target["id"]) if target else None
        if phy and phy.get("identity_supported"):
            phy_node = nodes[phy["node_id"]]
            users[phy_node.id].append(node)
            if _active(document, phy_node) == "disabled":
                if not add("can-transceiver-disabled", "warning",
                           "This CAN controller references a locally disabled active transceiver. A usable enabled CAN connection is not established.", [loc(node, "phys"), loc(phy_node, "status")]):
                    return result
        if not add("can-hardware-unknown", "unknown",
                   "These checks cover nominal source values and declared transceiver limits. Core clocks, timing segments, prescalers, pin routing, termination, bus wiring and runtime CAN operation remain unverified. CAN FD data-phase settings are preserved, not configured here.", [loc(node)]):
            return result

    for phy_id, consumers in users.items():
        if len(consumers) < 2:
            continue
        status = "unknown" if sum(_active(document, item) != "disabled" for item in consumers) > 1 else "warning"
        if not add("can-transceiver-sharing-unknown", status,
                   "Multiple local CAN controllers reference this transceiver. Declared bitrate limits apply to each; physical sharing, independent control and simultaneous operation are not established.",
                   [loc(item, "phys") for item in consumers] + [loc(nodes[phy_id])]):
            return result
    return result


def can_passive_dependants(document, projection, removed_ids):
    """Passive limits are selected by child name, without a literal phandle."""
    rows = {row["node_id"]: row for row in projection.get("nodes", [])}
    compatibles = {profile["compatible"] for profile in CAN_CONTROLLER_PROFILES}
    result = []
    for parent in document.syntax.nodes:
        if parent.id in removed_ids or not _known_compatible(document, parent, rows.get(parent.id, {}), compatibles):
            continue
        for child in _passive_children(document, parent):
            if child.id not in removed_ids:
                continue
            result.append({"id": "can-passive:" + child.id, "reference": document._path(child) or child.name,
                           "source_node_id": parent.id, "property_id": None,
                           "kind": "can-passive-transceiver", "location": location(document, parent),
                           "message": "This CAN controller contains a passive can-transceiver child that supplies or may supply bitrate limits through the CAN devicetree API. Remove or change that relationship explicitly in source."})
    return result


def can_repair_reason(document, projection, consumer, prop, removed_properties=()):
    """Do not turn optional single-property repairs into unsafe CAN rewrites."""
    row = next((row for row in projection.get("nodes", []) if row["node_id"] == consumer.id), {})
    if prop.name == "phys" and _known_compatible(document, consumer, row,
                                                {profile["compatible"] for profile in CAN_CONTROLLER_PROFILES}):
        if _passive_children(document, consumer):
            return "Removing phys would expose a passive CAN transceiver child with different or unresolved limits. Repair that relationship explicitly in source before removal."
    controls = {"enable-gpios", "standby-gpios"}
    if prop.name in controls and _known_compatible(document, consumer, row,
                                                   {profile["compatible"] for profile in CAN_TRANSCEIVER_PROFILES}):
        omitted = {prop.id, *removed_properties}
        remaining = [item for item in consumer.properties if item.name in controls and item.id not in omitted
                     and item.kind == "property" and len(_properties(consumer, item.name)) == 1]
        if not remaining:
            return "The CAN GPIO transceiver requires enable-gpios or standby-gpios. This repair would remove its last control property; repair the transceiver explicitly in source instead."
    return ""
