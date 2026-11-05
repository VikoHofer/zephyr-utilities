"""Local binding schemas preserve constraints, ambiguity and current input identity."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.features.devicetree.bindings import BindingCatalog, binding_mapping


class BindingCatalogTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.root = self.workspace / "bindings"
        self.root.mkdir()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def catalog(self, *extra):
        return BindingCatalog(str(self.workspace), [str(self.root), *map(str, extra)])

    def test_includes_merge_required_constraints_without_materializing_defaults(self):
        base = self.write("base.yaml", """properties:
  rate:
    type: int
    required: true
    default: 100
  label:
    type: string
    enum: [fast, slow]
""")
        chosen = self.write("vendor,device.yaml", """compatible: vendor,device
include: base.yaml
properties:
  rate:
    description: Sampling frequency
""")
        before = {path: path.read_bytes() for path in (base, chosen)}
        with patch("subprocess.Popen", side_effect=AssertionError("No tools")):
            result = self.catalog().match(["vendor,device"])
        self.assertEqual(result["state"], "matched")
        rate = result["schema"]["properties"]["rate"]
        self.assertEqual((rate["type"], rate["required"], rate["default"], rate["supported"]), ("int", True, 100, True))
        self.assertEqual(rate["description"], "Sampling frequency")
        self.assertEqual(result["schema"]["properties"]["label"]["enum"], ["fast", "slow"])
        self.assertEqual({item["path"] for item in result["dependencies"]}, {str(base), str(chosen)})
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_required_true_wins_between_includes_but_cannot_be_weakened_locally(self):
        self.write("optional.yaml", "properties:\n  flag: {type: boolean, required: false}\n")
        self.write("required.yaml", "properties:\n  flag: {type: boolean, required: true}\n")
        self.write("device.yaml", "compatible: vendor,device\ninclude: [optional.yaml, required.yaml]\n")
        result = self.catalog().match(["vendor,device"])
        self.assertTrue(result["schema"]["properties"]["flag"]["required"])
        self.write("device.yaml", "compatible: vendor,device\ninclude: required.yaml\nproperties:\n  flag: {required: false}\n")
        result = self.catalog().match(["vendor,device"])
        self.assertEqual(result["state"], "unsupported")
        self.assertIn("weaken", " ".join(result["diagnostics"]))

    def test_include_filters_preserve_nested_child_binding_rules(self):
        self.write("parent.yaml", """properties:
  keep: {type: int}
  omit: {type: int}
child-binding:
  properties:
    keep-child: {type: string}
    omit-child: {type: int}
  child-binding:
    properties:
      deep: {type: boolean}
      remove: {type: int}
""")
        self.write("device.yaml", """compatible: vendor,parent
include:
  - name: parent.yaml
    property-allowlist: [keep]
    child-binding:
      property-blocklist: [omit-child]
      child-binding:
        property-allowlist: [deep]
