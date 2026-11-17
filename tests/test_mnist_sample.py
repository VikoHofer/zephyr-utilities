"""Bundled MNIST templates use independent managed assets and exact target copies."""

from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects.creation import apply_project, preview_project
from zephyr_utilities.features.projects.mnist_sample import validate_mnist_dependency
from zephyr_utilities.features.projects.samples import preview_sample, sample_catalog, validate_sample_plan


TFLM_REVISION = "fcc760af130f3a595b5802cdebcc77461e54f382"
SPDX = "// SPDX-License-Identifier: Apache-2.0\n"
LICENSE = "Apache License\nVersion 2.0, January 2004\nSynthetic fixture, not license terms.\n"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def mnist_fixture(workspace, *, assets_root=None):
    """Create synthetic model bytes and licensed source; no trained assets are copied."""
    source = workspace / "fixture-template"
    assets_root = Path(assets_root) if assets_root is not None else workspace / "managed-assets"
    model = b"\x08\x00\x00\x00TFL3" + bytes(range(32))
    images = bytes(range(256)) * 91 + bytes(range(224))
    metadata = {
        "architecture": [784, 64, 10], "dtype": "float32", "seed": 42,
        "model_bytes": len(model), "model_sha256": digest(model), "images_sha256": digest(images),
        "embedded_test_indices": list(range(30)), "embedded_correct": 30,
        "test_accuracy": 0.9722, "test_examples": 10000,
        "training_examples": 54000, "validation_examples": 6000,
        "dataset_url": "https://example.invalid/synthetic-mnist.npz", "dataset_sha256": "0" * 64,
        "normalization": "row-major uint8 pixel / 255.0; black background, white strokes",
        "embedded_selection": "first three test examples per digit in original test order",
    }
    contents = {
        "CMakeLists.txt": "# SPDX-License-Identifier: Apache-2.0\n"
                          "cmake_minimum_required(VERSION 3.20.0)\n"
                          "# Synthetic upstream build input; the adapter must archive it.\n",
        "prj.conf": "CONFIG_CPP=y\nCONFIG_STD_CPP17=y\nCONFIG_TENSORFLOW_LITE_MICRO=y\nCONFIG_SHELL=y\n"
                    'CONFIG_SHELL_PROMPT_UART="mnist:~$ "\n',
        "README.rst": "Synthetic MNIST fixture\n#######################\n",
        "NOTICE.rst": "Synthetic utility-owned runtime template.\n",
        "sample.yaml": "sample:\n  name: Synthetic MNIST\ncommon:\n  platform_allow:\n"
                       "    - nucleo_h753zi/stm32h753xx\ntests:\n  sample.synthetic: {}\n",
        "LICENSE": LICENSE,
        "data/NOTICE.rst": "Synthetic MNIST image attribution: Yann LeCun and Corinna Cortes.\n"
                           "Dataset license: CC-BY-SA-3.0 (Creative Commons Attribution-ShareAlike 3.0).\n",
        "data/test_samples.inc": "// Synthetic fixture, not real MNIST observations.\n" +
                                 "\n".join(f"{{{i}, {i % 10}, {i % 10}, {{" +
                                           ", ".join("1.0f" if digit == i % 10 else "0.0f" for digit in range(10)) +
                                           "}}," for i in range(30)) + "\n",
        "model/metadata.json": json.dumps(metadata, sort_keys=True, indent=2) + "\n",
        "model/model_metadata.hpp": SPDX + "#pragma once\nnamespace mnist {\n"
                                    f'inline constexpr char kModelSha256[] = "{digest(model)}";\n'
                                    "inline constexpr float kTestAccuracy = 0.972200000f;\n}\n",
        "src/main.cpp": SPDX + '#include "inference.hpp"\nint main() { return 0; }\n',
        "src/inference.cpp": SPDX + '#include "inference.hpp"\n',
        "src/inference.hpp": SPDX + "#pragma once\nnamespace mnist {\n"
                             "inline constexpr size_t kArenaSize = 16 * 1024;\n}\n",
        "src/assets.cpp": SPDX + '#include "assets.hpp"\nconst unsigned char model[] = {\n'
                          '#include "model.inc"\n};\nconst unsigned char images[] = {\n'
                          '#include "images.inc"\n};\n',
        "src/assets.hpp": SPDX + "#pragma once\nnamespace mnist {\n"
                          "inline constexpr size_t kWidth = 28;\n"
                          "inline constexpr size_t kPixels = kWidth * kWidth;\n"
                          "inline constexpr size_t kClasses = 10;\n"
                          "inline constexpr size_t kSampleCount = 30;\n}\n",
        "boards/nucleo_h753zi.conf": "CONFIG_FPU=y\nCONFIG_FPU_SHARING=y\n",
    }
    for relative, content in contents.items():
        root = assets_root if relative.startswith(("model/", "data/")) else source
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    (assets_root / "LICENSE").write_text(LICENSE)
    (assets_root / "model/mnist.tflite").write_bytes(model)
    (assets_root / "data/test_images.bin").write_bytes(images)
    module = assets_root / "framework/tflite-micro"
    module_files = {
        "REVISION": TFLM_REVISION + "\n",
        "zephyr/module.yml": "name: tflite-micro\nbuild:\n  cmake-ext: True\n  kconfig-ext: True\n",
        "LICENSE": LICENSE,
        "tensorflow/lite/micro/micro_interpreter.h": SPDX,
        "tensorflow/lite/micro/micro_mutable_op_resolver.h": SPDX,
        "tensorflow/lite/schema/schema_generated.h": SPDX,
        "third_party/flatbuffers/include/flatbuffers/verifier.h": SPDX,
    }
    for relative, content in module_files.items():
        path = module / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return source, module, model, images


class MnistSampleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mnist-sample-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.zephyr = self.workspace / "zephyr"
        self.zephyr.mkdir()
        self.source, self.module, self.model, self.images = mnist_fixture(self.workspace)
        self.assets_root = self.module.parent.parent
        template = patch("zephyr_utilities.features.projects.mnist_sample.TEMPLATE_ROOT", self.source)
        template.start()
        self.addCleanup(template.stop)
        self.snapshot = {"zephyr": {"path": str(self.zephyr), "version": "synthetic", "commit": "fixture"}}
        self.recipe = {"cwd": str(self.workspace / "created"), "display_configure": "cmake configure",
                       "display_build": "cmake --build build"}

    def profile(self, target="nrf52840dk/nrf52840"):
        return {**deepcopy(self.snapshot), "target": target}

    def preview(self, profile=None):
        return preview_sample(self.snapshot, "tinyml/mnist-shell", self.recipe, profile=profile,
                              mnist_assets_root=self.assets_root)

    def plan(self, profile=None):
        profile = self.profile() if profile is None else profile
        preview = self.preview(profile)
        return {"name": "mnist_demo", "profile": profile, "recipe": self.recipe,
                "sample": preview["provenance"], "files": preview["files"]}

    def test_two_targets_share_model_and_source_but_get_exact_default_overlays(self):
        before = {str(path): path.read_bytes() for path in self.source.rglob("*") if path.is_file()}
        base = self.preview()
        self.assertEqual(base["provenance"]["schema_version"], 1)
        fingerprints = []
        for target in ("nrf52840dk/nrf52840", "nucleo_f446re/stm32f446xx"):
            with self.subTest(target=target):
                plan = self.plan(self.profile(target))
                provenance = plan["sample"]
                files = {item["path"]: item["content"] for item in plan["files"]}
                self.assertEqual(provenance["schema_version"], 3)
                self.assertEqual(provenance["source_fingerprint"], base["provenance"].get("source_fingerprint", base["provenance"]["fingerprint"]))
                self.assertEqual(provenance["default_overlay"]["target"], target)
                overlay = provenance["default_overlay"]["path"]
                self.assertIn(target, files[overlay])
                self.assertIn(target, files["CMakeLists.txt"])
                self.assertIn(str(self.module), files["CMakeLists.txt"])
                self.assertNotIn("upstream/mnist/CMakeLists.txt", files)
                self.assertNotIn("upstream/mnist/sample.yaml", files)
                self.assertIn("project(mnist_demo)", files["CMakeLists.txt"])
                self.assertNotIn("mnist_shell", files["CMakeLists.txt"])
                for relative in ("src/main.cpp", "src/inference.cpp", "src/assets.cpp", "model/metadata.json",
                                 "model/model_metadata.hpp", "data/test_samples.inc", "data/NOTICE.rst", "LICENSE"):
                    origin = self.assets_root if relative.startswith(("model/", "data/")) else self.source
                    self.assertEqual(files[relative], (origin / relative).read_text())
                self.assertEqual(json.loads(files["zephyr-sample.json"]), provenance)
                self.assertIn(self.recipe["display_build"], files["ZEPHYR-SAMPLE.md"])
                self.assertEqual(validate_sample_plan(plan), provenance)
                fingerprints.append(provenance["fingerprint"])
        self.assertNotEqual(*fingerprints)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.source.rglob("*") if path.is_file()})

    def test_generation_never_uses_an_original_or_previously_created_application(self):
        original = self.workspace / "applications/mnist_shell"
        created = self.workspace / "applications/ai_demo"
        original.mkdir(parents=True)
        created.mkdir()
        for path in (original, created):
            (path / "CMakeLists.txt").write_text('message(FATAL_ERROR "Never use this application")\n')
            (path / "src").mkdir()
            (path / "src/main.cpp").write_text("unrelated application data\n")
        first = self.preview(self.profile())
        original.rename(original.with_name("original-unavailable"))
        created.rename(created.with_name("created-unavailable"))
        second = self.preview(self.profile())
        self.assertEqual(first, second)
        for item in second["files"]:
            self.assertNotIn(str(original), item["content"])
            self.assertNotIn(str(created), item["content"])
        self.assertEqual(second["provenance"]["adapter_version"], 2)
        self.assertEqual(second["provenance"]["adapter"], "mnist-runtime-v2")

    def test_missing_managed_assets_never_fall_back_to_an_original_application(self):
        original = self.workspace / "applications/mnist_shell"
        original.mkdir(parents=True)
        (original / "README.rst").write_text("An old demo must never supply new assets.\n")
        with self.assertRaises((ValueError, OSError)):
            preview_sample(self.snapshot, "tinyml/mnist-shell", self.recipe,
                           mnist_assets_root=self.workspace / "uninstalled-assets")
        self.assertEqual((original / "README.rst").read_text(), "An old demo must never supply new assets.\n")

    def test_names_change_generated_cmake_without_changing_the_template_selection(self):
        first = preview_sample(self.snapshot, "tinyml/mnist-shell", self.recipe, profile=self.profile(),
                               mnist_assets_root=self.assets_root, name="first_application")
        second = preview_sample(self.snapshot, "tinyml/mnist-shell", self.recipe, profile=self.profile(),
                                mnist_assets_root=self.assets_root, name="second_application")
        self.assertEqual(first["provenance"]["source_fingerprint"], second["provenance"]["source_fingerprint"])
        self.assertNotEqual(first["provenance"]["fingerprint"], second["provenance"]["fingerprint"])
        for name, preview in (("first_application", first), ("second_application", second)):
            cmake = next(item["content"] for item in preview["files"] if item["path"] == "CMakeLists.txt")
            self.assertIn(f"project({name})", cmake)
            self.assertNotIn("project(mnist_shell)", cmake)

    def test_application_name_cannot_inject_cmake_instructions(self):
        for name in ("", "0-invalid", "too-" * 20, "bad)\nmessage(FATAL_ERROR injected)", "${VARIABLE}"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                preview_sample(self.snapshot, "tinyml/mnist-shell", self.recipe,
                               mnist_assets_root=self.assets_root, name=name)

    def test_binary_assets_become_exact_text_arrays_and_keep_metadata_hashes(self):
        preview = self.preview(self.profile())
        files = {item["path"]: item["content"] for item in preview["files"]}
        for original, generated in ((self.model, "model/model.inc"), (self.images, "data/images.inc")):
            with self.subTest(generated=generated):
                restored = bytes(int(value, 16) for value in re.findall(r"0x([0-9a-fA-F]{2})", files[generated]))
                self.assertEqual(restored, original)
        self.assertNotIn("model/mnist.tflite", files)
        self.assertNotIn("data/test_images.bin", files)
        metadata = json.loads(files["model/metadata.json"])
        self.assertEqual(metadata["model_sha256"], digest(self.model))
        self.assertEqual(metadata["images_sha256"], digest(self.images))
        for item in preview["files"]:
            self.assertEqual(item["sha256"], digest(item["content"].encode()))

    def test_catalogue_lists_local_demo_and_ignored_inputs_are_not_copied(self):
        for relative in ("build/zephyr/zephyr.elf", "validation/device.log", "tools/draw_digit.py",
                         "scripts/train_model.py", "model/weights.npz"):
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"ignored local input")
        preview = self.preview()
        files = {item["path"] for item in preview["files"]}
        self.assertFalse(any(path.startswith(("build/", "validation/", "tools/", "scripts/", ".deps/")) for path in files))
        self.assertNotIn("model/weights.npz", files)
        entry = next(item for item in sample_catalog(self.snapshot, mnist_assets_root=self.assets_root)["samples"] if item["id"] == "tinyml/mnist-shell")
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["fingerprint"], preview["provenance"].get("source_fingerprint", preview["provenance"]["fingerprint"]))
        self.assertEqual(entry["file_count"], len(preview["files"]))

    def test_missing_source_and_module_remain_explainable_catalogue_entries(self):
        for path in (self.source / "src/main.cpp", self.module / "zephyr/module.yml"):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.unlink()
                try:
                    with self.assertRaises((ValueError, OSError)):
                        self.preview()
                    entry = next(item for item in sample_catalog(self.snapshot, mnist_assets_root=self.assets_root)["samples"] if item["id"] == "tinyml/mnist-shell")
                    self.assertNotEqual(entry["status"], "available")
                    self.assertTrue(entry["diagnostics"])
                finally:
                    path.write_bytes(original)

    def test_mismatched_assets_and_malformed_metadata_are_rejected(self):
        paths = (self.assets_root / "model/mnist.tflite", self.assets_root / "data/test_images.bin",
                 self.assets_root / "model/metadata.json")
        for path in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"changed" if path.suffix != ".json" else b"{}")
                    with self.assertRaises(ValueError):
                        self.preview()
                finally:
                    path.write_bytes(original)

    def test_symlinked_source_files_directories_and_module_inputs_are_rejected(self):
        for path in (self.source / "src/main.cpp", self.assets_root / "model", self.module / "zephyr/module.yml"):
            with self.subTest(path=path):
                original = path.with_name(path.name + ".original")
                path.rename(original)
                path.symlink_to(original, target_is_directory=original.is_dir())
                try:
                    with self.assertRaises((ValueError, OSError)):
                        self.preview()
                finally:
                    path.unlink()
                    original.rename(path)

    def test_source_module_and_reviewed_output_changes_invalidate_a_saved_plan(self):
        for path in (self.source / "src/main.cpp", self.source / "LICENSE",
                     self.module / "tensorflow/lite/micro/micro_interpreter.h"):
            with self.subTest(path=path):
                plan = self.plan()
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"\n# changed local input\n")
                    with self.assertRaises((ValueError, OSError)):
                        validate_sample_plan(plan)
                finally:
                    path.write_bytes(original)
        plan = self.plan()
        plan["files"][0]["content"] += "changed after review\n"
        with self.assertRaises(ValueError):
            validate_sample_plan(plan)
        plan = self.plan()
        plan["profile"]["target"] = "nucleo_f446re/stm32f446xx"
        with self.assertRaises(ValueError):
            validate_sample_plan(plan)

    def test_unpinned_module_and_cancelled_preview_are_rejected(self):
        (self.module / "REVISION").write_text("0" * 40 + "\n")
        with self.assertRaises(ValueError):
            self.preview()
        (self.module / "REVISION").write_text(TFLM_REVISION + "\n")
        cancel = Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            preview_sample(self.snapshot, "tinyml/mnist-shell", self.recipe, cancel=cancel,
                           mnist_assets_root=self.assets_root)

    def test_runtime_rejects_dynamic_missing_and_external_headers(self):
        path = self.source / "src/main.cpp"
        original = path.read_text()
        for dependency in ('#include HEADER_NAME\n', '#include "missing.hpp"\n',
                           '#include "../outside.hpp"\n', '#include <unreviewed/library.hpp>\n',
                           '#include_next <zephyr/kernel.h>\n', '#embed "payload.bin"\n'):
            with self.subTest(dependency=dependency):
                path.write_text(original + dependency)
                with self.assertRaises(ValueError):
                    self.preview()
        path.write_text(original)
        self.assertEqual(self.preview()["provenance"]["id"], "tinyml/mnist-shell")

    def test_literal_shell_prompt_survives_but_dynamic_config_values_are_rejected(self):
        path = self.source / "prj.conf"
        original = path.read_text()
        files = {item["path"]: item["content"] for item in self.preview()["files"]}
        self.assertEqual(files["prj.conf"], original)
        self.assertIn('CONFIG_SHELL_PROMPT_UART="mnist:~$ "', files["prj.conf"])
        for value in ('"${ENV_VALUE}"', '"$(command)"', '"$NAME"'):
            with self.subTest(value=value):
                path.write_text(original + f"CONFIG_DYNAMIC_INPUT={value}\n")
                with self.assertRaises(ValueError):
                    self.preview()
        path.write_text(original)

    def test_legacy_or_unrecognized_cmake_is_never_used_as_a_template(self):
        marker = self.workspace / "unexpected-side-effect"
        original = f'execute_process(COMMAND touch "{marker}")\ninclude(../unreviewed.cmake)\n'
        (self.source / "CMakeLists.txt").write_text(original)
        with patch("subprocess.run", side_effect=AssertionError("Preview must not execute source instructions")):
            preview = self.preview(self.profile())
        files = {item["path"]: item["content"] for item in preview["files"]}
        self.assertNotIn("upstream/mnist/CMakeLists.txt", files)
        self.assertNotIn("execute_process", files["CMakeLists.txt"])
        self.assertNotIn("unreviewed.cmake", files["CMakeLists.txt"])
        self.assertFalse(marker.exists())

    def test_invalid_module_yaml_explains_unsupported_catalogue_entry(self):
        (self.module / "zephyr/module.yml").write_text("name: tflite-micro\nbuild: [unterminated\n")
        with self.assertRaises(ValueError):
            self.preview()
        entry = next(item for item in sample_catalog(self.snapshot, mnist_assets_root=self.assets_root)["samples"] if item["id"] == "tinyml/mnist-shell")
        self.assertEqual(entry["status"], "unsupported")
        self.assertTrue(entry["diagnostics"])

    def test_dependency_validation_detects_content_changes_beneath_pinned_head(self):
        provenance = self.preview()["provenance"]
        validate_mnist_dependency(provenance)
        header = self.module / "tensorflow/lite/micro/micro_interpreter.h"
        header.write_text(header.read_text() + "// Changed runtime dependency.\n")
        self.assertEqual((self.module / "REVISION").read_text().strip(), TFLM_REVISION)
        with self.assertRaises(ValueError):
            validate_mnist_dependency(provenance)

    def test_model_metadata_and_runtime_dimensions_cannot_disagree(self):
        cases = (
            ("model/model_metadata.hpp", lambda content: content.replace(digest(self.model), "0" * 64)),
            ("model/model_metadata.hpp", lambda content: content.replace("0.972200000f", "0.500000000f")),
            ("src/assets.hpp", lambda content: content.replace("kWidth = 28", "kWidth = 32")),
            ("src/inference.hpp", lambda content: content.replace("16 * 1024", "8 * 1024")),
            ("data/test_samples.inc", lambda content: content.replace("{0, 0, 0,", "{100, 0, 0,", 1)),
        )
        for relative, change in cases:
            with self.subTest(relative=relative):
                path = (self.assets_root if relative.startswith(("model/", "data/")) else self.source) / relative
                original = path.read_text()
                try:
                    path.write_text(change(original))
                    with self.assertRaises(ValueError):
                        self.preview()
                finally:
                    path.write_text(original)

    def test_commented_expected_constants_do_not_hide_invalid_runtime_metadata(self):
        cases = (
            ("src/assets.hpp", "kWidth = 28;", "kWidth = 32;"),
            ("src/inference.hpp", "kArenaSize = 16 * 1024;", "kArenaSize = 8 * 1024;"),
            ("model/model_metadata.hpp", f'kModelSha256[] = "{digest(self.model)}";',
             f'kModelSha256[] = "{"0" * 64}";'),
        )
        for relative, expected, changed in cases:
            with self.subTest(relative=relative):
                path = (self.assets_root if relative.startswith(("model/", "data/")) else self.source) / relative
                original = path.read_text()
                try:
                    path.write_text("// Expected old value: " + expected + "\n" + original.replace(expected, changed))
                    with self.assertRaises(ValueError):
                        self.preview()
                finally:
                    path.write_text(original)

    def test_projects_review_and_publication_recheck_source_and_publish_exact_copy(self):
        (self.workspace / ".west").mkdir()
        sdk = self.workspace / "sdk"
        sdk.mkdir()
        snapshot = {
            **self.snapshot, "schema_version": 1, "id": "synthetic-environment", "status": "complete",
            "fingerprint": "environment-fingerprint", "request": {},
            "workspace": {"path": str(self.workspace), "west_topdir": str(self.workspace),
                          "manifest_resolved": True, "manifest_path": str(self.zephyr / "west.yml")},
            "boards": [{"id": "board-one", "name": "nrf52840dk", "status": "discovered",
                        "targets": ["nrf52840dk/nrf52840"]}],
            "sdks": [{"id": "sdk-one", "path": str(sdk), "status": "discovered",
                      "toolchains": [{"name": "arm-zephyr-eabi", "family": "GNU"}]}],
            "modules": [], "roots": {},
            "tools": [{"name": name, "path": sys.executable, "status": "discovered"}
                      for name in ("Python", "cmake", "ninja")],
        }
        provenance = self.preview()["provenance"]
        choice = provenance.get("source_fingerprint", provenance["fingerprint"])
        body = {"name": "portable_mnist", "board_id": "board-one", "target": "nrf52840dk/nrf52840",
                "sdk_id": "sdk-one", "template_id": "local-sample", "sample_id": "tinyml/mnist-shell",
                "sample_fingerprint": choice}
        plan = preview_project(self.workspace, body, snapshot, recipe_factory=lambda *args: deepcopy(self.recipe),
                               mnist_assets_root=self.assets_root)
        destination = Path(plan["destination"])
        self.assertFalse(destination.exists())
        source = self.source / "src/main.cpp"
        original = source.read_text()
        source.write_text(original + "// Concurrent source edit.\n")
        with self.assertRaises(ValueError):
            apply_project(plan)
        self.assertFalse(destination.exists())
        source.write_text(original)
        plan = preview_project(self.workspace, body, snapshot, recipe_factory=lambda *args: deepcopy(self.recipe),
                               mnist_assets_root=self.assets_root)
        with patch("subprocess.run", side_effect=AssertionError("Creation must not configure, build or flash")):
            project = apply_project(plan)
        self.assertEqual(project["sample"], plan["sample"])
        self.assertIn("project(portable_mnist)", (destination / "CMakeLists.txt").read_text())
        self.assertNotIn("mnist_shell", (destination / "CMakeLists.txt").read_text())
        self.assertEqual(project["default_overlay"]["target"], "nrf52840dk/nrf52840")
        for item in plan["files"]:
            self.assertEqual((destination / item["path"]).read_bytes(), item["content"].encode())
        self.assertFalse((destination / "build").exists())
