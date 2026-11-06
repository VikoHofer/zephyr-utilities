"""Selected board plus canonical overlay observations, independent of application aliases."""

from copy import deepcopy
import hashlib
import json
import time
import uuid

from .binding_service import BindingService, _literal, _properties
from .bindings import BindingCatalog
from .board_sources import BoardSources
from .drafts import _BINDING_FIELDS, _GUARD_FIELDS
from .source import SourceDocument


SCOPE = ("Hardware describes the selected board DTS and this one overlay, resolved by explicit read-only preprocessing. "
         "No Configure, build or firmware execution is performed. GPIO slot counts are not package pin counts; "
         "electrical suitability, physical connections and driver readiness remain unverified.")


def _key(path):
    return "hardware:" + path


def _number(document, node, name):
    value = _literal(document, node, name)
    return value.value[0] if value and value.kind == "cells" and len(value.value) == 1 else None


def _references(document, node, name):
    value = _literal(document, node, name)
    return list(value.value) if value and value.kind == "phandles" else []


def _projection(document, catalog, worker, draft_path):
    projection = BindingService._projection(document, catalog)
    sources = {node["path"]: node for node in worker["nodes"]}
    targets = {target["id"]: target for target in projection["targets"]}
    nodes, controllers, providers, states, allocations, unknown_states = [], [], [], [], [], []
    for row in projection["nodes"]:
        source_node = document.node(row["node_id"])
        path = row["path"]
        target = targets.get(source_node.id, {})
        source = sources.get(path, {})
        locations = [source["source"], *source.get("property_sources", {}).values()] if source.get("source") else []
        locations = [dict(item) for item in {tuple(item.items()) for item in locations}]
        inherited = any(item["path"] != draft_path for item in locations)
        local = any(item["path"] == draft_path for item in locations)
        status = _literal(document, source_node, "status")
        properties = {field["name"]: deepcopy(field["local"]["parsed"]) for field in row["properties"] if field.get("local")}
        compatible = _literal(document, source_node, "compatible")
        compatible = ([compatible.value] if compatible.kind == "string" else list(compatible.value)
                      if compatible.kind == "string-array" else []) if compatible else []
        item = {"id": _key(path), "key": _key(path), "effective_node_id": source_node.id, "path": path,
                "name": row["name"], "labels": row["labels"], "reference": target.get("reference"),
                "compatible": compatible, "status": status.value if status and status.kind == "string" else "okay",
                "origin": "both" if inherited and local else "overlay" if local else "board",
                "source_locations": sorted(locations, key=lambda value: (value["path"], value["line"])),
                "binding": deepcopy(row["binding"]), "properties": properties,
                "parent_id": _key(document._path(document.node(source_node.parent_id))) if source_node.parent_id else None,
                "children": [_key(document._path(child)) for child in document.syntax.nodes if child.parent_id == source_node.id],
                "pinctrl": [], "supported": row["binding"]["state"] == "matched", "reason": ""}
        for prop in source_node.properties:
            if not prop.name.startswith("pinctrl-") or not prop.name[8:].isdigit():
                continue
            index = int(prop.name[8:])
            references = _references(document, source_node, prop.name)
            selected = [BindingService._target(projection["targets"], reference, required=False) for reference in references]
            names = properties.get("pinctrl-names") or []
            state = {"owner_id": item["id"], "index": index, "name": names[index] if index < len(names) else str(index),
                     "targets": [_key(target["path"]) for target in selected if target]}
            item["pinctrl"].append(state)
            states.append(state)
        buses = row["binding"].get("bus", [])
        item["buses"] = list(buses)
        if buses:
            item["kind"] = buses[0] if len(buses) == 1 else "bus"
            controllers.append(item)
        if _properties(source_node, "gpio-controller"):
            cells = target.get("cells", {}).get("gpio", [])
            count = _number(document, source_node, "ngpios")
            origin = "explicit" if count is not None else "unknown"
            field = next((field for field in row["properties"] if field["name"] == "ngpios"), {})
            if count is None and not _properties(source_node, "ngpios") and type(field.get("default")) is int:
                count, origin = field["default"], "binding-default"
            ranges = properties.get("gpio-reserved-ranges")
            ranges = [ranges[index:index + 2] for index in range(0, len(ranges), 2)] if isinstance(ranges, list) and len(ranges) % 2 == 0 else None
            provider = {**deepcopy(item), "cells": cells, "pin_count": count, "pin_count_origin": origin,
                        "reserved_ranges": ranges, "supported": item["supported"] and cells == ["pin", "flags"],
                        "reason": "" if item["supported"] and cells == ["pin", "flags"] else "The GPIO provider needs a matched pin/flags binding."}
            providers.append(provider)
        nodes.append(item)
    by_id = {node["id"]: node for node in nodes}
    by_path = {row["path"]: row for row in projection["nodes"]}
    # The standard STM32 packed pinmux has a documented port/line/function layout.
    # Decode only this binding; other families retain their source-linked states.
    for state in states:
        decoded = 0
        for selected in state["targets"]:
            row = by_path[by_id[selected]["path"]]
            pin = document.node(row["node_id"])
            parent = document.node(pin.parent_id) if pin.parent_id else None
            parent_row = by_path.get(document._path(parent)) if parent else None
            if (not worker.get("decoders", {}).get("stm32-pinmux-v1") or not parent_row
                    or parent_row["binding"].get("compatible") != "st,stm32-pinctrl"):
                continue
            encoded = _number(document, pin, "pinmux")
            if encoded is None or encoded & ~0x3fff:
                continue
            port, number = (encoded >> 9) & 31, (encoded >> 5) & 15
            provider = next((provider for provider in providers if "gpio" + chr(ord("a") + port) in provider["labels"]
                             and provider["parent_id"] == by_id[selected]["parent_id"]), None)
            if provider:
                decoded += 1
                allocations.append({"provider_id": provider["id"], "pin": number, "kind": "pinctrl",
                                    "owner_id": state["owner_id"], "state": state["name"], "role": by_id[selected]["labels"][0] if by_id[selected]["labels"] else "pinmux"})
        if not state["targets"] or decoded != len(state["targets"]):
            unknown_states.append(deepcopy(state))
    maps = [{**mapping, "id": _key(mapping["path"]), "entries": [
        {**entry, "provider_id": _key(entry["provider_path"])} for entry in mapping["entries"]]} for mapping in worker["connector_maps"]]
    return projection, {"nodes": nodes, "controllers": controllers, "gpio_providers": providers,
                        "pinctrl_states": states, "connector_maps": maps, "allocations": allocations,
                        "pin_routing_complete": not unknown_states, "unknown_pinctrl_states": unknown_states}


