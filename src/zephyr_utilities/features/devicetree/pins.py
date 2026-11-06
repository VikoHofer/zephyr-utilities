"""Observed Nordic pin assignments use the canonical, guarded overlay draft."""

from copy import deepcopy
from collections import defaultdict
from pathlib import Path
import re
import time
import uuid

from .binding_service import _literal
from .drafts import _GUARD_FIELDS
from .nordic_evidence import NordicEvidence
from .nordic_source import NordicSource


COMMANDS = frozenset({"pin_assign", "pin_unassign", "pin_gpio", "pin_gpio_unassign"})
SCOPE = ("Pins describe the supported board source and this overlay. Package pads, electrical safety, "
         "external wiring and runtime ownership remain unknown. Save and Configure are separate actions.")
HEADERS = {"zephyr/dt-bindings/pinctrl/nrf-pinctrl.h", "zephyr/dt-bindings/gpio/gpio.h"}


def _overlay_scope(document, evidence):
    """Do not turn an opaque composition into a claimed board allocation map."""
    for token in document.syntax.tokens:
        if token.kind == "directive":
            value = document.source[token.start:token.end].decode("utf-8").strip()
            include = re.fullmatch(r"#\s*include\s*<([^>]+)>\s*", value)
            if not include or include[1] not in HEADERS:
                raise ValueError("Nordic pins need one overlay with only the supported GPIO/pinctrl header includes; other preprocessing remains source-only.")
    if document.syntax.deletions:
        raise ValueError("Node deletion changes the inherited board map; resolve it in source and Configure before using Nordic pin forms.")
    controller_names = {"&" + label: item["id"] for item in evidence.controllers for label in [item["id"], *item.get("aliases", [])]}
    protected = {name[1:] for name in controller_names} | {"pinctrl", "gpio0", "gpio1"}
    protected |= {item["owner"] for item in evidence.gpio_uses}
    controllers = set(controller_names)
    for node in document.syntax.nodes:
        if set(node.labels) & protected:
            raise ValueError("A local declaration shadows a known board label. Use its reference block or source editing.")
        if node.name.startswith("&{"):
            raise ValueError("Absolute reference blocks need source editing; the Nordic adapter uses verified board labels.")
        path = document._path(node)
        if path == "/soc" or path and path.startswith("/soc/"):
            raise ValueError("SoC path declarations can change inherited controller identity; use source editing for this composition.")
        if node.name in controllers and any(prop.name in {"compatible", "reg"} for prop in node.properties):
            raise ValueError("Controller identity overrides are outside the observed Nordic profile.")
        if node.name not in controllers and any(re.fullmatch(r"pinctrl-\d+", prop.name) for prop in node.properties):
            raise ValueError("An additional pinctrl consumer is outside the known board controllers; its allocation remains unknown.")
        if node.name in {"&gpio0", "&gpio1"} and node.properties:
            raise ValueError("GPIO provider overrides change the observed pin capabilities and need source editing.")
        if controller_names.get(node.name) in {"qspi", "pwm0"} and any(prop.name != "status" for prop in node.properties):
            raise ValueError("Modified QSPI/PWM routing is outside the supported pin adapter; its allocations are unknown.")
    for consumer in evidence.gpio_uses:
        contributors = [node for node in document.syntax.nodes if node.name == "&" + consumer["owner"] or
                        consumer.get("path") and document._path(node) == consumer["path"]]
        if len(contributors) > 1:
            raise ValueError("A board GPIO consumer has multiple local contributors. Resolve its reference/path overrides in source first.")


def _selected_overlay(context, draft):
    reports = context.get("input_evidence", {}).get("predicted", [])
    paths = []
    for report in reports:
        if report.get("status") in {"unknown", "unavailable", "ambiguous"}:
            raise ValueError("Overlay selection is unknown. Select this single overlay explicitly in Projects.")
        for item in report.get("files", []):
            if item.get("state") in {"expected", "selected", "active"}:
                path = Path(item["path"])
                paths.append(path if path.is_absolute() else Path(context["application"]["path"]) / path)
    if paths != [Path(draft.path)]:
        raise ValueError("Nordic pins require this draft to be the only selected overlay. Select it explicitly in Projects.")


