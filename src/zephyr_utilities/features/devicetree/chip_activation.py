"""Source-backed pin activation, validated before one canonical draft commit."""

from copy import deepcopy
from dataclasses import replace
import re
import time

from .binding_service import _literal
from .board_topology import BoardTopologyService
from .chip_catalog import observe_chip
from .chip_gpio import resolve_gpio
from .chip_source import apply_activation, unique_prefix
from .drafts import _BINDING_FIELDS, _GUARD_FIELDS


COMMANDS = frozenset({"chip_assign", "chip_gpio"})
GPIO_NOTE = "Creates an application GPIO reference in /zephyr,user. Application code sets direction and runtime value; CONFIG_GPIO is required."


def _adapter(chip):
    if chip.get("chip", {}).get("family") == "stm32":
        from . import chip_stm32
        return chip_stm32
    if chip.get("chip", {}).get("family") == "nordic":
        from . import chip_nordic
        return chip_nordic
    raise ValueError("Pin activation is unavailable for this chip/package source profile.")


def _active(row, topology):
    nodes = {node["id"]: node for node in topology["nodes"]}
    seen = set()
    while row and row["id"] not in seen:
        seen.add(row["id"])
        if row.get("status", "okay") == "disabled":
            return False
        if row.get("status", "okay") not in {"okay", "ok"}:
            return None
        row = nodes.get(row.get("parent_id"))
    return True


def _uses(pin, topology):
    nodes = {node["id"]: node for node in topology["nodes"]}
    return [assignment for assignment in pin["assignments"]
            if _active(nodes.get(assignment.get("owner_id"), {"id": "unknown", "status": "unknown"}), topology) is not False]


def _provider(pin, topology):
    label = "gpio" + str(pin["port"]).lower()
    found = [row for row in topology["gpio_providers"] if label in row["labels"]]
    if len(found) != 1 or found[0].get("cells") != ["pin", "flags"]:
        raise ValueError(f"{pin['name']} has no verified GPIO pin/flags provider.")
    row = found[0]
    expected = "st,stm32-gpio" if isinstance(pin["port"], str) else "nordic,nrf-gpio"
    if (row.get("binding", {}).get("state") != "matched" or expected not in row.get("compatible", [])
            or not row.get("supported") or row.get("status") not in {"okay", "ok", "disabled"}):
        raise ValueError(f"{pin['name']} needs an understood GPIO provider and binding.")
    parent = next((item for item in topology["nodes"] if item["id"] == row.get("parent_id")), None)
    if parent is not None and _active(parent, topology) is not True:
        raise ValueError(f"{pin['name']}'s GPIO provider is below an unavailable parent.")
    count = row.get("pin_count")
    if type(count) is not int or not 0 <= pin["index"] < count:
        raise ValueError(f"{pin['name']} is outside its observed GPIO provider range.")
    return row, "&" + label


def _gpio_existing(pin, observed):
    document, topology = observed["document"], observed["payload"]
    provider, _ = _provider(pin, topology)
    node = next((node for node in document.syntax.nodes if document._path(node) == "/zephyr,user"), None)
    if node:
        for prop in node.properties:
            value = document.property_value(prop.id)
            if prop.name.endswith("-gpios") and value and value.kind == "phandle-array" and len(value.value) == 1:
                try:
                    resolved, cells = resolve_gpio(topology, *value.value[0])
                except ValueError:
                    continue
                if resolved["id"] == provider["id"] and cells[0] == pin["index"]:
                    return prop.name
    return None


def _other_uses(pin, topology, owner, replaced_gpio=()):
    # Pinctrl signals move together; preserved GPIO chip selects and other GPIO
    # consumers remain owners even when they belong to this same controller.
    other = [use for use in _uses(pin, topology) if use.get("owner_id") != owner]
    other.extend(use for use in pin["assignments"] if owner is not None and use.get("owner_id") == owner
                 and use.get("state") == "GPIO" and use.get("signal") not in replaced_gpio)
    return other


def _pin_issue(pin, observed, restrictions, *, owner=None, retained=False, gpio=False, replaced_gpio=()):
    if not retained and restrictions.get(pin["id"]):
        return " ".join(restrictions[pin["id"]])
    if gpio:
        try:
            _provider(pin, observed["payload"])
        except ValueError as exc:
            return str(exc)
    if retained:
        return ""
    other = _other_uses(pin, observed["payload"], owner, replaced_gpio)
    if other:
        return f"{pin['name']} is already assigned to " + ", ".join(dict.fromkeys(
            f"{use['controller']} {use['signal']}" for use in other)) + ". Choose another pin or change that source assignment first."
    return ""


