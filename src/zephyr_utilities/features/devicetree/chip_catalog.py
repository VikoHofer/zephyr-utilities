"""Read-only chip pin catalogues from the selected board's observed source closure.

Catalogues describe logical pins and source configurations, not physical pad placement
or runtime electrical activity. They never authorize mutations or execute source text.
"""

from pathlib import Path
import re

from .binding_service import _literal


EDIT_REASON = "Activation availability is checked separately against the current overlay and pin assignments."
SCOPE = ("Logical pin view from the selected source profile. Package pad positions, external wiring "
         "and runtime electrical activity are not inferred. Activation requires a current supported overlay observation.")
MAX_PINS = 256
MAX_ALTERNATIVES = 4096
_COMMENTS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|/\*.*?\*/|//[^\n]*', re.S)
_STM32_TABLE = re.compile(r"stm32[a-z0-9][a-z0-9()_-]*-pinctrl\.dtsi\Z")
_STM32_ENCODING = "(((((port)-'A')&STM32_PORT_MASK)<<STM32_PORT_SHIFT)|(((line)&STM32_LINE_MASK)<<STM32_LINE_SHIFT)|(((STM32_##mode)&STM32_MODE_MASK)<<STM32_MODE_SHIFT))"
_NRF_ENCODING = "((((((port)*32U)+(pin))&NRF_PIN_MSK)<<NRF_PIN_POS)|((NRF_FUN_##fun&NRF_FUN_MSK)<<NRF_FUN_POS))"


def _clean(text):
    return _COMMENTS.sub(lambda match: " " + "\n" * match[0].count("\n")
                         if match[0].startswith(("/*", "//")) else match[0], text)


def _file(sources, name):
    matches = [(path, value) for path, value in sources.files.items() if Path(path).name == name]
    if len(matches) != 1:
        raise ValueError(f"The selected sources need one unambiguous {name} pin profile.")
    return matches[0]


def _numbers(text):
    return {name: int(value, 0) for name, value in re.findall(
        r"(?m)^\s*#\s*define\s+(\w+)\s+(0[xX][0-9a-fA-F]+|[0-9]+)[uUlL]*\s*$", _clean(text))}


def _encoding(text, name, expected, constants):
    text = re.sub(r"\\\r?\n", "", _clean(text))
    found = re.search(r"(?m)^\s*#\s*define\s+" + name + r"\([^\n]*?\)\s+([^\n]+)$", text)
    if not found or re.sub(r"\s+", "", found[1]) != expected:
        raise ValueError(f"The selected {name} encoding is outside the understood pin profile.")
    numbers = _numbers(text)
    if any(numbers.get(key) != value for key, value in constants.items()):
        raise ValueError(f"The selected {name} fields changed; pin assignments remain unknown.")
    return numbers


def _pin(port, index, *, nordic=False):
    name = f"P{port}.{index:02d}" if nordic else f"P{port}{index}"
    return {"id": name, "name": name, "port": port, "index": index, "package_pad": None,
            "assignments": [], "functions": [], "restrictions": [], "board_names": [], "sources": []}


def _function(controller, signal, *, source=None, reason=EDIT_REASON):
    return {"controller": controller, "signal": signal,
            "label": f"{controller} {signal}".strip(), "available": False, "reason": reason, "source": source}


def _controller(row):
    return row.get("reference") or ("&" + row["labels"][0] if row.get("labels") else row.get("path", ""))


def _assignment(owner, signal, state, source=None):
    return {"controller": _controller(owner), "signal": signal, "state": state,
            "origin": owner.get("origin", "unknown"), "source": source or next(iter(owner.get("source_locations", [])), None),
            "status": owner.get("status", "unknown"), "owner_id": owner.get("id")}


def _append_assignment(pin, value):
    if value not in pin["assignments"]:
        pin["assignments"].append(value)


