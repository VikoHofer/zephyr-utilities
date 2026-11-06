"""Audited interrupt-signal GPIO properties using explicit local binding evidence."""

from copy import deepcopy
import time

from .binding_service import BindingService, _literal, _properties
from .interrupt_gpio_profiles import INTERRUPT_GPIO_RECIPES
from .pwm import _compatible_valid, _status


COMMANDS = frozenset({"interrupt_gpio_set"})
SCOPE = ("Interrupt GPIO forms cover selected compatible/property recipes and one literal GPIO specifier. "
         "Logical active level is flag bit 0; it does not select an interrupt edge or level. "
         "Driver trigger configuration, interrupt availability, electrical suitability, inherited pin use and Kconfig remain unknown. "
         "The supported GPIO API profile uses pin IDs 0–31 and 16-bit devicetree flags. Save and Configure remain separate actions.")


def _bound_value(row, name, kind, payload):
    field = next((item for item in row["properties"] if item["name"] == name), None)
    if not field or field.get("type") != kind or not field.get("editable") or not field.get("local"):
        return None
    value = field["local"]["parsed"]
    try:
        BindingService(None)._typed_value(field, value, payload)
    except ValueError:
        return None
    return value


def interrupt_gpio_providers(document, payload):
    """Keep local ranges explicit and withhold unmodeled or malformed allocations."""
    rows = {row["node_id"]: row for row in payload["nodes"]}
    providers = []
    for target in payload["targets"]:
        if "gpio" not in target["cells"]:
            continue
        node, row = document.node(target["id"]), rows[target["id"]]
        cells = target["cells"]["gpio"]
        reason = ""
        if cells != ["pin", "flags"]:
            reason = "This form needs the GPIO cell layout pin, flags in that order."
        elif _bound_value(row, "#gpio-cells", "int", payload) != 2:
            reason = "The explicit #gpio-cells count must satisfy its supported integer binding and equal 2."
        elif _bound_value(row, "gpio-controller", "boolean", payload) is not True:
            reason = "A binding-valid local gpio-controller boolean declaration is required."
        elif not _compatible_valid(row, payload):
            reason = "The provider compatible value must satisfy its supported local binding."
        count, ranges = None, None
        if _properties(node, "ngpios"):
            count = _bound_value(row, "ngpios", "int", payload)
            if type(count) is not int or count <= 0:
                count = None
                reason = reason or "The explicit ngpios count is opaque, invalid or outside its supported binding. Repair it before assigning a GPIO."
        if _properties(node, "gpio-reserved-ranges"):
            reserved = _bound_value(row, "gpio-reserved-ranges", "array", payload)
            if (not isinstance(reserved, list) or len(reserved) % 2
                    or any(count <= 0 or start + count > 2 ** 32 for start, count in zip(reserved[::2], reserved[1::2]))):
                reason = reason or "The explicit gpio-reserved-ranges need supported literal start/count pairs before assigning a GPIO."
            else:
                ranges = [list(pair) for pair in zip(reserved[::2], reserved[1::2])]
        if any(child.parent_id == node.id and _properties(child, "gpio-hog") for child in document.syntax.nodes):
            reason = reason or "This provider has local GPIO hog declarations whose implicit allocations are outside this form. Use source or generic binding editing."
        providers.append({**deepcopy(target), "cells": list(cells), "status": _status(document, node),
                          "supported": not reason, "reason": reason, "pin_count": count, "reserved_ranges": ranges})
    return providers


