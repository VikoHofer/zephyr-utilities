"""Pin routes from the selected STM32 package table and controller bindings.

The selected HAL table supplies legal alternate functions and electrical defaults.
Routes clone pin nodes rather than editing shared board or package declarations.
"""

from collections import defaultdict
from copy import deepcopy
from pathlib import Path
import re

from .binding_service import _literal
from .chip_catalog import _cells, _clean, _stm32, _stm32_table
from .source import SourceDocument


_KINDS = {"st,stm32-uart": "uart", "st,stm32-usart": "uart", "st,stm32-lpuart": "uart",
          "st,stm32-i2c-v1": "i2c", "st,stm32-i2c-v2": "i2c",
          "st,stm32-spi": "spi", "st,stm32-spi-fifo": "spi", "st,stm32h7-spi": "spi"}
_REQUIRED = {"uart": ("tx", "rx"), "i2c": ("scl", "sda"), "spi": ("sck", "mosi", "miso")}
_EXTRA = {"uart": ("rts", "cts"), "i2c": (), "spi": ("nss",)}
_BOOLEANS = {"bias-disable", "bias-pull-up", "bias-pull-down", "drive-push-pull",
             "drive-open-drain", "output-low", "output-high"}
_SLEW = {"low-speed", "medium-speed", "high-speed", "very-high-speed"}
_REF = re.compile(r"&[A-Za-z_]\w*")
_PIN = re.compile(r"P([A-Z])(\d{1,2})")
_TABLE_NODE = re.compile(r"\b([A-Za-z_]\w*)\s*:\s*[\w-]+\s*\{([^{}]*)\}\s*;", re.S)
_MUX = re.compile(r"<\s*STM32_PINMUX\(\s*'([A-Z])'\s*,\s*(\d+)\s*,\s*(AF\d+|ANALOG|GPIO)\s*\)\s*>")


def _properties(document, node):
    """Copy only understood electrical settings, excluding phandle identity."""
    result = {}
    for prop in node.properties:
        if prop.name in {"pinmux", "phandle", "linux,phandle"}:
            continue
        if prop.name in result:
            raise ValueError("A pin state repeats an electrical property; resolve it in source first.")
        value = document.property_value(prop.id)
        if prop.name in _BOOLEANS and value is not None and value.kind == "boolean":
            result[prop.name] = ""
        elif prop.name == "slew-rate" and value is not None and value.kind == "string" and value.value in _SLEW:
            result[prop.name] = '"' + value.value + '"'
        else:
            raise ValueError(f"Pin electrical property {prop.name} is outside the supported STM32 route format.")
    for group in ({"bias-disable", "bias-pull-up", "bias-pull-down"},
                  {"drive-push-pull", "drive-open-drain"}, {"output-low", "output-high"}):
        if len(group & result.keys()) > 1:
            raise ValueError("A pin state has conflicting electrical settings; resolve it in source first.")
    return result


def _catalogue(observed, chip):
    if not chip.get("supported") or chip.get("chip", {}).get("family") != "stm32":
        raise ValueError("This route adapter requires an observed STM32 package pin table.")
    sources = observed.get("sources")
    if sources is None:
        raise ValueError("The selected STM32 package source evidence is unavailable.")
    metadata, pins, encodings, _, _ = _stm32(sources)
    if metadata != chip["chip"]:
        raise ValueError("The selected STM32 package changed since chip discovery.")
    _, raw = _stm32_table(sources)
    entries = {}
    for found in _TABLE_NODE.finditer(_clean(raw["text"])):
        parsed = SourceDocument.parse(("/ { pin {" + found[2] + "}; };").encode())
        if parsed.syntax.blocked:
            continue
        node = next(node for node in parsed.syntax.nodes if node.name == "pin")
        mux = [prop for prop in node.properties if prop.name == "pinmux"]
        if len(mux) != 1 or mux[0].value_start is None:
            continue
        encoded = _MUX.fullmatch(parsed.source[mux[0].value_start:mux[0].value_end].decode().strip())
        if encoded is None:
            continue
        name = re.fullmatch(r"(.+?)_(.+)_p[a-z]\d+", found[1])
        if name is None:
            continue
        controller, role = "&" + name[1], name[2].lower()
        if role not in {item for values in (*_REQUIRED.values(), *_EXTRA.values()) for item in values}:
            continue
        pin = pins.get((encoded[1], int(encoded[2])))
        if pin is None:
            continue
        key = (controller, role, pin["id"])
        if key in entries:
            raise ValueError("The STM32 package table has ambiguous alternatives for one signal and pin.")
        try:
            properties = _properties(parsed, node)
        except ValueError:
            # Unknown table electrical formats cannot become writable candidates.
            continue
        if controller.startswith("&i2c") and role in {"scl", "sda"} and "drive-open-drain" not in properties:
            continue
        entries[key] = {"reference": "&" + found[1], "pin": pin["id"], "mode": encoded[3],
                        "properties": properties}
    return entries, encodings