def _cells(document, node, name):
    """Read resolved numeric groups without the writable model's single-group limit."""
    properties = [prop for prop in node.properties if prop.name == name]
    if len(properties) != 1 or properties[0].value_start is None:
        return None
    prop = properties[0]
    raw = _clean(document.source[prop.value_start:prop.value_end].decode())
    number = r"(?:0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)"
    group = r"<\s*" + number + r"(?:\s+" + number + r")*\s*>"
    if not re.fullmatch(r"\s*" + group + r"(?:\s*,\s*" + group + r")*\s*", raw):
        return None
    values = [int(token, 0) for token in re.findall(number, raw)]
    return values if len(values) <= MAX_PINS else None


def _stm32_table(sources):
    """Use the one selected package table, never a workspace-wide chip allowlist."""
    matches = [(path, value) for path, value in sources.files.items() if _STM32_TABLE.fullmatch(Path(path).name)]
    if len(matches) != 1:
        raise ValueError("The selected sources need one unambiguous STM32 package pin table.")
    return matches[0]


def _target_soc(sources, prefix):
    target = getattr(sources, "context", {}).get("target") or {}
    qualifiers = target.get("qualifiers", []) if isinstance(target, dict) else []
    return next((part for part in qualifiers[:1] if isinstance(part, str)
                 and re.fullmatch(prefix + r"[a-z0-9_]+", part)), None)


def _stm32(sources):
    path, value = _stm32_table(sources)
    if re.search(r"\bSTM32F1_PINMUX\s*\(", _clean(value["text"])):
        raise ValueError("STM32F1 remap pin tables need a separate encoding adapter; automatic pin activation is not available for this format yet.")
    header_path, header = _file(sources, "stm32-pinctrl.h")
    numbers = _encoding(header["text"], "STM32_PINMUX", _STM32_ENCODING,
                        {"STM32_MODE_SHIFT": 0, "STM32_MODE_MASK": 31, "STM32_LINE_SHIFT": 5,
                         "STM32_LINE_MASK": 15, "STM32_PORT_SHIFT": 9, "STM32_PORT_MASK": 31})
    pins, encodings = {}, {}
    text = _clean(value["text"])
    pattern = re.compile(r"\b([a-zA-Z_]\w*)\s*:\s*[\w-]+\s*\{([^{}]*)\}\s*;", re.S)
    count = 0
    for node in pattern.finditer(text):
        mux = re.search(r"\bpinmux\s*=\s*<\s*STM32_PINMUX\(\s*'([A-Z])'\s*,\s*(\d+)\s*,\s*(AF\d+|ANALOG|GPIO)\s*\)\s*>\s*;", node[2])
        if not mux:
            if re.search(r"\bpinmux\s*=", node[2]):
                raise ValueError("The STM32 package table contains an unsupported pinmux expression.")
            continue
        port, index, mode = mux[1], int(mux[2]), mux[3]
        expected_mode = int(mode[2:]) if mode.startswith("AF") else {"ANALOG": 16, "GPIO": 17}[mode]
        if (index > 15 or port > "Z" or mode.startswith("AF") and expected_mode > 15
                or numbers.get("STM32_" + mode) != expected_mode):
            raise ValueError("The STM32 package table contains an unsupported pin encoding.")
        count += 1
        if count > MAX_ALTERNATIVES:
            raise ValueError("The package table exceeds the pin alternative limit.")
        pin = pins.setdefault((port, index), _pin(port, index))
        source = {"path": path, "line": text.count("\n", 0, node.start()) + 1}
        if source not in pin["sources"]:
            pin["sources"].append(source)
        name = node[1]
        match = re.fullmatch(r"(.+?)_(.+)_p[a-z]\d+", name)
        controller, signal = (match[1], match[2].upper()) if match else ("", name)
        function = _function("&" + controller if controller else "", signal, source=source)
        function["reference"] = "&" + name
        pin["functions"].append(function)
        encoded = ((ord(port) - ord("A")) << 9) | (index << 5) | numbers["STM32_" + mode]
        encodings.setdefault(encoded, []).append((pin, controller, signal))
    if len(re.findall(r"\bpinmux\s*=", text)) != count:
        raise ValueError("The complete STM32 package table could not be decoded safely.")
    if not pins:
        raise ValueError("The STM32 package table has no understood pin alternatives.")
    for pin in pins.values():
        pin["functions"].insert(0, _function("", "GPIO", source={"path": path}))
    profile = Path(path).name.removesuffix("-pinctrl.dtsi")
    model = re.match(r"stm32[a-z]+\d+(?:[a-z]\d+)?", profile)
    model = model[0] if model else profile
    soc = _target_soc(sources, "stm32") or model
    chip = {"name": model.upper(), "soc": soc, "family": "stm32", "vendor": "STMicroelectronics",
            "package": profile.upper() + " source profile", "profile_source": path,
            "inventory": "pin-table", "layout": "logical"}
    return chip, pins, encodings, [{"path": path}, {"path": header_path}], "stm32"


