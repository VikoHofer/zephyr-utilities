"""Source-backed Nordic master-peripheral routes for the understood PSEL architecture.

The caller owns observation freshness, canonical source edits and publication. This
adapter clones understood pin states instead of modifying shared inherited groups.
"""

from copy import deepcopy
from pathlib import Path
import re

from .binding_service import _literal
from .chip_catalog import _cells, _encoding, _file, _nordic, _nordic_ports, _NRF_ENCODING
from .chip_gpio import resolve_gpio


_KINDS = {
    "nordic,nrf-uart": "uart", "nordic,nrf-uarte": "uart",
    "nordic,nrf-twi": "i2c", "nordic,nrf-twim": "i2c",
    "nordic,nrf-spi": "spi", "nordic,nrf-spim": "spi",
}
_SHARED_KINDS = {**_KINDS, "nordic,nrf-twis": "i2c", "nordic,nrf-spis": "spi"}
_ROLES = {
    "uart": {"tx": "UART_TX", "rx": "UART_RX", "rts": "UART_RTS", "cts": "UART_CTS"},
    "i2c": {"scl": "TWIM_SCL", "sda": "TWIM_SDA"},
    "spi": {"sck": "SPIM_SCK", "mosi": "SPIM_MOSI", "miso": "SPIM_MISO"},
}
_REQUIRED = {"uart": ("tx", "rx"), "i2c": ("scl", "sda"), "spi": ("sck", "mosi", "miso")}
_BOOLEAN = {"bias-disable", "bias-pull-up", "bias-pull-down", "low-power-enable"}
_LOW_FREQUENCY = {"P0.02", "P0.03", "P0.09", "P0.10", "P0.28", "P0.29", "P0.30", "P0.31",
                  *(f"P1.{index:02d}" for index in (*range(1, 8), *range(10, 16)))}
_PIN_REFERENCE = re.compile(r"P(\d{1,2})\.(\d{2})")
_LABEL = re.compile(r"[A-Za-z_][A-Za-z_0-9]{0,100}")
_PROFILE_REFERENCE = "https://docs.nordicsemi.com/r/bundle/ps_nrf52840/page/pin.html"


def _context(observed, chip):
    if not chip.get("supported") or chip.get("chip", {}).get("family") != "nordic":
        raise ValueError("Pin activation requires an observed Nordic GPIO and pinctrl source profile.")
    if not observed.get("payload", {}).get("supported") or observed.get("document") is None:
        raise ValueError("Pin activation needs a complete current board and overlay observation.")
    sources = observed["sources"]
    topology, document = observed["payload"], observed["document"]
    metadata, pins, _, _, _ = _nordic(sources, topology, document)
    if metadata != chip["chip"] or {pin["id"] for pin in pins.values()} != {pin["id"] for pin in chip["pins"]}:
        raise ValueError("The selected Nordic source inventory changed. Refresh chip discovery before activation.")
    _ownership(sources, topology, document, metadata)
    _, header = _file(sources, "nrf-pinctrl.h")
    numbers = _encoding(header["text"], "NRF_PSEL", _NRF_ENCODING,
                        {"NRF_PIN_POS": 0, "NRF_PIN_MSK": 511, "NRF_FUN_POS": 24, "NRF_FUN_MSK": 255})
    expected = {"UART_TX": 0, "UART_RX": 1, "UART_RTS": 2, "UART_CTS": 3,
                "SPIM_SCK": 4, "SPIM_MOSI": 5, "SPIM_MISO": 6, "TWIM_SCL": 11, "TWIM_SDA": 12}
    if any(numbers.get("NRF_FUN_" + role) != value for role, value in expected.items()):
        raise ValueError("The selected Nordic function encodings changed; activation remains unavailable.")
    if document.syntax.blocked:
        raise ValueError("Repair incomplete resolved source before assigning Nordic pins.")
    return observed["payload"], document, {value: role for role, value in expected.items()}