def _enabled(document, node_id, controllers):
    node = document.node(node_id)
    while node:
        properties = [prop for prop in node.properties if prop.name == "status"]
        state = _literal(document, node, "status")
        if properties and (len(properties) != 1 or state is None or state.kind != "string" or state.value not in {"okay", "ok", "disabled"}):
            raise ValueError("A GPIO consumer's enabled status is unknown. Resolve its status in source before assigning pins.")
        if state is not None and state.value == "disabled":
            return False
        inherited = next((row for row in controllers if node.name in {"&" + label for label in [row["id"], *row.get("aliases", [])]}), None)
        if inherited and inherited["status"] not in {"okay", "ok"}:
            return False
        node = document.node(node.parent_id) if node.parent_id else None
    return True


def _gpio_owner(document, row, evidence):
    node = document.node(row["node_id"])
    path = document._path(node)
    aliases = {item["id"]: item.get("aliases", []) for item in evidence.controllers}
    return next((item["owner"] for item in evidence.gpio_uses
                 if node.name in {"&" + label for label in [item["owner"], *aliases.get(item["owner"], [])]}
                 or path and path == item.get("path")), row["node_id"])


def _uses(document, projection, evidence):
    uses, unknown = [], []
    for controller in projection["controllers"]:
        if controller["status"] == "disabled":
            continue
        if controller["status"] not in {"okay", "ok"}:
            unknown.append(f"{controller['id']}: controller status is unknown.")
        for state in controller["states"]:
            if not state.get("known", state.get("editable", False)):
                unknown.append(f"{controller['id']} {state['name']}: {state.get('reason') or 'pin routing is unknown'}")
            for signal in state["signals"]:
                if signal.get("port") is not None:
                    uses.append({"owner": controller["id"], "state": state["name"], "role": signal["role"],
                                 "port": signal["port"], "pin": signal["pin"], "kind": "pinctrl"})
    replaced = set()
    for row in projection["gpio"]:
        owner = _gpio_owner(document, row, evidence)
        replaced.add((owner, row["property"]))
        if not _enabled(document, row["node_id"], projection["controllers"]):
            continue
        if row.get("port") is None:
            if not row.get("editable"):
                unknown.append(f"{row['name']} {row['property']}: GPIO allocation is unknown.")
            continue
        uses.append({"owner": owner, "node_id": row["node_id"], "property": row["property"], "index": row["index"],
                     "port": row["port"], "pin": row["pin"], "kind": "gpio", "state": None,
                     "role": row["property"]})
    uses.extend({**row, "kind": "gpio", "state": None, "role": row.get("property", "gpios")}
                for row in evidence.gpio_uses if (row["owner"], row.get("property", "gpios")) not in replaced and row.get("enabled", True))
    return uses, unknown


def _conflicts(projection, uses):
    messages = []
    limit = 256
    truncated = "Pin conflicts exceed the 256-entry inspection limit; remaining conflicts are not listed."
    active = [row for row in projection["controllers"] if row["status"] in {"okay", "ok"}]
    for index, left in enumerate(active):
        for right in active[index + 1:]:
            if left.get("instance") is not None and left["instance"] == right.get("instance"):
                messages.append(f"{left['id']} and {right['id']} share hardware instance {left['instance']} and are both enabled.")
                if len(messages) >= limit:
                    return [*messages[:limit - 1], truncated]
    pins = defaultdict(list)
    for use in uses:
        pins[(use["port"], use["pin"])].append(use)
    for group in pins.values():
        for index, left in enumerate(group):
            for right in group[index + 1:]:
                if left["kind"] == right["kind"] == "pinctrl" and left["owner"] == right["owner"] and left["state"] != right["state"]:
                    continue
                messages.append(f"P{left['port']}.{left['pin']:02d} is used concurrently by {left['owner']} {left['role']} and {right['owner']} {right['role']}.")
                if len(messages) >= limit:
                    return [*messages[:limit - 1], truncated]
    return list(dict.fromkeys(messages))


