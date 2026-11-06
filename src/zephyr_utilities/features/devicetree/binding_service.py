"""Binding-aware commands for explicitly observed local overlay declarations."""

from copy import deepcopy
from pathlib import Path
import time
import uuid

from .bindings import BindingCatalog
from .drafts import _GUARD_FIELDS
from .source import PropertyValue, SourceError


COMMANDS = frozenset({"binding_set", "binding_reset", "binding_delete", "add_label",
                      "set_alias", "set_chosen", "remove_alias", "remove_chosen"})
SUPPORTED_TYPES = frozenset({"boolean", "int", "string", "string-array", "array", "uint8-array",
                             "phandle", "phandles", "phandle-array", "path"})
LOCAL_SCOPE = ("Bindings describe declarations in this overlay only. Inherited board nodes, other overlays, "
               "preprocessor expressions and effective values are not resolved. Use source and explicit Configure for those cases.")


def _plain(value):
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _same(left, right):
    return type(left) is type(right) and (all(_same(a, b) for a, b in zip(left, right)) and len(left) == len(right)
                                        if isinstance(left, list) else left == right)


def _specifier(name, settings):
    explicit = settings.get("specifier_space")
    if isinstance(explicit, str):
        return explicit
    for group in ("gpio", "io-channel"):
        if name in {group, group + "s"} or name.endswith(("-" + group, "-" + group + "s")):
            return group
    return name[:-1] if name.endswith("s") else None


def _properties(node, name):
    return [prop for prop in node.properties if prop.name == name]


def _literal(document, node, name):
    properties = _properties(node, name)
    if len(properties) != 1 or properties[0].kind != "property":
        return None
    return document.property_value(properties[0].id)


def _references(value):
    if value is None:
        return None
    if value.kind in {"reference", "path"}:
        return [value.value]
    if value.kind == "phandles":
        return list(value.value)
    if value.kind == "phandle-array":
        return [item[0] if not isinstance(item, dict) else item["reference"] for item in value.value]
    return None


def _parsed(value, kind):
    if value is None:
        return None
    if kind == "boolean" and value.kind == "boolean":
        return True
    if kind == "int" and value.kind == "cells" and len(value.value) == 1:
        return value.value[0]
    if kind == "string" and value.kind == "string":
        return value.value
    if kind == "string-array" and value.kind in {"string", "string-array"}:
        return [value.value] if value.kind == "string" else list(value.value)
    if kind == "array" and value.kind == "cells":
        return list(value.value)
    if kind == "uint8-array" and value.kind == "bytes":
        return list(value.value)
    if kind == "phandle" and value.kind == "phandles" or kind == "path" and value.kind in {"reference", "path"}:
        references = _references(value)
        if references and len(references) == 1:
            return references[0]
    if kind == "path" and value.kind == "string" and value.value.startswith("/"):
        return value.value
    if kind == "phandles" and value.kind == "phandles":
        return list(value.value)
    if kind == "phandle-array" and value.kind == "phandle-array":
        return [{"reference": item["reference"], "cells": list(item["cells"])} if isinstance(item, dict)
                else {"reference": item[0], "cells": list(item[1])} for item in value.value]
    if kind == "phandle-array" and value.kind == "phandles":
        return [{"reference": reference, "cells": []} for reference in value.value]
    return None