def _ownership(sources, topology, document, metadata):
    """Do not equate a GPIO register slot with cross-core or cross-domain ownership."""
    target = (getattr(sources, "context", {}).get("target") or {}).get("requested", "")
    # Source bundles also retain board metadata for diagnostics. Only the
    # selected DTS include closure establishes a core or security domain.
    names = {Path(path).stem for path in sources.files if Path(path).suffix in {".dts", ".dtsi"}}
    if "ns" in target.split("/") or any(re.search(r"_ns(?:_|$)", name) for name in names):
        raise ValueError("Non-secure Nordic pin activation needs GPIO permissions from the secure image, which are outside this selected DTS observation.")
    if (any(part in target.split("/") for part in ("cpunet", "cpurad", "cpuflpr", "cpuppr"))
            or any(re.search(r"_(?:cpunet|cpurad|cpuflpr|cpuppr)(?:_|$)", name) for name in names)):
        raise ValueError("Pin activation needs ownership/forwarding evidence from the controlling core. This selected core's DTS alone cannot establish it.")
    if any("nordic,nrf-uicr-v2" in row.get("compatible", []) for row in topology.get("nodes", [])):
        raise ValueError("This Nordic source uses UICR domain ownership. Its GPIO ownership and dedicated peripheral routes are not yet decoded; pin activation is unavailable.")
    if any(name.startswith("nrf54") for name in names):
        raise ValueError("This Nordic source requires dedicated and cross-domain pin routing. Its DTS GPIO slots do not provide the complete routing matrix; pin activation is unavailable.")
    if not re.fullmatch(r"nrf(?:51\d+|52\d+|53\d+|91\d+)", metadata.get("soc", "")):
        raise ValueError("The Nordic GPIO/pinctrl encoding is understood, but this source's hardware routing and ownership model is not yet established.")
    for row in topology.get("nodes", []):
        node = document.find_node(row["path"])
        if any(prop.name in {"nordic,owner", "nordic,domain", "owned-pins", "secure-gpios"} for prop in node.properties):
            raise ValueError("Explicit Nordic pin ownership or security declarations require a verified ownership adapter before activation.")


def _has(document, node, name):
    return any(prop.name == name for prop in node.properties)


def _boolean(document, node, name):
    value = _literal(document, node, name)
    if value is None and _has(document, node, name):
        raise ValueError(f"The {name} setting is not an understood boolean.")
    if value is not None and value.kind != "boolean":
        raise ValueError(f"The {name} setting is not an understood boolean.")
    return value is not None


def pin_restrictions(observed, chip):
    """Hard source reservations for both peripheral routes and application GPIOs."""
    topology, document, _ = _context(observed, chip)
    restrictions = {pin["id"]: list(pin.get("restrictions", [])) for pin in chip["pins"]}
    ports = _nordic_ports(topology, document)
    provider_ports = {item["provider"]["id"]: item["port"] for item in ports}
    for item in ports:
        provider = item["provider"]
        node = document.find_node(provider["path"])
        reserved = _cells(document, node, "gpio-reserved-ranges") or []
        for pin in chip["pins"]:
            if pin["port"] != item["port"]:
                continue
            if any(start <= pin["index"] < start + size for start, size in zip(reserved[::2], reserved[1::2])):
                restrictions[pin["id"]].append("Reserved by the selected board's gpio-reserved-ranges.")
    for row in topology.get("nodes", []):
        if "nordic,nrf-gpio-forwarder" not in row.get("compatible", []) or row.get("status") == "disabled":
            continue
        if row.get("status") not in {"okay", "ok"}:
            raise ValueError("The GPIO forwarding state is unknown; resolve inter-core pin ownership first.")
        forwarder = document.find_node(row["path"])
        for child in forwarder.children:
            assignments = _literal(document, document.node(child), "gpios")
            if assignments is None or assignments.kind != "phandle-array" or not assignments.value:
                raise ValueError("Forwarded GPIO ownership could not be decoded from the selected source.")
            for reference, cells in assignments.value:
                provider, resolved = resolve_gpio(topology, reference, cells)
                if provider["id"] not in provider_ports:
                    raise ValueError("A forwarded pin refers to a GPIO provider outside the Nordic inventory.")
                name = f"P{provider_ports[provider['id']]}.{resolved[0]:02d}"
                restrictions[name].append("This GPIO is forwarded to another core by the selected source.")
    uicrs = [row for row in topology.get("nodes", []) if "nordic,nrf-uicr" in row.get("compatible", [])]
    if len(uicrs) > 1:
        raise ValueError("The Nordic UICR configuration is ambiguous.")
    uicr = next(iter(uicrs), None)
    node = document.find_node(uicr["path"]) if uicr else None
    soc = chip["chip"]["soc"]
    nfc = [row for row in topology.get("nodes", []) if "nordic,nrf-nfct" in row.get("compatible", [])]
    nfc_gpio = _boolean(document, node, "nfct-pins-as-gpios") if node else False
    if nfc and (not nfc_gpio or any(row.get("status") != "disabled" for row in nfc)):
        # Hardware-resource mappings from Zephyr's nordic,nrf-uicr binding;
        # these do not define the GPIO inventory or peripheral alternatives.
        # https://docs.zephyrproject.org/latest/build/dts/api/bindings/arm/nordic%2Cnrf-uicr.html
        pair = ("P0.09", "P0.10") if soc.startswith("nrf52") else ("P0.02", "P0.03") if soc == "nrf5340" else None
        if pair is None:
            raise ValueError("This source contains NFC hardware whose dedicated GPIO pins are not identified by the understood UICR schema.")
        for name in pair:
            if name in restrictions:
                restrictions[name].append("NFC pins require explicit UICR GPIO configuration and an inactive NFC peripheral before assignment.")
    if node and _boolean(document, node, "gpio-as-nreset"):
        pin = ("P0.21" if re.fullmatch(r"nrf528(?:05|10|11|32)", soc) else
               "P0.18" if re.fullmatch(r"nrf528(?:20|33|40)", soc) else None)
        if pin is None:
            raise ValueError("The source enables a GPIO reset function, but its dedicated pin is not established by the understood UICR schema.")
        if pin in restrictions:
            restrictions[pin].append(f"The selected UICR configuration uses {pin} as reset.")
    return {pin: list(dict.fromkeys(reasons)) for pin, reasons in restrictions.items()}