class InterruptGPIOService(BindingService):
    """Observe exact signal recipes and use the canonical source/history command path."""

    commands = COMMANDS

    @staticmethod
    def _projection(document, catalog):
        payload = BindingService._projection(document, catalog)
        targets = {target["id"] for target in payload["targets"]}
        providers = interrupt_gpio_providers(document, payload)
        supported = [provider for provider in providers if provider["supported"]]
        consumers = []
        for row in payload["nodes"]:
            node = document.node(row["node_id"])
            compatible = _literal(document, node, "compatible")
            compatibles = ([compatible.value] if compatible.kind == "string" else list(compatible.value)
                           if compatible.kind == "string-array" else []) if compatible else []
            for recipe in INTERRUPT_GPIO_RECIPES:
                if row["binding"].get("compatible") != recipe["compatible"]:
                    continue
                name = recipe["property"]
                original = next((field for field in row["properties"] if field["name"] == name), None)
                field = deepcopy(original) if original else {"name": name, "editable": False, "local": None,
                                                            "reason": "The recipe's GPIO property is absent from its resolved binding."}
                field["providers"] = deepcopy(supported)
                editable = bool(field.get("editable") and row["node_id"] in targets and row["binding"]["state"] == "matched"
                                and field.get("type") == "phandle-array" and field.get("specifier_space") == "gpio")
                reason = "" if editable else field.get("reason") or "A unique local consumer with the recipe's supported GPIO binding is required."
                if recipe["compatible"] not in compatibles:
                    editable, reason = False, "The consumer needs a literal local compatible naming this recipe; child-binding metadata alone does not establish device identity."
                if not _compatible_valid(row, payload):
                    editable, reason = False, "The consumer compatible value must satisfy its supported local binding."
                local = field.get("local")
                parsed = local.get("parsed") if local else None
                entry = deepcopy(parsed[0]) if isinstance(parsed, list) and len(parsed) == 1 else None
                if local and entry is None:
                    editable, reason = False, "Multiple, opaque or unresolved GPIO specifiers require source or generic binding editing."
                if not supported:
                    editable, reason = False, "No unique local GPIO provider has the supported cell layout, declaration and usable local range evidence."
                if entry is not None:
                    provider = BindingService._target(supported, entry["reference"], required=False)
                    if provider is None or len(entry["cells"]) != 2:
                        editable, reason = False, "The current GPIO provider or cell layout is unresolved or unsupported; its source is preserved."
                field.update(editable=editable, reason=reason)
                consumers.append({"node_id": row["node_id"], "name": row["name"], "path": row["path"],
                                  "property": name, "recipe_id": recipe["id"], "signal": recipe["signal"],
                                  "description": recipe["description"], "entry": entry, "field": field,
                                  "editable": editable, "reason": reason})
        payload.update(providers=providers, consumers=consumers)
        payload["diagnostics"].insert(0, SCOPE)
        return payload

    def plan(self, body, context, draft):
        command = body["command"]
        if (set(command) != {"kind", "schema_id", "node_id", "property", "entry"}
                or command["kind"] != "interrupt_gpio_set" or not isinstance(command["schema_id"], str)):
            raise ValueError("Choose the exact supported interrupt GPIO command fields.")
        observed = self.observations.get(command["schema_id"])
        guard = {key: value for key, value in body.items() if key != "command"}
        if observed is None or observed["guard"] != guard or observed["expires"] <= time.monotonic():
            raise ValueError("Interrupt GPIO forms belong to an earlier draft/context or expired. Load interrupt GPIOs again.")
        observed["catalog"].revalidate()
        payload, document = observed["payload"], draft.document
        consumer = next((item for item in payload["consumers"] if item["node_id"] == command["node_id"]
                         and item["property"] == command["property"]), None)
        if not consumer or not consumer["editable"]:
            raise ValueError(consumer["reason"] if consumer else "Choose a supported interrupt GPIO consumer and property.")
        entry = command["entry"]
        if (not isinstance(entry, dict) or set(entry) != {"reference", "cells"}
                or not isinstance(entry["cells"], list) or len(entry["cells"]) != 2):
            raise ValueError("Choose one GPIO provider reference with literal pin and flags cells.")
        pin, flags = entry["cells"]
        if type(pin) is not int or not 0 <= pin <= 31:
            raise ValueError("Enter a GPIO pin identifier from 0 to 31; hardware interrupt support remains unknown.")
        if type(flags) is not int or not 0 <= flags <= 65535:
            raise ValueError("Enter GPIO devicetree flags from 0 to 65535. Logical polarity is bit 0; trigger mode is separate.")
        provider = self._target(consumer["field"]["providers"], entry["reference"])
        if provider["pin_count"] is not None and pin >= provider["pin_count"]:
            raise ValueError("This GPIO pin lies outside the provider's explicit ngpios count.")
        if provider["reserved_ranges"] is not None and any(start <= pin < start + count for start, count in provider["reserved_ranges"]):
            raise ValueError("This GPIO pin lies in the provider's explicit gpio-reserved-ranges.")
        value = self._typed_value(consumer["field"], [entry], payload)
        change = document.plan_update_node(consumer["node_id"], properties=[(consumer["property"], value)])
        observed["catalog"].revalidate()
        return change
