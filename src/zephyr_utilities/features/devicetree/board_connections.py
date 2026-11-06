"""Scoped overlay additions against a retained, read-only board observation."""

from collections import defaultdict
from copy import deepcopy
import re

from .binding_service import BindingService, _literal, _properties, _references
from .resource_graph import _active
from .source import PropertyValue, SourceError


LED_GROUP = "/app-leds"
COMMANDS = frozenset({"board_module_add"})


def _number(document, node, name):
    value = _literal(document, node, name)
    return value.value[0] if value and value.kind == "cells" and len(value.value) == 1 else None


def _local_paths(source, effective):
    labels = defaultdict(list)
    for node in effective.syntax.nodes:
        for label in node.labels:
            labels[label].append(effective._path(node))
    paths, contributors = {}, defaultdict(list)
    for node in source.syntax.nodes:
        if node.parent_id:
            parent = paths.get(node.parent_id)
            path = parent.rstrip("/") + "/" + node.name if parent else None
        elif node.name == "/":
            path = "/"
        elif node.name.startswith("&{"):
            path = node.name[2:-1]
        elif node.name.startswith("&"):
            matches = labels.get(node.name[1:], [])
            path = matches[0] if len(matches) == 1 else None
        else:
            path = None
        paths[node.id] = path
        if path:
            contributors[path].append(node)
    return paths, contributors


def _conditional(source, position):
    depth = 0
    for token in source.syntax.tokens:
        if token.start >= position:
            break
        if token.kind != "directive":
            continue
        text = source.source[token.start:token.end].decode("utf-8")
        if re.match(r"#\s*(?:if|ifdef|ifndef)\b", text):
            depth += 1
        elif re.match(r"#\s*endif\b", text):
            depth = max(0, depth - 1)
    return depth > 0


def _local_reason(source, effective, path):
    _, contributors = _local_paths(source, effective)
    local = contributors[path]
    if len(local) > 1:
        return "Multiple overlay contributors target this node. Resolve the intended connection in source first."
    if local and (_conditional(source, local[0].start) or not source.can_edit_node(local[0].id)):
        return "This overlay contributor is conditional or conflicts with a deletion. Resolve it explicitly in source."
    return ""


def _schema(catalog, kind):
    match = catalog.match(["bosch,bme280"] if kind == "i2c" else ["gpio-leds"], bus="i2c" if kind == "i2c" else None)
    schema = match.get("schema") or {}
    if match["state"] != "matched" or kind == "i2c" and schema.get("on_bus") != "i2c":
        raise ValueError("The selected binding roots do not establish this device recipe and bus.")
    if kind == "gpio-led":
        if any(settings.get("required") for name, settings in schema.get("properties", {}).items() if name != "compatible"):
            raise ValueError("The GPIO LED group binding requires additional settings outside this connection form.")
        parent = schema
        schema = schema.get("child_binding") or {}
    else:
        parent = None
    expected = {"compatible": {"string", "string-array"}, "reg": {"array"}} if kind == "i2c" else {"gpios": {"phandle-array"}}
    properties = schema.get("properties", {})
    if any(not properties.get(name, {}).get("supported") or properties[name].get("type") not in types for name, types in expected.items()):
        raise ValueError("The device binding does not support the fields this connection form generates.")
    if any(settings.get("required") for name, settings in properties.items() if name not in expected):
        raise ValueError("The device binding requires additional settings outside this connection form.")
    if kind == "gpio-led" and properties["gpios"].get("specifier_space") != "gpio":
        raise ValueError("The GPIO LED binding must use standard GPIO specifiers.")
    return schema, parent


