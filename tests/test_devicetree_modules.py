"""Modules are guarded source commands; the logical graph is rebuilt without metadata."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.devicetree.recipes import recipes

from test_devicetree_peripherals import BINDINGS, SOURCE


MODULE_SOURCE = SOURCE.replace('    leds { compatible = "gpio-leds"; };', '''    leds { compatible = "gpio-leds"; };
    buttons { compatible = "gpio-keys"; };
    mystery { compatible = "unknown,device"; preserved = <UNKNOWN_MACRO(2)>; };''')


class ModuleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.bindings_dir = self.application / "dts/bindings"
        self.bindings_dir.mkdir(parents=True)
        (self.application / "CMakeLists.txt").write_text('message(FATAL_ERROR "Never execute fixture")\n')
        (self.application / "prj.conf").write_text("# Leave this software configuration unchanged.\n")
        self.overlay = self.application / "app.overlay"
        self.overlay.write_text(MODULE_SOURCE)
        for name, text in BINDINGS.items():
            (self.bindings_dir / name).write_text(text)
        for bus in ("i2c", "spi"):
            (self.bindings_dir / f"bme280-{bus}.yaml").write_text(BINDINGS[f"temp-{bus}.yaml"].replace("test,temperature", "bosch,bme280"))
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.adopt_services()

    def adopt_services(self):
        self.editor = self.app.context.services["devicetree_editor"]
        self.drafts = self.app.context.services["devicetree_drafts"]
        self.modules = self.app.context.services["devicetree_modules"]
        self.saving = self.app.context.services["devicetree_saving"]
        self.context = self.editor.open({"workspace": str(self.workspace), "application_path": str(self.application)})
        self.draft = self.drafts.open({**self.binding(), "document_id": self.context["documents"][0]["id"]})

    def binding(self):
        return {"workspace": str(self.workspace), "context_id": self.context["id"], "expected_revision": self.context["revision"]}

    def guard(self, **extra):
        return {**self.binding(), "draft_id": self.draft["id"], "expected_draft_revision": self.draft["revision"],
                "expected_draft_sha256": self.draft["sha256"], **extra}

    def load(self):
        self.schema = self.modules.inspect(self.guard())
        return self.schema

    def recipe(self, identifier):
        return next(item for item in self.schema["recipes"] if item["id"] == identifier)

    def instance(self, path):
        return next(item for item in self.schema["instances"] if item["path"] == path)

    def command(self, kind, **fields):
        self.draft = self.drafts.command(self.guard(command={"kind": kind, "schema_id": self.schema["schema_id"], **fields}))
        return self.draft

    def add(self, identifier, *, address=None, values=None, name=None, label="", **extra):
        recipe = self.recipe(identifier)
        command = {"recipe_id": identifier, "recipe_version": recipe["version"], "parent_id": recipe["parents"][0]["parent_id"],
                   "name": name or recipe["node_name"], "label": label, "address": address, "values": values or {}}
        return self.command("module_add", **{**command, **extra})

    def source(self, text):
        self.draft = self.drafts.source(self.guard(text=text))
        self.load()

    def test_catalogue_uses_selected_bindings_and_keeps_software_requirements_separate(self):
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")), \
                patch.object(self.app.context.jobs, "submit", side_effect=AssertionError("No jobs")):
            self.load()
        self.assertEqual(len(self.schema["recipes"]), 4)
        self.assertTrue(all(recipe["available"] and recipe["version"] == 1 for recipe in self.schema["recipes"]))
        self.assertEqual(self.recipe("bme280-spi")["parents"][0]["fields"][0]["enum"], [1000000, 4000000])
        self.assertTrue(self.recipe("gpio-button")["software_requirements"])
        self.assertEqual(self.overlay.read_text(), MODULE_SOURCE)
        self.assertEqual(self.draft["revision"], 0)

    def test_i2c_add_is_single_canonical_revision_and_preserves_shared_bus_and_configuration(self):
        self.load()
        self.add("bme280-i2c", address=0x76, label="environment")
        self.assertEqual(self.draft["revision"], 1)
        self.assertIn('environment: sensor@76', self.draft["text"])
        self.assertIn('compatible = "bosch,bme280";', self.draft["text"])
        self.assertIn('status = "disabled";', self.draft["text"])
        self.assertIn('handwritten = <SOME_MACRO(2)>;', self.draft["text"])
        self.assertEqual(self.overlay.read_text(), MODULE_SOURCE)
        self.assertEqual((self.application / "prj.conf").read_text(), "# Leave this software configuration unchanged.\n")
        added = self.draft["text"]
        self.draft = self.drafts.undo(self.guard())
        self.assertEqual(self.draft["text"], MODULE_SOURCE)
        self.draft = self.drafts.redo(self.guard())
        self.assertEqual(self.draft["text"], added)

    def test_i2c_collision_requires_distinct_address_and_label(self):
        self.load()
        self.add("bme280-i2c", address=0x76, label="sensor_first")
        self.load()
        with self.assertRaisesRegex(ValueError, "already uses"):
            self.add("bme280-i2c", address=0x76, label="sensor_second")
        with self.assertRaisesRegex(ValueError, "label.*already"):
            self.add("bme280-i2c", address=0x77, label="sensor_first")
        self.add("bme280-i2c", address=0x77, label="sensor_second")
        self.load()
        self.assertEqual(len([item for item in self.schema["instances"] if item["recipe_matches"]]), 2)

    def test_spi_uses_real_required_frequency_and_existing_chip_select_slots(self):
        self.load()
        for address, values in ((0, {}), (0, {"spi-max-frequency": 999}), (2, {"spi-max-frequency": 1000000})):
            with self.subTest(address=address, values=values), self.assertRaises(ValueError):
                self.add("bme280-spi", address=address, values=values)
        self.add("bme280-spi", address=1, values={"spi-max-frequency": 1000000})
        self.load()
        instance = self.instance("/spi/sensor@1")
        self.assertEqual(instance["recipe_matches"], [{"id": "bme280-spi", "version": 1}])
        self.command("module_update", node_id=instance["node_id"], address=0, values={"spi-max-frequency": 4000000})
        self.assertIn("sensor@0", self.draft["text"])
        self.assertIn("spi-max-frequency = <4000000>;", self.draft["text"])
        self.assertIn("cs-gpios = <&gpio0 1 0>, <&gpio0 2 0>;", self.draft["text"])

    def test_led_and_button_require_explicit_provider_cells_and_input_code(self):
        self.load()
        gpio = [{"reference": "&gpio0", "cells": [8, 0]}]
        with self.assertRaisesRegex(ValueError, "required"):
            self.add("gpio-button", values={"gpios": gpio})
        for value in ([{"reference": "&missing", "cells": [8, 0]}], [{"reference": "&gpio0", "cells": [8]}]):
            with self.assertRaises(ValueError):
                self.add("gpio-led", values={"gpios": value})
        self.add("gpio-button", values={"gpios": gpio, "zephyr,code": 1})
        self.load()
        self.add("gpio-led", values={"gpios": [{"reference": "&gpio0", "cells": [9, 1]}], "label": "Indicator"})
        self.load()
        edges = [edge for edge in self.schema["graph"]["edges"] if edge["kind"] == "gpio"]
        self.assertTrue(any(edge["source"] == "node:/gpio" and edge["target"] == "node:/leds/led" for edge in edges))
        instance = self.instance("/leds/led")
        self.command("module_update", node_id=instance["node_id"], address=None, values={"label": "Changed"})
        self.assertIn('label = "Changed";', self.draft["text"])

    def test_repeated_gpio_add_is_clear_conflict_and_distinct_names_are_distinct_modules(self):
        self.load()
        values = {"gpios": [{"reference": "&gpio0", "cells": [8, 0]}]}
        self.add("gpio-led", values=values)
        self.load()
        with self.assertRaisesRegex(ValueError, "distinct name"):
            self.add("gpio-led", values=values)
        self.add("gpio-led", name="another", values={"gpios": [{"reference": "&gpio0", "cells": [9, 0]}]})
        self.load()
        self.assertEqual(len([item for item in self.schema["instances"] if item["kind"] == "led"]), 2)

    def test_removing_module_preserves_parent_provider_and_other_source(self):
        self.load()
        self.add("gpio-led", values={"gpios": [{"reference": "&gpio0", "cells": [8, 0]}]})
        self.load()
        self.command("module_remove", node_id=self.instance("/leds/led")["node_id"])
        self.assertIn('leds { compatible = "gpio-leds";', self.draft["text"])
        self.assertIn('gpio0: gpio { compatible = "test,gpio";', self.draft["text"])
        self.assertIn('preserved = <UNKNOWN_MACRO(2)>;', self.draft["text"])
        self.load()
        self.assertFalse(any(item["path"] == "/leds/led" for item in self.schema["instances"]))

    def test_referenced_and_nested_module_removal_requires_source_resolution(self):
        self.source(MODULE_SOURCE.replace('handwritten = <SOME_MACRO(2)>;', 'handwritten = <SOME_MACRO(2)>; nested {};'))
        with self.assertRaisesRegex(ValueError, "nested"):
            self.command("module_remove", node_id=self.instance("/i2c/sensor@48")["node_id"])
        self.source(MODULE_SOURCE.replace('/ {', '/ { aliases { sensor = &first; };', 1))
        with self.assertRaisesRegex(ValueError, "references"):
            self.command("module_remove", node_id=self.instance("/i2c/sensor@48")["node_id"])

    def test_handwritten_nodes_are_recognized_without_conversion_or_ownership(self):
        self.source(MODULE_SOURCE.replace('compatible = "test,temperature";', 'compatible = "bosch,bme280";'))
        instance = self.instance("/i2c/sensor@48")
        self.assertEqual(instance["origin"], "source")
        self.assertEqual(instance["recipe_matches"], [{"id": "bme280-i2c", "version": 1}])
        mystery = next(node for node in self.schema["graph"]["nodes"] if node["path"] == "/mystery")
        self.assertFalse(mystery["editable"])
        self.assertFalse(mystery["removable"])
        self.assertEqual(mystery["recipe_matches"], [])
        self.assertNotIn("recipe", self.draft["text"])
        self.assertNotIn("layout", self.draft["text"])

    def test_missing_recipe_binding_never_implies_compatibility(self):
        (self.bindings_dir / "bme280-i2c.yaml").unlink()
        self.load()
        self.assertFalse(self.recipe("bme280-i2c")["available"])
        self.assertTrue(self.recipe("bme280-spi")["available"])
        with self.assertRaisesRegex(ValueError, "binding"):
            self.add("bme280-i2c", address=0x76)
        self.assertEqual(self.draft["text"], MODULE_SOURCE)

    def test_recipe_version_identity_parent_and_command_shape_are_checked(self):
        self.load()
        for extra in ({"recipe_version": 2}, {"recipe_version": True}, {"recipe_id": "download:device"},
                      {"parent_id": self.recipe("bme280-spi")["parents"][0]["parent_id"]}, {"script": "run"}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.add("bme280-i2c", address=0x76, **extra)
        with self.assertRaisesRegex(ValueError, "bus address"):
            self.add("gpio-led", address=1, values={"gpios": [{"reference": "&gpio0", "cells": [8, 0]}]})
        self.assertEqual(self.draft["text"], MODULE_SOURCE)

    def test_observation_expires_on_source_binding_and_context_changes(self):
        self.load()
        self.modules.observations[self.schema["schema_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.add("bme280-i2c", address=0x76)
        self.load()
        self.draft = self.drafts.source(self.guard(text=MODULE_SOURCE + "/* changed */\n"))
        with self.assertRaisesRegex(ValueError, "earlier"):
            self.add("bme280-i2c", address=0x76)
        self.load()
        (self.bindings_dir / "bme280-i2c.yaml").write_text('compatible: bosch,bme280\non-bus: i2c\n')
        with self.assertRaisesRegex(ValueError, "changed"):
            self.add("bme280-i2c", address=0x76)
        self.load()
        other = self.root / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaises(ValueError):
            self.load()

    def test_unique_paths_only_receive_stable_graph_keys(self):
        self.source(MODULE_SOURCE + '\n&i2c0 { status = "okay"; };\n')
        keys = [node["key"] for node in self.schema["graph"]["nodes"]]
        self.assertNotIn("node:/i2c", keys)
        self.assertNotIn("node:/i2c/sensor@48", keys)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertFalse(self.recipe("bme280-i2c")["available"])

    def test_canvas_bound_is_explicit_and_has_no_dangling_edges(self):
        nodes = "\n".join(f'device_{number} {{ compatible = "unknown,device"; }};' for number in range(260))
        self.source(MODULE_SOURCE.replace('/ {', '/ {\n' + nodes, 1))
        keys = {node["key"] for node in self.schema["graph"]["nodes"]}
        self.assertEqual(len(keys), 256)
        self.assertTrue(self.schema["graph"]["truncated"])
        self.assertTrue(any("first 256" in message for message in self.schema["diagnostics"]))
        self.assertTrue(all(edge["source"] in keys and edge["target"] in keys for edge in self.schema["graph"]["edges"]))

    def test_save_restart_reconstructs_graph_from_firmware_source_alone(self):
        self.load()
        self.add("bme280-i2c", address=0x76)
        self.load()
        self.add("gpio-led", values={"gpios": [{"reference": "&gpio0", "cells": [8, 0]}]})
        self.load()
        expected_nodes = [{key: value for key, value in node.items() if key != "node_id"} for node in self.schema["graph"]["nodes"]]
        expected_edges = self.schema["graph"]["edges"]
        plan = self.saving.review(self.guard())
        self.assertEqual(plan["status"], "ready", plan)
        result = self.saving.save(self.guard(plan_id=plan["plan"]["id"]))
        self.assertEqual(result["status"], "saved", result)
        self.app.close()
        self.app = Application(self.workspace, self.root / "fresh-data")
        self.addCleanup(self.app.close)
        self.adopt_services()
        self.load()
        self.assertEqual([{key: value for key, value in node.items() if key != "node_id"} for node in self.schema["graph"]["nodes"]], expected_nodes)
        self.assertEqual(self.schema["graph"]["edges"], expected_edges)
        self.assertFalse(self.draft["dirty"])

    def test_route_and_detached_observations_do_not_allow_recipe_tampering(self):
        result = self.app.router.dispatch(Request("POST", "/api/devicetree/editor/drafts/modules", body=self.guard())).data
        self.assertEqual(result["draft_id"], self.draft["id"])
        self.schema = result
        self.recipe("bme280-i2c")["version"] = 99
        with self.assertRaisesRegex(ValueError, "version"):
            self.add("bme280-i2c", address=0x76)
        detached = recipes()
        detached[0]["version"] = 99
        self.assertEqual(recipes()[0]["version"], 1)
        actual = self.drafts.drafts[self.draft["id"]]
        with self.editor.lock, self.drafts.lock:
            payload = self.modules.validate_observation(self.guard(schema_id=result["schema_id"]), self.context, actual)
        payload["graph"]["nodes"].clear()
        self.assertTrue(self.modules.observations[result["schema_id"]]["payload"]["graph"]["nodes"])

    def test_unknown_recipe_version_does_not_change_an_existing_handwritten_node(self):
        self.load()
        before = deepcopy(self.draft)
        with self.assertRaises(ValueError):
            self.add("bme280-i2c", address=0x76, recipe_version=0)
        self.assertEqual(self.draft, before)
