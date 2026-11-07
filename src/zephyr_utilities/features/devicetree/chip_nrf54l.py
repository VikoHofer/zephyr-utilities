"""Bounded nRF54L GPIO-domain and serial routing rules from selected source evidence.

The common 05/10/15 architecture supplies inventories through DTS. Register identity,
not board or peripheral labels, selects its routing domain. Other nRF54 architectures
must establish their own routing rules before using this adapter.
"""

from pathlib import Path
import re

from .binding_service import _literal
from .chip_catalog import _cells, _nordic_ports


_ARCHITECTURE = "nrf54l_05_10_15.dtsi"
# Register domains are verified against Zephyr's selected common architecture.
# Clock and dedicated signal roles follow Nordic's nRF54L15 pin assignment tables:
# https://docs.nordicsemi.com/r/bundle/ps_nrf54l15/page/chapters/pin.html
_GPIO = {0: (0x10a000, 7), 1: (0xd8200, 17), 2: (0x50400, 11)}
_SERIAL = {0x4a000: 2, 0xc6000: 1, 0xc7000: 1, 0xc8000: 1, 0x104000: 0}
_CLOCK = {0: {3, 4}, 1: {3, 4, 8, 11, 12}, 2: {1, 6}}
_SPI00 = {"sck": {1, 6}, "mosi": {2, 8}, "miso": {4, 9}}
_UART00 = {"tx": {2, 8}, "rx": {0, 7}, "rts": {5, 10}, "cts": {4, 9}}
_COMPATIBLE = {"uart": "nordic,nrf-uarte", "i2c": "nordic,nrf-twim", "spi": "nordic,nrf-spim"}


def selected(sources, metadata):
    """Recognize one shared source architecture, without a chip inventory list."""
    return (re.fullmatch(r"nrf54l\d+", metadata.get("soc", "")) is not None
            and any(Path(path).name == _ARCHITECTURE for path in sources.files))


def _bus(document, node):
    parent = document.node(node.parent_id) if node.parent_id else None
    if (parent is None or _cells(document, parent, "#address-cells") != [1]
            or _cells(document, parent, "#size-cells") != [1]
            or _cells(document, parent, "reg") != [0x50000000, 0x10000000]
            or _cells(document, parent, "ranges") != [0, 0x50000000, 0x10000000]):
        raise ValueError("nRF54L activation needs the observed secure peripheral address space; cross-domain or translated register layouts remain unavailable.")
    ancestor = document.node(parent.parent_id) if parent.parent_id else None
    while ancestor is not None:
        if (_cells(document, ancestor, "#address-cells") != [1]
                or _cells(document, ancestor, "#size-cells") != [1]):
            raise ValueError("The nRF54L peripheral ancestor address space is not understood.")
        if ancestor.parent_id:
            ranges = _literal(document, ancestor, "ranges")
            if ranges is None or ranges.kind != "boolean":
                raise ValueError("Translated nRF54L ancestor address spaces need explicit routing support before activation.")
        ancestor = document.node(ancestor.parent_id) if ancestor.parent_id else None
    return document._path(parent)


def validate(sources, topology, document, metadata):
    """Require current core, architecture and GPIO register evidence together."""
    target = (getattr(sources, "context", {}).get("target") or {}).get("requested", "")
    if not selected(sources, metadata) or "cpuapp" not in target.split("/"):
        raise ValueError("nRF54L dedicated/cross-domain routing requires a selected secure CPUAPP target and the understood common architecture sources.")
    buses = set()
    for port in _nordic_ports(topology, document):
        expected = _GPIO.get(port["port"])
        node = document.find_node(port["provider"]["path"])
        if expected is None or port["count"] > expected[1] or _cells(document, node, "reg") != [expected[0], 0x300]:
            raise ValueError("The nRF54L GPIO controller layout differs from the verified architecture; pin activation is unavailable.")
        buses.add(_bus(document, node))
    if len(buses) != 1:
        raise ValueError("nRF54L GPIO controllers need one unambiguous secure peripheral address space.")
    if any("nordic,nrf-vpr-coprocessor" in row.get("compatible", []) and row.get("status") != "disabled"
           for row in topology.get("nodes", [])):
        raise ValueError("An active nRF54L FLPR coprocessor needs verified GPIO ownership before pin activation; its firmware can control pins outside this DTS observation.")


def controller_candidates(topology, document, row, kind, chip):
    """Return legal per-signal pins on the controller's native domain only."""
    if _COMPATIBLE[kind] not in row.get("compatible", []):
        raise ValueError("This nRF54L controller does not use the verified EasyDMA serial peripheral format.")
    node = document.find_node(row["path"])
    bus = _bus(document, node)
    reg = _cells(document, node, "reg")
    port = _SERIAL.get(reg[0]) if reg and len(reg) == 2 and reg[1] == 0x1000 else None
    if port is None or kind == "i2c" and port == 2:
        raise ValueError("This nRF54L peripheral instance has no verified native GPIO routing domain.")
    if kind == "spi" and port == 2:
        for child_id in node.children:
            child = document.node(child_id)
            status = _literal(document, child, "status")
            if status is not None and status.kind == "string" and status.value == "disabled":
                continue
            frequency = _cells(document, child, "spi-max-frequency")
            if frequency is None or len(frequency) != 1 or frequency[0] > 8_000_000:
                raise ValueError("SPI00 devices above 8 MHz or with unknown frequency need explicit high-speed drive validation before pin rerouting.")
    providers = [item for item in _nordic_ports(topology, document) if item["port"] == port]
    if len(providers) != 1 or _bus(document, document.find_node(providers[0]["provider"]["path"])) != bus:
        raise ValueError("The nRF54L peripheral's native GPIO provider is unavailable in this address space.")
    default = _literal(document, node, "default-gpio-port")
    if default is not None:
        if default.kind != "phandles" or len(default.value) != 1:
            raise ValueError("The nRF54L default GPIO port is not an understood reference.")
        if document._path(document.find_node(default.value[0])) != providers[0]["provider"]["path"]:
            raise ValueError("The nRF54L default GPIO port conflicts with its hardware routing domain.")
    pins = {pin["id"] for pin in chip["pins"] if pin["port"] == port}
    roles = {"uart": ("tx", "rx", "rts", "cts"), "i2c": ("scl", "sda"), "spi": ("sck", "mosi", "miso")}[kind]
    result = {role: set(pins) for role in roles}
    if kind in {"i2c", "spi"}:
        clock = "scl" if kind == "i2c" else "sck"
        result[clock] &= {f"P{port}.{index:02d}" for index in _CLOCK[port]}
    if port == 2:
        for signal, indices in (_SPI00 if kind == "spi" else _UART00).items():
            result[signal] &= {f"P2.{index:02d}" for index in indices}
    return result, [f"nRF54L routes stay on native GPIO port P{port}; cross-domain routing is unavailable.",
                    *(["Clock signals use only verified clock-capable pins."] if kind in {"i2c", "spi"} else []),
                    *(["Serial00 uses dedicated signal pin choices."] if port == 2 else []),
                    *(["SPI00 drive validation is limited to devices at 8 MHz or below; higher-speed routes remain unavailable."]
                      if kind == "spi" and port == 2 else [])]