""")
        catalog = self.catalog()
        parent = catalog.match(["vendor,parent"])
        self.assertEqual(set(parent["schema"]["properties"]), {"keep"})
        child = catalog.match([], parent_binding=parent)
        self.assertEqual(set(child["schema"]["properties"]), {"keep-child"})
        grandchild = catalog.match([], parent_binding=child)
        self.assertEqual(set(grandchild["schema"]["properties"]), {"deep"})
        self.assertEqual(parent["dependencies"], child["dependencies"])
        self.assertEqual(parent["fingerprint"], grandchild["fingerprint"])

    def test_child_binding_includes_are_resolved_and_compatible_children_use_own_schema(self):
        self.write("child.yaml", "properties:\n  pin: {type: int, required: true}\n")
        self.write("parent.yaml", "compatible: vendor,parent\nchild-binding:\n  include: child.yaml\n")
        self.write("child-own.yaml", "compatible: vendor,child\nproperties:\n  own: {type: string}\n")
        catalog = self.catalog()
        parent = catalog.match(["vendor,parent"])
        self.assertTrue(catalog.match([], parent_binding=parent)["schema"]["properties"]["pin"]["required"])
        self.assertEqual(set(catalog.match(["vendor,child"], parent_binding=parent)["schema"]["properties"]), {"own"})

    def test_bus_qualified_bindings_precede_generic_and_include_inherited_on_bus(self):
        self.write("spi-base.yaml", "on-bus: spi\nproperties:\n  cs: {type: int}\n")
        self.write("generic.yaml", "compatible: vendor,device\nproperties:\n  mode: {type: string}\n")
        self.write("spi.yaml", "compatible: vendor,device\ninclude: spi-base.yaml\n")
        self.write("i2c.yaml", "compatible: vendor,device\non-bus: i2c\n")
        catalog = self.catalog()
        self.assertTrue(catalog.match(["vendor,device"], bus="spi")["schema"]["path"].endswith("spi.yaml"))
        self.assertTrue(catalog.match(["vendor,device"], bus=["i2c", "spi"])["schema"]["path"].endswith("i2c.yaml"))
        self.assertTrue(catalog.match(["vendor,device"], bus=None)["schema"]["path"].endswith("generic.yaml"))
        self.assertEqual(catalog.match(["vendor,device"], bus_known=False)["state"], "unsupported")

    def test_missing_and_ambiguous_candidates_are_distinct_and_fallback_is_ordered(self):
        self.write("device.yaml", "compatible: vendor,device\non-bus: spi\n")
        catalog = self.catalog()
        self.assertEqual(catalog.match(["vendor,device"], bus="i2c")["state"], "missing")
        self.assertEqual(catalog.match(["vendor,absent", "vendor,device"], bus="spi")["state"], "matched")
        self.write("other.yaml", "compatible: vendor,device\non-bus: spi\n")
        result = self.catalog().match(["vendor,device"], bus="spi")
        self.assertEqual(result["state"], "ambiguous")
        self.assertIsNone(result["schema"])
        self.assertEqual(len(result["candidates"]), 2)

    def test_compatible_inherited_from_an_include_cannot_hide_a_duplicate_binding(self):
        self.write("base.yaml", "compatible: vendor,device\n")
        self.write("duplicate.yaml", "include: base.yaml\n")
        result = self.catalog().match(["vendor,device"])
        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(len(result["candidates"]), 2)

    def test_ambiguous_or_missing_include_never_selects_an_arbitrary_file(self):
        self.write("one/common.yaml", "properties:\n  rate: {type: int}\n")
        self.write("two/common.yaml", "properties:\n  label: {type: string}\n")
        self.write("device.yaml", "compatible: vendor,device\ninclude: common.yaml\n")
        result = self.catalog().match(["vendor,device"])
        self.assertEqual(result["state"], "ambiguous")
        self.write("device.yaml", "compatible: vendor,device\ninclude: missing.yaml\n")
        self.assertEqual(self.catalog().match(["vendor,device"])["state"], "missing")

    def test_cycles_conflicting_constraints_and_bad_filters_are_unsupported(self):
        self.write("a.yaml", "include: b.yaml\n")
        self.write("b.yaml", "include: a.yaml\n")
        self.write("device.yaml", "compatible: vendor,device\ninclude: a.yaml\n")
        self.assertEqual(self.catalog().match(["vendor,device"])["state"], "unsupported")
        self.write("a.yaml", "properties:\n  rate: {type: int}\n")
        self.write("device.yaml", "compatible: vendor,device\ninclude: a.yaml\nproperties:\n  rate: {type: string}\n")
        self.assertEqual(self.catalog().match(["vendor,device"])["state"], "unsupported")
        self.write("device.yaml", "compatible: vendor,device\ninclude:\n  - name: a.yaml\n    property-allowlist: [rate]\n    property-blocklist: [rate]\n")
        self.assertEqual(self.catalog().match(["vendor,device"])["state"], "unsupported")

    def test_actual_provider_cell_names_and_specifier_space_are_retained(self):
        self.write("provider.yaml", """compatible: vendor,gpio
gpio-cells: [pin, flags]
properties:
  '#gpio-cells': {type: int, const: 2}
  gpio-controller: {type: boolean}
""")
        self.write("consumer.yaml", """compatible: vendor,consumer
properties:
  reset-gpios: {type: phandle-array}
  channel: {type: phandle-array, specifier-space: dma}
  sensor-io-channels: {type: phandle-array}
""")
        catalog = self.catalog()
        provider = catalog.match(["vendor,gpio"])["schema"]
        consumer = catalog.match(["vendor,consumer"])["schema"]
        self.assertEqual(provider["cells"], {"gpio": ["pin", "flags"]})
        self.assertEqual(provider["properties"]["#gpio-cells"]["const"], 2)
        self.assertEqual(consumer["properties"]["reset-gpios"]["specifier_space"], "gpio")
        self.assertEqual(consumer["properties"]["channel"]["specifier_space"], "dma")
        self.assertEqual(consumer["properties"]["sensor-io-channels"]["specifier_space"], "io-channel")

    def test_types_arrays_defaults_and_enums_remain_metadata(self):
        self.write("device.yaml", """compatible: vendor,device
properties:
  modes: {type: string-array, default: [fast, slow]}
  ranges: {type: array, default: [0, 10]}
  bytes: {type: uint8-array, default: [0, 255]}
  target: {type: phandle}
  targets: {type: phandles}
  path: {type: path}
  presence: {type: boolean}
""")
        properties = self.catalog().match(["vendor,device"])["schema"]["properties"]
        self.assertTrue(all(item["supported"] for item in properties.values()))
        self.assertEqual(properties["modes"]["default"], ["fast", "slow"])
        self.assertNotIn("default", properties["presence"])

    def test_unsupported_property_constraints_do_not_hide_supported_siblings(self):
        self.write("device.yaml", """compatible: vendor,device
