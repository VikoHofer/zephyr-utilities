from copy import deepcopy
import json
from pathlib import Path
import unittest

from zephyr_utilities.features.projects.demo_contracts import (
    compatibility_key,
    descriptor_fingerprint,
    validate_compatibility_evidence,
    validate_compatibility_identity,
    validate_demo_origin,
    validate_descriptor,
    validate_handoff_selection,
)


FIXTURES = Path(__file__).parent / "fixtures/tinyml_demo_contracts"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


class DemoContractTests(unittest.TestCase):
    def setUp(self):
        example = fixture("source-ready")
        self.descriptor = example["descriptor"]
        self.observation = example["evidence"]
        self.identity = self.observation["identity"]

    def handoff(self):
        return {"schema_version": 1, "workspace": self.identity["workspace"], "demo_id": "tflm-sine",
                "variant_id": "reference", "source_fingerprint": self.identity["source_fingerprint"],
                "environment_fingerprint": self.identity["environment_fingerprint"],
                "selection": {"board_id": "catalogue board ID", "target": "qemu_x86", "sdk_id": "catalogue SDK ID"}}

    def checked(self):
        observation = deepcopy(self.observation)
        observation["identity"]["configuration"] = {"kconfig_fingerprint": "a" * 64, "overlays_fingerprint": "b" * 64}
        observation["key"] = compatibility_key(observation["identity"])
        observation["configure"] = {"state": "passed", "evidence": {"kind": "sample-check", "id": "a" * 32}}
        return observation

    def origin(self):
        return {"schema_version": 1, "descriptor": self.descriptor, "identity": self.identity,
                "transformations": [{"path": "CMakeLists.txt", "source_sha256": "b" * 64,
                    "published_sha256": "c" * 64, "reason": "Replace understood glob with the reviewed explicit source list."}]}

    def test_examples_keep_availability_separate_from_configuration_and_build(self):
        for name, availability in (("source-ready", "source-ready"), ("missing-module", "missing-module"),
                                   ("unsupported-layout", "unsupported-layout")):
            with self.subTest(name=name):
                example = fixture(name)
                before = deepcopy(example)
                result = validate_compatibility_evidence(example["evidence"], descriptor=example["descriptor"])
                self.assertEqual(result["availability"], availability)
                self.assertEqual(result["configure"], {"state": "not-checked", "evidence": None})
                self.assertEqual(result["build"], {"state": "not-checked", "evidence": None})
                self.assertEqual(example, before)
                result["identity"]["modules"].clear()
                self.assertTrue(example["evidence"]["identity"]["modules"])

    def test_unknown_observations_are_not_replaced_with_inferred_values(self):
        example = fixture("unsupported-layout")
        descriptor = validate_descriptor(example["descriptor"])
        identity = validate_compatibility_identity(example["evidence"]["identity"])
        self.assertIsNone(descriptor["source"]["revision"])
        self.assertIsNone(descriptor["source"]["fingerprint"])
        self.assertIsNone(descriptor["model"]["sha256"])
        self.assertIsNone(identity["modules"][0]["revision"])
        self.assertIsNone(identity["modules"][0]["path"])
        self.assertEqual(identity["modules"][0]["status"], "unknown")
        identity["target"] = None
        identity["sdk"] = None
        unknown = validate_compatibility_identity(identity)
        self.assertIsNone(unknown["target"])
        self.assertIsNone(unknown["sdk"])

    def test_future_emlearn_seed_has_no_admitted_model_or_handoff(self):
        descriptor = validate_descriptor(fixture("emlearn-future-seed"))
        self.assertEqual(descriptor["variant_id"], "loadable-trees")
        self.assertEqual(descriptor["model"]["format"], "emlearn-trees-c-source")
        self.assertIsNone(descriptor["source"]["revision"])
        self.assertIsNone(descriptor["source"]["fingerprint"])
        self.assertIsNone(descriptor["model"]["sha256"])
        selected = self.handoff()
        selected.update(demo_id="emlearn-xor", variant_id="loadable-trees")
        with self.assertRaisesRegex(ValueError, "qualified"):
            validate_handoff_selection(selected, descriptor=descriptor)

    def test_future_versions_and_boolean_versions_are_not_silently_accepted(self):
        values = ((validate_descriptor, self.descriptor), (validate_compatibility_identity, self.identity),
                  (validate_compatibility_evidence, self.observation), (validate_handoff_selection, self.handoff()),
                  (validate_demo_origin, self.origin()))
        for validate, original in values:
            for version in (0, 2, True, "1", None):
                with self.subTest(validator=validate.__name__, version=version):
                    value = deepcopy(original)
                    value["schema_version"] = version
                    with self.assertRaisesRegex(ValueError, "version"):
                        validate(value)

    def test_browser_data_cannot_supply_commands_code_recipes_or_source_paths(self):
        for field, content in (("commands", ["cmake", "-P", "anything"]), ("code", "import os"),
                               ("recipe", {"execute": "anything"}), ("source_path", "/tmp/arbitrary")):
            for validate, original in ((validate_descriptor, self.descriptor), (validate_handoff_selection, self.handoff())):
                with self.subTest(field=field, validator=validate.__name__):
                    value = deepcopy(original)
                    value[field] = content
                    with self.assertRaises(ValueError):
                        validate(value)
        value = deepcopy(self.descriptor)
        value["source"]["locator_id"] = "/tmp/arbitrary"
        with self.assertRaisesRegex(ValueError, "curated locator"):
            validate_descriptor(value)
        value = deepcopy(self.descriptor)
        value["runtime"]["files"][0]["content"] = "arbitrary build code"
        with self.assertRaises(ValueError):
            validate_descriptor(value)

    def test_curated_identity_does_not_permit_runtime_substitution(self):
        for field, replacement in (("framework_id", "emlearn"), ("variant_id", "cmsis-nn"), ("demo_id", "unknown-demo")):
            value = deepcopy(self.descriptor)
            value[field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_descriptor(value)
        value = deepcopy(self.descriptor)
        value["adapter"]["version"] = 2
        with self.assertRaisesRegex(ValueError, "adapter"):
            validate_descriptor(value)
        value = deepcopy(self.descriptor)
        value["requirements"]["hardware"] = ["arbitrary accelerometer"]
        with self.assertRaisesRegex(ValueError, "CPU inference"):
            validate_descriptor(value)

    def test_runtime_manifest_rejects_unsafe_paths_collisions_and_unbounded_files(self):
        for path in (".", "..", "../main.c", "/tmp/main.c", "src/../main.c", "src//main.c", ".git/config",
                     "build/zephyr.elf", "build-debug/model.cpp", "src/.hidden.cpp"):
            value = deepcopy(self.descriptor)
            value["runtime"]["files"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_descriptor(value)
        for extra in (self.descriptor["runtime"]["files"][0], {"path": "src", "bytes": 1, "sha256": "c" * 64}):
            value = deepcopy(self.descriptor)
            value["runtime"]["files"].append(deepcopy(extra))
            with self.assertRaises(ValueError):
                validate_descriptor(value)
        for size in (-1, 1024 * 1024 + 1, True, "16"):
            value = deepcopy(self.descriptor)
            value["runtime"]["files"][0]["bytes"] = size
            with self.subTest(bytes=size), self.assertRaises(ValueError):
                validate_descriptor(value)
        value = deepcopy(self.descriptor)
        for item in value["runtime"]["files"]:
            item["bytes"] = 1024 * 1024
        with self.assertRaisesRegex(ValueError, "bounded publication"):
            validate_descriptor(value)

    def test_source_model_and_license_relationships_are_bound(self):
        for change in ("model", "license", "omission"):
            value = deepcopy(self.descriptor)
            if change == "model":
                value["model"]["sha256"] = "0" * 64
            elif change == "license":
                value["licenses"][0]["sha256"] = "0" * 64
            else:
                value["runtime"]["omitted"].append({"path": "src/main.c", "reason": "Contradictory inclusion."})
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_descriptor(value)

    def test_semantic_reordering_does_not_invalidate_identity(self):
        reordered = deepcopy(self.descriptor)
        for values in (reordered["runtime"]["files"], reordered["runtime"]["omitted"], reordered["requirements"]["kconfig"]):
            values.reverse()
        self.assertEqual(descriptor_fingerprint(reordered), descriptor_fingerprint(self.descriptor))
        identity = deepcopy(self.identity)
        identity["modules"].append({"id": "cmsis", "path": "/synthetic/modules/cmsis", "status": "present",
                                    "revision": None, "fingerprint": "c" * 64})
        identity["roots"].append({"kind": "dts", "path": "/synthetic/dts", "fingerprint": "d" * 64})
        reordered_fields = dict(reversed(list(identity.items())))
        self.assertEqual(compatibility_key(reordered_fields), compatibility_key(identity))
        for field in ("modules", "roots"):
            reordered_identity = deepcopy(identity)
            reordered_identity[field].reverse()
            with self.subTest(field=field):
                self.assertNotEqual(compatibility_key(reordered_identity), compatibility_key(identity))
        before = deepcopy(identity)
        compatibility_key(identity)
        self.assertEqual(identity, before)

    def test_capture_changes_invalidate_the_saved_compatibility_key(self):
        changes = ((["workspace"], "/different/workspace"), (["environment_fingerprint"], "a" * 64),
                   (["descriptor_fingerprint"], "a" * 64), (["source_fingerprint"], "a" * 64),
                   (["model_sha256"], "b" * 64), (["target", "board_id"], "another board"),
                   (["target", "requested"], "qemu_x86@1.0"), (["target", "effective"], "qemu_x86@2.0"),
                   (["target", "revision"], "2.0"), (["sdk", "id"], "another SDK"),
                   (["sdk", "fingerprint"], "b" * 64), (["sdk", "toolchain_fingerprint"], "b" * 64),
                   (["modules", 0, "path"], "/different/module"), (["modules", 0, "revision"], "different revision"),
                   (["modules", 0, "fingerprint"], "b" * 64), (["roots", 0, "path"], "/different/root"),
                   (["roots", 0, "fingerprint"], "b" * 64), (["configuration", "kconfig_fingerprint"], "a" * 64),
                   (["configuration", "overlays_fingerprint"], "b" * 64))
        key = compatibility_key(self.identity)
        for path, replacement in changes:
            identity = deepcopy(self.identity)
            target = identity
            for component in path[:-1]:
                target = target[component]
            target[path[-1]] = replacement
            with self.subTest(path=path):
                self.assertNotEqual(compatibility_key(identity), key)
                observation = deepcopy(self.observation)
                observation["identity"] = identity
                with self.assertRaisesRegex(ValueError, "stale"):
                    validate_compatibility_evidence(observation)

    def test_recomputed_key_cannot_rebind_a_different_descriptor(self):
        observation = deepcopy(self.observation)
        observation["identity"]["model_sha256"] = "b" * 64
        observation["key"] = compatibility_key(observation["identity"])
        with self.assertRaisesRegex(ValueError, "descriptor/source/model"):
            validate_compatibility_evidence(observation, descriptor=self.descriptor)

    def test_passed_states_require_complete_capture_and_execution_evidence(self):
        checked = self.checked()
        self.assertEqual(validate_compatibility_evidence(checked)["configure"]["state"], "passed")
        checked["build"] = {"state": "passed", "evidence": {"kind": "project-attempt", "id": "b" * 32}}
        self.assertEqual(validate_compatibility_evidence(checked)["build"]["state"], "passed")
        for change in ("no-id", "no-config", "no-config-capture", "missing-module", "sample-build", "hardware-claim"):
            value = deepcopy(checked)
            if change == "no-id":
                value["configure"]["evidence"] = None
            elif change == "no-config":
                value["configure"] = {"state": "not-checked", "evidence": None}
            elif change == "no-config-capture":
                value["identity"]["configuration"]["kconfig_fingerprint"] = None
            elif change == "missing-module":
                value["identity"]["modules"][0].update(status="missing", fingerprint=None)
                value["availability"] = "missing-module"
                value["diagnostics"] = ["Module unavailable."]
            elif change == "sample-build":
                value["build"]["evidence"]["kind"] = "sample-check"
            else:
                value["hardware_verified"] = True
            value["key"] = compatibility_key(value["identity"])
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_compatibility_evidence(value)

    def test_missing_and_unsupported_states_retain_a_visible_reason(self):
        for name in ("missing-module", "unsupported-layout"):
            value = fixture(name)["evidence"]
            value["diagnostics"] = []
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "visible reason"):
                validate_compatibility_evidence(value)
        value = deepcopy(self.observation)
        value["availability"] = "missing-module"
        with self.assertRaisesRegex(ValueError, "explicit missing"):
            validate_compatibility_evidence(value)

    def test_handoff_is_only_an_optional_exact_preselection(self):
        original = self.handoff()
        self.assertEqual(validate_handoff_selection(original, descriptor=self.descriptor), original)
        unselected = self.handoff()
        unselected.pop("selection")
        self.assertIsNone(validate_handoff_selection(unselected)["selection"])
        for field in ("board_id", "target", "sdk_id"):
            value = self.handoff()
            value["selection"].pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_handoff_selection(value)
        value = self.handoff()
        value["source_fingerprint"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "qualified"):
            validate_handoff_selection(value, descriptor=self.descriptor)
        value = self.handoff()
        value["workspace"] = "/synthetic/../outside"
        with self.assertRaises(ValueError):
            validate_handoff_selection(value)

    def test_optional_origin_preserves_legacy_and_preconfigure_creation(self):
        self.assertIsNone(validate_demo_origin(None))
        origin = self.origin()
        before = deepcopy(origin)
        accepted = validate_demo_origin(origin)
        self.assertIsNone(accepted["identity"]["configuration"]["kconfig_fingerprint"])
        self.assertEqual(accepted["descriptor"]["licenses"], origin["descriptor"]["licenses"])
        self.assertEqual(origin, before)
        accepted["descriptor"]["runtime"]["files"].clear()
        self.assertTrue(origin["descriptor"]["runtime"]["files"])
        for change in ("model", "source", "module", "transform"):
            value = deepcopy(origin)
            if change == "model":
                value["identity"]["model_sha256"] = "0" * 64
            elif change == "source":
                value["identity"]["source_fingerprint"] = "0" * 64
            elif change == "module":
                value["identity"]["modules"][0].update(status="unknown", fingerprint=None)
            else:
                value["transformations"][0]["source_sha256"] = "0" * 64
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_demo_origin(value)

    def test_malformed_enum_values_raise_validation_errors(self):
        for malformed in ({}, [], 1, True, None):
            cases = []
            descriptor = deepcopy(self.descriptor)
            descriptor["requirements"]["kconfig"][0]["value"] = malformed
            cases.append((validate_descriptor, descriptor))
            descriptor = deepcopy(self.descriptor)
            descriptor["licenses"][0]["spdx"] = malformed
            cases.append((validate_descriptor, descriptor))
            identity = deepcopy(self.identity)
            identity["modules"][0]["status"] = malformed
            cases.append((validate_compatibility_identity, identity))
            identity = deepcopy(self.identity)
            identity["roots"][0]["kind"] = malformed
            cases.append((validate_compatibility_identity, identity))
            evidence = deepcopy(self.observation)
            evidence["availability"] = malformed
            cases.append((validate_compatibility_evidence, evidence))
            evidence = deepcopy(self.observation)
            evidence["configure"]["state"] = malformed
            cases.append((validate_compatibility_evidence, evidence))
            for validate, value in cases:
                with self.subTest(validator=validate.__name__, value=malformed), self.assertRaises(ValueError):
                    validate(value)


if __name__ == "__main__":
    unittest.main()