class BindingService:
    """Explicit bounded schema observations; mutations still use DraftService.command."""

    commands = COMMANDS

    def __init__(self, drafts):
        self.drafts = drafts
        self.observations = {}

    @staticmethod
    def _roots(context):
        application = Path(context["application"]["path"])
        roots = [application / "dts/bindings"]
        environment = context.get("environment") or {}
        zephyr = environment.get("zephyr") or {}
        if zephyr.get("path"):
            roots.append(Path(zephyr["path"]) / "dts/bindings")
        roots.extend(Path(item["path"]) / "dts/bindings" for item in context.get("roots", []) if item["kind"] == "dts")
        return list(dict.fromkeys(map(str, roots)))

    def inspect(self, body):
        binding = self.drafts._body(body, _GUARD_FIELDS)
        with self.drafts.editor.lock, self.drafts.lock:
            context = self.drafts._context(binding)
            draft = self.drafts._scope(body, context)
            self.drafts._guard(body, draft)
            document = draft.document
            if document is None or document.syntax.blocked or self.drafts._visual_limit(document):
                raise ValueError("Repair incomplete or structurally unsupported source before loading binding properties.")
            current = time.monotonic()
            self.observations = {key: value for key, value in self.observations.items() if value["expires"] > current}
            roots = self._roots(context)
            catalog = None
            for observed in reversed(list(self.observations.values())):
                candidate = observed["catalog"]
                if candidate.workspace == context["workspace"] and candidate.roots == roots:
                    try:
                        candidate.revalidate()
                        catalog = candidate
                    except ValueError:
                        pass
                    break
            if catalog is None:
                catalog = BindingCatalog(context["workspace"], roots)
            payload = self._projection(document, catalog)
            self.drafts._scope(body, self.drafts._context(binding))
            self.drafts._guard(body, self.drafts.drafts[draft.id])
            current = time.monotonic()
            self.observations = {key: value for key, value in self.observations.items() if value["expires"] > current}
            while len(self.observations) >= 16:
                self.observations.pop(next(iter(self.observations)))
            schema_id = uuid.uuid4().hex
            payload.update(schema_version=1, schema_id=schema_id, fingerprint=catalog.fingerprint,
                           draft_id=draft.id, revision=draft.revision, sha256=document.sha256)
            self.observations[schema_id] = {"guard": deepcopy(body), "catalog": catalog,
                                           "payload": deepcopy(payload), "expires": current + 600}
            return payload

    @staticmethod
    def _projection(document, catalog):
        nodes = {node.id: node for node in document.syntax.nodes}
        paths = {node.id: document._path(node) for node in nodes.values()}
        label_nodes = {}
        path_nodes = {}
        label_counts = {}
        for token in document._label_tokens():
            label = document.source[token.start:token.end].decode("ascii")
            label_counts[label] = label_counts.get(label, 0) + 1
        for node in nodes.values():
            if node.name.startswith("&"):
                continue
            for label in node.labels:
                label_nodes.setdefault(label, []).append(node.id)
            if paths[node.id]:
                path_nodes.setdefault(paths[node.id], []).append(node.id)

        def ambiguous(node):
            parent = nodes.get(node.parent_id)
            if parent and (parent.name.startswith("&") or ambiguous(parent)):
                return True
            selectors = {"&" + label for label in node.labels}
            if paths[node.id]:
                selectors.add("&{" + paths[node.id] + "}")
            return (any(label_counts.get(label, 0) != 1 for label in node.labels) or
                    paths[node.id] is not None and len(path_nodes.get(paths[node.id], [])) > 1 or
                    any(other.id != node.id and other.name in selectors for other in nodes.values()))

        matched = {}

        def resolve(node):
            if node.id in matched:
                return matched[node.id]
            parent = nodes.get(node.parent_id)
            parent_binding = resolve(parent) if parent else None
            compatible = _literal(document, node, "compatible")
            compatible_properties = _properties(node, "compatible")
            invalid = bool(compatible_properties and (compatible is None or compatible.kind not in {"string", "string-array"}))
            compatibles = ([compatible.value] if compatible.kind == "string" else list(compatible.value)) if compatible and not invalid else []
            if invalid or ambiguous(node) or node.name.startswith("&") and not compatibles:
                result = {"state": "unsupported", "schema": None, "candidates": [], "diagnostics": [
                    "Compatible expressions or multiple source contributors prevent a unique local binding. Use source editing."]}
            else:
                bus_known = not node.name.startswith("&") and (parent is None or parent.name == "/" or
                    parent_binding and parent_binding["state"] == "matched")
                bus = (parent_binding.get("schema") or {}).get("bus") if parent_binding else None
                result = catalog.match(compatibles, bus=bus or None, bus_known=bool(bus_known), parent_binding=parent_binding)
            matched[node.id] = result
            return result

        for node in nodes.values():
            resolve(node)
        targets = []
        for node in nodes.values():
            if node.name.startswith("&") or ambiguous(node) or not document.can_edit_node(node.id):
                continue
            reference = "&" + node.labels[0] if node.labels else "&{" + paths[node.id] + "}" if paths[node.id] else None
            if not reference:
                continue
            cells = {}
            schema = matched[node.id].get("schema") or {}
            if matched[node.id]["state"] == "matched":
                for space, names in schema.get("cells", {}).items():
                    count = _literal(document, node, "#" + space + "-cells")
                    if count and count.kind == "cells" and len(count.value) == 1 and count.value[0] == len(names):
                        cells[space] = list(names)
            targets.append({"id": node.id, "reference": reference, "label": node.labels[0] if node.labels else paths[node.id],
                            "path": paths[node.id], "labels": list(node.labels), "cells": cells})
        rows = []
        for node in nodes.values():
            match = matched[node.id]
            schema = match.get("schema") or {}
            properties = []
            for name, settings in schema.get("properties", {}).items():
                local_props = _properties(node, name)
                deleted = any(prop.kind == "delete-property" for prop in local_props)
                prop = local_props[0] if len(local_props) == 1 and not deleted else None
                value = document.property_value(prop.id) if prop else None
                kind = settings.get("type")
                parsed = _parsed(value, kind)
                reason = settings.get("reason", "")
                supported = kind in SUPPORTED_TYPES and settings.get("supported", True)
                if kind in {"int", "array"}:
                    constraints = list(settings.get("enum", []))
                    constant = settings.get("const")
                    if constant is not None:
                        constraints.extend(constant if isinstance(constant, list) else [constant])
                    if any(type(item) is not int or not 0 <= item <= 0xffffffff for item in constraints):
                        supported = False
                        reason = "This binding requires integer values outside the visual editor's unsigned 32-bit range. Edit its source directly."
                editable = bool(supported and document.can_edit_node(node.id) and not deleted and len(local_props) <= 1
                                and (prop is None or parsed is not None))
                if not supported:
                    reason = reason or "This binding field uses unsupported metadata or a value type. Edit its source directly."
                elif len(local_props) > 1:
                    reason = "Repeated local property declarations require an explicit source edit."
                elif deleted:
                    reason = "Reset the local deletion directive before assigning this property."
                elif prop is not None and parsed is None:
                    reason = "This local value is an expression or unsupported representation. Its source is preserved."
                row = {"name": name, "type": kind, "description": settings.get("description", ""),
                       "required": settings.get("required", False), "editable": editable, "reason": reason,
                       "deleted": deleted, "local": None, "resettable": len(local_props) == 1 and document.can_edit_node(node.id),
                       "deletable": len(local_props) <= 1 and not deleted and document.can_edit_node(node.id)}
                for key in ("default", "const", "enum"):
                    if key in settings:
                        row[key] = _plain(settings[key])
                if prop:
                    row["local"] = {"id": prop.id, "value": {"kind": value.kind, "value": _plain(value.value)} if value else None,
                                    "parsed": parsed, "raw": document.source[prop.start:prop.end].decode("utf-8")}
                if kind == "phandle-array":
                    space = _specifier(name, settings)
                    row["specifier_space"] = space
                    row["providers"] = [{**deepcopy(target), "cells": target["cells"][space]}
                                        for target in targets if space in target["cells"]]
                    if not row["providers"]:
                        row.update(editable=False, reason="No unique local provider has matching binding cell names and an explicit #specifier-cells count.")
                    elif parsed is not None:
                        for entry in parsed:
                            provider = BindingService._target(row["providers"], entry["reference"], required=False)
                            if provider is None or len(entry["cells"]) != len(provider["cells"]):
                                row.update(editable=False, reason="The current reference or cell count cannot be resolved safely. Its source is preserved.")
                                break
                            entry["reference"] = provider["reference"]
                elif kind in {"path", "phandle", "phandles"} and parsed is not None:
                    references = parsed if kind == "phandles" else [parsed]
                    if any(BindingService._target(targets, reference, required=False) is None for reference in references):
                        row.update(editable=False, reason="The current reference is inherited, missing or ambiguous. Its source is preserved.")
                    else:
                        canonical = [BindingService._target(targets, reference)["reference"] for reference in references]
                        row["local"]["parsed"] = canonical if kind == "phandles" else canonical[0]
                properties.append(row)
            rows.append({"node_id": node.id, "name": node.name, "path": paths[node.id], "labels": list(node.labels),
                         "add_label": node.name != "/" and not node.name.startswith("&") and not ambiguous(node) and document.can_edit_node(node.id),
                         "binding": {"state": match["state"], "path": schema.get("path"), "description": schema.get("description", ""),
                                     "bus": schema.get("bus", []), "on_bus": schema.get("on_bus"), "compatible": schema.get("compatible"),
                                     "candidates": match.get("candidates", []), "diagnostics": match.get("diagnostics", [])},
                         "properties": properties})
        named = {"aliases": [], "chosen": []}
        for node in nodes.values():
            for kind in named:
                if paths[node.id] != "/" + kind:
                    continue
                for prop in node.properties:
                    value = document.property_value(prop.id) if prop.kind == "property" else None
                    refs = _references(value)
                    if value and value.kind == "string" and value.value.startswith("/"):
                        refs = [value.value]
                    reference = refs[0] if refs and len(refs) == 1 else None
                    resolved = BindingService._target(targets, reference, required=False) if reference else None
                    named[kind].append({"name": prop.name, "property_id": prop.id, "reference": reference,
                                       "state": "local" if resolved else "unresolved", "raw": document.source[prop.start:prop.end].decode("utf-8")})
        unresolved = [f"Reference {item['reference']} is {item['state']} within this overlay; inherited targets remain unknown."
                      for item in document.reference_inventory()["references"] if item["state"] != "resolved"]
        return {"nodes": rows, "targets": targets, **named, "diagnostics": [LOCAL_SCOPE, *catalog.diagnostics, *dict.fromkeys(unresolved)],
                "capabilities": {"add_label": True, "set_alias": bool(targets), "set_chosen": bool(targets)}}

    @staticmethod
    def _target(targets, reference, *, required=True):
        if not isinstance(reference, str) or not 1 <= len(reference) <= 4096:
            if required:
                raise ValueError("Choose a bounded literal node reference.")
            return None
        matches = [target for target in targets if reference == target["reference"] or
                   reference in ["&" + label for label in target.get("labels", [])] or
                   target.get("path") and reference in {target["path"], "&{" + target["path"] + "}"}]
        if len(matches) == 1:
            return matches[0]
        if required:
            raise ValueError("Choose a unique node declared in this overlay. Inherited, missing or ambiguous references require source editing.")
        return None

    def _typed_value(self, prop, raw, payload):
        kind = prop["type"]
        if kind == "boolean":
            if type(raw) is not bool:
                raise ValueError("Choose true or false for this boolean property.")
            value = PropertyValue.boolean() if raw else None
        elif kind == "int":
            value = PropertyValue.cells([raw])
        elif kind == "string":
            value = PropertyValue.string(raw)
        elif kind == "string-array":
            value = PropertyValue.strings(raw)
        elif kind == "array":
            value = PropertyValue.cells(raw)
        elif kind == "uint8-array":
            value = PropertyValue.bytes(raw)
        elif kind in {"path", "phandle", "phandles"}:
            references = raw if kind == "phandles" else [raw]
            if not isinstance(references, list) or not references:
                raise ValueError("Choose at least one local node reference.")
            selected = [self._target(payload["targets"], reference) for reference in references]
            value = PropertyValue.reference(selected[0]["reference"]) if kind == "path" else PropertyValue.phandles([target["reference"] for target in selected])
        elif kind == "phandle-array":
            if not isinstance(raw, list) or not 1 <= len(raw) <= 64:
                raise ValueError("Choose one to 64 provider specifiers.")
            entries = []
            for item in raw:
                if not isinstance(item, dict) or set(item) != {"reference", "cells"} or not isinstance(item["cells"], list):
                    raise ValueError("Each provider specifier needs a reference and literal cells.")
                target = self._target(prop["providers"], item["reference"])
                if len(item["cells"]) != len(target["cells"]):
                    raise ValueError("The cell count must match the selected provider's binding and #specifier-cells declaration.")
                entries.append((target["reference"], item["cells"]))
            value = PropertyValue.phandle_array(entries)
        else:
            raise ValueError("This schema type is not supported by visual editing.")
        if "const" in prop and not _same(raw, prop["const"]):
            raise ValueError("This property must match its binding constant.")
        enum_values = raw if kind in {"array", "string-array"} else [raw]
        if "enum" in prop and any(not any(_same(item, entry) for entry in prop["enum"]) for item in enum_values):
            raise ValueError("Choose one of the values permitted by this binding.")
        return value

    def plan(self, body, context, draft):
        command = body["command"]
        kind = command["kind"]
        fields = {"binding_set": {"node_id", "name", "value"}, "binding_reset": {"node_id", "name"},
                  "binding_delete": {"node_id", "name"}, "add_label": {"node_id", "label"},
                  "set_alias": {"name", "reference"}, "set_chosen": {"name", "reference"},
                  "remove_alias": {"name"}, "remove_chosen": {"name"}}[kind] | {"kind", "schema_id"}
        if set(command) != fields or not isinstance(command["schema_id"], str):
            raise ValueError("Choose the exact supported binding command fields.")
        observed = self.observations.get(command["schema_id"])
        guard = {key: value for key, value in body.items() if key != "command"}
        if observed is None or observed["guard"] != guard or observed["expires"] <= time.monotonic():
            raise ValueError("Binding properties belong to an earlier draft/context or expired. Load binding properties again.")
        observed["catalog"].revalidate()
        document = draft.document
        payload = observed["payload"]
        if kind.startswith("binding_"):
            node = next((node for node in payload["nodes"] if node["node_id"] == command["node_id"]), None)
            prop = next((prop for prop in node["properties"] if prop["name"] == command["name"]), None) if node else None
            if prop is None:
                raise ValueError("Choose a property from this node's resolved local binding.")
            if kind == "binding_set":
                if not prop["editable"]:
                    raise ValueError(prop["reason"] or "This property cannot be changed through its binding form.")
                value = self._typed_value(prop, command["value"], payload)
                if value is None:
                    change = self._delete(document, command["node_id"], command["name"])
                else:
                    change = document.plan_set_property(command["node_id"], command["name"], value,
                        property_id=prop["local"]["id"] if prop["local"] else None)
            elif kind == "binding_reset":
                if not prop["resettable"]:
                    raise ValueError("There is no unique local declaration to reset. Inherited values remain unknown.")
                change = document.plan_reset_property(command["node_id"], command["name"])
            else:
                if not prop["deletable"]:
                    raise ValueError("This property cannot be uniquely deleted. Resolve its source first.")
                change = self._delete(document, command["node_id"], command["name"])
        elif kind == "add_label":
            change = document.plan_add_label(command["node_id"], command["label"])
        elif kind in {"set_alias", "set_chosen"}:
            target = self._target(payload["targets"], command["reference"])
            change = document.plan_set_named_reference("aliases" if kind == "set_alias" else "chosen", command["name"], target["reference"])
        else:
            change = document.plan_remove_named_reference("aliases" if kind == "remove_alias" else "chosen", command["name"])
        observed["catalog"].revalidate()
        return change

    @staticmethod
    def _delete(document, node_id, name):
        node = document.node(node_id)
        props = _properties(node, name)
        if len(props) > 1:
            raise SourceError("Repeated declarations require explicit source editing before deletion.")
        if props:
            # Replacing the exact declaration preserves all unrelated bytes and
            # avoids a temporary assign/delete conflict in the syntax index.
            prop = props[0]
            if prop.kind == "delete-property":
                return document._plan("delete-property", [])
            if prop.labels:
                raise SourceError("A labeled property needs an explicit source edit before deletion.")
            document.plan_reset_property(node_id, name)
            return document._plan("delete-property", [document._edit(prop.start, prop.end,
                                  ("/delete-property/ " + name + ";").encode("ascii"))])
        return document.plan_delete_property(node_id, name)

    def close(self):
        self.observations.clear()