def pin_notes(observed, chip):
    """Package recommendations are separate from mux legality or board ownership."""
    _context(observed, chip)
    notes = {}
    if chip["chip"]["soc"].startswith(("nrf52", "nrf53")):
        notes.update({pin["id"]: ["Shared low-frequency crystal pin; verify the board clock configuration in Kconfig before use."]
                      for pin in chip["pins"] if pin["id"] in {"P0.00", "P0.01"}})
    if chip["chip"]["soc"] == "nrf52840" and chip["chip"].get("package") == "QIAA":
        notes.update({pin["id"]: ["Nordic recommends standard drive and signals up to 10 kHz on this package pin."]
                      for pin in chip["pins"] if pin["id"] in _LOW_FREQUENCY})
    return notes


def _identity(row, kind):
    labels = [label for label in row.get("labels", []) if re.fullmatch(("uart" if kind == "uart" else kind) + r"\d+", label)]
    if len(labels) == 1:
        return "&" + labels[0]
    reference = row.get("reference")
    if not isinstance(reference, str) or not reference.startswith("&") or not _LABEL.fullmatch(reference[1:]):
        raise ValueError("The peripheral needs an unambiguous source label before pin assignment.")
    return reference


def _group_properties(document, node):
    if node.children:
        raise ValueError("Nested Nordic pin groups require source editing.")
    values = {}
    for prop in node.properties:
        if prop.name in {"psels", "phandle", "linux,phandle"}:
            continue
        if prop.name in _BOOLEAN:
            if _boolean(document, node, prop.name):
                values[prop.name] = None
        elif prop.name == "nordic,drive-mode":
            value = _cells(document, node, prop.name)
            if value is None or len(value) != 1 or not 0 <= value[0] <= 7:
                raise ValueError("The pin group drive mode is outside the understood common Nordic drive modes.")
            values[prop.name] = f"<{value[0]}>"
        else:
            raise ValueError(f"The pin group setting {prop.name} is not yet supported for route cloning.")
    if len(set(values) & {"bias-disable", "bias-pull-up", "bias-pull-down"}) > 1:
        raise ValueError("The pin group contains conflicting bias settings.")
    return values