def _nordic_ports(topology, document=None):
    """Return source-described GPIO slots; these are not package pad positions."""
    result, seen = [], set()
    for provider in topology.get("gpio_providers", []):
        if "nordic,nrf-gpio" not in provider.get("compatible", []):
            continue
        if (provider.get("binding", {}).get("state") != "matched" or provider.get("supported") is not True
                or provider.get("cells") != ["pin", "flags"]):
            raise ValueError("Nordic pin discovery needs a matched GPIO pin/flags binding.")
        node = document.find_node(provider["path"]) if document is not None else None
        port_cells = _cells(document, node, "port") if node is not None else None
        port = port_cells[0] if port_cells is not None and len(port_cells) == 1 else provider.get("properties", {}).get("port")
        if (type(port) is not int or not 0 <= port < 16 or port in seen
                or [label for label in provider.get("labels", []) if re.fullmatch(r"gpio\d+", label)] != [f"gpio{port}"]):
            raise ValueError("Nordic pin discovery needs an unambiguous GPIO port number and matching gpioN label.")
        if node is not None and any(prop.name == "port" for prop in node.properties) and (port_cells is None or len(port_cells) != 1):
            raise ValueError("A Nordic GPIO port number could not be decoded.")
        count = provider.get("pin_count")
        count_cells = _cells(document, node, "ngpios") if node is not None else None
        explicit_count = node is not None and any(prop.name == "ngpios" for prop in node.properties)
        if explicit_count and (count_cells is None or len(count_cells) != 1 or count != count_cells[0]):
            raise ValueError("A Nordic GPIO slot count could not be decoded consistently.")
        if (type(count) is not int or not 1 <= count <= 32
                or not explicit_count and provider.get("pin_count_origin") not in {"explicit", "binding-default"}):
            raise ValueError("Nordic pin discovery needs a verified GPIO slot count from DTS or its binding.")
        ranges = provider.get("reserved_ranges") or []
        if node is not None and any(prop.name == "gpio-reserved-ranges" for prop in node.properties):
            cells = _cells(document, node, "gpio-reserved-ranges")
            if cells is None or len(cells) % 2:
                raise ValueError("The Nordic GPIO reserved ranges could not be decoded.")
            ranges = [cells[index:index + 2] for index in range(0, len(cells), 2)]
        if (not isinstance(ranges, (list, tuple)) or any(not isinstance(pair, (list, tuple)) or len(pair) != 2
                or any(type(value) is not int for value in pair) or pair[0] < 0 or pair[1] < 1
                or pair[0] + pair[1] > count for pair in ranges)):
            raise ValueError("A Nordic GPIO reserved range is outside the observed slot count.")
        seen.add(port)
        result.append({"port": port, "count": count, "provider": provider,
                       "source": next(iter(provider.get("source_locations", [])), None), "reserved_ranges": ranges})
    if not result:
        raise ValueError("The selected Nordic sources have no verified GPIO port inventory.")
    return sorted(result, key=lambda value: value["port"])


