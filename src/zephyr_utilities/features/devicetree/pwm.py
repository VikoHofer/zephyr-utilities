"""Binding-driven local PWM consumers; no inferred target or timer capabilities."""

from copy import deepcopy
import re
import time

from .binding_service import BindingService, _literal, _properties
from .source import PropertyValue


COMMANDS = frozenset({"pwm_set", "pwm_group_add", "pwm_led_add"})
LAYOUTS = (("channel", "period"), ("channel", "period", "flags"))
NODE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9,._+\-]{0,62}")
SCOPE = ("PWM forms cover single literal specifiers and unique providers declared in this overlay. "
         "Channel numbering, available channels, shared timer periods, pin routing and electrical suitability "
         "remain unknown without target evidence. Save and Configure remain separate actions.")


def _status(document, node):
    unknown = False
    while node:
        props = _properties(node, "status")
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


def _name(value):
    if not isinstance(value, str) or not NODE_NAME.fullmatch(value):
        raise ValueError("Enter a node name starting with a letter, without a unit-address or path.")


def _pwm_field(settings, providers):
    field = {**deepcopy(settings or {}), "name": "pwms", "local": None, "editable": False,
             "providers": deepcopy(providers), "reason": "The child binding needs a supported pwms property in PWM specifier space."}
    field["editable"] = bool(settings and settings.get("supported") and settings.get("type") == "phandle-array"
                             and settings.get("specifier_space") == "pwm")
    if field["editable"]:
        field["reason"] = ""
    return field


def _child_schema(schema, providers):
    child = (schema or {}).get("child_binding") or {}
    properties = child.get("properties", {})
    field = _pwm_field(properties.get("pwms"), providers)
    if not field["editable"] or not field.get("required"):
        return field, "A supported child binding with required pwms in PWM specifier space is needed."
    if any(settings.get("required") for name, settings in properties.items() if name != "pwms"):
        return field, "This PWM LED child binding requires additional fields outside this form."
    return field, ""


def _compatible_valid(row, payload):
    field = next((prop for prop in row["properties"] if prop["name"] == "compatible"), None)
    if field is None:
        return True
    if not field.get("editable") or not field.get("local"):
        return False
    try:
        BindingService(None)._typed_value(field, field["local"]["parsed"], payload)
    except ValueError:
        return False
    return True


def pwm_providers(document, payload):
    """Project only observed local PWM evidence, shared by forms and diagnostics."""
    rows = {row["node_id"]: row for row in payload["nodes"]}
    validator = BindingService(None)

    def bound_value(row, name, kind=None):
        field = next((prop for prop in row["properties"] if prop["name"] == name), None)
        if not field or kind and field.get("type") != kind or not field.get("editable") or not field.get("local"):
            return None
        value = field["local"]["parsed"]
        try:
            validator._typed_value(field, value, payload)
        except ValueError:
            return None
        return value

    providers = []
    for target in payload["targets"]:
        if "pwm" not in target["cells"]:
            continue
        node = document.node(target["id"])
        row = rows[node.id]
        names = target["cells"]["pwm"]
        count_valid = bound_value(row, "#pwm-cells", "int") == len(names)
        compatible_valid = _compatible_valid(row, payload)
        supported = tuple(names) in LAYOUTS and count_valid and compatible_valid
        reason = "" if supported else "This provider's PWM cell layout requires generic binding or source editing."
        if not count_valid:
            reason = "The explicit #pwm-cells count must satisfy its supported integer binding."
        elif not compatible_valid:
            reason = "The provider compatible value must satisfy its supported local binding."
        providers.append({**deepcopy(target), "cells": list(names), "status": _status(document, node),
                          "channel_count": bound_value(row, "channels", "int"), "supported": supported, "reason": reason})
    return providers