def _states(topology, document, row, kind, numbers, inventory):
    node = document.find_node(row["path"])
    names = _literal(document, node, "pinctrl-names")
    if names is None:
        if any(prop.name.startswith("pinctrl-") for prop in node.properties):
            raise ValueError("Pin state names and references need explicit default/sleep coverage.")
        return {}
    names = [names.value] if names.kind == "string" else list(names.value) if names.kind == "string-array" else []
    if names not in (["default"], ["default", "sleep"]):
        raise ValueError("Only default and optional sleep Nordic pin states are supported.")
    if any(re.fullmatch(r"pinctrl-\d+", prop.name) and int(prop.name[8:]) >= len(names) for prop in node.properties):
        raise ValueError("Additional unnamed pin states must be resolved before assignment.")
    controllers = [item for item in topology.get("nodes", []) if "nordic,nrf-pinctrl" in item.get("compatible", [])]
    if len(controllers) != 1:
        raise ValueError("A unique Nordic pin controller is required.")
    state_parent = controllers[0]["path"]
    result = {}
    for index, name in enumerate(names):
        references = _literal(document, node, f"pinctrl-{index}")
        if references is None or references.kind != "phandles" or not references.value:
            raise ValueError("Every Nordic pin state needs an understood direct reference list.")
        signals = {}
        for reference in references.value:
            state = document.find_node(reference)
            if not state.parent_id or document._path(document.node(state.parent_id)) != state_parent:
                raise ValueError("Nordic states must be children of the observed pin controller.")
            if any(prop.name not in {"phandle", "linux,phandle"} for prop in state.properties) or not state.children:
                raise ValueError("The Nordic state has unsupported root properties or no pin groups.")
            for child in state.children:
                group = document.node(child)
                properties = _group_properties(document, group)
                psels = _cells(document, group, "psels")
                if not psels:
                    raise ValueError("The Nordic pin group has undecoded function selections.")
                for value in psels:
                    pin, function = value & 511, numbers.get(value >> 24)
                    if value & 0x00fffe00 or function not in _ROLES[kind].values():
                        raise ValueError("The Nordic pin state contains an unsupported signal or encoding.")
                    signal = next(key for key, role in _ROLES[kind].items() if role == function)
                    if signal in signals:
                        raise ValueError("A Nordic signal appears more than once in the same state.")
                    pin_name = f"P{pin // 32}.{pin % 32:02d}"
                    if pin != 511 and pin_name not in inventory:
                        raise ValueError("The Nordic state uses a pin outside the observed GPIO inventory.")
                    signals[signal] = {"pin": None if pin == 511 else pin_name,
                                       "properties": properties}
        result[name] = signals
    return result


def _instance(document, row):
    """Scope a decoded register address to its parent bus, including 64-bit cells."""
    node = document.find_node(row["path"])
    parent = document.node(node.parent_id) if node.parent_id else None
    if parent is None:
        raise ValueError("The hardware instance needs a parent address space.")
    address_cells = _cells(document, parent, "#address-cells") if _has(document, parent, "#address-cells") else [2]
    size_cells = _cells(document, parent, "#size-cells") if _has(document, parent, "#size-cells") else [1]
    if (address_cells not in ([1], [2]) or size_cells not in ([0], [1], [2])):
        raise ValueError("The hardware instance's parent address/size cells are not understood.")
    values = _cells(document, node, "reg")
    count = address_cells[0]
    if values is None or len(values) != count + size_cells[0] or any(value > 0xffffffff for value in values):
        raise ValueError("The hardware instance address is not understood.")
    address = 0
    for cell in values[:count]:
        address = address << 32 | cell
    return document._path(parent) + "@" + hex(address)


def _controller_ports(document, node, chip):
    """Honor an explicit default port; do not invent cross-domain alternatives."""
    if not _has(document, node, "default-gpio-port"):
        if _has(document, node, "cross-domain-pins-supported"):
            raise ValueError("Cross-domain routing needs an explicit default GPIO port and verified alternative pin mapping.")
        return None
    value = _literal(document, node, "default-gpio-port")
    if value is None or value.kind != "phandles" or len(value.value) != 1:
        raise ValueError("The controller's default GPIO port is not an unambiguous reference.")
    port = document.find_node(value.value[0])
    number = _cells(document, port, "port")
    if number is None or len(number) != 1 or number[0] not in {pin["port"] for pin in chip["pins"]}:
        raise ValueError("The controller's default GPIO port is outside the observed inventory.")
    return {number[0]}


