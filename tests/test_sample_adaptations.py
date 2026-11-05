"""RGB adaptations bind reviewed hardware inputs and preserve source evidence."""

from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from test_project_samples import sample_fixture, SPDX
from zephyr_utilities.features.projects import sample_adaptations
from zephyr_utilities.features.projects.samples import preview_sample, validate_sample_plan


def rgb_fixture(root):
    """Use synthetic licensed inputs with a test-only reviewed digest table."""
    hashes = {}
    for path in sample_adaptations._KNOWN_INPUTS:
        content = SPDX + f"/* Synthetic adapter input: {path} */\n"
        if path.endswith(".overlay"):
            content += '#include <zephyr/dt-bindings/led/led.h>\n/ { aliases { led-strip = &strip; }; };\n'
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
        hashes[path] = hashlib.sha256(content.encode()).hexdigest()
    for path in ("device.h", "kernel.h", "sys/printk.h", "sys/util.h", "dt-bindings/led/led.h"):
        destination = root / "include/zephyr" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(SPDX)
    return hashes


class RgbSampleAdaptationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="rgb-adaptation-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.zephyr = self.root / "zephyr"
        self.source = sample_fixture(self.zephyr, "basic/blinky")
        self.snapshot = {"zephyr": {"path": str(self.zephyr), "version": "synthetic", "commit": "fixture"}}
        self.recipe = {"cwd": str(self.root), "display_configure": "cmake configure", "display_build": "cmake build"}
        self.hashes = rgb_fixture(self.zephyr)
        self.patcher = patch.object(sample_adaptations, "_KNOWN_INPUTS", self.hashes)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def profile(self, board="esp32c3_devkitc"):
        board_file = self.zephyr / "boards/espressif" / board / "board.yml"
        return {**self.snapshot, "target": board + "/esp32c3",
                "board_id": hashlib.sha256(f"{board_file}:{board}".encode()).hexdigest()[:20],
                "roots": {"boards": [{"path": str(self.zephyr)}]}}

    def preview(self, profile=None):
        return preview_sample(self.snapshot, "basic/blinky", self.recipe, profile=profile)

    def plan(self, profile=None):
        profile = self.profile() if profile is None else profile
        result = self.preview(profile)
        return {"profile": profile, "recipe": self.recipe, "sample": result["provenance"], "files": result["files"]}

    def test_both_exact_targets_get_reviewed_blink_files_and_original_evidence(self):
        before = {str(path): path.read_bytes() for path in self.zephyr.rglob("*") if path.is_file()}
        base = self.preview()["provenance"]
        fingerprints = []
        for board in ("esp32c3_devkitc", "esp32c3_devkitm"):
            with self.subTest(board=board), patch("subprocess.run", side_effect=AssertionError("Do not execute tools")):
                plan = self.plan(self.profile(board))
                provenance = plan["sample"]
                files = {item["path"]: item["content"] for item in plan["files"]}
                self.assertEqual(provenance["schema_version"], 3)
                self.assertEqual(provenance["source_fingerprint"], base["fingerprint"])
                self.assertEqual(provenance["files"], base["files"])
                self.assertEqual(provenance["attribution"], base["attribution"])
                self.assertNotEqual(provenance["fingerprint"], base["fingerprint"])
                fingerprints.append(provenance["fingerprint"])
                self.assertEqual(provenance["adaptation"]["target"], board + "/esp32c3")
                self.assertIn("500 ms", provenance["adaptation"]["message"])
                for path in self.source.rglob("*"):
                    if path.is_file():
                        self.assertEqual(files["upstream/basic-blinky/" + path.relative_to(self.source).as_posix()], path.read_text())
                for name in ("LICENSE", "REUSE.toml"):
                    self.assertEqual(files[name], (self.zephyr / name).read_text())
                self.assertNotIn("DT_ALIAS(led0)", files["src/main.c"])
                self.assertIn("led_strip_update_rgb", files["src/main.c"])
                self.assertIn("K_MSEC(500)", files["src/main.c"])
                self.assertIn("CONFIG_LED_STRIP=y", files["prj.conf"])
                self.assertIn(board + "/esp32c3", files["CMakeLists.txt"])
                self.assertIn("Regenerate the sample", files["CMakeLists.txt"])
                self.assertIn("ADAPTATION.md", files["ZEPHYR-SAMPLE.md"])
                self.assertIn(self.recipe["display_build"], files["ZEPHYR-SAMPLE.md"])
                self.assertEqual(json.loads(files["zephyr-sample.json"]), provenance)
                self.assertEqual(validate_sample_plan(plan), provenance)
        self.assertNotEqual(fingerprints[0], fingerprints[1])
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.zephyr.rglob("*") if path.is_file()})

    def test_catalogue_and_other_targets_keep_original_sample_and_legacy_plans(self):
        base = self.preview()
        self.assertEqual(base["provenance"]["schema_version"], 1)
        for target in ("some_other_board/esp32c3", "esp32c3_devkitc", "esp32c3_devkitc/esp32c3/custom", "esp32c3_devkitc@1/esp32c3"):
            with self.subTest(target=target):
                current = self.preview({**self.profile(), "target": target})
                self.assertNotIn("adaptation", current["provenance"])
                self.assertEqual(current["provenance"]["source_fingerprint"], base["provenance"]["fingerprint"])
                self.assertEqual(current["provenance"]["default_overlay"]["target"], target)
                source = next(item for item in current["files"] if item["path"] == "src/main.c")
                self.assertEqual(source, next(item for item in base["files"] if item["path"] == "src/main.c"))
        legacy = {"profile": self.profile(), "recipe": self.recipe, "sample": base["provenance"], "files": base["files"]}
        self.assertEqual(validate_sample_plan(legacy), base["provenance"])

    def test_official_names_do_not_authorize_custom_board_identity_or_roots(self):
        profiles = [{**self.profile(), "board_id": "custom"},
                    {**self.profile(), "zephyr": {"path": str(self.root)}}]
        profiles += [{**self.profile(), "roots": {kind: [{"path": str(self.root)}]}}
                     for kind in ("boards", "socs", "dts")]
        for profile in profiles:
            with self.subTest(profile=profile), self.assertRaisesRegex(ValueError, "official|custom|discovered active"):
                self.preview(profile)

    def test_discovered_active_module_devicetree_roots_are_supported_and_bound(self):
        profile = self.profile()
        module_path = self.root / "modules/hal/espressif"
        profile["modules"] = [{"name": "hal_espressif", "path": str(module_path), "status": "discovered",
                               "is_module": True, "active": True}]
        profile["roots"]["dts"] = [{"path": str(module_path), "origin": "module:hal_espressif"}]
        plan = self.plan(profile)
        self.assertEqual(validate_sample_plan(plan), plan["sample"])
        self.assertEqual(plan["sample"]["adaptation"]["environment_roots"], profile["roots"])
        for change in (lambda value: value["modules"][0].update(active=False),
                       lambda value: value["modules"][0].update(status="unsupported"),
                       lambda value: value["roots"]["dts"][0].update(origin="explicit"),
                       lambda value: value["roots"]["dts"][0].update(path=str(self.root)),
                       lambda value: value["roots"]["dts"][0].update(path=str(module_path / "dts"))):
            modified = deepcopy(plan)
            change(modified["profile"])
            with self.assertRaises(ValueError):
                validate_sample_plan(modified)

    def test_changed_board_overlay_driver_and_api_inputs_fail_before_copy(self):
        paths = ["boards/espressif/esp32c3_devkitc/board.yml",
                 "boards/espressif/esp32c3_devkitc/esp32c3_devkitc.dts",
                 "boards/espressif/esp32c3_devkitc/esp32c3_devkitc-pinctrl.dtsi",
                 "samples/drivers/led/led_strip/boards/esp32c3_devkitm.overlay",
                 "samples/drivers/led/led_strip/src/main.c", "include/zephyr/drivers/led_strip.h",
                 "drivers/led_strip/Kconfig.ws2812", "drivers/led_strip/ws2812_spi.c",
                 "dts/bindings/led_strip/worldsemi,ws2812-spi.yaml"]
        for path in paths:
            with self.subTest(path=path):
                plan = self.plan()
                destination = self.zephyr / path
                original = destination.read_bytes()
                try:
                    destination.write_bytes(original + b"/* Changed input */\n")
                    with self.assertRaisesRegex(ValueError, "changed local input"):
                        validate_sample_plan(plan)
                    with self.assertRaisesRegex(ValueError, "changed local input"):
                        self.preview(self.profile())
                finally:
                    destination.write_bytes(original)

    def test_missing_and_symlinked_adapter_inputs_are_rejected(self):
        path = self.zephyr / "drivers/led_strip/ws2812_spi.c"
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "supported local input"):
            self.preview(self.profile())
        elsewhere = self.root / "driver.c"
        elsewhere.write_bytes(original)
        path.symlink_to(elsewhere)
        with self.assertRaisesRegex(ValueError, "supported local input"):
            self.preview(self.profile())

    def test_reviewed_generated_bytes_and_adapter_metadata_cannot_be_forged(self):
        original = self.plan()
        for change in (lambda plan: plan["sample"]["adaptation"].update(version=2),
                       lambda plan: plan["sample"].update(source_fingerprint="0" * 64),
                       lambda plan: plan["profile"].update(target="esp32c3_devkitm/esp32c3"),
                       lambda plan: plan["files"][0].update(content="changed"),
                       lambda plan: plan["sample"]["adaptation"]["inputs"].clear()):
            with self.subTest(change=change):
                plan = deepcopy(original)
                change(plan)
                with self.assertRaises(ValueError):
                    validate_sample_plan(plan)
        plan = deepcopy(original)
        plan["sample"]["schema_version"] = 4
        with self.assertRaisesRegex(ValueError, "Unsupported.*schema"):
            validate_sample_plan(plan)

    def test_adapter_checks_cancellation_between_additional_inputs(self):
        cancel = Event()
        original = sample_adaptations._profile_board

        def cancel_after_sample(*args):
            board = original(*args)
            cancel.set()
            return board

        with patch.object(sample_adaptations, "_profile_board", side_effect=cancel_after_sample):
            with self.assertRaises(CancelledError):
                preview_sample(self.snapshot, "basic/blinky", self.recipe, profile=self.profile(), cancel=cancel)
