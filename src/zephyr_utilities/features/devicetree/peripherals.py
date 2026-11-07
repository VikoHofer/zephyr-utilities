"""Existing local device observations and guarded source maintenance."""

from copy import deepcopy
import re
import time

from .binding_service import BindingService, _literal, _properties
from .source import SourceError


COMMANDS = frozenset({"peripheral_controller", "peripheral_update", "peripheral_remove", "gpio_update"})
GROUPS = {"led": "gpio-leds", "button": "gpio-keys"}
SIMPLE = {"boolean", "int", "string", "array", "string-array", "uint8-array"}
SCOPE = ("Topology covers unique declarations in this overlay. Inherited devices, external consumers, "
         "electrical limits and pin routing remain unknown. Save and Configure are separate actions.")


def _integer(document, node, name):
    value = _literal(document, node, name)
    return value.value[0] if value and value.kind == "cells" and len(value.value) == 1 else None


def _address(document, node):
    value = _integer(document, node, "reg")
    suffix = node.name.rpartition("@")[2]
    unit = int(suffix, 16) if suffix and re.fullmatch(r"[0-9a-fA-F]+", suffix) and len(suffix) <= 8 else None
    return value, unit


def _field(settings, name, targets):
    row = {**deepcopy(settings), "name": name, "local": None, "deleted": False, "resettable": False, "deletable": False,
           "editable": settings.get("supported", False) and settings.get("type") in SIMPLE}
    if (name == "gpios" and settings.get("type") == "phandle-array" and settings.get("supported", False)
            and settings.get("specifier_space") == "gpio"):
        row["providers"] = [{**deepcopy(item), "cells": item["cells"]["gpio"]} for item in targets if "gpio" in item["cells"]]
        row["editable"] = bool(row["providers"])
    if not row["editable"]:
        row["reason"] = row.get("reason") or "This field needs source editing or a uniquely resolved local provider."
    return row


def _creation_fields(schema, targets, *, group=None):
    fields = []
    for name, settings in schema.get("properties", {}).items():
        if name in {"compatible", "reg"} and not group:
            continue
        if settings.get("required") or group and name in {"gpios", "label", "zephyr,code"}:
            row = _field(settings, name, targets)
            if group and name in {"compatible", "reg"}:
                row.update(editable=False, reason="This GPIO child binding needs identity/address semantics outside the dedicated form.")
            if group == "button" and name == "zephyr,code":
                row["required"] = True
                row["description"] = "Enter the input event code explicitly; enabled GPIO keys require a code in the driver."
            fields.append(row)
    return fields