class PinService:
    """A bounded board observation is revalidated for every atomic draft command."""

    commands = COMMANDS

    def __init__(self, drafts):
        self.drafts = drafts
        self.observations = {}

    @staticmethod
    def _adapter(document, evidence):
        return NordicSource(document, evidence.inherited_states, evidence.controllers,
                            gpio_flags=[row["name"] for row in evidence.gpio_flags])

    def _projection(self, document, evidence):
        _overlay_scope(document, evidence)
        result = self._adapter(document, evidence).project()
        for controller in result["controllers"]:
            controller["aliases"] = next(row.get("aliases", []) for row in evidence.controllers if row["id"] == controller["id"])
        uses, unknown = _uses(document, result, evidence)
        result["allocations"] = deepcopy(uses)
        result["allocation_unknown"] = list(unknown)
        result["reservations"] = deepcopy(evidence.reservations)
        result["conflicts"] = _conflicts(result, uses)
        result["diagnostics"] = list(dict.fromkeys([*evidence.diagnostics, *result.get("diagnostics", []),
                                                   *unknown, *result["conflicts"], SCOPE]))
        result["pins"] = deepcopy(evidence.pins)
        for pin in result["pins"]:
            pin["uses"] = [f"{row['owner']} {row['role']}" + (f" ({row['state']})" if row["state"] else "")
                           for row in uses if (row["port"], row["pin"]) == (pin["port"], pin["pin"])]
        return result

    def inspect(self, body):
        binding = self.drafts._body(body, _GUARD_FIELDS)
        with self.drafts.editor.lock, self.drafts.lock:
            context = self.drafts._context(binding)
            draft = self.drafts._scope(body, context)
            self.drafts._guard(body, draft)
            document = draft.document
            if document is None or document.syntax.blocked or self.drafts._visual_limit(document):
                raise ValueError("Repair incomplete source before loading Nordic pins.")
            now = time.monotonic()
            self.observations = {key: value for key, value in self.observations.items() if value["expires"] > now}
            evidence = None
            for observed in reversed(list(self.observations.values())):
                if observed["context"] == context and observed["evidence"].supported:
                    try:
                        observed["evidence"].revalidate()
                        evidence = observed["evidence"]
                    except ValueError:
                        pass
                    break
            if evidence is None:
                evidence = NordicEvidence(context)
            payload = {"supported": False, "reason": evidence.reason, "controllers": [], "gpio": [], "pins": [],
                       "allocations": [], "allocation_unknown": [], "reservations": [],
                       "diagnostics": list(evidence.diagnostics), "gpio_flags": evidence.gpio_flags,
                       "target": (context.get("target") or {}).get("requested"), "adapter": "nrf52840dk-v1"}
            if evidence.supported:
                try:
                    _selected_overlay(context, draft)
                    payload.update(self._projection(document, evidence), supported=True, reason="")
                except ValueError as exc:
                    payload.update(reason=str(exc), diagnostics=[str(exc), SCOPE])
            evidence.revalidate()
            self.drafts._scope(body, self.drafts._context(binding))
            self.drafts._guard(body, self.drafts.drafts[draft.id])
            now = time.monotonic()
            self.observations = {key: value for key, value in self.observations.items() if value["expires"] > now}
            while len(self.observations) >= 16:
                self.observations.pop(next(iter(self.observations)))
            schema_id = uuid.uuid4().hex
            payload.update(schema_version=1, schema_id=schema_id, fingerprint=evidence.fingerprint,
                           draft_id=draft.id, revision=draft.revision, sha256=document.sha256)
            self.observations[schema_id] = {"guard": deepcopy(body), "context": deepcopy(context), "evidence": evidence,
                                           "payload": deepcopy(payload), "expires": now + 600}
            return payload

    @staticmethod
    def _allocation(document, projection, evidence, command):
        port, pin = command["port"], command["pin"]
        if type(port) is not int or type(pin) is not int or not any(row["port"] == port and row["pin"] == pin for row in evidence.pins):
            raise ValueError("Choose a pin from the verified nRF52840 board table.")
        uses, unknown = _uses(document, projection, evidence)
        if unknown:
            raise ValueError("Some enabled pin allocations are unknown. Resolve them in source before assigning another pin: " + unknown[0])
        is_gpio = command["kind"] == "pin_gpio"
        if is_gpio:
            row = next((row for row in projection["gpio"] if (row["node_id"], row["property"], row["index"]) ==
                        (command["node_id"], command["property"], command["index"])), None)
            if row is None or not row["editable"]:
                raise ValueError("Choose an editable GPIO assignment from this observation.")
            owner, role = _gpio_owner(document, row, evidence), command["property"]
            active = _enabled(document, command["node_id"], projection["controllers"])
        else:
            owner, role = command["controller"], command["role"]
            controller = next((row for row in projection["controllers"] if row["id"] == owner), None)
            if controller is None:
                raise ValueError("Choose a controller from this board observation.")
            active = controller["status"] in {"okay", "ok"}
            if active and any(row["id"] != owner and row["status"] in {"okay", "ok"} and
                              row.get("instance") == controller.get("instance") for row in projection["controllers"]):
                raise ValueError("Another enabled controller shares this hardware instance. Change controller status explicitly in source first.")
        for reservation in evidence.reservations:
            if (reservation["port"], reservation["pin"]) == (port, pin):
                if reservation.get("owner") != owner:
                    raise ValueError(f"P{port}.{pin:02d} is reserved: {reservation['reason']}")
        if not active:
            return
        for used in uses:
            if (used["port"], used["pin"]) != (port, pin):
                continue
            if not is_gpio and used["kind"] == "pinctrl" and used["owner"] == owner:
                if used["state"] != command["state"] or used["role"] == role:
                    continue
            if is_gpio and used.get("node_id") == command["node_id"] and used.get("property") == role and used.get("index") == command["index"]:
                continue
            raise ValueError(f"P{port}.{pin:02d} is already used by {used['owner']} {used['role']}. No existing assignment was moved.")

    def plan(self, body, context, draft):
        command = body["command"]
        kind = command["kind"]
        fields = {"pin_assign": {"controller", "state", "role", "port", "pin", "split_shared"},
                  "pin_unassign": {"controller", "state", "role", "split_shared"},
                  "pin_gpio": {"node_id", "property", "index", "port", "pin", "flags"},
                  "pin_gpio_unassign": {"node_id", "property", "index"}}[kind] | {"kind", "schema_id"}
        if set(command) != fields or not isinstance(command["schema_id"], str):
            raise ValueError("Choose the exact supported pin command fields.")
        for key in ({"node_id", "property"} if kind.startswith("pin_gpio") else {"controller", "state", "role"}):
            if not isinstance(command[key], str) or not command[key] or len(command[key]) > 256:
                raise ValueError("Choose a literal controller, state, signal or GPIO property from the pin observation.")
        if kind.startswith("pin_gpio") and (type(command["index"]) is not int or command["index"] < 0):
            raise ValueError("Choose a GPIO entry from this observation.")
        observed = self.observations.get(command["schema_id"])
        guard = {key: value for key, value in body.items() if key != "command"}
        if observed is None or observed["guard"] != guard or observed["expires"] <= time.monotonic():
            raise ValueError("Pin data belongs to an earlier draft/context or expired. Load pin assignments again.")
        if not observed["payload"]["supported"]:
            raise ValueError(observed["payload"]["reason"])
        evidence = observed["evidence"]
        evidence.revalidate()
        document = draft.document
        _selected_overlay(context, draft)
        projection = self._projection(document, evidence)
        if kind in {"pin_assign", "pin_gpio"}:
            self._allocation(document, projection, evidence, command)
        adapter = self._adapter(document, evidence)
        pin = {"port": command["port"], "pin": command["pin"]} if kind in {"pin_assign", "pin_gpio"} else None
        if kind.startswith("pin_gpio"):
            if kind == "pin_gpio_unassign" and command["property"] == "cs-gpios":
                raise ValueError("Unassigning a chip select can reorder or invalidate bus-device indices. Edit the shared cs-gpios source explicitly.")
            change = adapter.plan_gpio(command["node_id"], command["property"], command["index"], pin, command.get("flags", []))
        else:
            if type(command["split_shared"]) is not bool:
                raise ValueError("Choose explicitly whether to copy the shared pinctrl state.")
            change = adapter.plan(command["controller"], command["state"], command["role"], pin, split=command["split_shared"])
        evidence.revalidate()
        return change

    def close(self):
        self.observations.clear()