def _nordic_identity(sources):
    candidates = {}
    packages = []
    for path in sources.files:
        name = Path(path).name
        match = re.fullmatch(r"(nrf\d[a-z0-9]*)(?:_([a-z0-9_]+))?\.dtsi", name)
        if match is None:
            continue
        candidates.setdefault(match[1], []).append(path)
        if match[2] and re.fullmatch(r"(?:cpuapp_|cpunet_|ns_)*(?:q|c|s|l)[a-z]{3}", match[2]):
            packages.append((match[1], match[2].split("_")[-1].upper(), path))
    soc = _target_soc(sources, "nrf")
    if soc is None:
        longest = [name for name in candidates if not any(other != name and other.startswith(name) for other in candidates)]
        if len(longest) != 1:
            raise ValueError("The selected sources do not identify one unambiguous Nordic SoC.")
        soc = longest[0]
    if not any(soc.startswith(name) or name.startswith(soc) for name in candidates):
        raise ValueError("The selected Nordic target has no matching observed SoC source.")
    packages = [(name, package, path) for name, package, path in packages if name == soc]
    if len({package for _, package, _ in packages}) > 1:
        raise ValueError("The selected sources contain ambiguous Nordic package descriptions.")
    package = packages[0][1] if packages else None
    evidence = [path for name, paths in candidates.items() if soc.startswith(name) or name.startswith(soc) for path in paths]
    return soc, package, evidence


def _nordic(sources, topology, document=None):
    header_path, header = _file(sources, "nrf-pinctrl.h")
    numbers = _encoding(header["text"], "NRF_PSEL", _NRF_ENCODING,
                        {"NRF_PIN_POS": 0, "NRF_PIN_MSK": 511, "NRF_FUN_POS": 24, "NRF_FUN_MSK": 255})
    soc, package, evidence = _nordic_identity(sources)
    ports = _nordic_ports(topology, document)
    pins = {}
    for port in ports:
        for index in range(port["count"]):
            pin = _pin(port["port"], index, nordic=True)
            pin["sources"] = list(port["provider"].get("source_locations", []))
            if not pin["sources"]:
                pin["sources"] = [{"path": path} for path in evidence]
            if any(start <= index < start + count for start, count in port["reserved_ranges"]):
                pin["restrictions"].append("Reserved by the selected board's gpio-reserved-ranges.")
            pins[port["port"], index] = pin
    signals = {value: name[8:] for name, value in numbers.items() if name.startswith("NRF_FUN_") and name not in {"NRF_FUN_POS", "NRF_FUN_MSK"}}
    allowed = {"nordic,nrf-uarte": ("UART_TX", "UART_RX", "UART_RTS", "UART_CTS"),
               "nordic,nrf-uart": ("UART_TX", "UART_RX", "UART_RTS", "UART_CTS"),
               "nordic,nrf-twim": ("TWIM_SDA", "TWIM_SCL"),
               "nordic,nrf-twi": ("TWIM_SDA", "TWIM_SCL"),
               "nordic,nrf-spi": ("SPIM_SCK", "SPIM_MOSI", "SPIM_MISO"),
               "nordic,nrf-spim": ("SPIM_SCK", "SPIM_MOSI", "SPIM_MISO")}
    for pin in pins.values():
        pin["functions"] = [_function("", "GPIO", source=next(iter(pin["sources"]), None))]
        for row in topology.get("nodes", []):
            roles = next((allowed[item] for item in row.get("compatible", []) if item in allowed), ())
            for role in roles:
                if role in signals.values():
                    pin["functions"].append(_function(_controller(row), role, source={"path": header_path},
                        reason=EDIT_REASON))
    if sum(len(pin["functions"]) for pin in pins.values()) > MAX_ALTERNATIVES:
        raise ValueError("The selected Nordic GPIO/controller combinations exceed the pin alternative limit.")
    chip = {"name": "nRF" + soc[3:].upper(), "soc": soc, "family": "nordic", "vendor": "Nordic Semiconductor",
            "package": package, "inventory": "gpio-slots", "layout": "logical"}
    return chip, pins, signals, [{"path": path} for path in [*evidence, header_path]], "nordic"


def _provider_pins(topology, pins, family):
    result = {}
    for provider in topology.get("gpio_providers", []):
        expected = "st,stm32-gpio" if family == "stm32" else "nordic,nrf-gpio"
        if provider.get("compatible") and expected not in provider["compatible"]:
            continue
        for label in provider.get("labels", []):
            match = re.fullmatch(r"gpio([a-z])" if family == "stm32" else r"gpio(\d+)", label)
            if match:
                port = match[1].upper() if family == "stm32" else int(match[1])
                result[provider["id"]] = {index: pin for (pin_port, index), pin in pins.items() if pin_port == port}
                break
    return result