properties:
  okay: {type: int, enum: [1, 2]}
  unknown: {type: int, minimum: 1}
  raw: {type: compound}
  malformed: {type: [int]}
  bad-default: {type: phandle, default: 1}
  bad-bool: {type: boolean, required: yesplease}
  bad-bytes: {type: uint8-array, default: [256]}
  byte-enum: {type: uint8-array, enum: [0, 255]}
  byte-const: {type: uint8-array, const: [0, 255]}
""")
        result = self.catalog().match(["vendor,device"])
        self.assertEqual(result["state"], "matched")
        properties = result["schema"]["properties"]
        self.assertTrue(properties.pop("okay")["supported"])
        self.assertTrue(all(not item["supported"] and item["reason"] for item in properties.values()))

    def test_unknown_top_level_keys_and_invalid_provider_cells_are_unsupported(self):
        for extra in ("arbitrary: yes\n", "gpio-cells: [pin, pin]\n", "bus: [1]\n", "child-binding: nope\n"):
            with self.subTest(extra=extra):
                self.write("device.yaml", "compatible: vendor,device\n" + extra)
                self.assertEqual(self.catalog().match(["vendor,device"])["state"], "unsupported")

    def test_yaml_aliases_tags_duplicate_keys_and_nonmapping_input_are_refused(self):
        for text in ("x: &x [1]\ny: *x\n", "x: !include bad.yaml\n", "x: 1\nx: 2\n", "[one, two]", "1: key\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                binding_mapping(text)
        self.write("device.yaml", "compatible: vendor,device\n")
        self.write("unreadable.yaml", "compatible: [\n")
        self.assertEqual(self.catalog().match(["vendor,device"])["state"], "unsupported")

    def test_revalidation_detects_changed_include_and_new_duplicate_without_mutating_capture(self):
        self.write("base.yaml", "properties:\n  rate: {type: int, default: 1}\n")
        self.write("device.yaml", "compatible: vendor,device\ninclude: base.yaml\n")
        catalog = self.catalog()
        result = catalog.match(["vendor,device"])
        self.assertEqual(catalog.revalidate(), result["fingerprint"])
        with patch("zephyr_utilities.features.devicetree.bindings.binding_mapping", side_effect=AssertionError("Revalidation does not parse YAML")):
            self.assertEqual(catalog.revalidate(), result["fingerprint"])
        self.write("base.yaml", "properties:\n  rate: {type: int, default: 2}\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            catalog.revalidate()
        self.assertEqual(result["schema"]["properties"]["rate"]["default"], 1)
        current = self.catalog()
        self.write("duplicate.yaml", "compatible: vendor,device\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            current.revalidate()

    def test_missing_optional_root_is_allowed_but_appearance_invalidates_observation(self):
        missing = self.workspace / "application/dts/bindings"
        self.write("device.yaml", "compatible: vendor,device\n")
        catalog = self.catalog(missing)
        self.assertEqual(catalog.match(["vendor,device"])["state"], "matched")
        self.assertEqual(catalog.revalidate(), catalog.fingerprint)
        missing.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "changed"):
            catalog.revalidate()

    def test_symlink_external_roots_and_include_traversal_are_refused(self):
        self.write("device.yaml", "compatible: vendor,device\n")
        (self.root / "link.yaml").symlink_to(self.root / "device.yaml")
        self.assertEqual(self.catalog().match(["vendor,device"])["state"], "unsupported")
        with self.assertRaises(ValueError):
            BindingCatalog(self.workspace, [self.workspace.parent])
        (self.root / "link.yaml").unlink()
        self.write("device.yaml", "compatible: vendor,device\ninclude: ../outside.yaml\n")
        self.assertEqual(self.catalog().match(["vendor,device"])["state"], "unsupported")

    def test_resource_limits_and_invalid_requests_fail_closed(self):
        self.write("one.yaml", "compatible: vendor,device\n")
        self.write("two.yaml", "compatible: vendor,other\n")
        with patch("zephyr_utilities.features.devicetree.bindings.MAX_FILES", 1):
            result = self.catalog().match(["vendor,device"])
            self.assertEqual(result["state"], "unsupported")
        catalog = self.catalog()
        for compatibles in ("vendor,device", [None], ["x"] * 65):
            with self.assertRaises(ValueError):
                catalog.match(compatibles)
        with self.assertRaises(ValueError):
            catalog.match(["vendor,device"], bus_known="unknown")

    def test_returned_schema_mutations_cannot_change_other_observations(self):
        self.write("device.yaml", "compatible: vendor,device\nproperties:\n  rate: {type: int, default: 1}\n")
        catalog = self.catalog()
        first = catalog.match(["vendor,device"])
        first["schema"]["properties"]["rate"]["default"] = 1000
        self.assertEqual(catalog.match(["vendor,device"])["schema"]["properties"]["rate"]["default"], 1)
