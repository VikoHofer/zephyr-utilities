"""Synthetic Nordic evidence profiles validate selection, mappings and stale reads."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree import nordic_evidence as evidence


def write_fixture(root):
    """Small synthetic board profile; no SDK, hardware or full board port required."""
    root = Path(root)
    zephyr, application = root / "zephyr", root / "app"
    application.mkdir(parents=True)
    identifiers = ("uart0", "uart1", "i2c0", "i2c1", "spi0", "spi1", "spi2", "spi3", "pwm0", "qspi")
    addresses = (0x40002000, 0x40028000, 0x40003000, 0x40004000, 0x40003000, 0x40004000,
                 0x40023000, 0x4002f000, 0x4001c000, 0x40029000)
    kinds = ("uart", "uart", "i2c", "i2c", "spi", "spi", "spi", "spi", "pwm", "qspi")
    compatibles = {"uart": "nordic,nrf-uarte", "i2c": "nordic,nrf-twi", "spi": "nordic,nrf-spim",
                   "pwm": "nordic,nrf-pwm", "qspi": "nordic,nrf-qspi"}
    soc = "/ { soc {\n"
    board = '#include <nordic/nrf52840.dtsi>\n#include <arm/nordic/override.dtsi>\n'
    states = "&pinctrl {\n"
    for label, address, kind in zip(identifiers, addresses, kinds):
        soc += f'{label}: {kind}@{address:x} {{ compatible = "{compatibles[kind]}"; reg = <0x{address:x} 0x1000>; status = "disabled"; }};\n'
        enabled = "okay" if label in {"uart0", "i2c0", "spi1", "spi3", "pwm0", "qspi"} else "disabled"
        cs = "cs-gpios = <&arduino_header 16 GPIO_ACTIVE_LOW>;" if label == "spi3" else ""
        alias = "arduino_serial: " if label == "uart1" else ""
        board += f'{alias}&{label} {{ status = "{enabled}"; pinctrl-0 = <&{label}_default>; pinctrl-1 = <&{label}_sleep>; pinctrl-names = "default", "sleep"; {cs} }};\n'
        for state in ("default", "sleep"):
            low_power = "low-power-enable;" if state == "sleep" else ""
            states += f'{label}_{state}: {label}_{state} {{ group1 {{ psels = <NRF_PSEL({evidence.ROLES[kind][0]}, 0, 3)>; {low_power} }}; }};\n'
    soc += 'gpio0: gpio@50000000 { #gpio-cells = <2>; port = <0>; };\n'
    soc += 'gpio1: gpio@50000300 { #gpio-cells = <2>; port = <1>; ngpios = <16>; };\n}; };\n'
    board += '''&gpio0 {
    gpio-reserved-ranges = <0 2>, <6 1>, <8 3>, <17 7>;
    gpio-line-names = "XL1", "XL2", "AREF", "A0", "A1", "RTS", "TXD", "CTS", "RXD", "NFC1", "NFC2", "BUTTON1", "BUTTON2", "LED1";
};
&gpio1 { gpio-line-names = "", "D0", "D1"; };
/ {
    arduino_header: connector { gpio-map = <ARDUINO_HEADER_R3_A0 0 &gpio0 3 0>, <ARDUINO_HEADER_R3_D10 0 &gpio1 12 0>; };
    leds { led0: status { gpios = <&gpio0 13 GPIO_ACTIVE_LOW>; label = "Status LED"; }; };
    buttons { button0: user { gpios = <&gpio0 11 (GPIO_ACTIVE_LOW | GPIO_PULL_UP)>; }; };
};
'''
    files = {evidence.BOARD: board, evidence.SOC: soc, evidence.PINCTRL: states + "};\n",
             evidence.GPIO_BINDING: "properties:\n  ngpios:\n    type: int\n    default: 32\n",
             evidence.NRF_GPIO_BINDING: 'compatible: nordic,nrf-gpio\ninclude: [gpio-controller.yaml, base.yaml]\ngpio-cells: [pin, flags]\n',
             evidence.PSEL_BINDING: 'compatible: nordic,nrf-pinctrl\ninclude: base.yaml\nchild-binding:\n  child-binding:\n    include: pincfg-node.yaml\n    properties:\n      psels: {type: array, required: true}\n',
             evidence.BASE_BINDING: 'include: pm.yaml\nproperties:\n  compatible: {type: string-array}\n',
             evidence.PM_BINDING: 'properties:\n  wakeup-source: {type: boolean}\n',
             evidence.PINCFG_BINDING: 'properties:\n  low-power-enable: {type: boolean}\n',
             evidence.GPIO_HEADER: "\n".join(f"#define {flag} 0" for flag in evidence.GPIO_FLAGS),
             evidence.PSEL_HEADER: "\n".join(f"#define NRF_FUN_{role} {index}U" for index, role in enumerate(
                 role for roles in evidence.ROLES.values() for role in roles)),
             "include/zephyr/dt-bindings/gpio/arduino-header-r3.h": "#define ARDUINO_HEADER_R3_A0 0\n#define ARDUINO_HEADER_R3_D10 16\n",
             "dts/arm/nordic/override.dtsi": "/* Synthetic empty override. */\n"}
    hashes = {}
    for relative, text in files.items():
        path = zephyr / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        hashes[relative] = hashlib.sha256(text.encode()).hexdigest()
    board_id = hashlib.sha256(f"{zephyr}/boards/nordic/nrf52840dk/board.yml:nrf52840dk".encode()).hexdigest()[:20]
    context = {"workspace": str(root), "application": {"path": str(application)},
               "target": {"requested": evidence.TARGET, "board_id": board_id},
               "environment": {"zephyr": {"path": str(zephyr)}}, "roots": []}
    return context, hashes


class NordicEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.context, self.hashes = write_fixture(self.root)
        patcher = patch.object(evidence, "PROFILE_HASHES", self.hashes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def observe(self):
        result = evidence.NordicEvidence(self.context)
        self.assertTrue(result.supported, result.reason)
        return result

    def test_local_mapping_and_controller_instance_are_observed_without_execution(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            result = self.observe()
        self.assertEqual(len(result.pins), 48)
        controllers = {item["id"]: item for item in result.controllers}
        self.assertEqual(controllers["i2c0"]["instance"], controllers["spi0"]["instance"])
        self.assertEqual(controllers["i2c1"]["instance"], controllers["spi1"]["instance"])
        self.assertEqual(controllers["uart1"]["aliases"], ["arduino_serial"])
        self.assertEqual(controllers["uart0"]["status"], "okay")
        self.assertEqual(controllers["uart1"]["status"], "disabled")
        self.assertFalse(controllers["qspi"]["editable"])
        self.assertEqual(controllers["uart1"]["states"]["sleep"], ["uart1_sleep"])
        self.assertIn(b"low-power-enable;", result.inherited_states["uart1_sleep"])
        pin = next(item for item in result.pins if (item["port"], item["pin"]) == (0, 3))
        self.assertEqual(pin["connector"], "Arduino A0")
        self.assertEqual(pin["line_name"], "A0")
        self.assertFalse(pin["reserved"])

    def test_reservations_and_gpio_consumers_retain_owners_and_flags(self):
        result = self.observe()
        reservations = {(item["port"], item["pin"]): item for item in result.reservations}
        self.assertEqual(reservations[0, 6]["owner"], "uart0")
        self.assertEqual(reservations[0, 8]["owner"], "uart0")
        self.assertEqual(reservations[0, 19]["owner"], "qspi")
        self.assertEqual(reservations[0, 18]["owner"], "board")
        consumers = {item["owner"]: item for item in result.gpio_uses}
        self.assertEqual(consumers["spi3"]["port"], 1)
        self.assertEqual(consumers["spi3"]["pin"], 12)
        self.assertEqual(consumers["spi3"]["property"], "cs-gpios")
        self.assertEqual(consumers["spi3"]["index"], 0)
        self.assertEqual(consumers["button0"]["flags"], "(GPIO_ACTIVE_LOW | GPIO_PULL_UP)")
        self.assertTrue(consumers["led0"]["enabled"])
        self.assertEqual([item["name"] for item in result.gpio_flags], list(evidence.GPIO_FLAGS))

    def test_exact_target_and_official_board_identity_are_required(self):
        for mutation in ({"requested": "nrf52840dk/nrf52811"}, {"requested": "nrf52840dk@1.0.0/nrf52840"}, {"board_id": "other"}):
            with self.subTest(mutation=mutation):
                context = deepcopy(self.context)
                context["target"].update(mutation)
                observed = evidence.NordicEvidence(context)
                self.assertFalse(observed.supported)
                self.assertFalse(observed.pins)
                observed.revalidate()

    def test_changed_evidence_is_unsupported_and_invalidates_open_form(self):
        result = self.observe()
        path = self.root / "zephyr" / evidence.SOC
        path.write_text(path.read_text() + "/* a new profile needs review */\n")
        current = evidence.NordicEvidence(self.context)
        self.assertFalse(current.supported)
        self.assertIn("outside supported", current.reason)
        with self.assertRaisesRegex(ValueError, "changed"):
            result.revalidate()

    def test_missing_or_symlinked_evidence_does_not_publish_partial_mapping(self):
        path = self.root / "zephyr" / evidence.PINCTRL
        original = path.read_bytes()
        path.unlink()
        self.assertFalse(evidence.NordicEvidence(self.context).supported)
        outside = self.root / "copy.dtsi"
        outside.write_bytes(original)
        path.symlink_to(outside)
        result = evidence.NordicEvidence(self.context)
        self.assertFalse(result.supported)
        self.assertEqual(result.controllers, [])

    def test_custom_board_roots_and_multiple_selected_overlays_are_unsupported(self):
        for changes in ({"roots": [{"kind": "boards", "path": str(self.root)}]},
                        {"fragments": {"overlays": {"files": ["a.overlay", "b.overlay"]}}},
                        {"input_evidence": {"predicted": [{"files": [{"state": "expected"}, {"state": "selected"}]}]}},
                        {"shields": ["addon"]}, {"snippets": ["addon"]}):
            with self.subTest(changes=changes):
                self.assertFalse(evidence.NordicEvidence({**self.context, **changes}).supported)

    def test_application_override_and_alternative_include_directory_are_detected(self):
        for relative in ("dts/arm/nordic/override.dtsi", "dts/vendor/nordic/nrf52840.dtsi"):
            with self.subTest(relative=relative):
                path = self.root / "app" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("/* shadow */\n")
                result = evidence.NordicEvidence(self.context)
                self.assertFalse(result.supported)
                self.assertIn("shadows", result.reason)
                path.unlink()

    def test_module_root_is_allowed_until_it_shadows_the_profile(self):
        module = self.root / "module"
        module.mkdir()
        self.context["roots"] = [{"kind": "dts", "path": str(module)}]
        result = self.observe()
        path = module / "dts/arm/nordic/override.dtsi"
        path.parent.mkdir(parents=True)
        path.write_text("/* a new override */\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            result.revalidate()

    def test_evidence_is_detached_from_context_and_current_revalidation_is_stable(self):
        result = self.observe()
        self.context["target"]["requested"] = "other"
        result.revalidate()
        again = evidence.NordicEvidence(result.context)
        self.assertEqual(result.fingerprint, again.fingerprint)
        self.assertTrue(result.files)

    def test_malformed_supported_profile_has_no_fabricated_provider_capacity(self):
        path = self.root / "zephyr" / evidence.SOC
        changed = path.read_text().replace("ngpios = <16>", "ngpios = <99>")
        path.write_text(changed)
        self.hashes[evidence.SOC] = hashlib.sha256(changed.encode()).hexdigest()
        result = evidence.NordicEvidence(self.context)
        self.assertFalse(result.supported)
        self.assertIn("provider layout", result.reason)
        self.assertEqual(result.pins, [])

    def test_binding_duplicate_under_another_filename_is_not_assumed_equivalent(self):
        before = self.observe()
        path = self.root / "app/dts/bindings/custom/duplicate.yaml"
        path.parent.mkdir(parents=True)
        path.write_text('compatible: nordic,nrf-gpio\ninclude: base.yaml\ngpio-cells: [pin, flags]\n')
        after = evidence.NordicEvidence(self.context)
        self.assertFalse(after.supported)
        self.assertIn("not uniquely resolved", after.reason)
        with self.assertRaisesRegex(ValueError, "changed"):
            before.revalidate()

    def test_included_binding_with_same_basename_in_another_directory_is_ambiguous(self):
        path = self.root / "app/dts/bindings/custom/base.yaml"
        path.parent.mkdir(parents=True)
        path.write_text('properties: {}\n')
        after = evidence.NordicEvidence(self.context)
        self.assertFalse(after.supported)
        self.assertIn("ambiguous", after.reason)

    def test_profile_binding_include_content_changes_invalidate_metadata(self):
        before = self.observe()
        path = self.root / "zephyr" / evidence.PINCFG_BINDING
        path.write_text(path.read_text() + "  extra: {type: boolean}\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            before.revalidate()
        self.assertFalse(evidence.NordicEvidence(self.context).supported)


if __name__ == "__main__":
    unittest.main()