def _annotate_gpio(topology, document, pins, family):
    from .chip_gpio import resolve_gpio

    providers = _provider_pins(topology, pins, family)
    syntax = {document._path(node): node for node in document.syntax.nodes}
    for provider in topology.get("gpio_providers", []):
        mapped = providers.get(provider["id"], {})
        if not mapped:
            continue
        names = provider.get("properties", {}).get("gpio-line-names", [])
        names = names if isinstance(names, list) else [names] if isinstance(names, str) else []
        reserved_ranges = provider.get("reserved_ranges") or []
        node = syntax.get(provider.get("path"))
        declared = node is not None and any(prop.name == "gpio-reserved-ranges" for prop in node.properties)
        if declared:
            raw_ranges = _cells(document, node, "gpio-reserved-ranges")
            if raw_ranges is None or len(raw_ranges) % 2:
                raise ValueError("The mapped GPIO reserved ranges could not be decoded.")
            reserved_ranges = [raw_ranges[index:index + 2] for index in range(0, len(raw_ranges), 2)]
        elif "gpio-reserved-ranges" in provider.get("properties", {}) and provider.get("reserved_ranges") is None:
            raise ValueError("The mapped GPIO reserved ranges could not be decoded.")
        limit = 16 if family == "stm32" else 32
        count = provider.get("pin_count")
        count = limit if count is None else count
        if not isinstance(reserved_ranges, (list, tuple)) or reserved_ranges and (
                type(count) is not int or not 0 < count <= limit
                or any(not isinstance(item, (list, tuple)) or len(item) != 2
                       or any(type(value) is not int for value in item)
                       or item[0] < 0 or item[1] <= 0 or sum(item) > count for item in reserved_ranges)):
            raise ValueError("A mapped GPIO reserved range is outside the understood slot count.")
        for index, pin in mapped.items():
            if index < len(names) and names[index]:
                pin["board_names"].append(str(names[index]))
            for reserved in reserved_ranges:
                if reserved[0] <= index < sum(reserved):
                    pin["restrictions"].append("Reserved by the selected board's gpio-reserved-ranges.")
    by_path = {row["path"]: row for row in topology.get("nodes", [])}
    for node in document.syntax.nodes:
        owner = by_path.get(document._path(node), {})
        for prop in node.properties:
            if prop.name != "gpios" and not prop.name.endswith("-gpios"):
                continue
            value = document.property_value(prop.id)
            if value and value.kind == "phandle-array":
                for reference, cells in value.value:
                    try:
                        provider, resolved = resolve_gpio(topology, reference, cells)
                    except ValueError:
                        continue
                    mapped = providers.get(provider["id"], {})
                    if resolved[0] in mapped:
                        _append_assignment(mapped[resolved[0]], _assignment(owner, prop.name, "GPIO"))
            elif value and value.kind == "cells" and _literal(document, node, "gpio-hog"):
                parent = by_path.get(document._path(document.node(node.parent_id)), {}) if node.parent_id else {}
                mapped = providers.get(parent.get("id"), {})
                if len(value.value) % 2 == 0:
                    for index in value.value[::2]:
                        if index in mapped:
                            _append_assignment(mapped[index], _assignment(owner, "GPIO hog", "GPIO"))
    for mapping in topology.get("connector_maps", []):
        for entry in mapping.get("entries", []):
            cells = entry.get("parent_cells", [])
            pin = providers.get(entry.get("provider_id"), {}).get(cells[0]) if cells else None
            if pin is not None:
                pin.setdefault("connectors", []).append({"path": mapping.get("path"), "cells": entry.get("child_cells", [])})


