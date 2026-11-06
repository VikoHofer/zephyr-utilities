"""Pin commands bind source, board evidence and resource conflicts atomically."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request


SOURCE = '''/* Keep this note and unrelated macro. */
/ { spare: spare { gpios = <&gpio1 10 GPIO_ACTIVE_LOW>; note = <HANDWRITTEN(2)>; }; };
'''


class Evidence:
    """Synthetic observed board; no SDK or upstream files required by unit tests."""

    def __init__(self, context):
        self.supported, self.reason, self.fingerprint = True, "", "fixture"
        self.diagnostics, self.files = [], []
        self.controllers = [
            {"id": "uart0", "label": "uart0", "kind": "uart", "status": "okay", "instance": "0x1000",
             "roles": ["UART_TX", "UART_RX"], "states": {"default": ["uart_default"], "sleep": ["uart_sleep"]},
             "compatible": "nordic,nrf-uarte", "editable": True, "path": "/soc/uart@1000", "aliases": []},
            {"id": "i2c0", "label": "i2c0", "kind": "i2c", "status": "okay", "instance": "0x2000",
             "roles": ["TWIM_SCL", "TWIM_SDA"], "states": {"default": [], "sleep": []}, "compatible": "nordic,nrf-twim", "editable": True,
             "path": "/soc/i2c@2000", "aliases": []},
            {"id": "spi0", "label": "spi0", "kind": "spi", "status": "disabled", "instance": "0x2000",
             "roles": ["SPIM_SCK", "SPIM_MOSI", "SPIM_MISO"], "states": {"default": [], "sleep": []}, "compatible": "nordic,nrf-spim",
             "editable": True, "path": "/soc/spi@2000", "aliases": []},
        ]
        self.inherited_states = {
            "uart_default": b"uart_default: uart_default { group1 { psels = <NRF_PSEL(UART_TX, 0, 6)>, <NRF_PSEL(UART_RX, 0, 8)>; }; };",
            "uart_sleep": b"uart_sleep: uart_sleep { group1 { psels = <NRF_PSEL(UART_TX, 0, 6)>, <NRF_PSEL(UART_RX, 0, 8)>; low-power-enable; }; };",
        }
        self.gpio_uses = [{"owner": "led0", "label": "LED", "path": "/leds/led_0", "port": 0, "pin": 13,
                           "flags": ["GPIO_ACTIVE_LOW"], "enabled": True}]
        self.reservations = [{"port": 0, "pin": 9, "owner": "nfc", "reason": "NFC"},
                             {"port": 0, "pin": 6, "owner": "uart0", "reason": "Board console"}]
        self.pins = [{"port": port, "pin": pin, "name": f"P{port}.{pin:02d}", "connector": None,
                      "reserved": (port, pin) in {(0, 9), (0, 6)}, "reason": "", "uses": []}
                     for port, count in ((0, 32), (1, 16)) for pin in range(count)]
        self.gpio_flags = [{"name": name, "label": name} for name in
                           ("GPIO_ACTIVE_HIGH", "GPIO_ACTIVE_LOW", "GPIO_PULL_UP", "GPIO_PULL_DOWN", "GPIO_OPEN_DRAIN")]
        self.changed = False

    def revalidate(self):
        if self.changed:
            raise ValueError("Board evidence changed. Load Nordic pins again.")


class PinServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.application.mkdir(parents=True)
        (self.application / "CMakeLists.txt").write_text('message(FATAL_ERROR "Never execute fixture")\n')
        self.overlay = self.application / "app.overlay"
        self.overlay.write_text(SOURCE)
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = self.app.context.services["devicetree_drafts"]
        self.pins = self.app.context.services["devicetree_pins"]
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        # An injected observation supplies selected-target evidence without executing Projects.
        self.editor.contexts[self.context["id"]].payload["input_evidence"]["predicted"] = [
            {"status": "expected", "files": [{"path": "app.overlay", "state": "expected"}]}]
        self.editor.contexts[self.context["id"]].payload["documents"][0]["usage"] = "predicted"
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})
        patched = patch("zephyr_utilities.features.devicetree.pins.NordicEvidence", Evidence)
        patched.start()
        self.addCleanup(patched.stop)

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def load(self):
        self.schema = self.app.router.dispatch(Request("POST", "/api/devicetree/editor/drafts/pins", body=self.guard())).data
        return self.schema

    def command(self, kind="pin_assign", **values):
        self.draft = self.drafts.command(self.guard(command={"kind": kind, "schema_id": self.schema["schema_id"], **values}))
        return self.draft

    def assign(self, pin=2, port=1, **values):
        return self.command(**{"controller": "uart0", "state": "default", "role": "UART_TX", "port": port,
                               "pin": pin, "split_shared": False, **values})

    def source(self, text):
        self.draft = self.drafts.source(self.guard(text=text))
        return self.load()

    def test_load_is_read_only_and_default_sleep_reuse_is_not_a_collision(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            self.load()
        self.assertTrue(self.schema["supported"], self.schema)
        self.assertFalse(self.schema["conflicts"])
        self.assertEqual(len(self.schema["pins"]), 48)
        self.assertEqual(self.draft["revision"], 0)
        self.assertEqual(self.overlay.read_text(), SOURCE)

    def test_inherited_assignment_is_atomic_undoable_and_unsaved(self):
        self.load()
        self.assign()
        self.assertEqual(self.draft["revision"], 1)
        self.assertIn("NRF_PSEL(UART_TX, 1, 2)", self.draft["text"])
        self.assertIn("NRF_PSEL(UART_RX, 0, 8)", self.draft["text"])
        self.assertIn("HANDWRITTEN(2)", self.draft["text"])
        self.assertEqual(self.overlay.read_text(), SOURCE)
        changed = self.draft["text"]
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], SOURCE)
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft["text"], changed)

    def test_pin_ranges_reserved_and_other_owner_collisions_are_rejected(self):
        self.load()
        for port, pin in ((1, 16), (2, 0), (True, 1), (0, 9), (0, 13), (1, 10), (0, 8)):
            with self.subTest(port=port, pin=pin), self.assertRaises(ValueError):
                self.assign(port=port, pin=pin)
        self.assertEqual(self.draft["text"], SOURCE)

    def test_own_reserved_pin_and_default_sleep_are_allowed(self):
        self.load()
        self.assign(port=0, pin=6)
        self.load()
        self.assign(port=1, pin=2, state="default")
        self.load()
        self.assign(port=1, pin=2, state="sleep")
        self.assertIn("NRF_PSEL(UART_TX, 1, 2)", self.draft["text"])

    def test_shared_hardware_instances_are_diagnosed_and_assignment_rejected(self):
        self.source(SOURCE + '&spi0 { status = "okay"; };\n')
        self.assertTrue(any("share hardware" in message for message in self.schema["conflicts"]))
        with self.assertRaisesRegex(ValueError, "hardware instance"):
            self.assign(controller="spi0", role="SPIM_SCK")

    def test_stale_board_or_draft_observation_cannot_mutate(self):
        self.load()
        observed = self.pins.observations[self.schema["schema_id"]]
        observed["evidence"].changed = True
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            self.assign()
        observed["evidence"].changed = False
        self.draft = self.drafts.source(self.guard(text=SOURCE + "// edit\n"))
        with self.assertRaisesRegex(ValueError, "earlier draft"):
            self.assign()

    def test_evidence_is_revalidated_after_planning(self):
        self.load()
        observed = self.pins.observations[self.schema["schema_id"]]
        with patch.object(observed["evidence"], "revalidate", side_effect=[None, ValueError("Evidence raced")]):
            with self.assertRaisesRegex(ValueError, "raced"):
                self.assign()
        self.assertEqual(self.drafts.get({**self.binding(), "draft_id": self.draft["id"]})["text"], SOURCE)

    def test_unsupported_composition_is_read_only_with_explanation(self):
        for source in ('#include "other.overlay"\n', '#define NRF_PSEL(x,y,z) 1\n',
                       '&gpio0 { ngpios = <4>; };\n', '&{/soc/uart@1000} { status = "okay"; };\n',
                       '&qspi { pinctrl-0 = <&other>; };\n'):
            with self.subTest(source=source):
                self.source(SOURCE + source)
                self.assertFalse(self.schema["supported"])
                self.assertTrue(self.schema["reason"])
                with self.assertRaises(ValueError):
                    self.assign()

    def test_other_overlay_selection_is_not_promoted_to_hardware_evidence(self):
        self.editor.contexts[self.context["id"]].payload["input_evidence"]["predicted"][0]["files"].append(
            {"path": "second.overlay", "state": "expected"})
        self.load()
        self.assertFalse(self.schema["supported"])
        self.assertIn("only selected overlay", self.schema["reason"])

    def test_symbolic_gpio_assign_unassign_and_reassign(self):
        self.load()
        def select():
            row = self.schema["gpio"][0]
            return {key: row[key] for key in ("node_id", "property", "index")}
        self.command("pin_gpio", **select(), port=1, pin=3, flags=["GPIO_ACTIVE_LOW", "GPIO_PULL_UP"])
        self.assertIn("GPIO_ACTIVE_LOW", self.draft["text"])
        self.assertIn("GPIO_PULL_UP", self.draft["text"])
        self.load()
        self.command("pin_gpio_unassign", **select())
        self.assertIn("/delete-property/ gpios;", self.draft["text"])
        self.load()
        self.command("pin_gpio", **select(), port=1, pin=4, flags=["GPIO_ACTIVE_HIGH"])
        self.assertIn("&gpio1 4 GPIO_ACTIVE_HIGH", self.draft["text"])
        self.assertNotIn("/delete-property/ gpios", self.draft["text"])

    def test_bad_flags_and_command_fields_leave_source_unchanged(self):
        self.load()
        row = self.schema["gpio"][0]
        select = {key: row[key] for key in ("node_id", "property", "index")}
        for flags in (["EVIL(1)"], ["GPIO_PULL_UP", "GPIO_PULL_DOWN"], ["GPIO_ACTIVE_HIGH", "GPIO_ACTIVE_LOW"], None):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.command("pin_gpio", **select, port=1, pin=3, flags=flags)
        with self.assertRaises(ValueError):
            self.assign(extra="unexpected")
        with self.assertRaises(ValueError):
            self.assign(split_shared="yes")
        self.assertEqual(self.draft["text"], SOURCE)

    def test_returned_metadata_is_detached(self):
        self.load()
        saved = deepcopy(self.pins.observations[self.schema["schema_id"]]["payload"])
        self.schema["pins"].clear()
        self.schema["controllers"].clear()
        self.assertEqual(self.pins.observations[self.schema["schema_id"]]["payload"], saved)

    def test_unknown_controller_status_cannot_hide_allocations(self):
        self.source(SOURCE + '&i2c0 { status = SOME_STATUS; };\n')
        with self.assertRaisesRegex(ValueError, "unknown"):
            self.assign()

    def test_readonly_resolved_controller_does_not_block_unrelated_allocation(self):
        class ReadOnlyEvidence(Evidence):
            def __init__(self, context):
                super().__init__(context)
                self.controllers.append({**self.controllers[0], "id": "pwm0", "kind": "pwm", "instance": "0x3000", "editable": False})
        with patch("zephyr_utilities.features.devicetree.pins.NordicEvidence", ReadOnlyEvidence):
            self.load()
        self.assign(split_shared=True)
        self.assertIn("NRF_PSEL(UART_TX, 1, 2)", self.draft["text"])

    def test_hidden_controller_routes_do_not_reuse_the_board_baseline(self):
        for text in ('/ { soc { uart@1000 { status = "disabled"; }; }; };',
                     '&uart0 { compatible = "other,uart"; };',
                     '/ { other { pinctrl-0 = <&uart_default>; }; };'):
            with self.subTest(text=text):
                self.source(SOURCE + text)
                self.assertFalse(self.schema["supported"])

    def test_chip_select_removal_never_reorders_device_indices(self):
        self.source(SOURCE + '&spi0 { cs-gpios = <&gpio1 7 GPIO_ACTIVE_LOW>, <&gpio1 8 GPIO_ACTIVE_LOW>; };')
        row = next(row for row in self.schema["gpio"] if row["property"] == "cs-gpios")
        before = self.draft["text"]
        with self.assertRaisesRegex(ValueError, "chip select"):
            self.command("pin_gpio_unassign", **{key: row[key] for key in ("node_id", "property", "index")})
        self.assertEqual(self.draft["text"], before)

    def test_malformed_identifiers_are_client_errors(self):
        self.load()
        for field in ("controller", "state", "role"):
            for value in (None, [], {}, 1, ""):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    self.assign(**{field: value})
        self.assertEqual(self.draft["text"], SOURCE)

    def test_inherited_gpio_path_and_reference_contributors_are_not_editable(self):
        self.source(SOURCE + '&led0 { gpios = <&gpio1 2 GPIO_ACTIVE_LOW>; };\n'
                    '/ { leds { led_0 { gpios = <&gpio1 3 GPIO_ACTIVE_LOW>; }; }; };')
        self.assertFalse(self.schema["supported"])
        self.assertIn("multiple local contributors", self.schema["reason"])
        self.assertEqual(self.overlay.read_text(), SOURCE)

    def test_aliases_cannot_bypass_controller_identity_guards(self):
        class AliasEvidence(Evidence):
            def __init__(self, context):
                super().__init__(context)
                self.controllers[0]["aliases"] = ["console_uart"]
        with patch("zephyr_utilities.features.devicetree.pins.NordicEvidence", AliasEvidence):
            self.source(SOURCE + '&console_uart { compatible = "other,device"; reg = <0x5000 0x1000>; };')
        self.assertFalse(self.schema["supported"])
        self.assertIn("identity", self.schema["reason"])

    def test_invalid_gpio_status_is_unknown_instead_of_freeing_the_pin(self):
        for status in ('<1>', 'STATUS_MACRO', '"reserved"'):
            with self.subTest(status=status):
                self.source(SOURCE.replace('gpios =', 'status = ' + status + '; gpios ='))
                self.assertFalse(self.schema["supported"])
                self.assertIn("status is unknown", self.schema["reason"])

    def test_cached_evidence_is_revalidated_before_reuse(self):
        self.load()
        evidence = self.pins.observations[self.schema["schema_id"]]["evidence"]
        with patch("zephyr_utilities.features.devicetree.pins.NordicEvidence", side_effect=AssertionError("Reuse observed evidence")):
            self.load()
        self.assertIs(self.pins.observations[self.schema["schema_id"]]["evidence"], evidence)
        evidence.changed = True
        self.load()
        self.assertIsNot(self.pins.observations[self.schema["schema_id"]]["evidence"], evidence)
