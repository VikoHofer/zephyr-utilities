"""Versioned, read-only nRF52840 DK evidence from a selected local checkout.

The first adapter accepts one observed source profile, not every Nordic release.
Hashes establish the bounded inherited baseline; displayed mappings are then read
from those actual files. No preprocessor, CMake, build or hardware tool is run.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re

from ...core.source_files import WorkspaceSourceReader
from .bindings import BindingCatalog
from .source import SourceDocument


TARGET = "nrf52840dk/nrf52840"
PROFILE = "nrf52840dk-zephyr-4.4.2-v1"
BOARD = "boards/nordic/nrf52840dk/nrf52840dk_nrf52840.dts"
SOC = "dts/arm/nordic/nrf52840.dtsi"
PINCTRL = "boards/nordic/nrf52840dk/nrf52840dk_nrf52840-pinctrl.dtsi"
GPIO_BINDING = "dts/bindings/gpio/gpio-controller.yaml"
NRF_GPIO_BINDING = "dts/bindings/gpio/nordic,nrf-gpio.yaml"
PSEL_BINDING = "dts/bindings/pinctrl/nordic,nrf-pinctrl.yaml"
BASE_BINDING = "dts/bindings/base/base.yaml"
PM_BINDING = "dts/bindings/base/pm.yaml"
PINCFG_BINDING = "dts/bindings/pinctrl/pincfg-node.yaml"
GPIO_HEADER = "include/zephyr/dt-bindings/gpio/gpio.h"
PSEL_HEADER = "include/zephyr/dt-bindings/pinctrl/nrf-pinctrl.h"
GPIO_FLAGS = (
    "GPIO_ACTIVE_HIGH", "GPIO_ACTIVE_LOW", "GPIO_PUSH_PULL", "GPIO_OPEN_DRAIN",
    "GPIO_OPEN_SOURCE", "GPIO_PULL_UP", "GPIO_PULL_DOWN", "GPIO_INT_WAKEUP",
)
ROLES = {"uart": ["UART_TX", "UART_RX", "UART_RTS", "UART_CTS"],
         "i2c": ["TWIM_SDA", "TWIM_SCL"], "spi": ["SPIM_SCK", "SPIM_MOSI", "SPIM_MISO"],
         "pwm": ["PWM_OUT0", "PWM_OUT1", "PWM_OUT2", "PWM_OUT3"],
         "qspi": ["QSPI_SCK", "QSPI_CSN", "QSPI_IO0", "QSPI_IO1", "QSPI_IO2", "QSPI_IO3"]}

# Observed in Zephyr commit fbab2f598d545ad043ce50a75fefa4550b053f0b (4.4.2).
# The complete board DTS include closure, pin encoders, GPIO binding and pinctrl
# implementation are fingerprints only; upstream sources are not bundled.
PROFILE_HASHES = {
    "dts/bindings/base/base.yaml": "cf934d2d8961561aca9c3741b18ce09cf504ae2a1926442e6866e67843677090",
    "dts/bindings/base/pm.yaml": "c151c952cf94477b45d7e761b52d144fd25268eadd70185336a72fe4200462b0",
    "dts/bindings/pinctrl/pincfg-node.yaml": "118f73af6e1aa2dde6ca320ea3bf15766c7174f17a33b40ec280e3c9976a349b",
    "cmake/modules/pre_dt.cmake": "8c796a8d0bbdbfa064c36c7133c09e62b9abcb82e7085037fe8934311dec1abf",
    "boards/nordic/nrf52840dk/board.yml": "1f5d943fb37356054ae1cadd8f5d7bb41bed38c4f4ca612d8103d93835b2edf3",
    "boards/nordic/nrf52840dk/nrf52840dk_nrf52840-pinctrl.dtsi": "ef464f13a41610f87a3d21444e9f717be155b8a7d899f09f18dd50338882276a",
    "boards/nordic/nrf52840dk/nrf52840dk_nrf52840.dts": "3fea9d8202f5136380c8cb8270606cd6a4d4a839c1904934b07c535e1fdd43af",
    "boards/nordic/nrf52840dk/nrf52840dk_nrf52840.yaml": "4d21d9cf3ca934335e58d583bd6ed15586b135a0a49efd2da6c845df4a2a89d8",
    "drivers/pinctrl/pinctrl_nrf.c": "e0e786026ba48b67d600902bf4a9ed1aa1fdfb5603ec8ff939e3ec5352faa13f",
    "dts/arm/armv7-m.dtsi": "587d5610ee4f6e15f3e3c7d1f21bb4435c237b2b6cc7fa43dde383982b02247b",
    "dts/arm/nordic/nrf52840.dtsi": "d411d98e53103df02335accc2fc9bf5567cdd35bb035095b1d33e5c994ac26d3",
    "dts/arm/nordic/nrf52840_qiaa.dtsi": "d68a576f02486a7a9c71b1fc65b4138670c71003b81847a3825d327ffa5cc62f",
    "dts/arm/nordic/override.dtsi": "dc6eab2c8ec499092d7b1dae05f426341b57354f0550a3b13f9548cdfa42abfc",
    "dts/bindings/gpio/gpio-controller.yaml": "7df3e307d854a1d4e95562855925150f9be096b04111a96c553c2101febec363",
    "dts/bindings/gpio/nordic,nrf-gpio.yaml": "905de492c65a88b7eee663b19105d91c08be3ff1172a3b98c423885e3a3dab6d",
    "dts/bindings/pinctrl/nordic,nrf-pinctrl.yaml": "e7b0d7fc08e7f6cc21b35a4451659d24188e8d1387fdeaac92842d2dbadb40cb",
    "dts/common/freq.h": "d467a79747d451af6bc3d4d9847d251014b6c29de41e366d684cfc64448a6320",
    "dts/common/mem.h": "821f919790b2fc301b93d9b29b9b2d8545f51f38620df29e1466b170a4fc473c",
    "dts/common/skeleton.dtsi": "60951eca031682c1211477c7985379299ef60c8411ab651b7e1adc970352cce6",
    "dts/vendor/nordic/nrf52840_partition.dtsi": "651313a9e42e17061c5b6e1379cb419eddd2af7e1d3a465965ffc9cd74beda14",
    "dts/vendor/nordic/nrf_common.dtsi": "bf3941610d914c654c330cceff8e22348c0539d8daed07c0ff3b5da5c16d6285",
    "include/zephyr/dt-bindings/adc/adc.h": "f468cca862b6eee105ceaf0b19f6910a9b6c03ec46a26a99cafc40f0587a3d26",
    "include/zephyr/dt-bindings/adc/nrf-saadc.h": "fcb156f2258b80601e33fd12ce08f732f66ffed3e7a9ed296b7daaa17dc8641b",
    "include/zephyr/dt-bindings/dt-util.h": "3e790d0a9d34046831ffb410fb92887ce01c79ffb05a7e6be42017f60f0a38db",
    "include/zephyr/dt-bindings/gpio/arduino-header-r3.h": "47e33c6270fffd4fc419c504c5c0c045a1abfdfbf71c42475104578b3f4b8445",
    "include/zephyr/dt-bindings/gpio/gpio.h": "5c139f8079c86558733696564752064b70625126c48eabafb85f45426dde52be",
    "include/zephyr/dt-bindings/gpio/nordic-nrf-gpio.h": "d940e573c6a30593fdbc0c4ac4d0a7f6ac6b5589428dcbd922a9501b7ab59732",
    "include/zephyr/dt-bindings/i2c/i2c.h": "6584ab84efca212223d5403d4c41dbca74c2f2b61d6c1e21054a498d6701d1b3",
    "include/zephyr/dt-bindings/input/input-event-codes.h": "a44d582f9dc074cdaa776645076adfa5a679b51ffb79e85d5b14b28d231755e8",
    "include/zephyr/dt-bindings/pinctrl/nrf-pinctrl.h": "7606c7a0b1596b515ffec702cf07ca328e7a2bc7e4cd1f549b8d1934bfa53a22",
    "include/zephyr/dt-bindings/pwm/pwm.h": "acca8309491f6e062467cc5c6e96957d6aa5d1f1e7d40018b9fe88f8b79f8b2a",
    "include/zephyr/dt-bindings/regulator/nrf5x.h": "41a081a72fa06931d25dda9b7b1c05e0005f3e9dc64af072a4c3cf153da42173",
    "include/zephyr/dt-bindings/sensor/qdec_nrf.h": "7b7b7ec5d7ab42894f3f098949edc205c1c46e104f570d00bf63b910949613ae",
    "include/zephyr/sys/util_internal.h": "c9f3c6c0e9b05fc5f1ca6666aba7d9499f00aa08881a58718ff88159f90fbb6e",
    "include/zephyr/sys/util_internal_is_eq.h": "007622210ea3fc07b6e45ffdbb048a4818bf1a51ecae4d3ce22f43b2e97fdb79",
    "include/zephyr/sys/util_internal_util_dec.h": "088a96ac49587c2cc569be1c69a5b8c273ed02bbf8d11b4b1d5c1ccf5f45b412",
    "include/zephyr/sys/util_internal_util_inc.h": "63b79157a40115209325a69e8c22297ef69573fb9aea2600112017cb49743048",
    "include/zephyr/sys/util_internal_util_x2.h": "bec1a1f556693ed236291a53967f1037498e0a890b834f564576ad256327d332",
    "include/zephyr/sys/util_listify.h": "7a2dd9ffe655182c6c815244a71b2ed58705f165340c52a5319de851987c037a",
    "include/zephyr/sys/util_loops.h": "9c5c9d5c4bf65c9390ea26b836123c0801057ab4990481a3d73538ee2f4b8e97",
    "include/zephyr/sys/util_macro.h": "35993d1760a770f68ec2f6d918f017d6247a05d10f57ddd06900ebb4ae815a36"
}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _property(document, node, name):
    matches = [prop for prop in node.properties if prop.name == name]
    if len(matches) > 1:
        raise ValueError(f"The supported board evidence repeats {name}.")
    return matches[0] if matches else None


def _raw(document, node, name):
    prop = _property(document, node, name)
    if prop is None:
        return None
    if prop.value_start is None:
        return ""
    return document.source[prop.value_start:prop.value_end].decode()


def _literal(document, node, name, default=None):
    prop = _property(document, node, name)
    if prop is None:
        return default
    value = document.property_value(prop.id)
    if value is None:
        raise ValueError(f"The supported board evidence needs literal {name}.")
    return value.value


def _integers(value):
    if value is None:
        return []
    stripped = re.sub(r"[<>,\s]", "", value)
    tokens = re.findall(r"0[xX][0-9a-fA-F]+|[0-9]+", value)
    if stripped != "".join(tokens):
        raise ValueError("The supported board evidence needs literal GPIO cells.")
    return [int(token, 0) if token.startswith(("0x", "0X")) else int(token) for token in tokens]


class NordicEvidence:
    """An immutable observation with explicit unsupported state and revalidation."""

    def __init__(self, context, *, catalog=None):
        self.context = deepcopy(context)
        self.supported, self.reason = False, ""
        self.profile = PROFILE
        self.controllers, self.pins, self.gpio_uses, self.reservations = [], [], [], []
        self.gpio_flags, self.files, self.diagnostics = [], [], []
        self.inherited_states, self.gpio_nodes = {}, {}
        self.catalog = catalog
        self.fingerprint = ""
        self._texts, self._absent = {}, []
        try:
            self._observe()
            self._extract()
            self.supported = True
            self.diagnostics.append(
                "Pins describe the verified local nRF52840 DK profile and this overlay. "
                "Package pads, solder bridges, external wiring and electrical suitability remain unknown.")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.reason = str(exc)
            self.diagnostics.append(self.reason)
            self.controllers, self.pins, self.gpio_uses, self.reservations = [], [], [], []
            self.inherited_states, self.gpio_nodes = {}, {}
        self.fingerprint = _fingerprint({"profile": PROFILE, "supported": self.supported,
                                         "files": self.files, "absent": self._absent,
                                         "bindings": self.catalog.fingerprint if self.catalog else None})

    def _observe(self):
        context = self.context
        if (context.get("target") or {}).get("requested") != TARGET:
            raise ValueError(f"Nordic pin forms currently support the exact target {TARGET} only.")
        environment = context.get("environment") or {}
        zephyr = (environment.get("zephyr") or {}).get("path")
        if not zephyr:
            raise ValueError("Select a project configuration with a local Zephyr checkout before loading Nordic pins.")
        self.reader = WorkspaceSourceReader(context["workspace"])
        self.zephyr = self.reader.path(zephyr)
        self.application = self.reader.path(context["application"]["path"])
        expected_board = hashlib.sha256(f"{self.zephyr}/boards/nordic/nrf52840dk/board.yml:nrf52840dk".encode()).hexdigest()[:20]
        board_id = context["target"].get("board_id")
        if board_id != expected_board:
            raise ValueError("Select the official nRF52840 DK board from the selected local checkout.")
        roots = context.get("roots") or []
        if any(item.get("kind") in {"board", "boards", "soc", "socs"} and
               item.get("path") != str(self.zephyr) for item in roots):
            raise ValueError("This Nordic profile does not support custom board or SoC roots.")
        selected = (context.get("fragments") or {}).get("overlays") or {}
        if len(selected.get("files") or []) > 1:
            raise ValueError("Nordic pin forms require one selected application overlay.")
        for observation in (context.get("input_evidence") or {}).get("predicted", []):
            active = [item for item in observation.get("files", []) if item.get("state") in {"active", "expected", "selected"}]
            if len(active) > 1:
                raise ValueError("Nordic pin forms do not combine multiple predicted application overlays.")
        if any(context.get(key) for key in ("shields", "snippets")):
            raise ValueError("Nordic pin forms do not resolve shields or snippets.")
        for relative, expected in PROFILE_HASHES.items():
            observed = self.reader.read_text(self.zephyr / relative, limit=2 * 1024 * 1024)
            self.files.append({"path": relative, "sha256": observed["sha256"]})
            if observed["sha256"] != expected:
                raise ValueError(f"The local file {relative} is outside supported Nordic profile {PROFILE}; use source editing.")
            self._texts[relative] = observed["text"]
        if self.reader.bytes_read > 4 * 1024 * 1024:
            raise ValueError("Nordic pin evidence exceeds the 4 MiB observation limit.")
        self._check_shadows(roots)
        self._check_bindings(roots)

    def _check_bindings(self, roots):
        directories = list(dict.fromkeys([str(self.application / "dts/bindings"),
                                           str(self.zephyr / "dts/bindings"),
                                           *[str(self.reader.path(item["path"]) / "dts/bindings")
                                             for item in roots if item.get("kind") == "dts"]]))
        if self.catalog is not None and self.catalog.workspace == self.context["workspace"] and self.catalog.roots == directories:
            self.catalog.revalidate()
        else:
            self.catalog = BindingCatalog(self.context["workspace"], directories)
        expected = {"nordic,nrf-gpio": {NRF_GPIO_BINDING, GPIO_BINDING, BASE_BINDING, PM_BINDING},
                    "nordic,nrf-pinctrl": {PSEL_BINDING, BASE_BINDING, PM_BINDING, PINCFG_BINDING}}
        for compatible, dependencies in expected.items():
            resolved = self.catalog.match([compatible])
            if resolved["state"] != "matched":
                raise ValueError(f"The selected {compatible} binding is not uniquely resolved: " +
                                 "; ".join(resolved["diagnostics"]))
            actual = {entry["path"]: entry["sha256"] for entry in resolved["dependencies"]}
            wanted = {str(self.zephyr / name): PROFILE_HASHES[name] for name in dependencies}
            if actual != wanted:
                raise ValueError(f"The selected {compatible} binding dependencies are outside the supported Nordic profile.")

    def _check_shadows(self, roots):
        # Absence is observed as well as presence: adding an override after a form
        # was opened must invalidate its commands, even when baseline hashes match.
        include_names, local_candidates = set(), set()
        for relative, content in self._texts.items():
            if Path(relative).suffix not in {".h", ".dts", ".dtsi"}:
                continue
            for opening, name in re.findall(r'^\s*#\s*include\s*([<"])([^>"]+)[>"]', content, re.M):
                include_names.add(name)
                if opening == '"':
                    local_candidates.add(self.zephyr / Path(relative).parent / name)
        prefixes = ("include", "include/zephyr", "dts/common", "dts/vendor", "dts/arm", "dts")
        candidates = {str(Path(prefix) / name) for prefix in prefixes for name in include_names}
        candidates.update(relative for relative in PROFILE_HASHES if relative.startswith("dts/bindings/"))
        search_roots = [self.application, self.zephyr / Path(BOARD).parent, self.zephyr,
                        *[self.reader.path(item["path"]) for item in roots
                          if item.get("kind") == "dts" and item.get("path") != str(self.zephyr)]]
        paths = set(local_candidates)
        for root in dict.fromkeys(search_roots):
            self.reader.directory_identity(root)
            paths.update(root / relative for relative in candidates)
        for path in sorted(paths):
            try:
                path.lstat()
            except FileNotFoundError:
                self._absent.append(str(path))
                continue
            if path.is_relative_to(self.zephyr) and str(path.relative_to(self.zephyr)) in PROFILE_HASHES:
                continue
            raise ValueError(f"A local include or binding shadows the supported Nordic profile: {path}")

    def _extract(self):
        board = SourceDocument.parse(self._texts[BOARD].encode())
        soc = SourceDocument.parse(self._texts[SOC].encode())
        pinctrl = SourceDocument.parse(self._texts[PINCTRL].encode())
        if any(document.syntax.blocked for document in (board, soc, pinctrl)):
            raise ValueError("The supported Nordic evidence has an unsupported source structure.")
        for node in pinctrl.syntax.nodes:
            if node.parent_id and pinctrl.node(node.parent_id).name == "&pinctrl":
                if len(node.labels) != 1:
                    raise ValueError("A baseline pinctrl state needs exactly one label.")
                self.inherited_states[node.labels[0]] = pinctrl.source[node.start:node.end]
        for label in ("uart0", "uart1", "i2c0", "i2c1", "spi0", "spi1", "spi2", "spi3", "pwm0", "qspi"):
            node = soc.find_node("&" + label)
            update = board.find_node("&" + label)
            kind = re.sub(r"\d+$", "", label)
            states = {}
            names = _literal(board, update, "pinctrl-names", ())
            if isinstance(names, str):
                names = (names,)
            for index, name in enumerate(names):
                raw = _raw(board, update, "pinctrl-" + str(index))
                if name not in {"default", "sleep"} or raw is None or not re.fullmatch(r"<\s*&\w+(?:\s+&\w+)*\s*>", raw):
                    raise ValueError("The baseline pinctrl states need supported direct references.")
                states[name] = re.findall(r"&([A-Za-z_]\w*)", raw)
                if any(reference not in self.inherited_states for reference in states[name]):
                    raise ValueError("A baseline pinctrl state reference is unresolved.")
            roles = ROLES[kind]
            for role in roles:
                if not re.search(r"^#define\s+NRF_FUN_" + role + r"\s+", self._texts[PSEL_HEADER], re.M):
                    raise ValueError("The pin encoding header does not define an expected signal role.")
            reg = _integers(_raw(soc, node, "reg"))
            self.controllers.append({"id": label, "kind": kind, "compatible": _literal(board, update, "compatible", _literal(soc, node, "compatible")),
                                     "status": _literal(board, update, "status", _literal(soc, node, "status", "okay")),
                                     "instance": hex(reg[0]), "path": soc._path(node), "states": states,
                                     "roles": list(roles), "editable": kind in {"uart", "i2c", "spi"},
                                     "aliases": list(update.labels)})
        connectors = {}
        connector = board.find_node("&arduino_header")
        mapping = _raw(board, connector, "gpio-map") or ""
        for name, port, pin in re.findall(r"<ARDUINO_HEADER_R3_([AD]\d+)\s+0\s+&gpio([01])\s+(\d+)\s+0>", mapping):
            connectors[int(port), int(pin)] = "Arduino " + name
        for port in (0, 1):
            base = soc.find_node(f"&gpio{port}")
            update = board.find_node(f"&gpio{port}")
            count = _literal(soc, base, "ngpios")
            if count is None:
                match = re.search(r"\bngpios:\s*\n\s+type: int\s*\n\s+default: (\d+)", self._texts[GPIO_BINDING])
                if not match:
                    raise ValueError("GPIO slot count is unknown in this profile.")
                count = (int(match[1]),)
            count = count[0]
            if count != (32 if port == 0 else 16) or _literal(soc, base, "#gpio-cells") != (2,):
                raise ValueError("The selected GPIO provider layout is not supported.")
            names = _literal(board, update, "gpio-line-names", ())
            ranges = _integers(_raw(board, update, "gpio-reserved-ranges"))
            if len(ranges) % 2:
                raise ValueError("The board GPIO reservations need start/count pairs.")
            reserved = {pin for start, size in zip(ranges[::2], ranges[1::2]) for pin in range(start, start + size)}
            for pin in range(count):
                name = f"P{port}.{pin:02d}"
                line_name = names[pin] if pin < len(names) else ""
                reason = f"Board GPIO reservation: {line_name or name}." if pin in reserved else ""
                self.pins.append({"port": port, "pin": pin, "name": name, "line_name": line_name,
                                  "connector": connectors.get((port, pin), ""), "reserved": pin in reserved,
                                  "reason": reason, "uses": []})
                if pin in reserved:
                    owner = "uart0" if port == 0 and pin in {6, 8} else "qspi" if port == 0 and pin in {17, 19, 20, 21, 22, 23} else "board"
                    self.reservations.append({"port": port, "pin": pin, "owner": owner, "reason": reason})
        for node in board.syntax.nodes:
            raw = _raw(board, node, "gpios")
            if raw is None:
                continue
            match = re.fullmatch(r"<\s*&gpio([01])\s+(\d+)\s+(.+)>", raw, re.S)
            if not match:
                raise ValueError("The baseline board has an unsupported GPIO consumer.")
            owner = node.labels[0] if node.labels else board._path(node)
            self.gpio_nodes[owner] = board.source[node.start:node.end]
            enabled = _literal(board, node, "status", "okay") == "okay"
            parent = board.node(node.parent_id) if node.parent_id else None
            if parent:
                enabled = enabled and _literal(board, parent, "status", "okay") == "okay"
            self.gpio_uses.append({"owner": owner, "label": _literal(board, node, "label", owner),
                                   "path": board._path(node), "port": int(match[1]), "pin": int(match[2]),
                                   "flags": match[3].strip(), "enabled": enabled, "property": "gpios", "index": 0})
        # This selected board routes SPI3 CS through its Arduino GPIO nexus.
        spi = board.find_node("&spi3")
        chip_select = _raw(board, spi, "cs-gpios")
        match = re.fullmatch(r"<\s*&arduino_header\s+(\d+)\s+(.+)>", chip_select or "", re.S)
        if match:
            header = self._texts["include/zephyr/dt-bindings/gpio/arduino-header-r3.h"]
            symbol = next((name for name, index in re.findall(r"^#define\s+ARDUINO_HEADER_R3_([AD]\d+)\s+(\d+)", header, re.M)
                           if int(index) == int(match[1])), None)
            location = next((key for key, name in connectors.items() if name == "Arduino " + str(symbol)), None)
            if location is None:
                raise ValueError("The board SPI chip-select mapping is unresolved.")
            self.gpio_uses.append({"owner": "spi3", "label": "SPI3 chip select", "path": None,
                                   "port": location[0], "pin": location[1], "flags": match[2].strip(),
                                   "enabled": _literal(board, spi, "status", "okay") == "okay",
                                   "property": "cs-gpios", "index": 0})
        elif chip_select:
            raise ValueError("The baseline SPI chip-select source is unsupported.")
        for use in self.gpio_uses:
            if use["enabled"]:
                pin = next(item for item in self.pins if (item["port"], item["pin"]) == (use["port"], use["pin"]))
                pin["uses"].append(use["label"])
        for flag in GPIO_FLAGS:
            if not re.search(r"^#define\s+" + flag + r"\s+", self._texts[GPIO_HEADER], re.M):
                raise ValueError("A supported symbolic GPIO flag is absent from the selected header.")
            self.gpio_flags.append({"name": flag, "label": flag})

    def revalidate(self):
        if not self.supported:
            return
        current = NordicEvidence(self.context, catalog=self.catalog)
        if not current.supported or current.fingerprint != self.fingerprint:
            raise ValueError("The Nordic pin evidence changed. Load pins again. " + current.reason)