def _source_issue(observed, chip, draft):
    if draft is None:
        return "Choose an existing application overlay before assigning pins."
    if not chip.get("supported") or not observed["payload"].get("supported"):
        return chip.get("reason") or observed["payload"].get("reason") or "Chip source evidence is unavailable."
    if observed["payload"].get("coverage") not in {"board+selected-overlay", "board+unsaved-overlay"}:
        return "Pin activation needs the selected application's complete single-overlay observation."
    if draft.document is None or draft.document.syntax.blocked or draft.document.syntax.deletions:
        return "Repair incomplete source or node deletions before assigning pins."
    if chip.get("unknown_assignments"):
        return "Some source pin assignments could not be decoded. Resolve those assignments before activating another pin."
    nodes = {row["path"]: row for row in observed["payload"]["nodes"]}

    def understood(provider, cells):
        return (provider is not None and provider.get("cells") == ["pin", "flags"]
                and len(cells) == 2 and all(type(cell) is int for cell in cells)
                and type(provider.get("pin_count")) is int and 0 <= cells[0] < provider["pin_count"]
                and 0 <= cells[1] <= 0xffffffff)

    for node in observed["document"].syntax.nodes:
        row = nodes.get(observed["document"]._path(node))
        if row is None or _active(row, observed["payload"]) is False:
            continue
        for prop in node.properties:
            if prop.name != "gpios" and not prop.name.endswith("-gpios"):
                continue
            value = observed["document"].property_value(prop.id)
            hog = _literal(observed["document"], node, "gpio-hog")
            valid = False
            if value is not None and value.kind == "phandle-array":
                try:
                    valid = bool(value.value) and all(understood(*resolve_gpio(observed, reference, cells)) for reference, cells in value.value)
                except ValueError:
                    valid = False
            elif value is not None and hog and value.kind == "cells":
                provider = next((item for item in observed["payload"]["gpio_providers"] if item["id"] == row.get("parent_id")), None)
                cells = value.value
                valid = bool(cells) and len(cells) % 2 == 0 and all(understood(provider, cells[index:index + 2]) for index in range(0, len(cells), 2))
            if not valid:
                return f"The GPIO assignment {row['path']} {prop.name} cannot be decoded. Resolve it before activating another pin."
    return ""