def _node(observed, row):
    document = observed.get("document")
    if document is None or document.syntax.blocked:
        raise ValueError("The current resolved source is unavailable for pin routing.")
    matches = [node for node in document.syntax.nodes if document._path(node) == row.get("path")]
    if len(matches) != 1:
        raise ValueError("The selected controller does not have one resolved source declaration.")
    return document, matches[0]


def _flag(document, node, name):
    properties = [prop for prop in node.properties if prop.name == name]
    if not properties:
        return False
    value = _literal(document, node, name)
    if len(properties) != 1 or value is None or value.kind != "boolean":
        raise ValueError(f"Controller property {name} needs a known boolean value before routing.")
    return True


def _reference(row):
    value = row.get("reference")
    if isinstance(value, str) and _REF.fullmatch(value):
        return value
    labels = row.get("labels", [])
    return "&" + labels[0] if labels and _REF.fullmatch("&" + labels[0]) else None


def _state_pins(observed, row, encodings, entries):
    document, node = _node(observed, row)
    topology = observed["payload"]
    states = [state for state in topology.get("pinctrl_states", []) if state.get("owner_id") == row["id"]]
    declared = [prop for prop in node.properties if re.fullmatch(r"pinctrl-\d+", prop.name)]
    if len(declared) != len(states):
        raise ValueError("Controller pinctrl states could not all be resolved.")
    if not states:
        return {}
    if any(state.get("name") not in {"default", "sleep"} or state.get("index") != (0 if state.get("name") == "default" else 1)
           for state in states) or len({state["name"] for state in states}) != len(states):
        raise ValueError("Only unambiguous default and optional sleep STM32 pin states can be rerouted.")
    if not any(state["name"] == "default" for state in states):
        raise ValueError("The selected controller needs a known default pin state before rerouting sleep pins.")
    by_path = {"hardware:" + document._path(item): item for item in document.syntax.nodes}
    result = {}
    default_by_pin = {}
    reference = _reference(row)
    for state in sorted(states, key=lambda item: item["index"]):
        signals = {}
        if not state.get("targets"):
            raise ValueError("The selected controller has an empty or unresolved pin state.")
        for target in state["targets"]:
            selected = by_path.get(target)
            if selected is None or selected.children:
                raise ValueError("Grouped or unresolved STM32 pin states require source editing.")
            values = _cells(document, selected, "pinmux")
            if values is None or len(values) != 1 or values[0] & ~0x3fff:
                raise ValueError("A STM32 pin state has an unknown mux encoding.")
            mux = values[0]
            port, index, mode = chr(ord("A") + ((mux >> 9) & 31)), (mux >> 5) & 15, mux & 31
            pin_id = f"P{port}{index}"
            candidates = [(pin, owner, signal) for pin, owner, signal in encodings.get(mux, [])
                          if "&" + owner == reference and (reference, signal.lower(), pin_id) in entries]
            if state["name"] == "default":
                roles = {signal.lower() for _, _, signal in candidates}
                if len(roles) != 1:
                    raise ValueError("A default STM32 pin cannot be mapped to one signal of this controller.")
                role = roles.pop()
                if pin_id in default_by_pin:
                    raise ValueError("The default state assigns more than one signal to the same pin.")
                default_by_pin[pin_id] = role
            else:
                role = default_by_pin.get(pin_id)
                if role is None or mode not in {16, 17} and not any(signal.lower() == role for _, _, signal in candidates):
                    raise ValueError("Sleep pins must correspond to the understood default route and mux modes.")
            if role in signals:
                raise ValueError("The pin state repeats a controller signal; resolve it in source first.")
            signals[role] = {"pin": pin_id, "mode": "ANALOG" if mode == 16 else "GPIO" if mode == 17 else "AF" + str(mode),
                             "properties": _properties(document, selected)}
        result[state["name"]] = signals
    if "sleep" in result and result["sleep"].keys() != result["default"].keys():
        raise ValueError("Default and sleep pin states must cover the same signals before rerouting.")
    return result