def _controller(topology, document, row, kind, numbers, chip, restrictions):
    reference = _identity(row, kind)
    result = {"id": reference, "label": f"{reference[1:].upper()} ({kind.upper()})", "kind": kind,
              "owner_id": row["id"], "path": row["path"], "status": row.get("status", "unknown"),
              "compatible": deepcopy(row.get("compatible", [])), "current": {}, "signals": [],
              "available": True, "reason": "", "states": {}, "instance": None,
              "shared_with": [], "blocked_by": [], "notes": [], "route_complete": True}
    try:
        if row.get("binding", {}).get("state") != "matched":
            raise ValueError("The peripheral needs a matched local binding before route generation.")
        if row.get("status") not in {"okay", "ok", "disabled"}:
            raise ValueError("The selected peripheral's status is not an editable enabled/disabled value.")
        node = document.find_node(row["path"])
        result["instance"] = _instance(document, row)
        states = _states(topology, document, row, kind, numbers, {pin["id"] for pin in chip["pins"]})
        result["states"] = states
        current = {signal: settings["pin"] for signal, settings in states.get("default", {}).items() if settings["pin"]}
        result["current"] = current
        required = list(_REQUIRED[kind])
        if kind == "uart":
            result["route_complete"] = not _boolean(document, node, "disable-rx")
            if _boolean(document, node, "hw-flow-control") or any(signal in values for values in states.values() for signal in ("rts", "cts")):
                required.extend(("rts", "cts"))
        ports = _controller_ports(document, node, chip)
        if ports is not None:
            result["notes"].append("Alternatives stay on the declared default GPIO port; cross-domain alternatives need additional routing evidence.")
        for signal in required:
            candidates = [pin["id"] for pin in chip["pins"]
                          if ((ports is None or pin["port"] in ports) and not restrictions.get(pin["id"]))
                          or row.get("status") in {"okay", "ok"} and pin["id"] == current.get(signal)]
            result["signals"].append({"id": signal, "label": signal.upper(), "required": True, "candidates": candidates})
        for state in states.values():
            if any(signal not in required for signal in state):
                raise ValueError("The configured signal set cannot be preserved by this complete-route form.")
        if states and any(signal not in states["default"] for signal in _REQUIRED[kind]):
            result["notes"].append("The new route completes missing default-state signals.")
        if "sleep" not in states:
            result["notes"].append("A matching low-power sleep state is generated for this route.")
        if kind == "uart" and not _has(document, node, "current-speed"):
            result["notes"].append("The new UART uses 115200 baud because no current speed is declared.")
    except ValueError as exc:
        result.update(available=False, reason=str(exc))
    return result


def profile(observed, chip):
    """Describe exact complete routes, including disabled TWI/SPI controllers."""
    topology, document, numbers = _context(observed, chip)
    restrictions = pin_restrictions(observed, chip)
    result = []
    for row in topology.get("nodes", []):
        kinds = {_KINDS[compatible] for compatible in row.get("compatible", []) if compatible in _KINDS}
        if len(kinds) != 1:
            continue
        try:
            result.append(_controller(topology, document, row, kinds.pop(), numbers, chip, restrictions))
        except ValueError:
            continue
    instances = []
    unknown_instances = []
    for row in topology.get("nodes", []):
        kinds = {_SHARED_KINDS[compatible] for compatible in row.get("compatible", []) if compatible in _SHARED_KINDS}
        if len(kinds) == 1:
            try:
                instance = _instance(document, row)
                instances.append({"owner_id": row["id"], "id": row.get("reference") or row["path"],
                                  "instance": instance, "status": row.get("status", "unknown")})
            except ValueError:
                if row.get("status") != "disabled":
                    unknown_instances.append(row.get("reference") or row["path"])
    for controller in result:
        if controller["instance"] is None:
            continue
        peers = [other for other in instances if other["owner_id"] != controller["owner_id"] and other["instance"] == controller["instance"]]
        controller["shared_with"] = [peer["id"] for peer in peers]
        controller["blocked_by"] = [peer["id"] for peer in peers if peer["status"] != "disabled"]
        if controller["blocked_by"]:
            controller.update(available=False, reason="Shares its hardware instance with " + ", ".join(controller["blocked_by"])
                              + ". Disable that controller explicitly before activating this route.")
        elif unknown_instances:
            controller.update(available=False, reason="The active hardware instance addresses for " + ", ".join(unknown_instances)
                              + " cannot be checked for shared peripherals. Resolve their register declarations before activation.")
    return result