class PeripheralService(BindingService):
    """Reuses binding observation guards; every mutation enters DraftService.command."""

    commands = COMMANDS

    @staticmethod
    def _projection(document, catalog):
        payload = BindingService._projection(document, catalog)
        rows = {row["node_id"]: row for row in payload["nodes"]}
        targets = {item["id"]: item for item in payload["targets"]}
        nodes = {node.id: node for node in document.syntax.nodes}
        children = {node.id: [other for other in nodes.values() if other.parent_id == node.id] for node in nodes.values()}
        controllers, groups = [], []
        device_catalogs = {}

        def path_consumers(node, *, deleting=False):
            path = rows[node.id]["path"]
            if not path:
                return []
            consumers = []
            for other in rows.values():
                source_node = nodes[other["node_id"]]
                if deleting and node.start <= source_node.start < node.end:
                    continue
                for prop in other["properties"]:
                    if prop["type"] != "path":
                        continue
                    for occurrence in _properties(source_node, prop["name"]):
                        value = document.property_value(occurrence.id) if occurrence.kind == "property" else None
                        if not value or value.kind not in {"string", "path"}:
                            continue
                        target = value.value
                        if target == path or target.startswith(path + "/"):
                            consumers.append(f"{other['path'] or other['name']}: {prop['name']}")
            return consumers

        def removal(node):
            if node.id not in targets:
                return False, "This declaration has unresolved or repeated source contributors."
            if children[node.id]:
                return False, "This device has nested declarations. Review and remove them explicitly in source."
            if path_consumers(node, deleting=True):
                return False, "A binding path property references this device. Remove that consumer explicitly first."
            try:
                document.plan_remove_node(node.id)
            except SourceError as exc:
                return False, str(exc)
            return True, ""

        def child_row(node, kind):
            row = rows[node.id]
            address, unit = _address(document, node)
            problems = []
            editable = node.id in targets and row["binding"]["state"] == "matched" and not children[node.id]
            if not editable:
                problems.append("A unique supported leaf device is required; use source for unresolved or nested declarations.")
            if kind in {"i2c", "spi"} and (address is None or unit != address):
                editable = False
                problems.append("The literal unit-address and single-cell reg must match before editing this device.")
            allowed = {"gpios", "label", "zephyr,code"} if kind in GROUPS else set()
            fields = [deepcopy(prop) for prop in row["properties"] if prop["name"] not in {"compatible", "reg"} and
                      (prop["name"] in allowed if kind in GROUPS else prop["required"])]
            for prop in fields:
                if kind == "button" and prop["name"] == "zephyr,code":
                    prop["required"] = True
            removable, remove_reason = removal(node)
            reg = next((deepcopy(prop) for prop in row["properties"] if prop["name"] == "reg"), None)
            if kind in {"i2c", "spi"} and (not reg or reg["type"] != "array" or not reg["editable"]):
                editable = False
                problems.append("The reg binding cannot safely describe this single-cell bus address.")
            paths = path_consumers(node)
            return {"node_id": node.id, "name": node.name, "path": row["path"], "label": node.labels[0] if node.labels else "",
                    "compatible": row["binding"].get("compatible"), "address": address if kind in {"i2c", "spi"} else None,
                    "address_field": reg, "address_editable": not paths,
                    "address_reason": "A binding path property depends on the current node path." if paths else "",
                    "editable": editable, "reason": " ".join(problems), "fields": fields,
                    "removable": removable, "remove_reason": remove_reason, "diagnostics": problems}

        for node in nodes.values():
            row = rows[node.id]
            binding = row["binding"]
            bus = binding.get("bus", [])
            kind = bus[0] if len(bus) == 1 and bus[0] in {"uart", "i2c", "spi"} else None
            group = next((key for key, compatible in GROUPS.items() if binding.get("compatible") == compatible), None)
            if not kind and not group:
                continue
            unique = node.id in targets and binding["state"] == "matched"
            current_kind = kind or group
            entries = [child_row(child, current_kind) for child in children[node.id]]
            status_value = _literal(document, node, "status")
            status = status_value.value if status_value and status_value.kind == "string" else None
            item = {"node_id": node.id, "name": node.name, "path": row["path"], "kind": current_kind,
                    "status": status, "editable": unique, "reason": "" if unique else "This controller is not a unique local declaration.",
                    "children": entries, "diagnostics": [], "addable": unique, "add_reason": ""}
            if group:
                match = catalog.match([GROUPS[group]])
                schema = (match.get("schema") or {}).get("child_binding") or {}
                item["add_fields"] = _creation_fields(schema, payload["targets"], group=group)
                required = {prop["name"] for prop in item["add_fields"] if prop.get("required")}
                expected = {"gpios", "zephyr,code"} if group == "button" else {"gpios"}
                if not expected <= required or any(not prop["editable"] for prop in item["add_fields"] if prop.get("required")):
                    item.update(addable=False, add_reason="The group child binding or required provider fields cannot be resolved safely.")
                groups.append(item)
                continue
            fields = {"status", "current-speed" if kind == "uart" else "clock-frequency"}
            item["fields"] = [deepcopy(prop) for prop in row["properties"] if prop["name"] in fields]
            if kind in {"i2c", "spi"}:
                if _integer(document, node, "#address-cells") != 1 or _integer(document, node, "#size-cells") != 0:
                    item.update(addable=False, add_reason="This bus requires explicit local #address-cells = <1> and #size-cells = <0>.")
                occupied = {}
                for child in children[node.id]:
                    address, unit = _address(document, child)
                    if address is None or unit != address:
                        item.update(addable=False, add_reason="A child has an unresolved or inconsistent address; uniqueness cannot be established.")
                    for candidate in {address, unit} - {None}:
                        occupied.setdefault(candidate, []).append(child.id)
                for value, users in occupied.items():
                    if len(users) > 1:
                        item["diagnostics"].append(f"Duplicate {'I2C address' if kind == 'i2c' else 'chip select'} 0x{value:x} in this overlay.")
                if kind == "spi":
                    cs = next((prop for prop in row["properties"] if prop["name"] == "cs-gpios"), None)
                    if _properties(node, "cs-gpios"):
                        if cs and cs["editable"] and cs["local"] and isinstance(cs["local"]["parsed"], list):
                            item["chip_selects"] = list(range(len(cs["local"]["parsed"])))
                        else:
                            item.update(addable=False, add_reason="The existing cs-gpios layout is unresolved. Use source editing.")
                            item["chip_selects"] = []
                    else:
                        item["chip_selects"] = None
                        item["diagnostics"].append("No local GPIO chip-select array is declared; native chip-select capacity remains unknown.")
            if kind not in device_catalogs:
                device_catalogs[kind] = catalog.devices(kind)
            candidates = []
            for candidate in device_catalogs[kind]["candidates"]:
                schema = candidate.get("schema") or {}
                candidate_fields = _creation_fields(schema, payload["targets"])
                usable = candidate["state"] == "matched" and all(prop["editable"] for prop in candidate_fields)
                reg_settings = schema.get("properties", {}).get("reg")
                reg = _field(reg_settings, "reg", payload["targets"]) if reg_settings else None
                if kind in {"i2c", "spi"} and (not reg or reg["type"] != "array" or not reg["editable"]):
                    usable = False
                compatible_settings = schema.get("properties", {}).get("compatible")
                compatible_field = _field(compatible_settings, "compatible", payload["targets"]) if compatible_settings else None
                if compatible_field and (not compatible_field["editable"] or compatible_field["type"] not in {"string", "string-array"}):
                    usable = False
                if kind == "uart" and schema.get("properties", {}).get("reg", {}).get("required"):
                    usable = False
                candidates.append({"compatible": candidate["compatible"], "name": "device", "description": schema.get("description", ""),
                                   "fields": candidate_fields, "address_field": reg, "compatible_field": compatible_field, "addable": usable,
                                   "reason": "" if usable else "The binding is ambiguous, unsupported or needs required fields outside this form."})
            item["candidates"] = candidates
            item["diagnostics"].extend(device_catalogs[kind].get("diagnostics", []))
            controllers.append(item)
        roots = [node for node in nodes.values() if node.name == "/" and node.parent_id is None and node.id in targets]
        options = []
        for kind, compatible in GROUPS.items():
            match = catalog.match([compatible])
            available = len(roots) == 1 and match["state"] == "matched" and bool(match["schema"].get("child_binding"))
            properties = (match.get("schema") or {}).get("properties", {})
            compatible_settings = properties.get("compatible")
            compatible_field = _field(compatible_settings, "compatible", payload["targets"]) if compatible_settings else None
            if any(settings.get("required") for name, settings in properties.items() if name != "compatible"):
                available = False
            if compatible_field and (not compatible_field["editable"] or compatible_field["type"] not in {"string", "string-array"}):
                available = False
            options.append({"kind": kind, "name": "leds" if kind == "led" else "buttons", "available": available,
                            "compatible_field": compatible_field,
                            "reason": "" if available else "A unique local root and resolved GPIO group binding without additional required group fields are needed."})
        payload.update(controllers=controllers, gpio_groups=groups, group_options=options,
                       gpio_providers=[{**deepcopy(target), "cells": target["cells"]["gpio"]}
                                       for target in payload["targets"] if "gpio" in target["cells"]])
        payload["diagnostics"].insert(0, SCOPE)
        return payload

    @staticmethod
    def _values(values, fields, *, required=False):
        if not isinstance(values, dict) or len(values) > 64 or set(values) - {field["name"] for field in fields}:
            raise ValueError("Choose only the supported properties shown for this peripheral.")
        if required and any(field.get("required") and field["name"] not in values for field in fields):
            raise ValueError("Enter each required device property explicitly; binding defaults are not inserted automatically.")
        return values

    def _properties(self, values, fields, payload, *, required=False):
        self._values(values, fields, required=required)
        result = []
        for name, raw in values.items():
            field = next(field for field in fields if field["name"] == name)
            if not field["editable"]:
                raise ValueError(field.get("reason") or "This property remains source-only.")
            value = self._typed_value(field, raw, payload)
            if value is None:
                raise ValueError("A required boolean property must be present. Use the binding editor for explicit deletion.")
            result.append((name, value))
        return result

    @staticmethod
    def _check_address(document, parent, kind, address, *, exclude=None):
        if kind == "uart":
            if address is not None:
                raise ValueError("UART child devices do not use the I2C/SPI address form.")
            return
        if type(address) is not int or not 0 <= address <= 0xffffffff:
            raise ValueError("Enter one unsigned 32-bit device address or chip-select index.")
        if kind == "i2c" and not 0x08 <= address <= 0x77:
            raise ValueError("The supported I2C form uses non-reserved 7-bit addresses from 0x08 to 0x77.")
        controller = document.node(parent["node_id"])
        if _integer(document, controller, "#address-cells") != 1 or _integer(document, controller, "#size-cells") != 0:
            raise ValueError("The local bus cell layout must be one address cell and zero size cells.")
        for node in document.syntax.nodes:
            if node.parent_id != controller.id or node.id == exclude:
                continue
            reg, unit = _address(document, node)
            if reg is None or unit != reg:
                raise ValueError("A sibling device has an unresolved or inconsistent address; edit its source before allocating another address.")
            if address == reg:
                raise ValueError("Another device already uses this address or chip select, including disabled local devices.")
        if kind == "spi" and parent.get("chip_selects") is not None and address not in parent["chip_selects"]:
            raise ValueError("Choose an index from the existing resolved cs-gpios array; it is never resized automatically.")

    def plan(self, body, context, draft):
        command = body["command"]
        kind = command["kind"]
        fields = {"peripheral_controller": {"node_id", "values"},
                  "peripheral_update": {"node_id", "address", "values"}, "peripheral_remove": {"node_id"},
                  "gpio_update": {"node_id", "values"}}[kind] | {"kind", "schema_id"}
        if set(command) != fields or not isinstance(command["schema_id"], str):
            raise ValueError("Choose the exact supported peripheral command fields.")
        observed = self.observations.get(command["schema_id"])
        guard = {key: value for key, value in body.items() if key != "command"}
        if observed is None or observed["guard"] != guard or observed["expires"] <= time.monotonic():
            raise ValueError("Peripheral topology belongs to an earlier draft/context or expired. Load peripherals again.")
        observed["catalog"].revalidate()
        payload = observed["payload"]
        document = draft.document
        parents = [*payload["controllers"], *payload["gpio_groups"]]
        if kind == "peripheral_controller":
            parent = next((item for item in payload["controllers"] if item["node_id"] == command["node_id"]), None)
            if not parent or not parent["editable"]:
                raise ValueError("Choose a unique supported local controller.")
            if not command["values"]:
                raise ValueError("Choose at least one controller property to change.")
            self._values(command["values"], parent["fields"])
            if "status" in command["values"] and command["values"]["status"] not in ("okay", "disabled"):
                raise ValueError("Choose okay or disabled for controller status.")
            props = self._properties(command["values"], parent["fields"], payload)
            change = document.plan_update_node(parent["node_id"], properties=props)
        else:
            found = [(parent, child) for parent in parents for child in parent["children"] if child["node_id"] == command["node_id"]]
            if len(found) != 1:
                raise ValueError("Choose a unique device from this local topology.")
            parent, child = found[0]
            if kind == "peripheral_remove":
                if not child["removable"]:
                    raise ValueError(child["remove_reason"])
                change = document.plan_remove_node(child["node_id"])
            else:
                if not child["editable"] or (kind == "gpio_update") != (parent["kind"] in GROUPS):
                    raise ValueError(child["reason"] or "This device requires explicit source editing.")
                props = self._properties(command["values"], child["fields"], payload)
                if kind == "peripheral_update":
                    self._check_address(document, parent, parent["kind"], command["address"], exclude=child["node_id"])
                    if parent["kind"] != "uart":
                        self._typed_value(child["address_field"], [command["address"]], payload)
                        proposed_name = child["name"].split("@", 1)[0] + f"@{command['address']:x}"
                        if proposed_name != child["name"] and not child["address_editable"]:
                            raise ValueError(child["address_reason"])
                    change = document.plan_update_node(child["node_id"], unit_address=command["address"], properties=props)
                else:
                    change = document.plan_update_node(child["node_id"], properties=props)
        observed["catalog"].revalidate()
        return change