def _recovery(document, node, row):
    result = {}
    for role in ("scl", "sda"):
        name = role + "-gpios"
        if not any(prop.name == name for prop in node.properties):
            continue
        value = _literal(document, node, name)
        if value is None or value.kind != "phandle-array" or len(value.value) != 1 or len(value.value[0][1]) != 2:
            raise ValueError(f"{row['reference']} {name} needs one understood GPIO reference before rerouting.")
        result[role] = value.value[0][1][1]
    return result


def _chip_select_pins(observed, document, node):
    if not any(prop.name == "cs-gpios" for prop in node.properties):
        return []
    value = _literal(document, node, "cs-gpios")
    if value is None or value.kind != "phandle-array":
        raise ValueError("SPI chip selects need known GPIO references before changing the signal route.")
    pins = []
    for reference, cells in value.value:
        providers = [row for row in observed["payload"].get("gpio_providers", [])
                     if reference in {_reference(row), *["&" + label for label in row.get("labels", [])]}]
        if len(providers) != 1 or providers[0].get("cells") != ["pin", "flags"] or len(cells) != 2:
            raise ValueError("SPI chip selects need verified GPIO pin/flags providers before rerouting.")
        port = re.fullmatch(r"&gpio([a-z])", _reference(providers[0]) or "")
        if port is None or not 0 <= cells[0] <= 15:
            raise ValueError("A SPI chip-select pin is outside the understood STM32 GPIO format.")
        pins.append("P" + port[1].upper() + str(cells[0]))
    return pins


def _details(observed, chip, row, entries, encodings):
    document, node = _node(observed, row)
    compatible = row.get("compatible", [])
    kind = next((_KINDS[value] for value in compatible if value in _KINDS), None)
    reference = _reference(row)
    if kind is None or reference is None:
        raise ValueError("Choose a supported STM32 controller with a stable source label.")
    if row.get("binding", {}).get("state") != "matched":
        raise ValueError("The selected STM32 controller needs a matched local binding before activation.")
    if row.get("status") not in {"okay", "ok", "disabled"}:
        raise ValueError("The selected controller has an unknown status.")
    for property_name in (("single-wire", "tx-rx-swap", "de-enable") if kind == "uart" else ("ioswp",) if kind == "spi" else ()):
        if _flag(document, node, property_name):
            raise ValueError(f"{property_name} changes signal routing and is outside the ordinary {kind.upper()} pin editor.")
    states = _state_pins(observed, row, encodings, entries)
    if kind == "i2c" and any("drive-open-drain" not in pin["properties"] for pin in states.get("default", {}).values()):
        raise ValueError("I2C default pins need explicit open-drain settings before the route can be changed.")
    current = {role: value["pin"] for role, value in states.get("default", {}).items()}
    required = list(_REQUIRED[kind])
    if set(current) - set((*_REQUIRED[kind], *_EXTRA[kind])):
        raise ValueError("The controller has additional pin signals that this route editor cannot preserve.")
    if kind == "uart" and (_flag(document, node, "hw-flow-control") or any(role in current for role in ("rts", "cts"))):
        required.extend(("rts", "cts"))
    has_cs = any(prop.name == "cs-gpios" for prop in node.properties)
    chip_select_pins = _chip_select_pins(observed, document, node) if kind == "spi" else []
    if kind == "spi" and ("nss" in current or not has_cs and not _flag(document, node, "st,soft-nss")):
        required.append("nss")
    if "sleep" in states and set(states["sleep"]) != set(required):
        raise ValueError("The existing sleep state lacks required companion signals; resolve it in source first.")
    signals = []
    for role in required:
        candidates = sorted(pin for controller, signal, pin in entries if controller == reference and signal == role)
        if not candidates:
            raise ValueError(f"The selected package has no verified {reference} {role.upper()} pin alternatives.")
        unavailable = {pin: "This pin is used by a preserved GPIO chip select." for pin in candidates
                       if pin in chip_select_pins and not (row["status"] in {"okay", "ok"} and current.get(role) == pin)}
        signals.append({"id": role, "label": role.upper(), "required": True, "candidates": candidates,
                        "unavailable": unavailable})
    recovery = _recovery(document, node, row) if kind == "i2c" else {}
    notes = ["Application firmware must enable the corresponding Zephyr driver; this action changes only the overlay."]
    if kind == "spi":
        notes.append("Existing GPIO chip selects are preserved." if has_cs else
                     "Hardware NSS is part of this complete route." if "nss" in required else
                     "The existing software-NSS mode is preserved.")
        if row["status"] in {"okay", "ok"} and set(current.values()) & set(chip_select_pins):
            notes.append("The current SPI route already overlaps a GPIO chip select. Retained assignments preserve that source conflict.")
    notes.extend(_clock_notes(observed, chip))
    return {"id": reference, "kind": kind, "label": reference[1:].upper(), "owner_id": row["id"],
            "status": row["status"], "signals": signals, "current": current, "available": True,
            "reason": "", "notes": notes, "states": states, "recovery": recovery,
            "chip_select_pins": chip_select_pins}


