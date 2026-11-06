"""Binding-driven local ADC consumers and common channel configuration forms."""

from copy import deepcopy
import time

from .binding_service import BindingService, _literal, _properties
from .peripherals import _address
from .pwm import _compatible_valid, _status


COMMANDS = frozenset({"adc_set", "adc_channel_add", "adc_channel_set"})
CHANNEL_FIELDS = {"gain": "zephyr,gain", "reference": "zephyr,reference",
                  "acquisition_time": "zephyr,acquisition-time"}
SCOPE = ("ADC forms cover one literal io-channels specifier and common channel configuration on unique local providers. "
         "Channel identifiers use the ADC API range 0–31; driver support, physical inputs, reference voltage, "
         "acquisition timing, routing and electrical suitability remain unknown. Optional channel settings stay unchanged. "
         "Save and Configure remain separate actions.")


def _bound_value(row, name, payload, kind=None):
    field = next((item for item in row["properties"] if item["name"] == name), None)
    if not field or kind and field.get("type") != kind or not field.get("editable") or not field.get("local"):
        return None
    value = field["local"]["parsed"]
    try:
        BindingService(None)._typed_value(field, value, payload)
    except ValueError:
        return None
    return value


def _channel_schema(row, catalog):
    binding = row["binding"]
    if catalog is None or binding["state"] != "matched" or not binding.get("compatible"):
        return {}
    match = catalog.match([binding["compatible"]], bus=binding.get("on_bus"))
    schema = match.get("schema") or {}
    if match["state"] != "matched" or schema.get("path") != binding.get("path"):
        return {}
    return schema.get("child_binding") or {}


def _common_fields(schema):
    properties = schema.get("properties", {})
    fields = {}
    for key, name in CHANNEL_FIELDS.items():
        settings = properties.get(name, {})
        kind = "int" if key == "acquisition_time" else "string"
        editable = bool(settings.get("supported") and settings.get("required") and settings.get("type") == kind
                        and (kind == "int" or settings.get("enum")))
        fields[key] = {**deepcopy(settings), "name": name, "editable": editable, "local": None}
    reg = deepcopy(properties.get("reg", {}))
    supported = bool(reg.get("supported") and reg.get("required") and reg.get("type") == "array"
                     and all(field["editable"] for field in fields.values()))
    return fields, reg, supported


def adc_providers(document, payload, catalog=None):
    """Return common ADC binding evidence; generic I/O providers remain unsupported."""
    rows = {row["node_id"]: row for row in payload["nodes"]}
    providers = []
    for target in payload["targets"]:
        if "io-channel" not in target["cells"]:
            continue
        node, row = document.node(target["id"]), rows[target["id"]]
        cells = target["cells"]["io-channel"]
        schema = _channel_schema(row, catalog)
        fields, reg, common = _common_fields(schema)
        count_valid = _bound_value(row, "#io-channel-cells", payload, "int") == len(cells)
        compatible_valid = _compatible_valid(row, payload)
        reason = ""
        if cells != ["input"]:
            reason = "This provider needs the single input cell layout for the ADC form."
        elif not count_valid:
            reason = "The explicit #io-channel-cells count must satisfy its supported integer binding."
        elif not compatible_valid:
            reason = "The provider compatible value must satisfy its supported local binding."
        elif not common:
            reason = "A supported common ADC child binding is required; generic I/O providers are not assumed to be ADCs."
        add_reason = reason
        if not add_reason and any(settings.get("required") for name, settings in schema.get("properties", {}).items()
                                  if name not in {"reg", *CHANNEL_FIELDS.values()}):
            add_reason = "This ADC child binding requires additional fields outside the common channel form."
        if not add_reason and (_bound_value(row, "#address-cells", payload, "int") != 1
                               or _bound_value(row, "#size-cells", payload, "int") != 0):
            add_reason = "Channel configuration needs binding-valid local #address-cells = <1> and #size-cells = <0>."
        children = [child for child in document.syntax.nodes if child.parent_id == node.id]
        occupied = {}
        for child in children:
            address, unit = _address(document, child)
            if address is None or address != unit or not child.name.startswith("channel@"):
                add_reason = add_reason or "A child has an unresolved or inconsistent channel address; uniqueness cannot be established."
            for candidate in {address, unit} - {None}:
                occupied.setdefault(candidate, []).append(child.id)
        if any(len(users) > 1 for users in occupied.values()):
            add_reason = add_reason or "Duplicate channel identifiers must be repaired explicitly in source."
        providers.append({**deepcopy(target), "cells": list(cells), "status": _status(document, node),
                          "supported": not reason, "reason": reason, "addable": not add_reason,
                          "add_reason": add_reason, "channel_fields": fields, "reg_field": reg,
                          "occupied_channels": sorted(occupied)})
    return providers