def prepare_activation(observed, chip, draft):
    """Annotate this immutable observation with decisions the server will recheck."""
    reason = _source_issue(observed, chip, draft)
    gpio_notes = [GPIO_NOTE]
    if chip.get("chip", {}).get("inventory") == "gpio-slots":
        gpio_notes.append("DTS GPIO slots do not establish package bonding, board wiring or clock selection. Check those board details before using a newly assigned GPIO.")
    result = {"available": False, "reason": reason, "observation_id": observed["payload"]["observation_id"],
              "controllers": [], "gpio": {"available": False, "reason": reason, "pins": [], "unavailable": {}, "notes": gpio_notes}}
    if reason:
        return result
    try:
        adapter = _adapter(chip)
        restrictions = {pin["id"]: list(pin.get("restrictions", [])) for pin in chip["pins"]}
        for pin_id, reasons in adapter.pin_restrictions(observed, chip).items():
            restrictions.setdefault(pin_id, []).extend(reasons)
        restrictions = {key: list(dict.fromkeys(value)) for key, value in restrictions.items()}
        pin_notes = adapter.pin_notes(observed, chip) if hasattr(adapter, "pin_notes") else {}
        controllers = adapter.profile(observed, chip)
        pins = {pin["id"]: pin for pin in chip["pins"]}
        nodes = {row["id"]: row for row in observed["payload"]["nodes"]}
        for controller in controllers:
            row = nodes.get(controller.get("owner_id"), {})
            parent = nodes.get(row.get("parent_id"))
            if parent is not None and _active(parent, observed["payload"]) is not True:
                controller.update(available=False, reason="The controller is below an unavailable parent. Enable its parent explicitly in source first.")
            enabled = controller.get("status") in {"okay", "ok"}
            replaced_gpio = ("scl-gpios", "sda-gpios") if controller["kind"] == "i2c" else ()
            for signal in controller["signals"]:
                unavailable = dict(signal.get("unavailable", {}))
                for pin_id in signal["candidates"]:
                    if pin_id not in pins:
                        unavailable[pin_id] = "The pin is absent from this package inventory."
                        continue
                    retained = enabled and controller.get("current", {}).get(signal["id"]) == pin_id
                    issue = _pin_issue(pins[pin_id], observed, restrictions, owner=controller.get("owner_id"), retained=retained,
                                       replaced_gpio=replaced_gpio)
                    if issue:
                        unavailable[pin_id] = issue
                    elif retained:
                        existing = _other_uses(pins[pin_id], observed["payload"], controller.get("owner_id"), replaced_gpio)
                        if existing or restrictions.get(pin_id):
                            controller.setdefault("notes", []).append(f"{pin_id} retains its existing assignment, including existing reservations or source conflicts. Board wiring is unchanged.")
                signal["unavailable"] = unavailable
                if signal.get("required", True) and not any(pin not in unavailable for pin in signal["candidates"]):
                    controller.update(available=False, reason=f"{signal['label']} has no available pin in this configuration.")
            controller.setdefault("notes", []).append("Apply changes this controller's devicetree route and status. Firmware driver configuration and physical wiring remain separate.")
            controller["notes"].append("Firmware requires " + {"uart": "CONFIG_SERIAL", "i2c": "CONFIG_I2C", "spi": "CONFIG_SPI"}[controller["kind"]] + ".")
        available, unavailable = [], {}
        for pin in chip["pins"]:
            try:
                existing = _gpio_existing(pin, observed)
            except ValueError as exc:
                unavailable[pin["id"]] = str(exc)
                continue
            issue = _pin_issue(pin, observed, restrictions, gpio=True, retained=bool(existing))
            if issue:
                unavailable[pin["id"]] = issue
            else:
                available.append(pin["id"])
        result.update(available=True, reason="", controllers=controllers, pin_notes=pin_notes,
                      gpio={"available": bool(available), "reason": "" if available else "No unclaimed GPIO is available.",
                            "pins": available, "unavailable": unavailable, "notes": gpio_notes})
        observed["activation_restrictions"] = restrictions
    except (ValueError, KeyError, TypeError) as exc:
        result.update(reason=str(exc))
        result["gpio"]["reason"] = str(exc)
    return result