def profile(observed, chip):
    """Expose complete routable controllers and explicit unsupported reasons."""
    entries, encodings = _catalogue(observed, chip)
    result = []
    for row in observed.get("payload", {}).get("nodes", []):
        kind = next((_KINDS[value] for value in row.get("compatible", []) if value in _KINDS), None)
        if kind is None:
            continue
        reference = _reference(row)
        try:
            item = _details(observed, chip, row, entries, encodings)
            item.pop("states")
            item.pop("recovery")
        except ValueError as exc:
            item = {"id": reference or row.get("path", ""), "kind": kind,
                    "label": (reference or row.get("path", "Controller")).lstrip("&").upper(),
                    "owner_id": row["id"], "status": row.get("status"), "signals": [], "current": {},
                    "available": False, "reason": str(exc), "notes": []}
        result.append(item)
    return result


def _active_clocks(observed):
    kinds = {"st,stm32-hse-clock": "HSE", "st,stm32-lse-clock": "LSE"}
    return {kinds[value] for row in observed.get("payload", {}).get("nodes", [])
            if row.get("status") in {"okay", "ok"}
            for value in row.get("compatible", []) if value in kinds}


def _clock_pins(chip, clock):
    roles = {"HSE": {"OSC_IN", "OSC_OUT", "HSE_IN", "HSE_OUT"},
             "LSE": {"OSC32_IN", "OSC32_OUT", "LSE_IN", "LSE_OUT"}}
    pins = {pin["id"] for pin in chip.get("pins", [])
            if any(choice.get("signal") in roles[clock] for choice in pin.get("functions", []))}
    # Keep the earlier verified package protection, without extending its pad
    # locations to other STM32 packages. HAL pin tables generally omit crystals.
    # ST STM32F446xC/E datasheet: https://www.st.com/resource/en/datasheet/stm32f446re.pdf
    if Path(chip.get("chip", {}).get("profile_source", "")).name == "stm32f446r(c-e)tx-pinctrl.dtsi":
        known = {"HSE": {"PH0", "PH1"}, "LSE": {"PC14", "PC15"}}
        pins.update(known[clock] & {pin["id"] for pin in chip.get("pins", [])})
    return pins


def _clock_notes(observed, chip):
    missing = sorted(clock for clock in _active_clocks(observed) if len(_clock_pins(chip, clock)) < 2)
    if not missing:
        return []
    return ["The selected Zephyr pin table does not identify every active " + "/".join(missing)
            + " oscillator pad. Check the board's clock wiring before assigning pins; an available source route does not establish that a physical pin is unused."]


def pin_notes(observed, chip):
    """Expose electrical reservations absent from the source package tables."""
    notes = _clock_notes(observed, chip)
    return {pin["id"]: list(notes) for pin in chip.get("pins", [])} if notes else {}


def pin_restrictions(observed, chip):
    """Protect evidenced debug, oscillator and remapped logical pins."""
    _catalogue(observed, chip)
    restrictions = defaultdict(list)
    for pin in chip.get("pins", []):
        if any("SWDIO" in choice.get("signal", "") or "SWCLK" in choice.get("signal", "") for choice in pin.get("functions", [])):
            restrictions[pin["id"]].append("SWD debug pins are protected; the visual editor does not disable the debug connection.")
    for clock in _active_clocks(observed):
        for pin in _clock_pins(chip, clock):
            restrictions[pin].append(f"The {clock} oscillator pad is protected while that clock is enabled.")
    document = observed["document"]
    remaps = {"remap-pa11": {"PA9", "PA11"}, "remap-pa12": {"PA10", "PA12"},
              "remap-pa11-pa12": {"PA9", "PA10", "PA11", "PA12"}}
    for node in document.syntax.nodes:
        if "pinctrl" not in node.labels:
            continue
        for prop in node.properties:
            if prop.name.startswith("remap-") and prop.name not in remaps:
                raise ValueError("The selected pin controller has an unrecognized pin remap; source editing is required.")
            if prop.name in remaps and _flag(document, node, prop.name):
                for pin in remaps[prop.name]:
                    restrictions[pin].append(f"The selected pin controller enables {prop.name}; these remapped pins require source editing.")
    return dict(restrictions)