def _render_state(label, state, selections, kind, existing):
    lines = [f"{label}: {label} {{"]
    for signal, name in selections.items():
        previous = existing.get(signal)
        properties = deepcopy(previous["properties"]) if previous else {}
        if previous is None:
            if state == "sleep":
                properties["low-power-enable"] = None
            elif kind == "uart" and signal in {"rx", "cts"}:
                properties["bias-pull-up"] = None
        match = _PIN_REFERENCE.fullmatch(name)
        role = _ROLES[kind][signal]
        expression = (f"NRF_PSEL_DISCONNECTED({role})" if state == "sleep" and previous and previous["pin"] is None
                      else f"NRF_PSEL({role}, {int(match[1])}, {int(match[2])})")
        lines.extend((f"\tgroup_{signal} {{", f"\t\tpsels = <{expression}>;"))
        for property_name, value in sorted(properties.items()):
            lines.append(f"\t\t{property_name}" + (f" = {value}" if value is not None else "") + ";")
        lines.append("\t};")
    lines.append("};")
    return "\n".join(lines)


def route(observed, chip, controller, selections, *, label_prefix="zu_nordic"):
    """Plan a complete route; callers must revalidate and commit it atomically."""
    profiles = profile(observed, chip)
    identifier = controller.get("id") if isinstance(controller, dict) else controller
    selected = next((item for item in profiles if item["id"] == identifier), None)
    if selected is None:
        raise ValueError("Choose a Nordic peripheral from the current chip observation.")
    if not selected["available"]:
        raise ValueError(selected["reason"])
    if not isinstance(selections, dict) or set(selections) != {item["id"] for item in selected["signals"]}:
        raise ValueError("Choose every signal in the complete peripheral route.")
    if any(not isinstance(value, str) for value in selections.values()):
        raise ValueError("Choose a package pin name for every signal.")
    if len(set(selections.values())) != len(selections):
        raise ValueError("A complete route needs a distinct pin for every signal.")
    for signal in selected["signals"]:
        if not isinstance(selections[signal["id"]], str) or selections[signal["id"]] not in signal["candidates"]:
            raise ValueError(f"Choose an available package pin for {signal['label']}.")
    if not isinstance(label_prefix, str) or not _LABEL.fullmatch(label_prefix):
        raise ValueError("The generated pin state label is invalid.")
    topology, document, _ = _context(observed, chip)
    node = document.find_node(selected["path"])
    labels = [label_prefix + "_" + name for name in ("default", "sleep")]
    for label in labels:
        if any(label in node.labels or node.name == label for node in document.syntax.nodes):
            raise ValueError("The generated pin state label already exists. Choose a fresh label prefix.")
    statements = [{"label": label, "name": label,
                   "source": _render_state(label, name, selections, selected["kind"], selected["states"].get(name, {}))}
                  for label, name in zip(labels, ("default", "sleep"))]
    if any("NRF_PSEL_DISCONNECTED" in statement["source"] for statement in statements):
        _, header = _file(observed["sources"], "nrf-pinctrl.h")
        _encoding(header["text"], "NRF_PSEL_DISCONNECTED",
                  "(NRF_PIN_DISCONNECTED|((NRF_FUN_##fun&NRF_FUN_MSK)<<NRF_FUN_POS))", {})
        if not re.search(r"(?m)^\s*#\s*define\s+NRF_PIN_DISCONNECTED\s+NRF_PIN_MSK\s*$", header["text"]):
            raise ValueError("The selected Nordic disconnected-pin encoding is not understood.")
    properties = {"status": '"okay"', "pinctrl-0": f"<&{labels[0]}>", "pinctrl-1": f"<&{labels[1]}>",
                  "pinctrl-names": '"default", "sleep"'}
    if selected["kind"] == "uart":
        if _has(document, node, "disable-rx"):
            properties["disable-rx"] = None
        if not _has(document, node, "current-speed"):
            properties["current-speed"] = "<115200>"
    notes = list(selected["notes"])
    advisories = {}
    for pin, messages in pin_notes(observed, chip).items():
        if pin in selections.values():
            for message in messages:
                advisories.setdefault(message, []).append(pin)
    for message, pins in advisories.items():
        notes.append(", ".join(sorted(pins)) + ": " + message
                     + (" " + _PROFILE_REFERENCE if "10 kHz" in message else ""))
    return {"updates": [{"reference": selected["id"], "properties": properties}],
            "includes": ["zephyr/dt-bindings/pinctrl/nrf-pinctrl.h"], "pinctrl_nodes": statements,
            "pins": list(selections.values()), "notes": notes}