class ADCService(BindingService):
    """Observe local ADC schemas and plan one canonical draft change per action."""

    commands = COMMANDS

    @staticmethod
    def _projection(document, catalog):
        payload = BindingService._projection(document, catalog)
        rows = {row["node_id"]: row for row in payload["nodes"]}
        targets = {target["id"] for target in payload["targets"]}
        providers = adc_providers(document, payload, catalog)
        supported = [provider for provider in providers if provider["supported"]]
        consumers, channels = [], []
        for row in rows.values():
            node = document.node(row["node_id"])
            original = next((prop for prop in row["properties"] if prop["name"] == "io-channels"), None)
            if original is None and not _properties(node, "io-channels"):
                continue
            field = deepcopy(original) if original else {"name": "io-channels", "editable": False, "local": None,
                                                        "reason": "This declaration has no resolved io-channels binding."}
            field["providers"] = deepcopy(supported)
            editable = bool(field.get("editable") and node.id in targets and row["binding"]["state"] == "matched"
                            and field.get("type") == "phandle-array" and field.get("specifier_space") == "io-channel")
            reason = "" if editable else field.get("reason") or "A unique local declaration with a supported io-channels binding is needed."
            if not _compatible_valid(row, payload):
                editable, reason = False, "The consumer compatible value must satisfy its supported local binding."
            local = field.get("local")
            parsed = local.get("parsed") if local else None
            entry = deepcopy(parsed[0]) if isinstance(parsed, list) and len(parsed) == 1 else None
            if local and entry is None:
                editable, reason = False, "Multiple, opaque or unresolved ADC specifiers require generic binding or source editing."
            if not supported:
                editable, reason = False, "No unique local provider has the supported ADC binding and matching explicit #io-channel-cells."
            if entry is not None:
                provider = BindingService._target(supported, entry["reference"], required=False)
                if provider is None or len(entry["cells"]) != 1:
                    editable, reason = False, "The current I/O provider is unresolved or outside the supported ADC profile; its source is preserved."
            if _properties(node, "io-channel-names"):
                value = _literal(document, node, "io-channel-names")
                if not value or not (value.kind == "string" or value.kind == "string-array" and len(value.value) == 1):
                    editable, reason = False, "The existing io-channel-names must be one literal name for this single-specifier form."
            field.update(editable=editable, reason=reason)
            consumers.append({"node_id": node.id, "name": row["name"], "path": row["path"],
                              "editable": editable, "reason": reason, "entry": entry, "field": field})

        for provider in providers:
            for node in document.syntax.nodes:
                if node.parent_id != provider["id"]:
                    continue
                row = rows[node.id]
                address, unit = _address(document, node)
                fields = {key: deepcopy(next((field for field in row["properties"] if field["name"] == name), {}))
                          for key, name in CHANNEL_FIELDS.items()}
                values = {key: (field.get("local") or {}).get("parsed") for key, field in fields.items()}
                reason = provider["add_reason"]
                if not reason and (node.id not in targets or row["binding"]["state"] != "matched" or node.children):
                    reason = "A unique local channel leaf with its parent's ADC child binding is required."
                if not reason and (not node.name.startswith("channel@") or address != unit or type(address) is not int
                                   or not 0 <= address <= 31):
                    reason = "A channel@hex-address node and matching literal reg in the ADC API range 0–31 are required."
                if not reason and (_properties(node, "compatible") or any(not field.get("editable") or not field.get("local")
                                                                           for field in fields.values())):
                    reason = "All three common ADC settings need supported literal values in the inherited child binding."
                if not reason:
                    reg = next((field for field in row["properties"] if field["name"] == "reg"), None)
                    try:
                        if not reg or not reg["editable"]:
                            raise ValueError("The channel reg binding is unsupported.")
                        BindingService(None)._typed_value(reg, [address], payload)
                    except ValueError:
                        reason = "The channel identifier must satisfy its supported reg binding."
                channels.append({"node_id": node.id, "parent_id": provider["id"], "name": node.name,
                                 "path": row["path"], "channel": address, **values, "fields": fields,
                                 "editable": not reason, "reason": reason})
        payload.update(providers=providers, consumers=consumers, channels=channels)
        payload["diagnostics"].insert(0, SCOPE)
        return payload

    @staticmethod
    def _channel(value):
        if type(value) is not int or not 0 <= value <= 31:
            raise ValueError("Enter an ADC channel identifier from 0 to 31; driver support remains unknown.")
        return value

    def _config(self, command, fields, payload):
        value = command["acquisition_time"]
        if type(value) is not int or not 0 <= value <= 65535:
            raise ValueError("Enter an encoded acquisition time from 0 to 65535; 0 selects the hardware default.")
        return [(name, self._typed_value(fields[key], command[key], payload)) for key, name in CHANNEL_FIELDS.items()]

    def plan(self, body, context, draft):
        command = body["command"]
        kind = command["kind"]
        fields = {"adc_set": {"node_id", "entry"},
                  "adc_channel_add": {"parent_id", "channel", *CHANNEL_FIELDS},
                  "adc_channel_set": {"node_id", *CHANNEL_FIELDS}}[kind] | {"kind", "schema_id"}
        if set(command) != fields or not isinstance(command["schema_id"], str):
            raise ValueError("Choose the exact supported ADC command fields.")
        observed = self.observations.get(command["schema_id"])
        guard = {key: value for key, value in body.items() if key != "command"}
        if observed is None or observed["guard"] != guard or observed["expires"] <= time.monotonic():
            raise ValueError("ADC forms belong to an earlier draft/context or expired. Load ADC channels again.")
        observed["catalog"].revalidate()
        payload, document = observed["payload"], draft.document
        if kind == "adc_set":
            consumer = next((item for item in payload["consumers"] if item["node_id"] == command["node_id"]), None)
            if not consumer or not consumer["editable"]:
                raise ValueError(consumer["reason"] if consumer else "Choose a supported local ADC consumer.")
            entry = command["entry"]
            if (not isinstance(entry, dict) or set(entry) != {"reference", "cells"}
                    or not isinstance(entry["cells"], list) or len(entry["cells"]) != 1):
                raise ValueError("Choose one ADC provider reference and one literal input cell.")
            self._channel(entry["cells"][0])
            value = self._typed_value(consumer["field"], [entry], payload)
            change = document.plan_update_node(consumer["node_id"], properties=[("io-channels", value)])
        elif kind == "adc_channel_add":
            provider = next((item for item in payload["providers"] if item["id"] == command["parent_id"]), None)
            if not provider or not provider["addable"]:
                raise ValueError(provider["add_reason"] if provider else "Choose a supported local ADC provider.")
            channel = self._channel(command["channel"])
            if channel in provider["occupied_channels"]:
                raise ValueError("This channel identifier is already declared; edit its existing configuration.")
            reg = self._typed_value(provider["reg_field"], [channel], payload)
            properties = [("reg", reg), *self._config(command, provider["channel_fields"], payload)]
            change = document.plan_add_node(provider["id"], f"channel@{channel:x}", properties=properties)
        else:
            channel = next((item for item in payload["channels"] if item["node_id"] == command["node_id"]), None)
            if not channel or not channel["editable"]:
                raise ValueError(channel["reason"] if channel else "Choose a supported local ADC channel.")
            change = document.plan_update_node(channel["node_id"], properties=self._config(command, channel["fields"], payload))
        observed["catalog"].revalidate()
        return change