def _gpio_uses(document, projection, payload):
    providers = {item["effective_node_id"]: item for item in payload["gpio_providers"]}
    used, unknown = defaultdict(set), set()
    targets = projection["targets"]
    connector_paths = {item["path"] for item in payload.get("connector_maps", [])}
    connector_providers = {item["path"]: {entry["provider_id"] for entry in item["entries"]}
                           for item in payload.get("connector_maps", [])}
    allocations = [item for item in payload.get("allocations", []) if item.get("kind") != "gpio"]

    def occupy(provider, pin, owner, role):
        used[provider["id"]].add(pin)
        allocations.append({"provider_id": provider["id"], "pin": pin, "kind": "gpio",
                            "owner_id": "hardware:" + document._path(owner), "role": role})

    def uncertain_target(target):
        if target and target["id"] in providers:
            unknown.add(providers[target["id"]]["id"])
        elif target and target["path"] in connector_providers:
            unknown.update(connector_providers[target["path"]] or [provider["id"] for provider in providers.values()])

    for row in projection["nodes"]:
        node = document.node(row["node_id"])
        handled = set()
        for field in row["properties"]:
            if field.get("specifier_space") != "gpio" or not field.get("local"):
                continue
            handled.add(field["local"]["id"])
            entries = field["local"].get("parsed")
            if not isinstance(entries, list):
                refs = _references(_literal(document, node, field["name"])) or []
                for reference in refs:
                    target = BindingService._target(targets, reference, required=False)
                    uncertain_target(target)
                continue
            for entry in entries:
                target = BindingService._target(targets, entry["reference"], required=False)
                provider = providers.get(target["id"]) if target else None
                if not provider:
                    uncertain_target(target)
                    continue
                cells = entry["cells"]
                if provider["cells"] == ["pin", "flags"] and len(cells) == 2 and type(cells[0]) is int:
                    occupy(provider, cells[0], node, field["name"])
                else:
                    unknown.add(provider["id"])
        if document._path(node) not in {"/aliases", "/chosen"}:
            for prop in node.properties:
                if (prop.id in handled or prop.kind != "property"
                        or prop.name == "gpio-map" and document._path(node) in connector_paths):
                    continue
                for reference in _references(document.property_value(prop.id)) or []:
                    target = BindingService._target(targets, reference, required=False)
                    uncertain_target(target)
        if node.parent_id in providers and _properties(node, "gpio-hog"):
            provider = providers[node.parent_id]
            value = _literal(document, node, "gpios")
            if value and value.kind == "cells" and len(value.value) % 2 == 0:
                for pin in value.value[::2]:
                    occupy(provider, pin, node, "gpio-hog")
            else:
                unknown.add(provider["id"])
    payload["allocations"] = allocations
    for allocation in allocations:
        if type(allocation.get("pin")) is int:
            used[allocation["provider_id"]].add(allocation["pin"])
    return used, unknown


def _group_reason(source, document, rows):
    group = next((node for node in document.syntax.nodes if document._path(node) == LED_GROUP), None)
    if group:
        if rows[group.id]["binding"].get("compatible") != "gpio-leds":
            return "The application LED group path is already used by another declaration. Preserve it and choose source editing."
        if _active(document, group) != "enabled":
            return "The existing application LED group is disabled or has unknown status. Enable it explicitly before adding a connection."
        return _local_reason(source, document, LED_GROUP)
    return _local_reason(source, document, "/")


