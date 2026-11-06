"""Source-derived module graphs and single-leaf recipes through canonical commands."""

from copy import deepcopy
import time

from .binding_service import BindingService
from .peripherals import GROUPS, PeripheralService
from .recipes import CATALOGUE_VERSION, recipes


COMMANDS = frozenset({"module_add", "module_update", "module_remove"})
MAX_GRAPH_NODES = 256
SCOPE = ("The canvas describes unique local source declarations, not physical wiring or an effective inherited tree. "
         "Recipe matches are informational; source nodes are never converted or upgraded automatically. "
         "Software requirements are guidance only; Kconfig and controller status are not changed.")


class ModuleService(PeripheralService):
    """Versioned recipe observations reuse D7 schemas, source plans and safety guards."""

    commands = COMMANDS

    @staticmethod
    def _projection(document, catalog):
        payload = PeripheralService._projection(document, catalog)
        targets = {item["id"]: item for item in payload["targets"]}
        rows = {row["node_id"]: row for row in payload["nodes"]}

        def key(node_id):
            target = targets.get(node_id)
            return "node:" + target["path"] if target and target["path"] else None

        parents = [{**deepcopy(parent), "key": key(parent["node_id"]),
                    "role": "group" if parent["kind"] in GROUPS else "controller"}
                   for parent in [*payload["controllers"], *payload["gpio_groups"]]]
        catalogue = recipes()
        for recipe in catalogue:
            options = []
            for parent in parents:
                if parent["kind"] != recipe["kind"]:
                    continue
                candidate = (next((candidate for candidate in parent["candidates"]
                                   if candidate["compatible"] == recipe["compatible"]), None)
                             if parent["kind"] not in GROUPS else None)
                fields = candidate["fields"] if candidate else parent.get("add_fields", [])
                addable = parent["addable"] and bool(parent["key"])
                reason = parent["add_reason"]
                if parent["kind"] not in GROUPS and (not candidate or not candidate["addable"]):
                    addable = False
                    reason = candidate["reason"] if candidate else "The selected environment has no unique supported binding for this recipe and bus."
                options.append({"parent_id": parent["node_id"], "parent_key": parent["key"],
                                "name": parent["name"], "path": parent["path"], "kind": parent["kind"],
                                "addable": addable, "reason": reason, "fields": deepcopy(fields),
                                "address_field": deepcopy(candidate["address_field"]) if candidate else None,
                                "chip_selects": parent.get("chip_selects")})
            recipe.update(parents=options, available=any(option["addable"] for option in options),
                          reason="" if any(option["addable"] for option in options) else
                          "No suitable unique local parent and binding are available. Use Peripherals to prepare a group or source to declare a supported bus.")

        instances = []
        for parent in parents:
            for child in parent["children"]:
                matches = [{"id": recipe["id"], "version": recipe["version"]} for recipe in catalogue
                           if parent["kind"] == recipe["kind"] and rows[child["node_id"]]["binding"]["state"] == "matched"
                           and (parent["kind"] in GROUPS or child["compatible"] == recipe["compatible"])]
                instances.append({**deepcopy(child), "key": key(child["node_id"]), "parent_id": parent["node_id"],
                                  "parent_key": parent["key"], "kind": parent["kind"], "recipe_matches": matches, "origin": "source"})

        graph_nodes = {}

        def add_node(row, role, kind, *, parent_key=None, matches=()):
            node_key = key(row["node_id"])
            if node_key and node_key not in graph_nodes:
                graph_nodes[node_key] = {"key": node_key, "node_id": row["node_id"], "name": row["name"],
                                         "path": row["path"], "role": role, "kind": kind, "parent_key": parent_key,
                                         "recipe_matches": list(matches), "editable": row.get("editable", False),
                                         "removable": row.get("removable", False)}

        for parent in parents:
            add_node(parent, parent["role"], parent["kind"])
        for instance in instances:
            add_node(instance, "module", instance["kind"], parent_key=instance["parent_key"], matches=instance["recipe_matches"])
        for provider in payload["gpio_providers"]:
            add_node(rows[provider["id"]], "provider", "gpio")
        for row in rows.values():
            source_node = document.node(row["node_id"])
            if any(prop.name == "compatible" for prop in source_node.properties):
                add_node(row, "module", "source")

        truncated = len(graph_nodes) > MAX_GRAPH_NODES
        if truncated:
            graph_nodes = dict(list(graph_nodes.items())[:MAX_GRAPH_NODES])
            payload["diagnostics"].append("The canvas shows the first 256 unique local declarations. Remaining nodes remain available in source and peripheral forms.")
        edges = []
        for instance in instances:
            if instance["key"] in graph_nodes and instance["parent_key"] in graph_nodes:
                edges.append({"key": "parent:" + instance["key"], "source": instance["parent_key"],
                              "target": instance["key"], "kind": "bus", "property": None, "index": None})
        for node in graph_nodes.values():
            for prop in rows[node["node_id"]]["properties"]:
                if prop.get("specifier_space") != "gpio" or not prop["editable"] or not prop.get("local"):
                    continue
                for index, reference in enumerate(prop["local"]["parsed"] or []):
                    provider = BindingService._target(payload["gpio_providers"], reference["reference"], required=False)
                    provider_key = key(provider["id"]) if provider else None
                    if provider_key in graph_nodes:
                        edges.append({"key": f"gpio:{node['key']}:{prop['name']}:{index}", "source": provider_key,
                                      "target": node["key"], "kind": "gpio", "property": prop["name"], "index": index})
        payload.update(catalogue_version=CATALOGUE_VERSION, recipes=catalogue, parents=parents, instances=instances,
                       graph={"nodes": list(graph_nodes.values()), "edges": edges, "truncated": truncated})
        payload["diagnostics"].insert(0, SCOPE)
        return payload

    def validate_observation(self, body, context, draft):
        """Validate a guarded schema for module/layout consumers holding the draft locks."""
        if not isinstance(body.get("schema_id"), str):
            raise ValueError("Load the module catalogue before changing this view.")
        observed = self.observations.get(body["schema_id"])
        guard = {key: value for key, value in body.items() if key != "schema_id"}
        if observed is None or observed["guard"] != guard or observed["expires"] <= time.monotonic():
            raise ValueError("Module catalogue belongs to an earlier draft/context or expired. Load modules again.")
        self.drafts._scope(guard, context)
        self.drafts._guard(guard, draft)
        observed["catalog"].revalidate()
        return deepcopy(observed["payload"])

    def plan(self, body, context, draft):
        command = body["command"]
        kind = command["kind"]
        fields = {"module_add": {"recipe_id", "recipe_version", "parent_id", "name", "label", "address", "values"},
                  "module_update": {"node_id", "address", "values"}, "module_remove": {"node_id"}}[kind] | {"kind", "schema_id"}
        if set(command) != fields or not isinstance(command["schema_id"], str):
            raise ValueError("Choose the exact supported module command fields.")
        guard = {key: value for key, value in body.items() if key != "command"}
        payload = self.validate_observation({**guard, "schema_id": command["schema_id"]}, context, draft)
        translated = {"schema_id": command["schema_id"]}
        if kind == "module_add":
            recipe = next((recipe for recipe in payload["recipes"] if recipe["id"] == command["recipe_id"]), None)
            if not recipe or type(command["recipe_version"]) is not int or command["recipe_version"] != recipe["version"]:
                raise ValueError("Choose an available built-in recipe and its exact observed version.")
            parent = next((parent for parent in recipe["parents"] if parent["parent_id"] == command["parent_id"]), None)
            if not parent or not parent["addable"]:
                raise ValueError(parent["reason"] if parent else "Choose a supported local parent for this recipe.")
            self._name(command["name"])
            gpio = recipe["kind"] in GROUPS
            if gpio and command["address"] is not None:
                raise ValueError("GPIO modules do not use a bus address.")
            if gpio and any(node.parent_id == parent["parent_id"] and node.name == command["name"] for node in draft.document.syntax.nodes):
                raise ValueError("A source node already has this name. Choose a distinct name for another module.")
            translated.update(kind="gpio_add" if gpio else "peripheral_add",
                              **{field: command[field] for field in ("parent_id", "name", "label", "values")})
            if not gpio:
                translated.update(compatible=recipe["compatible"], address=command["address"])
        else:
            instance = next((item for item in payload["instances"] if item["node_id"] == command["node_id"]), None)
            if not instance:
                raise ValueError("Choose a unique leaf device from this local module graph.")
            translated["node_id"] = command["node_id"]
            if kind == "module_remove":
                translated["kind"] = "peripheral_remove"
            else:
                gpio = instance["kind"] in GROUPS
                if gpio and command["address"] is not None:
                    raise ValueError("GPIO modules do not use a bus address.")
                translated.update(kind="gpio_update" if gpio else "peripheral_update", values=command["values"])
                if not gpio:
                    translated["address"] = command["address"]
        return super().plan({**body, "command": translated}, context, draft)