def route(observed, chip, controller, selections, *, label_prefix="zu_stm32"):
    """Plan complete symbolic overlay updates without changing any source file."""
    entries, encodings = _catalogue(observed, chip)
    reference = controller.get("id") if isinstance(controller, dict) else controller
    rows = [row for row in observed.get("payload", {}).get("nodes", []) if _reference(row) == reference]
    if len(rows) != 1:
        raise ValueError("Choose one observed STM32 controller.")
    row = rows[0]
    details = _details(observed, chip, row, entries, encodings)
    required = {signal["id"] for signal in details["signals"]}
    if not isinstance(selections, dict) or set(selections) != required:
        raise ValueError("Select every required companion signal for the complete route.")
    if not isinstance(label_prefix, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,127}", label_prefix):
        raise ValueError("The generated pin route needs a valid collision-safe label prefix.")
    if any(not isinstance(pin, str) or (reference, role, pin) not in entries for role, pin in selections.items()):
        raise ValueError("A selected pin is not a legal signal alternative for this controller and package.")
    if len(set(selections.values())) != len(selections):
        raise ValueError("Each signal in a route must use a distinct pin.")
    retained = {role for role, pin in selections.items()
                if details["status"] in {"okay", "ok"} and details["current"].get(role) == pin}
    changed_pins = {pin for role, pin in selections.items() if role not in retained}
    if changed_pins & set(details["chip_select_pins"]):
        raise ValueError("A selected SPI signal overlaps a preserved GPIO chip select.")
    hard = pin_restrictions(observed, chip)
    if any(pin in hard for pin in changed_pins):
        raise ValueError("This route selects a protected or remapped pin. Check its pin restrictions before assigning it.")
    states = details["states"] or {"default": {}}
    nodes, references = [], {}
    for state, signals in states.items():
        references[state] = []
        for signal in details["signals"]:
            role = signal["id"]
            selected = entries[reference, role, selections[role]]
            existing = signals.get(role)
            properties = deepcopy(existing["properties"] if existing else selected["properties"])
            # AF modes follow the new package alternative. Sleep ANALOG/GPIO modes
            # retain their original meaning while moving to the companion pad.
            mode = existing["mode"] if state == "sleep" and existing and existing["mode"] in {"ANALOG", "GPIO"} else selected["mode"]
            matched = _PIN.fullmatch(selected["pin"])
            mux = f"<STM32_PINMUX('{matched[1]}', {int(matched[2])}, {mode})>"
            label = f"{label_prefix}_{state}_{role}"
            name = label.replace("_", "-")
            lines = [f"{label}: {name} {{", f"\tpinmux = {mux};"]
            lines.extend("\t" + prop + (" = " + value if value else "") + ";" for prop, value in properties.items())
            lines.append("};")
            nodes.append({"label": label, "name": name, "source": "\n".join(lines)})
            references[state].append("&" + label)
    updates = {"status": '"okay"', "pinctrl-names": ", ".join('"' + state + '"' for state in references)}
    updates.update({"pinctrl-" + str(index): "<" + " ".join(refs) + ">" for index, refs in enumerate(references.values())})
    document, node = _node(observed, row)
    if details["kind"] == "uart" and not any(prop.name == "current-speed" for prop in node.properties):
        updates["current-speed"] = "<115200>"
    if details["kind"] == "i2c" and not any(prop.name == "clock-frequency" for prop in node.properties):
        updates["clock-frequency"] = "<100000>"
    for role, flags in details["recovery"].items():
        pin = _PIN.fullmatch(selections[role])
        provider = "&gpio" + pin[1].lower()
        providers = [item for item in observed["payload"].get("gpio_providers", []) if _reference(item) == provider]
        if len(providers) != 1 or providers[0].get("cells") != ["pin", "flags"]:
            raise ValueError("The selected I2C recovery pin needs a verified GPIO pin/flags provider.")
        updates[role + "-gpios"] = f"<{provider} {int(pin[2])} {flags}>"
    return {"updates": [{"reference": reference, "properties": updates}],
            "includes": ["zephyr/dt-bindings/pinctrl/stm32-pinctrl.h"], "pinctrl_nodes": nodes,
            "pins": list(selections.values()), "notes": details["notes"]}