def _assignments(topology, document, pins, encodings, family):
    rows = {row["id"]: row for row in topology.get("nodes", [])}
    syntax = {"hardware:" + document._path(node): node for node in document.syntax.nodes}
    unknown = 0
    for state in topology.get("pinctrl_states", []):
        owner = rows.get(state["owner_id"], {})
        for reference in state.get("targets", []):
            root = syntax.get(reference)
            if root is None:
                unknown += 1
                continue
            descendants = [root]
            for node in descendants:
                descendants.extend(document.node(child) for child in node.children)
            decoded = 0
            for node in descendants:
                property_name = "pinmux" if family == "stm32" else "psels"
                if not any(prop.name == property_name for prop in node.properties):
                    continue
                values = _cells(document, node, property_name)
                if values is None:
                    unknown += 1
                    continue
                for encoded in values:
                    if family == "stm32":
                        candidates = encodings.get(encoded, [])
                        matching = [item for item in candidates if item[1] in owner.get("labels", [])]
                        candidates = matching or candidates
                        if candidates:
                            pin = candidates[0][0]
                            signal = " / ".join(dict.fromkeys(item[2] for item in candidates))
                        else:
                            unknown += 1
                            continue
                    else:
                        number, role = encoded & 511, encoded >> 24
                        if number == 511:
                            decoded += 1
                            continue
                        pin = pins.get((number // 32, number % 32))
                        signal = encodings.get(role)
                        if pin is None or signal is None or encoded & 0x00fffe00:
                            unknown += 1
                            continue
                    row = rows.get("hardware:" + document._path(node), {})
                    source = next(iter(row.get("source_locations", [])), None)
                    _append_assignment(pin, _assignment(owner, signal, state.get("name", "default"), source))
                    decoded += 1
            if not decoded:
                unknown += 1
    return unknown


def observe_chip(sources, topology, document, *, target=None):
    """Project guarded source observations into a deliberately read-only pin view."""
    target = target or getattr(sources, "context", {}).get("target") or {}
    requested = target.get("requested", "") if isinstance(target, dict) else str(target)
    result = {"schema_version": 1, "supported": False, "reason": "", "chip": {
        "name": requested or "Selected chip", "soc": None, "vendor": None, "package": None, "layout": "logical"},
        "pins": [], "diagnostics": [SCOPE], "sources": [], "coverage": "unsupported", "editing_available": False}
    try:
        stm32 = any(_STM32_TABLE.fullmatch(Path(path).name) for path in sources.files)
        nordic = any("nordic,nrf-gpio" in row.get("compatible", []) for row in topology.get("gpio_providers", []))
        if stm32 and nordic:
            raise ValueError("The selected sources contain ambiguous chip families.")
        if stm32:
            chip, pins, encodings, evidence, family = _stm32(sources)
        elif nordic or "nrf-pinctrl.h" in {Path(path).name for path in sources.files}:
            chip, pins, encodings, evidence, family = _nordic(sources, topology, document)
        else:
            raise ValueError("A verified chip/package pin catalogue is not available for this selected source profile yet.")
        if len(pins) > MAX_PINS:
            raise ValueError("The selected pin inventory exceeds the display limit.")
        _annotate_gpio(topology, document, pins, family)
        unknown = _assignments(topology, document, pins, encodings, family)
        ordered = [pins[key] for key in sorted(pins)]
        for pin in ordered:
            pin["restrictions"] = list(dict.fromkeys(pin["restrictions"]))
            pin["status"] = "reserved" if pin["restrictions"] else "configured" if pin["assignments"] else "unconfigured"
            pin["functions"] = list({(item["controller"], item["signal"]): item for item in pin["functions"]}.values())
        result.update(supported=True, chip=chip, pins=ordered, sources=evidence,
                      coverage="partial-assignments" if unknown else "source-profile", unknown_assignments=unknown)
        if unknown:
            result["diagnostics"].append(f"{unknown} pin state entries could not be decoded; their assignments remain unknown.")
        if family == "nordic":
            result["diagnostics"].append("Nordic logical GPIO slots come from the selected DTS and binding counts, including declared reserved ranges. Physical package pads and undeclared board wiring are not established. Activation checks routing and source ownership separately.")
    except (ValueError, KeyError, TypeError) as exc:
        result["reason"] = str(exc)
        result["diagnostics"].append(str(exc))
    return result