def connection_options(document, projection, catalog, payload, source):
    """Report action-specific capabilities without changing source or applying defaults."""
    rows = {row["node_id"]: row for row in projection["nodes"]}
    targets = {target["id"]: target for target in projection["targets"]}
    _, local = _local_paths(source, document)
    recipes = {}
    for kind in ("i2c", "gpio-led"):
        try:
            _schema(catalog, kind)
            recipes[kind] = ""
        except ValueError as exc:
            recipes[kind] = str(exc)
    result = {}
    for controller in payload["controllers"]:
        if controller.get("kind") != "i2c":
            continue
        node = document.node(controller["effective_node_id"])
        reason = recipes["i2c"] or _local_reason(source, document, controller["path"])
        row = rows[node.id]
        if node.id not in targets or row["binding"]["state"] != "matched":
            reason = reason or "This controller needs a unique matched board binding."
        if _number(document, node, "#address-cells") != 1 or _number(document, node, "#size-cells") != 0:
            reason = reason or "This connection requires one I2C address cell and zero size cells."
        fields = {field["name"]: field for field in row["properties"]}
        for name, value in (("#address-cells", 1), ("#size-cells", 0)):
            try:
                if not fields.get(name, {}).get("editable"):
                    raise ValueError("The controller address layout is not supported by its binding.")
                BindingService(None)._typed_value(fields[name], value, projection)
            except ValueError as exc:
                reason = reason or str(exc)
        for field in row["properties"]:
            if field["name"] in {"pinctrl-0", "pinctrl-names"} and field.get("required") and not field.get("local"):
                reason = reason or "Required board pin routing is missing. Configure the controller's routing before connecting a device."
        parent = document.node(node.parent_id) if node.parent_id else None
        if parent and _active(document, parent) != "enabled":
            reason = reason or "An ancestor controller is disabled or unresolved. Enable it explicitly before connecting this device."
        status = controller["status"]
        if status not in {"okay", "ok", "disabled"}:
            reason = reason or "The controller status is outside the supported enabled/disabled states."
        if status == "disabled":
            status_field = next((field for field in row["properties"] if field["name"] == "status"), {})
            try:
                if not status_field.get("editable"):
                    raise ValueError("The controller status binding cannot authorize enabling this node.")
                BindingService(None)._typed_value(status_field, "okay", projection)
            except ValueError as exc:
                reason = reason or str(exc)
        if status == "disabled" and local[controller["path"]] and not reason:
            try:
                source.plan_set_status(local[controller["path"]][0].id, "okay")
            except SourceError as exc:
                reason = str(exc)
        occupied = set()
        for child in document.syntax.nodes:
            if child.parent_id != node.id:
                continue
            number = _number(document, child, "reg")
            suffix = child.name.rpartition("@")[2]
            if number is None or not re.fullmatch(r"[0-9a-fA-F]{1,8}", suffix) or int(suffix, 16) != number:
                reason = reason or "An existing I2C child has an unresolved or inconsistent address. Resolve it before adding a device."
            else:
                occupied.add(number)
        result[controller["id"]] = [{"kind": "i2c", "compatible": "bosch,bme280", "addable": not reason, "reason": reason,
                                      "address_min": 8, "address_max": 119, "occupied_addresses": sorted(occupied),
                                      "enable_required": status == "disabled"}]
    used, uncertain = _gpio_uses(document, projection, payload)
    group_reason = _group_reason(source, document, rows)
    for provider in payload["gpio_providers"]:
        node = document.node(provider["effective_node_id"])
        row = rows[node.id]
        fields = {field["name"]: field for field in row["properties"]}
        reason = recipes["gpio-led"] or group_reason
        count = provider.get("pin_count")
        gpio_controller = _literal(document, node, "gpio-controller")
        if (not provider.get("supported") or node.id not in targets or provider.get("cells") != ["pin", "flags"]
                or _number(document, node, "#gpio-cells") != 2 or not gpio_controller or gpio_controller.kind != "boolean"):
            reason = reason or "A unique board GPIO controller with standard pin/flags cells is required."
        for name, value in (("#gpio-cells", 2), ("gpio-controller", True)):
            field = fields.get(name, {})
            try:
                if not field.get("editable"):
                    raise ValueError("The GPIO controller metadata is not supported by its binding.")
                BindingService(None)._typed_value(field, value, projection)
            except ValueError as exc:
                reason = reason or str(exc)
        if type(count) is not int or count <= 0:
            reason = reason or "The GPIO provider has no established pin count. Choose a provider with observed range evidence."
        if _properties(node, "ngpios") and _number(document, node, "ngpios") is None:
            reason = reason or "The explicit GPIO pin count is unresolved. Resolve it before selecting a pin."
        if type(count) is int:
            try:
                if not fields.get("ngpios", {}).get("editable"):
                    raise ValueError("The GPIO pin count is not supported by its binding.")
                BindingService(None)._typed_value(fields["ngpios"], count, projection)
            except ValueError as exc:
                reason = reason or str(exc)
        if _active(document, node) != "enabled":
            reason = reason or "The GPIO provider is disabled or unresolved. Enable it explicitly before connecting a LED."
        ranges = provider.get("reserved_ranges") or []
        if _properties(node, "gpio-reserved-ranges") and provider.get("reserved_ranges") is None:
            reason = reason or "GPIO reserved ranges are unresolved. Resolve them before selecting a pin."
        if any(len(pair) != 2 or any(type(value) is not int or value < 0 for value in pair) for pair in ranges):
            reason = reason or "GPIO reservations must be literal start/count pairs."
        if provider["id"] in uncertain:
            reason = reason or "A GPIO consumer has unresolved pin semantics. Its pins cannot be assumed available."
        if payload.get("pin_routing_complete") is False:
            reason = reason or "Some board pin routing cannot be decoded for GPIO conflicts. Its pins cannot be assumed available."
        result[provider["id"]] = [{"kind": "gpio-led", "addable": not reason, "reason": reason,
                                   "pin_min": 0, "pin_max": min(count - 1, 31) if type(count) is int and count > 0 else None,
                                   "occupied_pins": sorted(used[provider["id"]]), "reserved_ranges": deepcopy(ranges),
                                   "flags_min": 0, "flags_max": 65535}]
    return result


