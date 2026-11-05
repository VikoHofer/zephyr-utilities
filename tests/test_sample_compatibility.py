"""Configured sample requirements are conservative, bounded and disposable."""

from concurrent.futures import CancelledError
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects import sample_compatibility as checker
from zephyr_utilities.features.projects.probing import ProbeError


SOURCES = {
    "hello": ("hello_world", "int main(void) { return 0; }"),
    "led": ("basic/blinky", "DT_ALIAS(led0) GPIO_DT_SPEC_GET(LED_NODE, gpios)"),
    "strip": ("basic/blinky", "DT_ALIAS(led_strip) led_strip_update_rgb(strip, pixels, 1)"),
    "legacy": ("basic/button", "DT_ALIAS(sw0) gpio_pin_interrupt_configure(button, flags)"),
    "input": ("basic/button", "#include <zephyr/input/input.h>\nINPUT_CALLBACK_DEFINE(NULL, callback, NULL)"),
}


def files(kind="led"):
    return [{"path": "CMakeLists.txt", "content": "# Synthetic configuration"},
            {"path": "prj.conf", "content": "CONFIG_GPIO=y\n"},
            {"path": "src/main.c", "content": SOURCES[kind][1]}]


def node(**changes):
    return {"path": "/leds/led", "enabled": True, "compatible": [],
            "gpios": {"valid": True, "enabled": True, "controller": "/gpio"},
            "chain_length": None, "code": None, **changes}


def facts(**aliases):
    return {"schema_version": 1, "aliases": {"led0": None, "sw0": None, "led-strip": None, **aliases},
            "gpio_keys": []}


class SampleCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sample-check-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.storage = self.root / "feature-data/checks"
        self.source = self.root / "application"
        self.source.mkdir()
        self.profile = {"workspace": str(self.root), "target": "board/chip",
                        "zephyr": {"path": str(self.root / "selected-zephyr")},
                        "tools": [{"name": "Python", "path": sys.executable, "status": "discovered"}]}
        self.data = facts(led0=node())
        self.config = "CONFIG_GPIO=y\n"
        self.captured = []

    def configure(self, profile, source, build, operation, **kwargs):
        self.captured.append((source, build))
        self.assertEqual(operation, "configure")
        self.assertEqual((source / "src/main.c").read_text(), self.checked_files[2]["content"])
        (build / "zephyr").mkdir(parents=True)
        (build / "zephyr/zephyr.dts").write_text("/dts-v1/; / {};\n")
        (build / "zephyr/.config").write_text(self.config)
        self.compile_commands(build, source)
        direct = ["/synthetic/board.dts", str(source / "captured.overlay")]
        return {"status": "configured", "context": {"configured": True, "source_dir": str(source),
                "requested_target": profile["target"], "devicetree": {"files_observed": True, "files": direct,
                "dependencies": direct, "dependency_coverage": "complete"}}}

    def compile_commands(self, build, source, entries=None):
        entries = entries if entries is not None else [{
            "directory": str(build), "file": str(source / "src/main.c"),
            "output": "CMakeFiles/app.dir/src/main.c.obj"}]
        (build / "compile_commands.json").write_text(json.dumps(entries))

    def run_check(self, kind="led", **kwargs):
        self.checked_files = files(kind)
        with patch.object(checker, "execute_build", side_effect=self.configure) as configure, \
                patch.object(checker, "run_probe", return_value=json.dumps(self.data)) as probe:
            result = checker.check_sample(self.profile, self.checked_files, SOURCES[kind][0],
                                          root=self.storage, **kwargs)
        return result, configure, probe

    def inspect(self, kind="led", *, data=None, config=None, dts="/dts-v1/; / {};\n", probe_error=None):
        build = self.root / "output"
        (build / "zephyr").mkdir(parents=True, exist_ok=True)
        (build / "zephyr/zephyr.dts").write_text(dts)
        (build / "zephyr/.config").write_text(self.config if config is None else config)
        self.compile_commands(build, self.source)
        with patch.object(checker, "run_probe", return_value=json.dumps(self.data if data is None else data),
                          side_effect=probe_error) as probe:
            result = checker.inspect_sample_requirements(self.profile, files(kind), SOURCES[kind][0], build,
                                                         source_dir=self.source)
        return result, probe

    def test_explicit_check_copies_reviewed_sources_configures_exact_profile_and_cleans_up(self):
        original = deepcopy(self.profile)
        result, configure, probe = self.run_check(environ={"SECRET": "private"})
        self.assertEqual(result["status"], "compatible")
        self.assertEqual(result["overlay_usage"]["status"], "confirmed")
        self.assertEqual(result["overlay_usage"]["files"][0]["path"], "captured.overlay")
        self.assertEqual(result["overlay_usage"]["files"][0]["state"], "active")
        self.assertIn("not tested", " ".join(result["details"]))
        self.assertEqual(self.profile, original)
        self.assertEqual(configure.call_args.args[0], original)
        self.assertEqual(configure.call_args.kwargs["environ"], {"SECRET": "private"})
        self.assertEqual(probe.call_args.args[0][:4], [sys.executable, "-I", "-B", "-c"])
        self.assertEqual(probe.call_args.args[0][-2], str(self.root / "selected-zephyr/scripts/dts/python-devicetree/src"))
        self.assertNotIn("SECRET", probe.call_args.kwargs["environ"])
        self.assertEqual(list(self.storage.iterdir()), [])
        self.assertTrue(all(not path.exists() for paths in self.captured for path in paths))

    def test_gpio_alias_missing_disabled_bad_specifier_and_controller_are_definite(self):
        for kind, alias in (("led", "led0"), ("legacy", "sw0")):
            for value, text in ((None, "alias"), (node(enabled=False), "disabled"),
                                (node(gpios=None), "specifier"),
                                (node(gpios={"valid": False, "enabled": True}), "specifier"),
                                (node(gpios={"valid": True, "enabled": False}), "controller")):
                with self.subTest(kind=kind, value=value):
                    result, _ = self.inspect(kind, data=facts(**{alias: value}))
                    self.assertEqual(result["status"], "incompatible")
                    self.assertIn(text, result["message"])
            result, _ = self.inspect(kind, data=facts(**{alias: node()}))
            self.assertEqual(result["status"], "compatible")
            result, _ = self.inspect(kind, data=facts(**{alias: node()}), config="# CONFIG_GPIO is not set\n")
            self.assertEqual(result["status"], "incompatible")
            self.assertIn("CONFIG_GPIO", result["message"])

    def test_addressable_led_requires_its_alias_single_pixel_and_enabled_driver(self):
        strip = node(compatible=["worldsemi,ws2812-spi"], chain_length=1, gpios=None)
        config = "CONFIG_LED_STRIP=y\nCONFIG_WS2812_STRIP_SPI=y\nCONFIG_SPI=y\n"
        result, _ = self.inspect("strip", data=facts(**{"led-strip": strip}), config=config)
        self.assertEqual(result["status"], "compatible")
        for change, text in (({"chain_length": 2}, "chain-length"), ({"enabled": False}, "disabled")):
            result, _ = self.inspect("strip", data=facts(**{"led-strip": {**strip, **change}}), config=config)
            self.assertEqual(result["status"], "incompatible")
            self.assertIn(text, result["message"])
        for option in ("CONFIG_LED_STRIP", "CONFIG_WS2812_STRIP_SPI", "CONFIG_SPI"):
            result, _ = self.inspect("strip", data=facts(**{"led-strip": strip}), config=config.replace(option + "=y\n", ""))
            self.assertEqual(result["status"], "incompatible")
            self.assertIn(option, result["message"])
        result, _ = self.inspect("strip", data=facts(**{"led-strip": {**strip, "compatible": ["custom,strip"]}}), config=config)
        self.assertEqual(result["status"], "unknown")
        result, _ = self.inspect("strip", data=facts(led0=node()), config=config)
        self.assertEqual(result["status"], "incompatible")
        self.assertIn("led-strip", result["message"])

    def test_current_button_proves_enabled_input_key_driver_without_requiring_optional_led(self):
        data = facts()
        data["gpio_keys"] = [node(path="/buttons/button", code=11)]
        config = "CONFIG_INPUT=y\nCONFIG_INPUT_GPIO_KEYS=y\nCONFIG_GPIO=y\n"
        result, _ = self.inspect("input", data=data, config=config)
        self.assertEqual(result["status"], "compatible")
        for change, text in (({"code": None}, "zephyr,code"), ({"gpios": None}, "specifier")):
            data["gpio_keys"] = [node(code=11), node(**{"path": "/buttons/broken", "code": 12, **change})]
            result, _ = self.inspect("input", data=data, config=config)
            self.assertEqual(result["status"], "incompatible")
            self.assertIn(text, result["message"])
        result, _ = self.inspect("input", data=facts(), config=config)
        self.assertEqual(result["status"], "unknown")
        data["gpio_keys"] = [node(code=11)]
        result, _ = self.inspect("input", data=data, config="CONFIG_INPUT=y\nCONFIG_GPIO=y\n")
        self.assertEqual(result["status"], "unknown")
        result, _ = self.inspect("input", data=data, config="CONFIG_INPUT_GPIO_KEYS=y\nCONFIG_GPIO=y\n")
        self.assertEqual(result["status"], "incompatible")
        self.assertIn("CONFIG_INPUT", result["message"])

    def test_hello_world_has_no_peripheral_requirement_but_configuration_must_exist(self):
        result, _ = self.inspect("hello", data=facts(), config="")
        self.assertEqual(result["status"], "compatible")
        with patch.object(checker, "execute_build", return_value={"status": "failed", "error": "toolchain missing"}), \
                patch.object(checker, "run_probe") as probe:
            result = checker.check_sample(self.profile, files("hello"), "hello_world", root=self.storage)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("toolchain missing", result["details"])
        probe.assert_not_called()
        self.assertEqual(list(self.storage.iterdir()), [])

    def test_failed_configure_bounds_diagnostic_and_removes_partial_output(self):
        def fail(*args, **kwargs):
            self.configure(*args, **kwargs)
            for i in range(20):
                kwargs["emit"](str(i) + ":" + "x" * 10000)
            return {"status": "failed", "error": "configuration failed"}
        self.checked_files = files()
        with patch.object(checker, "execute_build", side_effect=fail), patch.object(checker, "run_probe") as probe:
            result = checker.check_sample(self.profile, self.checked_files, "basic/blinky", root=self.storage)
        self.assertEqual(result["status"], "unknown")
        self.assertLessEqual(len(result["details"]), 8)
        self.assertTrue(all(len(item) <= 1800 for item in result["details"]))
        self.assertEqual(list(self.storage.iterdir()), [])
        probe.assert_not_called()

    def test_cancellation_before_during_and_after_configuration_cleans_temporary_files(self):
        cancelled = Event()
        cancelled.set()
        with patch.object(checker, "execute_build") as configure, self.assertRaises(CancelledError):
            checker.check_sample(self.profile, files(), "basic/blinky", root=self.storage, cancel=cancelled)
        configure.assert_not_called()
        for outcome in (CancelledError(), {"status": "cancelled"}):
            with self.subTest(outcome=outcome), \
                    patch.object(checker, "execute_build", side_effect=outcome if isinstance(outcome, Exception) else None,
                                 return_value=outcome), self.assertRaises(CancelledError):
                checker.check_sample(self.profile, files(), "basic/blinky", root=self.storage)
            self.assertEqual(list(self.storage.iterdir()), [])
        self.checked_files = files()
        with patch.object(checker, "execute_build", side_effect=self.configure), \
                patch.object(checker, "run_probe", side_effect=CancelledError()), self.assertRaises(CancelledError):
            checker.check_sample(self.profile, self.checked_files, "basic/blinky", root=self.storage)
        self.assertEqual(list(self.storage.iterdir()), [])

    def test_file_validation_rejects_unsafe_duplicate_conflicting_and_oversized_sources_before_execution(self):
        invalid = [[*files(), {"path": path, "content": "x"}] for path in
                   ("../escape", "/absolute", "a/../b", "a//b", "a\\b", "build/x", ".git/config")]
        invalid.extend([[*files(), files()[0]], [*files(), {"path": "src", "content": "conflict"}],
                        [*files(), {"path": "large", "content": "x" * (1024 * 1024 + 1)}],
                        [{"path": str(i), "content": "x"} for i in range(129)]])
        with patch.object(checker, "execute_build") as configure:
            for value in invalid:
                with self.subTest(path=value[-1]["path"]), self.assertRaises(ValueError):
                    checker.check_sample(self.profile, value, "basic/blinky", root=self.storage)
            configure.assert_not_called()
        self.assertFalse(self.storage.exists())

    def test_unknown_sample_variant_and_unresolved_dts_do_not_execute_probe(self):
        changed = files()
        changed[2]["content"] = "// DT_ALIAS(led0) GPIO_DT_SPEC_GET\nint main(void) {}"
        with patch.object(checker, "execute_build") as configure:
            result = checker.check_sample(self.profile, changed, "basic/blinky", root=self.storage)
        self.assertEqual(result["status"], "unknown")
        configure.assert_not_called()
        for directive in ('/include/ "external.dtsi"', '/incbin/("external.bin")'):
            result, probe = self.inspect(dts=directive)
            self.assertEqual(result["status"], "unknown")
            probe.assert_not_called()

    def test_probe_failure_malformed_evidence_and_missing_outputs_stay_unknown(self):
        for error in (ProbeError("unsupported selected dtlib"), ValueError("bad JSON")):
            result, _ = self.inspect(probe_error=error)
            self.assertEqual(result["status"], "unknown")
        for data in ({}, {**facts(), "schema_version": 2}, facts(led0={"enabled": True}),
                     {**facts(), "gpio_keys": [None]}):
            result, _ = self.inspect(data=data)
            self.assertEqual(result["status"], "unknown")
        result = checker.inspect_sample_requirements(self.profile, files(), "basic/blinky", self.root / "missing",
                                                     source_dir=self.source)
        self.assertEqual(result["status"], "unknown")

    def test_existing_configured_build_can_inspect_just_main_without_reconfiguring(self):
        self.inspect()
        with patch.object(checker, "execute_build") as configure, \
                patch.object(checker, "run_probe", return_value=json.dumps(self.data)):
            result = checker.inspect_sample_requirements(self.profile, [files()[2]], "basic/blinky", self.root / "output",
                                                         source_dir=self.source)
            self.assertEqual(result["status"], "compatible")
            result = checker.inspect_sample_requirements(self.profile, [], "basic/blinky", self.root / "output",
                                                         source_dir=self.source)
            self.assertEqual(result["status"], "unknown")
        configure.assert_not_called()

    def test_generated_symlink_and_nonregular_files_are_not_parsed(self):
        build = self.root / "output"
        (build / "zephyr").mkdir(parents=True)
        target = self.root / "outside.dts"
        target.write_text("/dts-v1/; / {};\n")
        self.compile_commands(build, self.source)
        (build / "zephyr/zephyr.dts").symlink_to(target)
        with patch.object(checker, "run_probe") as probe:
            result = checker.inspect_sample_requirements(self.profile, files(), "basic/blinky", build,
                                                         source_dir=self.source)
        self.assertEqual(result["status"], "unknown")
        probe.assert_not_called()
        (build / "zephyr/zephyr.dts").unlink()
        (build / "zephyr/zephyr.dts").mkdir()
        with patch.object(checker, "run_probe") as probe:
            result = checker.inspect_sample_requirements(self.profile, files(), "basic/blinky", build,
                                                         source_dir=self.source)
        self.assertEqual(result["status"], "unknown")
        probe.assert_not_called()

    def test_unused_sample_main_and_other_target_inputs_cannot_reject_an_edited_application(self):
        self.inspect()
        build = self.root / "output"
        for source, output in ((self.source / "src/replacement.c", "CMakeFiles/app.dir/src/replacement.c.obj"),
                               (self.root / "different-app/src/main.c", "CMakeFiles/app.dir/src/main.c.obj"),
                               (self.source / "src/main.c", "CMakeFiles/unused.dir/src/main.c.obj"),
                               (self.source / "src/main.c", str(self.root / "different-build/CMakeFiles/app.dir/main.c.obj"))):
            with self.subTest(source=source, output=output):
                self.compile_commands(build, self.source, [{"directory": str(build), "file": str(source), "output": output}])
                with patch.object(checker, "run_probe", return_value=json.dumps(facts())) as probe:
                    result = checker.inspect_sample_requirements(self.profile, [files()[2]], "basic/blinky", build,
                                                                 source_dir=self.source)
                self.assertEqual(result["status"], "unknown")
                self.assertIn("does not compile", result["details"][0])
                probe.assert_not_called()

    def test_relative_compiler_inputs_and_supported_output_encodings_bind_to_application(self):
        self.inspect()
        build = self.root / "output"
        for output in ({"output": "../output/CMakeFiles/app.dir/src/main.c.obj"},
                       {"arguments": ["cc", "-o", "../output/CMakeFiles/app.dir/src/main.c.obj", "-c", "src/main.c"]},
                       {"command": "cc -o ../output/CMakeFiles/app.dir/src/main.c.obj -c src/main.c"}):
            with self.subTest(output=output):
                self.compile_commands(build, self.source, [{"directory": str(self.source), "file": "src/main.c", **output}])
                with patch.object(checker, "run_probe", return_value=json.dumps(self.data)):
                    result = checker.inspect_sample_requirements(self.profile, [files()[2]], "basic/blinky", build,
                                                                 source_dir=self.source)
                self.assertEqual(result["status"], "compatible")

    def test_missing_malformed_oversized_or_redirected_compile_evidence_stays_unknown(self):
        self.inspect()
        build = self.root / "output"
        commands = build / "compile_commands.json"
        for content in ("{}", "[", "[null]", "[]", "[" * 1200, " " * (8 * 1024 * 1024 + 1)):
            commands.write_text(content)
            with patch.object(checker, "run_probe") as probe:
                result = checker.inspect_sample_requirements(self.profile, [files()[2]], "basic/blinky", build,
                                                             source_dir=self.source)
            self.assertEqual(result["status"], "unknown")
            probe.assert_not_called()
        commands.unlink()
        for redirected in (False, True):
            if redirected:
                outside = self.root / "outside.json"
                outside.write_text("[]")
                commands.symlink_to(outside)
            with patch.object(checker, "run_probe") as probe:
                result = checker.inspect_sample_requirements(self.profile, [files()[2]], "basic/blinky", build,
                                                             source_dir=self.source)
            self.assertEqual(result["status"], "unknown")
            probe.assert_not_called()
        result = checker.inspect_sample_requirements(self.profile, [files()[2]], "basic/blinky", build)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("source directory", result["details"][0])

    def test_symlink_storage_root_is_rejected_before_configuration(self):
        self.storage.parent.mkdir(parents=True)
        self.storage.symlink_to(self.root, target_is_directory=True)
        with patch.object(checker, "execute_build") as configure, self.assertRaises(ValueError):
            checker.check_sample(self.profile, files(), "basic/blinky", root=self.storage)
        configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
