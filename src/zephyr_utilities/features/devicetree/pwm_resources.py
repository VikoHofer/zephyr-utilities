"""Conservative PWM observations from literal local source and binding metadata."""

from collections import defaultdict

from .binding_service import BindingService
from .pwm import pwm_providers
from .resource_graph import MAX_DIAGNOSTICS, _active, diagnostic, location


def pwm_diagnostics(document, projection):
    """Report known invalid periods without inventing channel or timer capabilities."""
    nodes = {node.id: node for node in document.syntax.nodes}
    rows = {row["node_id"]: row for row in projection.get("nodes", [])}
    unique = {target["id"] for target in projection.get("targets", [])}
    supported = [item for item in pwm_providers(document, projection) if item["supported"]]
    provider_rows = {item["id"]: item for item in supported}
    allocations, providers = defaultdict(list), defaultdict(list)
    result = []

    def add(code, severity, message, locations):
        if len(result) < MAX_DIAGNOSTICS - 1:
            result.append(diagnostic(code, severity, message, locations))
            return True
        if len(result) < MAX_DIAGNOSTICS:
            result.append(diagnostic("pwm-diagnostics-truncated", "unknown",
                                     "PWM diagnostics exceed the 256-entry inspection limit; remaining allocations are unknown."))
        return False

    def state(*items):
        states = [_active(document, item) for item in items]
        return "disabled" if "disabled" in states else "unknown" if "unknown" in states else "enabled"

    for node in nodes.values():
        properties = [prop for prop in node.properties if prop.name == "pwms"]
        if not properties:
            continue
        prop = properties[0]
        loc = location(document, node, prop)
        row = rows.get(node.id, {})
        field = next((item for item in row.get("properties", []) if item["name"] == "pwms"), None)
        if (len(properties) != 1 or prop.kind != "property" or node.id not in unique
                or row.get("binding", {}).get("state") != "matched" or not field
                or field.get("type") != "phandle-array" or field.get("specifier_space") != "pwm"):
            if not add("pwm-consumer-unknown", "unknown",
                       "PWM allocation requires one unique local declaration and a supported PWM consumer binding. Repeated, deleted or unresolved source remains source-only.", [loc]):
                return result
            continue
        entries = (field.get("local") or {}).get("parsed")
        if not isinstance(entries, list) or len(entries) != 1:
            if not add("pwm-allocation-unknown", "unknown",
                       "This PWM inspection covers one literal specifier per consumer. Expressions, absent values and multiple specifiers remain source-only.", [loc]):
                return result
            continue
        entry = entries[0]
        provider = BindingService._target(supported, entry.get("reference"), required=False)
        cells = entry.get("cells")
        if (not provider or provider["id"] not in nodes
                or not isinstance(cells, list) or len(cells) != len(provider["cells"])
                or any(type(value) is not int or not 0 <= value <= 0xffffffff for value in cells)):
            if not add("pwm-provider-unknown", "unknown",
                       "PWM provider identity and its explicit cell count must resolve to channel, period, and optional flags. Inherited providers and unsupported layouts have unknown allocation.", [loc]):
                return result
            continue
        if not field.get("editable"):
            if not add("pwm-consumer-unknown", "unknown",
                       "This PWM property contains unsupported binding metadata or source constraints. Its allocation remains source-only.", [loc]):
                return result
            continue
        provider_node = nodes[provider["id"]]
        current_state = state(node, provider_node)
        provider_status = next((item for item in provider_node.properties if item.name == "status"), None)
        if _active(document, provider_node) == "disabled":
            if not add("pwm-provider-disabled", "warning",
                       "This consumer references a locally disabled PWM provider; the declaration does not establish a usable enabled output.",
                       [loc, location(document, provider_node, provider_status)]):
                return result
        channel, period = cells[:2]
        if period == 0:
            severity = "warning" if current_state == "disabled" else "unknown" if current_state == "unknown" else "error"
            if not add("pwm-period-invalid", severity,
                       "The PWM specifier declares a zero period. This form requires a positive period in nanoseconds; disabled or uncertain declarations do not establish simultaneous operation.", [loc]):
                return result
        allocation = {"location": loc, "channel": channel, "period": period, "state": current_state}
        allocations[(provider_node.id, channel)].append(allocation)
        providers[provider_node.id].append(allocation)

    for provider_id, consumers in providers.items():
        provider_node = nodes[provider_id]
        count_property = next((item for item in provider_node.properties if item.name == "channels"), None)
        known_count = provider_rows[provider_id]["channel_count"]
        count_text = (f"The local channels value is {known_count}, but a count does not establish channel numbering. "
                      if known_count is not None else "No local channel count is established. ")
        if not add("pwm-channel-range-unknown", "unknown", count_text +
                   "Valid channel identifiers, vendor flags, physical routing and achievable timing require target-specific evidence.",
                   [location(document, provider_node, count_property)]):
            return result
        positive = [item for item in consumers if item["period"] > 0]
        if len({item["channel"] for item in positive}) > 1 and len({item["period"] for item in positive}) > 1:
            active = [item for item in positive if item["state"] != "disabled"]
            severity = "unknown" if len({item["period"] for item in active}) > 1 else "warning"
            if not add("pwm-timer-coupling-unknown", severity,
                       "Consumers request different periods on channels of this provider. Channels may share a timer; independent timing is not established. Disabled declarations do not prove simultaneous use.",
                       [item["location"] for item in positive]):
                return result
    for (_, channel), consumers in allocations.items():
        if len(consumers) < 2:
            continue
        severity = "unknown" if sum(item["state"] != "disabled" for item in consumers) > 1 else "warning"
        if not add("pwm-channel-sharing-unknown", severity,
                   f"PWM channel {channel} has multiple local consumers. Sharing, complementary outputs and vendor flag semantics are not established by these bindings; this is not a proven simultaneous conflict.",
                   [item["location"] for item in consumers]):
            return result
    return result
