"""Explicit, source-bound removal previews with individually selected local repairs."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
import time
import uuid

from .drafts import _GUARD_FIELDS, _diff
from .source_syntax import SourceError


MAX_REVIEWS = 16
MAX_REPAIRS = 64
TTL = 600
REFERENCE_TYPES = {"phandle", "phandles", "phandle-array", "path"}
LOCAL_SCOPE = ("This review covers declarations in this overlay and known consumers from source or verified pin evidence. "
               "Included files, other overlays, inherited values and macro-generated consumers may remain. "
               "Removing a local override can expose an inherited value; Save and Configure remain separate actions.")


def _location(document, node, prop=None):
    start, end = (prop.start, prop.end) if prop else (node.start, node.end)
    before = document.source[:start]
    return {"node_id": node.id, "property_id": prop.id if prop else None,
            "path": document._path(node), "name": prop.name if prop else node.name, "start": start, "end": end,
            "line": before.count(b"\n") + 1,
            "column": len(before.rsplit(b"\n", 1)[-1].decode("utf-8")) + 1}


def _node(document, node):
    return {"node_id": node.id, "name": node.name, "path": document._path(node),
            "location": _location(document, node)}


def _inside(node, target):
    return target.start <= node.start < target.end


class RemovalService:
    """Reviews never write; accepted plans become one canonical draft SourceChange."""

    commands = frozenset({"remove_reviewed"})

    def __init__(self, drafts, resources):
        self.drafts, self.resources = drafts, resources
        self.reviews = {}

    def review(self, body):
        binding = self.drafts._body(body, _GUARD_FIELDS | {"schema_id", "node_id", "repairs"})
        selected = body["repairs"]
        if (not isinstance(selected, list) or len(selected) > MAX_REPAIRS
                or any(not isinstance(item, str) or len(item) > 256 for item in selected)
                or len(set(selected)) != len(selected)):
            raise ValueError("Choose at most 64 distinct repairs from this removal review.")
        with self.drafts.editor.lock, self.drafts.lock:
            context = self.drafts._context(binding)
            draft = self.drafts._scope(body, context)
            self.drafts._guard(body, draft)
            guard = {key: value for key, value in body.items() if key not in {"schema_id", "node_id", "repairs"}}
            observed = self.resources.validate_observation({**guard, "schema_id": body["schema_id"]}, context, draft)
            if draft.document is None or draft.document.syntax.blocked or self.drafts._visual_limit(draft.document):
                raise ValueError("Repair incomplete or unsupported source before reviewing a removal.")
            preview, change = self._preview(draft.document, observed, body["node_id"], set(selected))
            self.resources.validate_observation({**guard, "schema_id": body["schema_id"]}, context, draft)
            current = time.monotonic()
            self.reviews = {key: value for key, value in self.reviews.items() if value["expires"] > current}
            while len(self.reviews) >= MAX_REVIEWS:
                self.reviews.pop(next(iter(self.reviews)))
            review_id = uuid.uuid4().hex
            preview.update(schema_version=1, review_id=review_id, schema_id=body["schema_id"],
                           draft_id=draft.id, revision=draft.revision, sha256=draft.document.sha256,
                           expires_at=(datetime.now(timezone.utc) + timedelta(seconds=TTL)).isoformat())
            self.reviews[review_id] = {"guard": deepcopy(guard), "schema_id": body["schema_id"],
                                       "expires": current + TTL, "change": change}
            return preview

    @staticmethod
    def _preview(document, observed, node_id, selected):
        target = document.node(node_id)
        descendants = [node for node in document.syntax.nodes if node.id != target.id and _inside(node, target)]
        removed_ids = {target.id, *(node.id for node in descendants)}
        removed_paths = {document._path(node) for node in [target, *descendants]} - {None}
        nodes = {node.id: node for node in document.syntax.nodes}
        bindings = {node["node_id"]: node for node in observed["bindings"]["nodes"]}
        graph = observed["graph"]
        unique_ids = {node["node_id"] for node in graph["nodes"] if node.get("unique")}
        blockers, dependants, choices = [], [], []
        unknowns = [LOCAL_SCOPE]

        def block(message, node=target, prop=None):
            item = {"message": message, "location": _location(document, node, prop)}
            if item not in blockers:
                blockers.append(item)

        if target.name == "/" or target.name.startswith("&"):
            block("Root and reference-override blocks require explicit source editing; choose a local child declaration.")
        if not document.can_edit_node(target.id):
            block("The target has incomplete structure or a conflicting deletion directive. Resolve it in source first.")
        target_row = next((node for node in graph["nodes"] if node["node_id"] == target.id), None)
        if not target_row or not target_row.get("unique"):
            block("This target has unresolved or repeated source contributors. Resolve its declaration in source first.")
        if graph.get("truncated"):
            block("Dependency evidence was truncated. Narrow or simplify the source before applying a removal.")
        if graph.get("external_unknown"):
            unknowns.append("Includes or opaque expressions prevent complete consumer resolution. No missing consumer is assumed to be absent.")

        references = graph["references"]
        relevant = []
        for reference in references:
            path = reference["reference"][2:-1] if reference["reference"].startswith("&{") else None
            path_dependency = path and any(path == removed or path.startswith(removed + "/") for removed in removed_paths)
            if not removed_ids.intersection(reference.get("target_node_ids", [])) and not path_dependency:
                continue
            source_id = reference.get("source_node_id")
            if source_id in removed_ids:
                continue
            source_node = nodes.get(source_id)
            prop = document._property_index.get(reference.get("property_id"), (None, None))[1]
            location = (_location(document, source_node, prop) if source_node else
                        {"node_id": source_id, "property_id": reference.get("property_id"), "path": None,
                         "name": reference["reference"], "start": reference["start"], "end": reference["end"],
                         "line": document.source[:reference["start"]].count(b"\n") + 1, "column": 1})
            relevant.append(reference)
            dependants.append({"id": reference["id"], "reference": reference["reference"],
                               "source_node_id": source_id, "property_id": reference.get("property_id"),
                               "kind": reference["kind"], "location": location,
                               "message": f"{reference['reference']} is used by {location['path'] or (source_node.name if source_node else 'source')}"
                                          + (f" property {prop.name}." if prop else ".")})

        ancestors = set()
        parent = nodes.get(target.parent_id)
        while parent:
            ancestors.add(parent.id)
            parent = nodes.get(parent.parent_id)
        for reference in references:
            if reference.get("source_node_id") in removed_ids or not ancestors.intersection(reference.get("target_node_ids", [])):
                continue
            consumer, prop = document._property_index.get(reference.get("property_id"), (None, None))
            if not prop or not re.fullmatch(r"pinctrl-[0-9]+", prop.name):
                continue
            dependants.append({"id": "ancestor:" + reference["id"], "reference": reference["reference"],
                               "source_node_id": consumer.id, "property_id": prop.id,
                               "kind": "ancestor-pinctrl-reference", "location": _location(document, consumer, prop),
                               "message": f"{reference['reference']} selects a pin state containing {target.name}; removing this group changes that controller's state."})
            block("This nested pin group belongs to a state still used by a controller. Repair the pins or state references explicitly before removing the group.", consumer, prop)

        for consumer in graph.get("pin_state_consumers", []):
            if consumer["target_node_id"] not in removed_ids | ancestors:
                continue
            location = consumer.get("location")
            source_id = location.get("node_id") if location else None
            property_id = location.get("property_id") if location else None
            if source_id in removed_ids:
                continue
            # Literal properties are already listed above. Inherited references
            # have no overlay span, but remain established consumer evidence.
            if any(item["property_id"] == property_id and property_id is not None
                   and item["reference"] == consumer["reference"] for item in dependants):
                continue
            dependants.append({"id": consumer["id"], "reference": consumer["reference"],
                               "source_node_id": source_id, "property_id": property_id,
                               "kind": "known-pin-state-consumer", "location": location,
                               "message": f"{consumer['controller']} {consumer['state']} references {consumer['reference']} according to {consumer['evidence']}; removing this state or group changes that controller's pin state."})
            blockers.append({"message": "Use Pins or source to change the controller state reference first. Known inherited consumers cannot be repaired by deleting a local property.",
                             "location": location})

        controllers = {row["node_id"]: row for row in observed["bindings"].get("controllers", [])}
        for property_id in {item["property_id"] for item in relevant if item.get("property_id")}:
            consumer, prop = document._property(property_id)
            if prop.name != "cs-gpios":
                continue
            for child in controllers.get(consumer.id, {}).get("children", []):
                child_node = nodes[child["node_id"]]
                if child_node.id in removed_ids:
                    continue
                reg = next((item for item in child_node.properties if item.name == "reg"), None)
                address = child.get("address")
                dependants.append({"id": "chip-select:" + child_node.id, "reference": f"cs-gpios[{address if address is not None else 'unknown'}]",
                                   "source_node_id": child_node.id, "property_id": reg.id if reg else None,
                                   "kind": "spi-chip-select-consumer", "location": _location(document, child_node, reg),
                                   "message": f"{document._path(child_node) or child_node.name} uses a chip-select mapping from {prop.name}; removing that property changes routing."})

        properties = {reference["property_id"] for reference in relevant if reference.get("property_id")}
        repair_properties = {}
        for property_id in sorted(properties):
            consumer, prop = document._property(property_id)
            entries = [reference for reference in references if reference.get("property_id") == property_id]
            field = next((field for field in bindings.get(consumer.id, {}).get("properties", [])
                          if (field.get("local") or {}).get("id") == property_id), None)
            # An optional whole-property repair is intentionally narrower than
            # rewiring an individual specifier. Shared/mixed arrays stay intact.
            exclusive = bool(entries) and all(reference.get("resolution") == "resolved"
                and reference.get("target_node_ids") and set(reference["target_node_ids"]) <= removed_ids for reference in entries)
            named = document._path(consumer) in {"/aliases", "/chosen"}
            value = document.property_value(property_id)
            literal = value is not None and value.kind in {"reference", "phandles", "phandle-array", "string", "path"}
            optional = field and field["type"] in REFERENCE_TYPES and not field["required"] and field["editable"]
            routing = prop.name == "cs-gpios" or re.fullmatch(r"pinctrl-[0-9]+", prop.name)
            safe = bool(not routing and exclusive and literal and (named or optional) and consumer.id in unique_ids and document.can_edit_node(consumer.id)
                        and len([item for item in consumer.properties if item.name == prop.name]) == 1)
            if safe and len(choices) >= MAX_REPAIRS:
                block("This removal exceeds the 64-repair review limit. Repair some consumers explicitly before reviewing again.", consumer, prop)
                safe = False
            if safe:
                try:
                    document.plan_remove_property(property_id)
                except SourceError:
                    safe = False
            if safe:
                repair_id = "remove-property:" + property_id
                repair_properties[repair_id] = property_id
                choices.append({"id": repair_id, "property_id": property_id,
                                "label": f"Remove local {prop.name} from {document._path(consumer) or consumer.name}",
                                "location": _location(document, consumer, prop), "selected": repair_id in selected,
                                "consequence": "Removes the complete local property; an inherited or earlier value may become visible. No consumer or provider node is removed."})
                for item in dependants:
                    if item["property_id"] == property_id:
                        item["repair_id"] = repair_id
            else:
                reason = ("Indexed chip-select or pin-state routing requires explicit source or pin-form repair; removing the property could change index or companion-state semantics."
                          if routing else
                          "A required reference must be repaired explicitly in source or its supported form before removal."
                          if field and field["required"] else
                          "This dependency is mixed, opaque, ambiguous or lacks an established optional binding. Repair it explicitly in source before removal.")
                block(reason, consumer, prop)

        if selected - repair_properties.keys():
            raise ValueError("A selected repair is unavailable or belongs to another removal preview.")
        for item in dependants:
            if item["kind"] == "known-pin-state-consumer":
                continue
            if not item.get("property_id"):
                block("A structural or declaration reference depends on this target. Repair it explicitly in source before removal.", nodes.get(item["source_node_id"], target))
            elif item.get("repair_id") and item["repair_id"] not in selected:
                blockers.append({"message": "Select the offered property repair or keep the target and edit its consumer first.",
                                 "location": item["location"]})

        # Untyped strings can name a source path even when no schema established
        # that the property is a reference. Do not quietly treat them as harmless.
        known_props = {reference.get("property_id") for reference in references}
        for consumer in document.syntax.nodes:
            if consumer.id in removed_ids:
                continue
            for prop in consumer.properties:
                value = document.property_value(prop.id)
                if (prop.id not in known_props and value and value.kind in {"string", "path"}
                        and any(value.value == path or value.value.startswith(path + "/") for path in removed_paths)):
                    block("An untyped path-like value may depend on this target. Establish or repair that dependency explicitly in source.", consumer, prop)
                    unknowns.append("At least one path-like consumer has no established reference binding.")

        change = None
        if not blockers:
            try:
                edits = [edit for repair_id in sorted(selected)
                         for edit in document.plan_remove_property(repair_properties[repair_id]).edits]
                intermediate = document._plan("review-removal-repairs", edits).apply(document)
                adjusted_start = target.start + sum(len(edit.after) - len(edit.before) for edit in edits if edit.end <= target.start)
                intermediate_target = next(node for node in intermediate.syntax.nodes if node.start == adjusted_start)
                checked = intermediate.plan_remove_node(intermediate_target.id).apply(intermediate)
                change = document._plan("reviewed-remove-local-node", [*edits, document._edit(target.start, target.end, b"")],
                                        ("Only the reviewed node subtree and explicitly selected local properties are removed.", LOCAL_SCOPE))
                candidate = change.apply(document)
                if candidate.source != checked.source:
                    raise SourceError("The reviewed source ranges changed. Review the removal again.")
            except (SourceError, StopIteration) as exc:
                block(str(exc) or "The target could not be preserved through the selected source repairs.")
                change = None
        candidate = change.apply(document).source if change else None
        return {"target": _node(document, target), "descendants": [_node(document, node) for node in descendants],
                "dependants": dependants, "repair_choices": choices, "blockers": blockers,
                "unknowns": list(dict.fromkeys(unknowns)), "can_apply": change is not None,
                "diff": _diff(document.source, candidate) if candidate is not None else {"text": "", "truncated": False},
                "candidate_text": candidate.decode("utf-8") if candidate is not None else None}, change

    def plan(self, body, context, draft):
        command = body["command"]
        if set(command) != {"kind", "review_id"} or command["kind"] != "remove_reviewed" or not isinstance(command["review_id"], str):
            raise ValueError("Choose the exact reviewed removal command fields.")
        review = self.reviews.get(command["review_id"])
        guard = {key: value for key, value in body.items() if key != "command"}
        if review is None or review["expires"] <= time.monotonic() or review["guard"] != guard:
            raise ValueError("This removal review expired or belongs to an earlier draft/context. Review it again.")
        self.resources.validate_observation({**guard, "schema_id": review["schema_id"]}, context, draft)
        if review["change"] is None:
            raise ValueError("This removal has unresolved dependants. Select supported repairs or edit source before reviewing again.")
        review["change"].apply(draft.document)
        self.resources.validate_observation({**guard, "schema_id": review["schema_id"]}, context, draft)
        return review["change"]

    def close(self):
        self.reviews.clear()