class BoardTopologyService:
    """Retain guarded inherited identity; effective source is never an edit target."""

    def __init__(self, drafts):
        self.drafts = drafts
        self.observations = {}

    def inspect(self, body):
        binding = self.drafts._body(body, _GUARD_FIELDS)
        with self.drafts.editor.lock, self.drafts.lock:
            context = self.drafts._context(binding)
            draft = self.drafts._scope(body, context)
            self.drafts._guard(body, draft)
            board = deepcopy(context.get("board") or (context.get("environment") or {}).get("board") or {})
            board.setdefault("name", (context.get("target") or {}).get("board") or "Selected board")
            payload = {"schema_version": 1, "observation_id": uuid.uuid4().hex, "target": deepcopy(context.get("target")), "board": board,
                       "supported": False, "reason": "", "coverage": "board+selected-overlay", "nodes": [], "controllers": [],
                       "gpio_providers": [], "pinctrl_states": [], "connector_maps": [], "allocations": [], "diagnostics": [SCOPE],
                       "pin_routing_complete": False, "unknown_pinctrl_states": [],
                       "draft_id": draft.id, "revision": draft.revision, "sha256": draft.document.sha256 if draft.document else None,
                       "connection_options": {}, "fingerprint": None}
            observed = {"guard": deepcopy(body), "payload": payload, "expires": time.monotonic() + 1800,
                        "sources": None, "catalog": None, "document": None, "projection": None}
            try:
                if draft.document is None or draft.document.syntax.blocked:
                    raise ValueError("Repair incomplete overlay source before discovering board hardware.")
                sources = BoardSources(context, draft)
                worker = sources.run(draft)
                document = SourceDocument.parse(worker["source"].encode())
                if document.syntax.blocked:
                    raise ValueError("The resolved board exceeds the supported editable topology syntax; its source remains available.")
                catalog = BindingCatalog(context["workspace"], sources.binding_roots)
                projection, topology = _projection(document, catalog, worker, draft.path)
                payload.update(topology, supported=True, selection=deepcopy(sources.selection))
                payload["diagnostics"].append(f"Board source: {sources.selection['board_source']}; target architecture: {sources.selection['architecture']}.")
                payload["diagnostics"].extend(catalog.diagnostics)
                payload["fingerprint"] = hashlib.sha256(json.dumps({"files": {key: value["sha256"] for key, value in sources.files.items()},
                    "absent": sorted(sources.absent), "tools": sources.tools, "bindings": catalog.fingerprint,
                    "draft": draft.document.sha256, "target": context.get("target")}, sort_keys=True).encode()).hexdigest()
                observed.update(sources=sources, catalog=catalog, document=document, projection=projection)
                from .board_connections import connection_options
                payload["connection_options"] = connection_options(document, projection, catalog, payload, draft.document)
                sources.revalidate()
                catalog.revalidate()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                payload.update(supported=False, reason=str(exc))
                payload["diagnostics"].append(str(exc))
            self.drafts._scope(body, self.drafts._context(binding))
            self.drafts._guard(body, self.drafts.drafts[draft.id])
            self.observations = {key: value for key, value in self.observations.items() if value["expires"] > time.monotonic()}
            while len(self.observations) >= 16:
                self.observations.pop(next(iter(self.observations)))
            self.observations[payload["observation_id"]] = observed
            return deepcopy(payload)

    def validate(self, body, context, draft):
        if (not isinstance(body, dict) or set(body) != {*_BINDING_FIELDS, *_GUARD_FIELDS, "observation_id"}
                or not isinstance(body["observation_id"], str)):
            raise ValueError("Choose the exact board observation and current draft guards.")
        observation = self.observations.get(body["observation_id"])
        guard = {key: value for key, value in body.items() if key != "observation_id"}
        if observation is None or observation["guard"] != guard or observation["expires"] <= time.monotonic():
            raise ValueError("Board hardware belongs to an earlier draft/context or expired. Load board hardware again.")
        self.drafts._guard(guard, draft)
        if not observation["payload"]["supported"]:
            raise ValueError(observation["payload"]["reason"])
        observation["sources"].revalidate()
        observation["catalog"].revalidate()
        return observation

    def close(self):
        self.observations.clear()