def _ensure_target(source, effective, target):
    _, local = _local_paths(source, effective)
    matches = local[target["path"]]
    if len(matches) == 1:
        return source, matches[0]
    if matches:
        raise ValueError("Multiple overlay contributors target this connection. Use source editing.")
    reference = target.get("reference") or "&{" + target["path"] + "}"
    source = source.plan_add_node(None, reference).apply(source)
    return source, source.find_node(reference)


class BoardConnectionService:
    """Plan one inherited-controller connection; all writes remain in the overlay."""

    commands = COMMANDS

    def __init__(self, drafts, board):
        self.drafts, self.board = drafts, board

    def plan(self, body, context, draft):
        command = body["command"]
        if set(command) != {"kind", "observation_id", "connection"} or command["kind"] != "board_module_add" or not isinstance(command["observation_id"], str):
            raise ValueError("Choose the exact observed board connection command fields.")
        connection = command["connection"]
        common = {"kind", "controller_id", "name", "label"}
        if not isinstance(connection, dict) or connection.get("kind") not in {"i2c", "gpio-led"}:
            raise ValueError("Choose an I2C sensor or GPIO LED connection.")
        kind = connection["kind"]
        expected = common | ({"compatible", "address", "enable"} if kind == "i2c" else {"pin", "flags"})
        if set(connection) != expected or not isinstance(connection["controller_id"], str):
            raise ValueError("Choose the exact fields for this board connection.")
        if not isinstance(connection["name"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9-]{0,63}", connection["name"]):
            raise ValueError("Choose a simple node name without an address suffix.")
        label = connection["label"]
        if not isinstance(label, str) or label and not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", label):
            raise ValueError("Choose a literal node label or leave it empty.")
        guard = {key: value for key, value in body.items() if key != "command"}
        request = {**guard, "observation_id": command["observation_id"]}
        observed = self.board.validate(request, context, draft)
        effective, projection, catalog, payload = (observed[key] for key in ("document", "projection", "catalog", "payload"))
        options = connection_options(effective, projection, catalog, payload, draft.document)
        option = next((item for item in options.get(connection["controller_id"], []) if item["kind"] == kind), None)
        if not option or not option["addable"]:
            raise ValueError(option["reason"] if option else "Choose a supported observed controller for this connection.")
        targets = payload["controllers"] if kind == "i2c" else payload["gpio_providers"]
        target = next(item for item in targets if item["id"] == connection["controller_id"])
        if label and any(label in node.labels for node in effective.syntax.nodes):
            raise ValueError("The requested label already belongs to a board or overlay node.")
        schema, parent_schema = _schema(catalog, kind)
        typed = BindingService(None)
        source = draft.document
        if kind == "i2c":
            if connection["compatible"] != "bosch,bme280" or type(connection["enable"]) is not bool:
                raise ValueError("Choose the BME280 recipe and an explicit controller enable choice.")
            address = connection["address"]
            if type(address) is not int or not 8 <= address <= 119 or address in option["occupied_addresses"]:
                raise ValueError("Choose an unused, unreserved 7-bit I2C address from the observed board and overlay.")
            if option["enable_required"] and not connection["enable"]:
                raise ValueError("Select controller enable before connecting a device to this disabled I2C controller.")
            source, parent = _ensure_target(source, effective, target)
            if connection["enable"]:
                field = next((field for row in projection["nodes"] if row["node_id"] == target["effective_node_id"]
                              for field in row["properties"] if field["name"] == "status"), None)
                if not field or not field.get("editable"):
                    raise ValueError("The controller status binding cannot authorize enabling this node.")
                status = typed._typed_value(field, "okay", projection)
                source = source.plan_set_property(parent.id, "status", status).apply(source)
                _, current = _local_paths(source, effective)
                parent = current[target["path"]][0]
            name = connection["name"] + f"@{address:x}"
            compatible = schema["properties"]["compatible"]
            properties = [("compatible", typed._typed_value(compatible, ["bosch,bme280"] if compatible["type"] == "string-array" else "bosch,bme280", projection)),
                          ("reg", typed._typed_value(schema["properties"]["reg"], [address], projection))]
            parent_path = target["path"]
        else:
            pin, flags = connection["pin"], connection["flags"]
            if (type(pin) is not int or not 0 <= pin <= option["pin_max"] or pin in option["occupied_pins"]
                    or any(start <= pin < start + count for start, count in option["reserved_ranges"])):
                raise ValueError("Choose a pin inside the observed GPIO range without a known reservation or existing consumer.")
            if type(flags) is not int or not 0 <= flags <= 65535:
                raise ValueError("Choose literal GPIO flags from 0 to 65535.")
            group = next((item for item in payload["nodes"] if item["path"] == LED_GROUP), None)
            if group:
                source, parent = _ensure_target(source, effective, group)
            else:
                _, local = _local_paths(source, effective)
                if not local["/"]:
                    source = source.plan_add_node(None, "/").apply(source)
                    root = source.find_node("/")
                else:
                    root = local["/"][0]
                settings = parent_schema.get("properties", {}).get("compatible")
                value = typed._typed_value(settings, ["gpio-leds"] if settings["type"] == "string-array" else "gpio-leds", projection) if settings else PropertyValue.string("gpio-leds")
                source = source.plan_add_node(root.id, LED_GROUP[1:], properties=[("compatible", value)]).apply(source)
                _, local = _local_paths(source, effective)
                parent = local[LED_GROUP][0]
            gpio = deepcopy(schema["properties"]["gpios"])
            gpio["providers"] = [{"id": target["effective_node_id"], "reference": target["reference"], "labels": target["labels"], "path": target["path"], "cells": ["pin", "flags"]}]
            properties = [("gpios", typed._typed_value(gpio, [{"reference": target["reference"], "cells": [pin, flags]}], projection))]
            name, parent_path = connection["name"], LED_GROUP
        path = parent_path.rstrip("/") + "/" + name
        if any(effective._path(node) == path for node in effective.syntax.nodes):
            raise ValueError("A board or overlay node already occupies the requested device path. Choose a new name.")
        source = source.plan_add_node(parent.id, name, label=label or None, properties=properties).apply(source)
        self.board.validate(request, context, draft)
        # Checked intermediate lexical edits form one original-bound history action.
        return draft.document._plan("board-module-add", [draft.document._edit(0, len(draft.document.source), source.source)],
                                    ("Only the selected overlay connection is added. Board sources, existing consumers and routing remain unchanged.",))
