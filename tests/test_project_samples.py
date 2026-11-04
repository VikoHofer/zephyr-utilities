"""Local sample previews retain upstream bytes and reject incomplete copies."""

from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects import samples
from zephyr_utilities.features.projects.samples import preview_sample, sample_catalog, validate_sample_plan


LICENSE = "Apache License\nVersion 2.0, January 2004\nSynthetic test fixture, not license terms.\n"
REUSE = '''version = 1
[[annotations]]
path = ["**/*.conf", "**/*.rst", "**/*.yaml", "**/CMakeLists.txt"]
SPDX-License-Identifier = "Apache-2.0"
SPDX-FileCopyrightText = "Copyright Synthetic Contributors"
'''
SPDX = "/* SPDX-License-Identifier: Apache-2.0 */\n"


def sample_fixture(zephyr, sample_id="hello_world"):
    """Create a small licensed sample fixture; root license text is synthetic."""
    zephyr = Path(zephyr)
    zephyr.mkdir(parents=True, exist_ok=True)
    for name, content in (("LICENSE", LICENSE), ("REUSE.toml", REUSE)):
        if not (zephyr / name).exists():
            (zephyr / name).write_text(content)
    root = zephyr / "samples" / sample_id
    (root / "src").mkdir(parents=True)
    (root / "CMakeLists.txt").write_text(
        "# SPDX-License-Identifier: Apache-2.0\n"
        "cmake_minimum_required(VERSION 3.20.0)\n"
        "find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})\n"
        f"project({sample_id.split('/')[-1]})\n"
        "target_sources(app PRIVATE src/main.c)\n")
    source = '#include <stdio.h>\nint main(void) { return 0; }\n'
    if sample_id == "basic/blinky":
        source = ('#include <zephyr/drivers/gpio.h>\n#define LED0_NODE DT_ALIAS(led0)\n'
                  'static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(LED0_NODE, gpios);\n'
                  'int main(void) { return 0; }\n')
    elif sample_id == "basic/button":
        source = ('#include <zephyr/input/input.h>\nINPUT_CALLBACK_DEFINE(NULL, NULL, NULL);\n'
                  'int main(void) { return 0; }\n')
    for header in ("drivers/gpio.h", "input/input.h"):
        path = zephyr / "include/zephyr" / header
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SPDX)
    (root / "src/main.c").write_text(SPDX + source)
    (root / "prj.conf").write_text("CONFIG_PRINTK=y\n")
    (root / "README.rst").write_text("Synthetic sample\n****************\n\nRead local hardware requirements.\n")
    (root / "sample.yaml").write_text("sample:\n  name: Synthetic sample\ntests:\n  sample.synthetic:\n    tags: test\n")
    return root


class LocalSampleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sample-cloning-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.zephyr = self.root / "zephyr"
        self.source = sample_fixture(self.zephyr)
        self.snapshot = {"zephyr": {"path": str(self.zephyr), "version": "4.4.2", "commit": "abc123", "dirty": True}}
        self.recipe = {"cwd": str(self.root), "display_configure": "cmake -S app -B app/build/manual",
                       "display_build": "cmake --build app/build/manual"}

    def preview(self, sample_id="hello_world"):
        return preview_sample(self.snapshot, sample_id, self.recipe)

    def plan(self):
        value = self.preview()
        return {"profile": deepcopy(self.snapshot), "recipe": deepcopy(self.recipe),
                "files": value["files"], "sample": value["provenance"]}

    def test_preview_retains_all_upstream_bytes_licenses_and_independent_commands(self):
        (self.source / "boards").mkdir()
        (self.source / "boards/example.overlay").write_text(SPDX + "/ { aliases { led0 = &led; }; };\n")
        (self.source / "boards/example.conf").write_text("CONFIG_GPIO=y\n")
        (self.zephyr / "NOTICE").write_text("Synthetic attribution notice.\n")
        before = {str(path): path.read_bytes() for path in self.zephyr.rglob("*") if path.is_file()}
        with patch("subprocess.run", side_effect=AssertionError("Must not run tools")):
            value = self.preview()
        files = {item["path"]: item for item in value["files"]}
        for path in self.source.rglob("*"):
            if path.is_file():
                self.assertEqual(files[path.relative_to(self.source).as_posix()]["content"].encode(), path.read_bytes())
        for name in ("LICENSE", "REUSE.toml", "NOTICE"):
            self.assertEqual(files[name]["content"], (self.zephyr / name).read_text())
        self.assertIn(self.recipe["display_configure"], files["ZEPHYR-SAMPLE.md"]["content"])
        self.assertIn(self.recipe["display_build"], files["ZEPHYR-SAMPLE.md"]["content"])
        self.assertEqual(json.loads(files["zephyr-sample.json"]["content"]), value["provenance"])
        self.assertEqual(value["provenance"]["zephyr"]["revision"], "abc123")
        self.assertNotIn("dirty", value["provenance"]["zephyr"])
        self.assertEqual(value["provenance"]["checkout_metadata"], "saved environment observation")
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.zephyr.rglob("*") if path.is_file()})
        for item in value["files"]:
            self.assertEqual(item["sha256"], hashlib.sha256(item["content"].encode()).hexdigest())

    def test_catalogue_explains_missing_and_unsupported_samples_without_writing(self):
        blinky = sample_fixture(self.zephyr, "basic/blinky")
        (blinky / "CMakeLists.txt").write_text("include(../../external.cmake)\n")
        catalogue = sample_catalog(self.snapshot)
        values = {item["id"]: item for item in catalogue["samples"]}
        self.assertEqual(values["hello_world"]["status"], "available")
        self.assertEqual(values["hello_world"]["fingerprint"], self.preview()["provenance"]["fingerprint"])
        self.assertEqual(values["hello_world"]["file_count"], len(self.preview()["files"]))
        self.assertEqual(values["basic/blinky"]["status"], "unsupported")
        self.assertIn("CMake", values["basic/blinky"]["diagnostics"][0])
        self.assertEqual(values["basic/button"]["status"], "missing")
        self.assertEqual(sample_catalog(None)["samples"], [])

    def test_requirements_follow_current_and_legacy_button_implementations(self):
        sample_fixture(self.zephyr, "basic/blinky")
        button = sample_fixture(self.zephyr, "basic/button")
        self.assertIn("led0", " ".join(self.preview("basic/blinky")["provenance"]["requirements"]))
        self.assertIn("KEY events", " ".join(self.preview("basic/button")["provenance"]["requirements"]))
        (button / "src/main.c").write_text(SPDX + "#define SW DT_ALIAS(sw0)\nint main(void) { gpio_pin_interrupt_configure(); }\n")
        self.assertIn("sw0", " ".join(self.preview("basic/button")["provenance"]["requirements"]))
        (button / "src/main.c").write_text(SPDX + "int main(void) { return 0; }\n")
        with self.assertRaisesRegex(ValueError, "requirement adapter"):
            self.preview("basic/button")

    def test_valid_plan_rechecks_and_forged_provenance_or_generated_bytes_fail(self):
        plan = self.plan()
        self.assertEqual(validate_sample_plan(plan), plan["sample"])
        for change in (lambda value: value["sample"].update(title="Forged"),
                       lambda value: value["files"].pop(),
                       lambda value: value["files"][0].update(content="changed"),
                       lambda value: value["sample"]["licenses"].clear()):
            altered = deepcopy(plan)
            change(altered)
            with self.assertRaisesRegex(ValueError, "changed after preview"):
                validate_sample_plan(altered)

    def test_changed_added_removed_and_license_inputs_invalidate_retained_plan(self):
        paths = [self.source / "src/main.c", self.source / "src/new.h", self.source / "README.rst",
                 self.zephyr / "LICENSE", self.zephyr / "REUSE.toml", self.zephyr / "NOTICE"]
        for path in paths:
            with self.subTest(path=path):
                plan = self.plan()
                before = path.read_bytes() if path.exists() else None
                try:
                    if path.name == "README.rst":
                        path.unlink()
                    else:
                        path.write_bytes((before or SPDX.encode()) + b"\n# extra evidence\n")
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        validate_sample_plan(plan)
                finally:
                    if before is None:
                        path.unlink()
                    else:
                        path.write_bytes(before)

    def test_replaced_sample_directory_invalidates_even_identical_contents(self):
        plan = self.plan()
        original = self.source.with_name("original")
        self.source.rename(original)
        self.source.mkdir()
        for path in original.rglob("*"):
            target = self.source / path.relative_to(original)
            if path.is_dir():
                target.mkdir()
            else:
                target.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, "changed after preview"):
            validate_sample_plan(plan)

    def test_unsafe_or_dynamic_cmake_is_rejected_without_execution(self):
        path = self.source / "CMakeLists.txt"
        good = path.read_text()
        for content in (good + "execute_process(COMMAND touch marker)\n", good + "include(../shared.cmake)\n",
                        good.replace("src/main.c", "../sibling.c"), good.replace("src/main.c", "${SOURCES}"),
                        good.replace("src/main.c", '"src/main.c"'), good + "add_subdirectory(child)\n",
                        "#[[\n" + good + "]]\n", good.replace("project(hello_world)", "project(hello_world)\\\n")):
            with self.subTest(content=content):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    self.preview()
        self.assertFalse((self.root / "marker").exists())

    def test_external_dynamic_and_obscured_source_includes_are_rejected(self):
        path = self.source / "src/main.c"
        good = path.read_text()
        includes = ['#include "../other.h"', '#include "missing.h"', "#include HEADER", "#include <module/header.h>",
                    '#include_next "outside.h"', '#embed "outside.bin"', '#inc\\\nlude "../outside.h"',
                    '%:include "../outside.h"', '??=include "../outside.h"', '#include <zephyr/../outside.h>']
        for include in includes:
            with self.subTest(include=include):
                path.write_text(good + "\n" + include + "\n")
                with self.assertRaises(ValueError):
                    self.preview()
        path.write_text(good + '\n#include "local.h"\n')
        (self.source / "src/local.h").write_text(SPDX + "#define LOCAL 1\n")
        self.assertIn("src/local.h", {item["path"] for item in self.preview()["files"]})

    def test_external_overlay_and_configuration_dependencies_are_rejected(self):
        path = self.source / "app.overlay"
        for content in ('/include/ "../external.dtsi"', '/ { value = /incbin/("outside.bin"); };'):
            path.write_text(SPDX + content + "\n")
            with self.assertRaises(ValueError):
                self.preview()
        path.unlink()
        (self.source / "prj.conf").write_text('CONFIG_NAME="$(shell external)"\n')
        with self.assertRaisesRegex(ValueError, "configuration input"):
            self.preview()

    def test_links_special_files_binary_data_and_generated_inputs_are_not_silently_omitted(self):
        (self.root / "external.h").write_text(SPDX)
        candidate = self.source / "bad.h"
        cases = [lambda: candidate.symlink_to(self.root / "external.h"), lambda: os.mkfifo(candidate),
                 lambda: candidate.write_bytes(b"\x00binary"), lambda: candidate.write_bytes(b"\xffinvalid")]
        for prepare in cases:
            prepare()
            try:
                with self.assertRaises(ValueError):
                    self.preview()
            finally:
                candidate.unlink()
        for name in ("build", ".hidden", "scripts", "empty"):
            candidate = self.source / name
            candidate.mkdir()
            try:
                with self.assertRaises(ValueError):
                    self.preview()
            finally:
                candidate.rmdir()
        for name in ("CMakeCache.txt", "script.py", "LICENSE", ".gitignore", "REUSE.toml", "zephyr-sample.json"):
            candidate = self.source / name
            candidate.write_text(SPDX)
            try:
                with self.assertRaises(ValueError):
                    self.preview()
            finally:
                candidate.unlink()

    def test_limits_fail_closed_instead_of_showing_a_partial_catalogue(self):
        for constant, limit in (("MAX_FILES", 7), ("MAX_FILE_BYTES", 20), ("MAX_TOTAL_BYTES", 300), ("MAX_DEPTH", 1)):
            with self.subTest(constant=constant), patch.object(samples, constant, limit):
                value = sample_catalog(self.snapshot)["samples"][0]
                self.assertEqual(value["status"], "unsupported")
                self.assertIsNone(value["fingerprint"])

    def test_missing_license_or_conflicting_attribution_blocks_copy(self):
        path = self.source / "src/main.c"
        good = path.read_text()
        for content in (good.replace("Apache-2.0", "MIT"), good.replace(SPDX, "")):
            path.write_text(content)
            with self.assertRaisesRegex(ValueError, "license"):
                self.preview()
        path.write_text(good)
        (self.zephyr / "LICENSE").unlink()
        with self.assertRaises(FileNotFoundError):
            self.preview()

    def test_complex_reuse_annotations_require_an_adapter_update(self):
        path = self.zephyr / "REUSE.toml"
        for content in (REUSE + '\n[[annotations]]\npath = ["src/main.c"]\nSPDX-License-Identifier = "MIT"\n',
                        REUSE.replace("Apache-2.0", "MIT"), REUSE.replace("version = 1", "version = 2")):
            path.write_text(content)
            with self.assertRaisesRegex(ValueError, "REUSE"):
                self.preview()

    def test_sample_metadata_cannot_request_unhandled_build_inputs(self):
        path = self.source / "sample.yaml"
        good = path.read_text()
        for addition in ("    extra_args: EXTERNAL=../shared\n", "    required_modules: [external]\n", "    sysbuild: true\n"):
            path.write_text(good + addition)
            with self.assertRaisesRegex(ValueError, "additional build inputs"):
                self.preview()

    def test_invalid_sample_ids_and_cancelled_reads_do_not_execute_or_write(self):
        for sample_id in (None, "", "../hello_world", "samples/hello_world", {}, ["hello_world"]):
            with self.subTest(sample_id=sample_id), self.assertRaisesRegex(ValueError, "available local samples"):
                self.preview(sample_id)
        cancel = Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            preview_sample(self.snapshot, "hello_world", self.recipe, cancel=cancel)
        self.assertFalse((self.root / "applications").exists())

    def test_unsupported_host_reports_unavailable_cloning_and_deep_yaml_is_bounded(self):
        with patch.object(samples.os, "O_NOFOLLOW", 0):
            value = sample_catalog(self.snapshot)
            self.assertTrue(all(item["status"] == "unsupported" for item in value["samples"]))
            self.assertIn("no-follow", value["samples"][0]["diagnostics"][0])
        (self.source / "sample.yaml").write_text("nested: " + "[" * 1500 + "0" + "]" * 1500 + "\n")
        self.assertEqual(sample_catalog(self.snapshot)["samples"][0]["status"], "unsupported")
