"""D0 source qualification is bounded metadata inspection, never firmware execution."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import yaml


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "qualify_tinyml_demo_sources.py"
SPEC = importlib.util.spec_from_file_location("qualify_tinyml_demo_sources", SCRIPT)
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)

NOTICE = "/* SPDX-License-Identifier: Apache-2.0 */\n"


class DemoQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.base = self.workspace / "zephyr"
        self.sample = self.base / qualification.SAMPLE
        self.sample.mkdir(parents=True)
        self.write("VERSION", "VERSION_MAJOR = 4\nVERSION_MINOR = 4\nPATCHLEVEL = 2\nVERSION_TWEAK = 0\nEXTRAVERSION =\n")
        self.write("LICENSE", "Apache License\nVersion 2.0, January 2004\nSynthetic license evidence, not an upstream copy.\n")
        for relative in qualification.RUNTIME:
            self.source(relative, NOTICE)
        self.source("src/main.c", NOTICE + '#include "main_functions.h"\nint main(void) { setup(); loop(); return 0; }\n')
        self.source("src/main_functions.h", NOTICE + 'extern "C" { void setup(void); void loop(void); }\n')
        self.source("src/main_functions.cpp", NOTICE + '''#include "model.hpp"
#include <tensorflow/lite/micro/micro_interpreter.h>
// Synthetic recognisable source tokens; this fixture is not compiled.
tflite::GetModel(g_model); MicroMutableOpResolver resolver; resolver.AddFullyConnected();
static tflite::MicroInterpreter interpreter; interpreter.AllocateTensors(); interpreter->Invoke();
''')
        self.source("src/model.cpp", NOTICE + '''#include "model.hpp"
alignas(8) const unsigned char g_model[] = {0x00,0x00,0x00,0x00,0x54,0x46,0x4c,0x33};
const int g_model_len = 8;
''')
        self.source("CMakeLists.txt", qualification.CMAKE + "\n")
        self.source("prj.conf", "# SPDX-License-Identifier: Apache-2.0\n" + "\n".join(
            f"{key}={value}" for key, value in qualification.CONFIG.items()) + "\n")
        self.source("sample.yaml", yaml.safe_dump({
            "common": {"modules": ["tflite-micro"]},
            "tests": {"sample.tensorflow.helloworld": {
                "platform_allow": ["qemu_x86", "qemu_x86_64"],
                "integration_platforms": ["qemu_x86"], "filter": "CONFIG_FULL_LIBC_SUPPORTED"},
                "sample.tensorflow.helloworld.cmsis_nn": {"platform_allow": ["mps3/corstone300/fvp"]}},
        }))
        for relative in qualification.OMITTED:
            if not (self.sample / relative).exists():
                self.source(relative, b"\x89PNG\x00synthetic" if relative.endswith(".png") else "Synthetic omitted documentation.\n")
        self.write("west.yml", "manifest:\n  self:\n    import: submanifests\n")
        self.write("submanifests/optional.yaml", yaml.safe_dump({"manifest": {"projects": [{
            "name": "tflite-micro", "path": "optional/selected/tflm", "revision": "a" * 40,
            "groups": ["optional"],
        }]}}))

    def write(self, relative, content):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return path

    def source(self, relative, content):
        return self.write(f"{qualification.SAMPLE}/{relative}", content)

    def inspect(self, **kwargs):
        return qualification.qualify_sources(self.base, workspace=self.workspace, **kwargs)

    def test_source_qualified_with_missing_optional_module_and_exact_hashes(self):
        before = {str(path.relative_to(self.workspace)): path.read_bytes()
                  for path in self.workspace.rglob("*") if path.is_file()}
        result = self.inspect(target="qemu_x86/atom")
        self.assertEqual(result["qualification"], {"status": "source-qualified", "reasons": []})
        module = result["dependencies"]["tflite_micro"]
        self.assertEqual(module["manifest"]["path"], "optional/selected/tflm")
        self.assertEqual(module["manifest"]["revision"], "a" * 40)
        self.assertEqual(module["manifest"]["groups"], ["optional"])
        self.assertEqual(module["installation"]["status"], "missing")
        files = {item["path"]: item for item in result["source"]["files"]}
        self.assertEqual(set(files), qualification.EXPECTED)
        for relative, item in files.items():
            content = (self.sample / relative).read_bytes()
            self.assertEqual(item["bytes"], len(content))
            self.assertEqual(item["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(files["train/train_hello_world_model.ipynb"]["action"], "omit")
        self.assertEqual(files["images/model_architecture.png"]["action"], "omit")
        self.assertEqual(files["CMakeLists.txt"]["action"], "transform")
        self.assertEqual(result["source"]["plan"]["model"]["bytes"], 8)
        self.assertFalse(result["selection"]["target"]["declared_reference_target"])
        self.assertEqual(result["source"]["plan"]["validation"]["build"], "not-checked")
        self.assertEqual(before, {str(path.relative_to(self.workspace)): path.read_bytes()
                                  for path in self.workspace.rglob("*") if path.is_file()})
        serialized = json.dumps(result)
        self.assertNotIn("0x54", serialized)
        self.assertNotIn("Synthetic omitted documentation", serialized)

    def test_declared_module_metadata_presence_is_separate_from_build_readiness(self):
        module = self.workspace / "optional/selected/tflm/zephyr"
        module.mkdir(parents=True)
        (module / "module.yml").write_text("name: tflite-micro\nbuild:\n  cmake: zephyr\n  kconfig: zephyr/Kconfig\n")
        result = self.inspect(target="qemu_x86")
        installation = result["dependencies"]["tflite_micro"]["installation"]
        self.assertEqual(installation["status"], "present")
        self.assertIsNone(installation["revision_matches_pin"])
        self.assertEqual(installation["metadata_evidence"][0]["declared_name"], "tflite-micro")
        self.assertEqual(result["qualification"]["status"], "source-qualified")
        self.assertTrue(result["selection"]["target"]["declared_reference_target"])

    def test_conventional_module_location_is_never_guessed(self):
        conventional = self.workspace / "modules/lib/tflite-micro/zephyr"
        conventional.mkdir(parents=True)
        (conventional / "module.yml").write_text("name: tflite-micro\n")
        self.assertEqual(self.inspect()["dependencies"]["tflite_micro"]["installation"]["status"], "missing")
        self.write("west.yml", "manifest:\n  projects: []\n")
        result = self.inspect()
        self.assertEqual(result["dependencies"]["tflite_micro"]["manifest"]["status"], "not-declared")
        self.assertEqual(result["dependencies"]["tflite_micro"]["installation"]["status"], "unknown")

    def test_symlink_module_metadata_does_not_establish_installation(self):
        module = self.workspace / "optional/selected/tflm/zephyr"
        module.mkdir(parents=True)
        external = self.root / "module.yml"
        external.write_text("name: tflite-micro\n")
        (module / "module.yml").symlink_to(external)
        result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "source-qualified")
        self.assertEqual(result["dependencies"]["tflite_micro"]["installation"]["status"], "unknown")
        self.assertIn("Unsafe", result["diagnostics"][0])

    def test_unknown_source_layout_is_visible_and_cmake_is_never_executed(self):
        self.source("src/new_runtime.cpp", NOTICE)
        result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "unsupported")
        self.assertIn("new_runtime.cpp", result["qualification"]["reasons"][0])
        (self.sample / "src/new_runtime.cpp").unlink()
        marker = self.root / "must-not-exist"
        self.source("CMakeLists.txt", qualification.CMAKE + f"\nexecute_process(COMMAND touch {marker})\n")
        result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "unsupported")
        self.assertIn("CMake", result["qualification"]["reasons"][0])
        self.assertFalse(marker.exists())

    def test_malformed_sample_metadata_is_visibly_unsupported(self):
        original = (self.sample / "sample.yaml").read_text()
        cases = (("common", None), ("common", []), ("tests", None), ("tests", []),
                 ("common", {"modules": None}), ("common", {"modules": "tflite-micro"}),
                 ("tests", {"sample.tensorflow.helloworld": []}))
        for field, value in cases:
            with self.subTest(field=field, value=value):
                metadata = yaml.safe_load(original)
                metadata[field] = value
                self.source("sample.yaml", yaml.safe_dump(metadata))
                result = self.inspect()
                self.assertEqual(result["qualification"]["status"], "unsupported")
                self.assertIn("metadata", result["qualification"]["reasons"][0])

    def test_dynamic_and_unsupported_dependency_directives_are_rejected(self):
        directives = ("#include EXTRA_HEADER", '#include_next "constants.h"',
                      '#import "constants.h"', '#embed "model.bin"',
                      '#include <tensorflow/lite/../outside.h>', '#include "constants.h" trailing')
        for directive in directives:
            with self.subTest(directive=directive):
                self.source("src/constants.c", NOTICE + directive + "\n")
                result = self.inspect()
                self.assertEqual(result["qualification"]["status"], "unsupported")
                self.assertTrue(result["qualification"]["reasons"])
        self.source("src/constants.c", NOTICE + '/*\n#include EXTRA_HEADER\n*/\n# include "constants.h" // local header\n')
        result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "source-qualified")
        self.assertIn({"path": "src/constants.c", "include": "constants.h", "provider": "sample"},
                      result["source"]["plan"]["includes"])

    def test_missing_runtime_and_missing_license_remain_unavailable_or_unsupported(self):
        valid_model = (self.sample / "src/model.cpp").read_bytes()
        (self.sample / "src/model.cpp").unlink()
        result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "unsupported")
        self.assertIn("missing=", result["qualification"]["reasons"][0])
        self.source("src/model.cpp", NOTICE + "alignas(8) const unsigned char g_model[] = {0,1};\nconst int g_model_len=2;")
        result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "unsupported")
        self.assertIn("byte array", result["qualification"]["reasons"][0])
        self.source("src/model.cpp", valid_model)
        self.source("src/constants.c", "// License notice removed.\n")
        result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "unsupported")
        self.assertIn("license notice", result["qualification"]["reasons"][0])

    def test_symlink_source_and_ancestor_are_rejected(self):
        model = self.sample / "src/model.cpp"
        external = self.root / "external.cpp"
        external.write_bytes(model.read_bytes())
        model.unlink()
        model.symlink_to(external)
        self.assertEqual(self.inspect()["qualification"]["status"], "unsupported")
        alias = self.root / "linked-zephyr"
        alias.symlink_to(self.base, target_is_directory=True)
        result = qualification.qualify_sources(alias, workspace=self.workspace)
        self.assertEqual(result["qualification"]["status"], "unsupported")

    def test_bound_excessive_omitted_file_without_materializing_it(self):
        path = self.sample / "train/train_hello_world_model.ipynb"
        with path.open("wb") as stream:
            stream.truncate(qualification.MAX_FILE_BYTES + 1)
        result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "unsupported")
        self.assertIn("byte inspection limit", result["qualification"]["reasons"][0])

    def test_mutation_after_file_hash_is_detected_before_qualification(self):
        original = qualification._read
        changed = False

        def read_then_change(path, **kwargs):
            nonlocal changed
            result = original(path, **kwargs)
            if Path(path) == self.sample / "CMakeLists.txt" and not changed:
                changed = True
                self.source("CMakeLists.txt", qualification.CMAKE + "\n# changed after hashing\n")
            return result

        with patch.object(qualification, "_read", side_effect=read_then_change):
            result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "unsupported")
        self.assertIn("changed", result["qualification"]["reasons"][0])

    def test_unsafe_manifest_path_and_yaml_alias_do_not_redirect_module_reads(self):
        self.write("submanifests/optional.yaml", "manifest:\n  projects:\n  - name: tflite-micro\n    path: ../outside\n    revision: fixed\n")
        result = self.inspect()
        self.assertEqual(result["qualification"]["status"], "source-qualified")
        self.assertEqual(result["dependencies"]["tflite_micro"]["installation"]["status"], "unknown")
        self.assertIn("traversal", result["diagnostics"][0])
        self.write("submanifests/optional.yaml", "manifest: &loop\n  self: *loop\n")
        self.assertIn("aliases", self.inspect()["diagnostics"][0])

    def test_sdk_is_metadata_only_and_target_is_exact(self):
        sdk = self.root / "sdk"
        sdk.mkdir()
        (sdk / "sdk_version").write_text("1.0.1\n")
        result = self.inspect(sdk=sdk, target="mps3/corstone300/fvp")
        self.assertEqual(result["selection"]["sdk"]["version"], "1.0.1")
        self.assertEqual(result["selection"]["sdk"]["toolchain_execution"], "not-checked")
        self.assertFalse(result["selection"]["target"]["declared_reference_target"])
        with self.assertRaises(qualification.InspectionError):
            self.inspect(target="qemu_x86;touch sentinel")

    def test_command_outputs_new_report_but_never_overwrites_or_writes_source(self):
        output = self.root / "report.json"
        arguments = ["--zephyr-base", str(self.base), "--workspace", str(self.workspace)]
        with redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(qualification.main(arguments), 0)
        self.assertEqual(json.loads(captured.getvalue())["qualification"]["status"], "source-qualified")
        self.assertEqual(qualification.main([*arguments, "--output", str(output)]), 0)
        saved = output.read_bytes()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(qualification.main([*arguments, "--output", str(output)]), 2)
            self.assertEqual(qualification.main([*arguments, "--output", str(self.base / "report.json")]), 2)
        self.assertEqual(output.read_bytes(), saved)
        self.assertFalse((self.base / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