class ChipActivationService:
    commands = COMMANDS

    def __init__(self, drafts, workspace):
        self.drafts, self.workspace = drafts, workspace

    def _observation(self, identifier, guard):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Choose a current chip observation before assigning pins.")
        with self.workspace.lock:
            records = list(self.workspace.records.items())
            for job_id, record in records:
                if record["expires"] <= time.monotonic():
                    continue
                job = self.workspace.jobs.get(job_id)
                if job["status"] != "completed" or (job.get("result") or {}).get("activation", {}).get("observation_id") != identifier:
                    continue
                if record["guard"] != guard:
                    raise ValueError("The chip or draft changed. Wait for its refreshed pin observation.")
                self.workspace._revalidate(record, sources=True)
                return record["observation"], deepcopy(job["result"])
        raise ValueError("This pin observation expired. Retry chip discovery before applying the assignment.")

    def command(self, body):
        binding = self.drafts._body(body, _GUARD_FIELDS | {"command"})
        command = body["command"]
        kind = command.get("kind")
        expected = ({"kind", "observation_id", "controller", "signals"} if kind == "chip_assign" else
                    {"kind", "observation_id", "pin"} if kind == "chip_gpio" else set())
        if not expected or set(command) != expected:
            raise ValueError("Choose the exact supported pin activation fields.")
        guard = {key: body[key] for key in _BINDING_FIELDS | _GUARD_FIELDS}
        context, draft = self.workspace._capture(guard)
        observed, chip = self._observation(command["observation_id"], guard)
        activation = prepare_activation(observed, chip, draft)
        if not activation["available"]:
            raise ValueError(activation["reason"])
        if kind == "chip_gpio":
            candidate = self._gpio(draft, observed, chip, activation, command)
        else:
            candidate = self._route(draft, observed, chip, activation, command)
        # Expensive preprocessing never holds the editor/draft locks. Only a checked
        # final candidate may join canonical source/history; discovery does no writes.
        if candidate.source != draft.source:
            checked = BoardTopologyService.observe(context, replace(draft, source=candidate.source, document=candidate,
                                                                    revision=candidate.revision))
            if not checked["payload"]["supported"]:
                raise ValueError("Generated overlay was not applied: " + checked["payload"]["reason"])
            resulting = observe_chip(checked["sources"], checked["payload"], checked["document"], target=context.get("target"))
            issue = _source_issue(checked, resulting, replace(draft, document=candidate))
            if issue:
                raise ValueError("Generated overlay was not applied: " + issue)
            self._verify_result(command, resulting, checked)
        self.workspace._revalidate({"guard": guard, "observation": observed}, sources=True)
        if candidate.source != draft.source:
            checked["sources"].revalidate()
            checked["catalog"].revalidate()
        with self.drafts.editor.lock, self.drafts.lock:
            current = self.drafts._scope(body, self.drafts._context(binding))
            self.drafts._guard(body, current)
            return self.drafts._commit(body, binding, current, candidate.source, parsed=candidate)

    @staticmethod
    def _gpio(draft, observed, chip, activation, command):
        pin_id = command["pin"]
        if not isinstance(pin_id, str) or pin_id not in activation["gpio"]["pins"]:
            raise ValueError(activation["gpio"]["unavailable"].get(pin_id, "Choose an available package GPIO.") if isinstance(pin_id, str)
                             else "Choose an available package GPIO.")
        pin = next(pin for pin in chip["pins"] if pin["id"] == pin_id)
        provider, reference = _provider(pin, observed["payload"])
        existing = _gpio_existing(pin, observed)
        if existing and provider.get("status") in {"okay", "ok"}:
            return draft.document
        base = "app-" + re.sub(r"[^a-z0-9]", "-", pin_id.lower())
        names = {prop.name for node in observed["document"].syntax.nodes
                 if observed["document"]._path(node) == "/zephyr,user" for prop in node.properties}
        name = base + "-gpios"
        index = 1
        while name in names:
            name = f"{base}-{index}-gpios"
            index += 1
        updates = [{"reference": reference, "properties": {"status": '"okay"'}}]
        if not existing:
            updates.append({"reference": "/zephyr,user", "properties": {name: f"<{reference} {pin['index']} GPIO_ACTIVE_HIGH>"}})
        return apply_activation(draft.document, observed, {"includes": ["zephyr/dt-bindings/gpio/gpio.h"], "updates": updates})

    @staticmethod
    def _route(draft, observed, chip, activation, command):
        controller = next((row for row in activation["controllers"] if row["id"] == command["controller"]), None)
        if controller is None or not controller.get("available"):
            raise ValueError((controller or {}).get("reason") or "Choose an available UART, I2C or SPI controller.")
        signals = command["signals"]
        if not isinstance(signals, dict) or set(signals) != {row["id"] for row in controller["signals"]}:
            raise ValueError("Choose every signal required by this complete controller route.")
        for signal in controller["signals"]:
            pin = signals[signal["id"]]
            if not isinstance(pin, str) or pin not in signal["candidates"]:
                raise ValueError(f"Choose a legal {signal['label']} pin for this chip and controller.")
            if pin in signal["unavailable"]:
                raise ValueError(signal["unavailable"][pin])
        if len(set(signals.values())) != len(signals):
            raise ValueError("Different controller signals cannot occupy the same pin.")
        if (controller.get("status") in {"okay", "ok"} and signals == controller.get("current")
                and controller.get("route_complete", True)):
            return draft.document
        plan = _adapter(chip).route(observed, chip, controller, signals,
                                    label_prefix=unique_prefix(draft.document, observed, controller["id"]))
        return apply_activation(draft.document, observed, plan)

    @staticmethod
    def _verify_result(command, chip, observed):
        if not chip.get("supported") or chip.get("unknown_assignments"):
            raise ValueError("Generated pin assignments could not be decoded; the draft is unchanged.")
        if command["kind"] == "chip_gpio":
            pin = next((pin for pin in chip["pins"] if pin["id"] == command["pin"]), None)
            if pin is None or not _gpio_existing(pin, observed):
                raise ValueError("Generated GPIO reference was not observed; the draft is unchanged.")
        else:
            profile = _adapter(chip).profile(observed, chip)
            controller = next((row for row in profile if row["id"] == command["controller"]), None)
            if (controller is None or controller.get("status") not in {"okay", "ok"}
                    or any(controller.get("current", {}).get(signal) != pin for signal, pin in command["signals"].items())):
                raise ValueError("Generated controller route did not match its requested pins; the draft is unchanged.")