class PWMService(BindingService):
    """Observe local PWM schemas; mutations use the shared canonical draft command."""

    commands = COMMANDS

    @staticmethod
    def _projection(document, catalog):
        payload = BindingService._projection(document, catalog)
        rows = {row["node_id"]: row for row in payload["nodes"]}
        targets = {target["id"]: target for target in payload["targets"]}
        validator = BindingService(None)
        providers = pwm_providers(document, payload)
        supported = [provider for provider in providers if provider["supported"]]
        consumers = []
        for row in rows.values():
            node = document.node(row["node_id"])
            original = next((prop for prop in row["properties"] if prop["name"] == "pwms"), None)
            if original is None and not _properties(node, "pwms"):
                continue
            field = deepcopy(original) if original else {"name": "pwms", "editable": False, "local": None,
                                                        "reason": "This declaration has no resolved pwms binding."}
            field["providers"] = deepcopy(supported)
            reason = field.get("reason", "")
            editable = bool(field.get("editable") and row["node_id"] in targets and row["binding"]["state"] == "matched"
                            and field.get("type") == "phandle-array" and field.get("specifier_space") == "pwm")
            if not editable:
                reason = reason or "A unique local declaration with a supported pwms binding is needed."
            if not _compatible_valid(row, payload):
                editable, reason = False, "The consumer compatible value must satisfy its supported local binding."
            local = field.get("local")
            parsed = local.get("parsed") if local else None
            entry = deepcopy(parsed[0]) if isinstance(parsed, list) and len(parsed) == 1 else None
            if local and entry is None:
                editable, reason = False, "Multiple, opaque or unresolved PWM specifiers require generic binding or source editing."
            if not supported:
                editable, reason = False, "No unique local provider has a supported PWM cell layout and matching explicit #pwm-cells."
            if entry is not None:
                provider = BindingService._target(supported, entry["reference"], required=False)
                if provider is None or len(entry["cells"]) != len(provider["cells"]):
                    editable, reason = False, "The current PWM provider or cell layout is unresolved or unsupported; its source is preserved."
            names = _properties(node, "pwm-names")
            if names:
                value = _literal(document, node, "pwm-names")
                if not value or not (value.kind == "string" or value.kind == "string-array" and len(value.value) == 1):
                    editable, reason = False, "The existing pwm-names must be one literal name for this single-specifier form."
            field.update(editable=editable, reason=reason)
            consumers.append({"node_id": row["node_id"], "name": row["name"], "path": row["path"],
                              "editable": editable, "reason": reason, "entry": entry, "field": field})

        match = catalog.match(["pwm-leds"])
        schema = match.get("schema") or {}
        child_field, child_reason = _child_schema(schema, supported)
        compatible = schema.get("properties", {}).get("compatible")
        group_reason = "" if match["state"] == "matched" else "The local pwm-leds binding cannot be resolved uniquely."
        if not group_reason and any(settings.get("required") for name, settings in schema.get("properties", {}).items()
                                    if name != "compatible"):
            group_reason = "The PWM LED group binding requires additional fields outside this form."
        if not group_reason and compatible:
            if not compatible.get("supported") or compatible.get("type") not in {"string", "string-array"}:
                group_reason = "The PWM LED compatible field has unsupported constraints."
            else:
                try:
                    validator._typed_value(compatible, ["pwm-leds"] if compatible["type"] == "string-array" else "pwm-leds", payload)
                except ValueError:
                    group_reason = "The PWM LED compatible value does not satisfy its local binding."
        roots = [node for node in document.syntax.nodes if node.name == "/" and node.parent_id is None and node.id in targets]
        root_reason = "" if len(roots) == 1 else "A unique editable local root is needed to add a PWM LED group."
        groups = []
        for row in rows.values():
            if row["binding"].get("compatible") != "pwm-leds":
                continue
            reason = child_reason
            if row["node_id"] not in targets or row["binding"]["state"] != "matched":
                reason = "A unique editable local PWM LED group is needed."
            elif not _compatible_valid(row, payload):
                reason = "The group compatible value must satisfy its supported local binding."
            if not reason and not supported:
                reason = "Add a supported local PWM provider before creating a PWM LED."
            groups.append({"node_id": row["node_id"], "name": row["name"], "path": row["path"],
                           "addable": not reason, "reason": reason})
        option_reason = group_reason or child_reason or root_reason
        payload.update(providers=providers, consumers=consumers, groups=groups,
                       group_option={"available": not option_reason, "reason": option_reason, "name": "pwm_leds"},
                       led_field=child_field, compatible_field=deepcopy(compatible))
        payload["diagnostics"].insert(0, SCOPE)
        return payload

    def _entry(self, entry, field, payload):
        if not isinstance(entry, dict) or set(entry) != {"reference", "cells"} or not isinstance(entry["cells"], list):
            raise ValueError("Choose one PWM provider reference and its literal cells.")
        provider = self._target(field["providers"], entry["reference"])
        if not provider.get("supported") or tuple(provider["cells"]) not in LAYOUTS:
            raise ValueError("Choose a provider with the supported channel, period and optional flags layout.")
        value = self._typed_value(field, [entry], payload)
        if type(entry["cells"][1]) is not int or entry["cells"][1] <= 0:
            raise ValueError("Enter a positive PWM period in nanoseconds.")
        return value

    def plan(self, body, context, draft):
        command = body["command"]
        kind = command["kind"]
        fields = {"pwm_set": {"node_id", "entry"}, "pwm_group_add": {"name"},
                  "pwm_led_add": {"parent_id", "name", "label", "entry"}}[kind] | {"kind", "schema_id"}
        if set(command) != fields or not isinstance(command["schema_id"], str):
            raise ValueError("Choose the exact supported PWM command fields.")
        observed = self.observations.get(command["schema_id"])
        guard = {key: value for key, value in body.items() if key != "command"}
        if observed is None or observed["guard"] != guard or observed["expires"] <= time.monotonic():
            raise ValueError("PWM forms belong to an earlier draft/context or expired. Load PWM resources again.")
        observed["catalog"].revalidate()
        payload, document = observed["payload"], draft.document
        if kind == "pwm_set":
            consumer = next((item for item in payload["consumers"] if item["node_id"] == command["node_id"]), None)
            if not consumer or not consumer["editable"]:
                raise ValueError(consumer["reason"] if consumer else "Choose a supported local PWM consumer.")
            value = self._entry(command["entry"], consumer["field"], payload)
            change = document.plan_update_node(consumer["node_id"], properties=[("pwms", value)])
        elif kind == "pwm_group_add":
            if not payload["group_option"]["available"]:
                raise ValueError(payload["group_option"]["reason"])
            _name(command["name"])
            compatible = payload["compatible_field"]
            if compatible:
                self._typed_value(compatible, ["pwm-leds"] if compatible["type"] == "string-array" else "pwm-leds", payload)
            root = next(node for node in document.syntax.nodes if node.name == "/" and node.parent_id is None)
            change = document.plan_add_node(root.id, command["name"], properties=[("compatible", PropertyValue.string("pwm-leds"))])
        else:
            group = next((item for item in payload["groups"] if item["node_id"] == command["parent_id"]), None)
            if not group or not group["addable"]:
                raise ValueError(group["reason"] if group else "Choose a supported local PWM LED group.")
            _name(command["name"])
            if not isinstance(command["label"], str):
                raise ValueError("Enter a literal node label or leave it empty.")
            value = self._entry(command["entry"], payload["led_field"], payload)
            change = document.plan_add_node(group["node_id"], command["name"], label=command["label"] or None,
                                            properties=[("pwms", value)])
        observed["catalog"].revalidate()
        return change
