"""Build execution uses captured tools, new outputs and explicit evidence."""

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import shlex
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects.builds import capture_context, command_recipe, execute_build


FAKE_CMAKE = r'''
import os
from pathlib import Path
import subprocess
import sys
import time

args = sys.argv[1:]
if "--build" in args:
    root = Path(args[args.index("--build") + 1])
    if (root / "fail-build").exists():
        print("synthetic compiler diagnostic", flush=True)
        sys.exit(7)
    (root / "zephyr/zephyr.elf").write_bytes(b"synthetic ELF evidence")
    print("compile completed", flush=True)
    sys.exit(0)
root = Path(args[args.index("-B") + 1])
source = Path(args[args.index("-S") + 1])
values = dict(arg[2:].split("=", 1) for arg in args if arg.startswith("-D"))
(root / "zephyr").mkdir()
(root / "CMakeCache.txt").write_text("CMAKE_C_COMPILER:STRING=" + values["ZEPHYR_SDK_INSTALL_DIR"] + "/bin/test-gcc\nAPPLICATION_SOURCE_DIR:PATH=" + str(source) + "\nBOARD:STRING=" + values["BOARD"] + "\n")
(root / "build_info.yml").write_text("cmake:\n  board:\n    name: test_board\n    qualifiers: chip/cpu0\n    revision: '1.0.0'\n  toolchain:\n    name: zephyr\n    path: '" + values["ZEPHYR_SDK_INSTALL_DIR"] + "'\n")
(root / "zephyr/zephyr.dts").write_text("/dts-v1/;\n/ {};\n")
(root / "zephyr/.config").write_text("CONFIG_TEST=y\n")
mode = (source / "mode").read_text() if (source / "mode").exists() else ""
if mode == "fail-build":
    (root / "fail-build").touch()
if mode == "fail-configure":
    print("synthetic configuration diagnostic", flush=True)
    sys.exit(2)
if mode == "wait-child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdout=subprocess.DEVNULL)
    print("child:" + str(child.pid), flush=True)
    time.sleep(60)
if mode == "closed-pipe":
    print("ready-to-cancel", flush=True)
    os.close(1)
    os.close(2)
    time.sleep(60)
if mode == "large-output":
    print("x" * 10000, flush=True)
if mode == "timeout":
    time.sleep(60)
print("configure completed", flush=True)
'''


class BuildExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="creator builds ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "my application"
        self.source.mkdir()
        (self.source / "CMakeLists.txt").write_text("# synthetic fixture\n")
        self.outputs = self.source / "build"
        self.outputs.mkdir()
        self.cmake = self.root / "cmake fixture"
        self.cmake.write_text(f"#!{sys.executable}\n" + FAKE_CMAKE)
        self.cmake.chmod(0o755)
        self.profile = {
            "workspace": str(self.root), "target": "test_board@1.0.0/chip/cpu0",
            "zephyr": {"path": str(self.root / "zephyr")},
            "sdk": {"id": "sdk-one", "path": str(self.root / "SDK one"), "version": "1.0.1"},
            "tools": [{"name": name, "path": path, "status": "discovered"} for name, path in
                      (("cmake", str(self.cmake)), ("ninja", "/usr/bin/ninja"), ("Python", sys.executable))],
            "roots": {"boards": [{"path": str(self.root / "custom boards")}], "socs": [], "dts": [], "snippets": []},
            "modules": [{"path": str(self.root / "module one"), "is_module": True, "active": True, "status": "discovered"}],
        }
        self.environment = {"PATH": os.defpath, "HOME": str(self.root), "SECRET_TOKEN": "must-not-be-captured",
                            "BOARD": "wrong", "ZEPHYR_SDK_INSTALL_DIR": "/wrong", "CFLAGS": "-wrong", "CONF_FILE": "wrong.conf"}

    def run_build(self, operation="build", *, mode="", cancel=None, emit=None):
        if mode:
            (self.source / "mode").write_text(mode)
        return execute_build(self.profile, self.source, self.outputs / "attempt", operation,
                             environ=self.environment, emit=emit, cancel=cancel)

    def test_recipe_pins_toolchain_and_modules_without_inheriting_build_overrides(self):
        recipe = command_recipe(self.profile, self.source, self.outputs / "attempt", environ=self.environment)
        self.assertIn("-DBOARD=test_board@1.0.0/chip/cpu0", recipe["configure"])
        self.assertIn(f"-DZEPHYR_SDK_INSTALL_DIR={self.root}/SDK one", recipe["configure"])
        self.assertIn(f"-DZEPHYR_MODULES={self.root}/module one", recipe["configure"])
        self.assertIn(f"-DBOARD_ROOT={self.root}/custom boards", recipe["configure"])
        self.assertIn("-DCMAKE_DISABLE_FIND_PACKAGE_ZephyrBuildConfiguration=TRUE", recipe["configure"])
        self.assertNotIn("SECRET_TOKEN", recipe["environment"])
        self.assertNotIn("BOARD", recipe["environment"])
        self.assertNotIn("CFLAGS", recipe["environment"])
        self.assertNotIn("CONF_FILE", recipe["environment"])
        self.assertEqual(recipe["environment"]["ZEPHYR_SDK_INSTALL_DIR"], str(self.root / "SDK one"))
        self.assertEqual(recipe["build"][-2:], ["--parallel", "2"])

    def test_standalone_display_preserves_space_and_shell_metacharacters(self):
        source = self.root / "app $HOME `touch never`"
        recipe = command_recipe(self.profile, source, self.outputs / "manual", environ=self.environment)
        tokens = shlex.split(recipe["display_configure"])
        self.assertEqual(tokens[:5], ["cd", str(self.root), "&&", "env", "-i"])
        start = tokens.index(recipe["configure"][0])
        self.assertEqual(tokens[start:], recipe["configure"])
        self.assertIn(str(source), tokens)

    def test_second_installed_sdk_produces_distinct_captured_recipe(self):
        first = command_recipe(self.profile, self.source, self.outputs / "first", environ=self.environment)
        changed = deepcopy(self.profile)
        changed["sdk"].update(id="sdk-two", path=str(self.root / "SDK two"))
        second = command_recipe(changed, self.source, self.outputs / "second", environ=self.environment)
        self.assertNotEqual(first["environment"]["ZEPHYR_SDK_INSTALL_DIR"], second["environment"]["ZEPHYR_SDK_INSTALL_DIR"])
        self.assertIn(f"-DZEPHYR_SDK_INSTALL_DIR={self.root}/SDK two", second["configure"])
        self.assertNotIn(f"-DZEPHYR_SDK_INSTALL_DIR={self.root}/SDK one", second["configure"])

    def test_incomplete_modules_and_tools_cannot_become_implicit_selections(self):
        for change in (lambda p: p.update(modules=[]), lambda p: p["modules"][0].update(status="unresolved"),
                       lambda p: p["tools"][0].update(status="unavailable")):
            profile = deepcopy(self.profile)
            change(profile)
            with self.assertRaises(ValueError):
                command_recipe(profile, self.source, self.outputs / "attempt", environ=self.environment)
        self.assertFalse((self.outputs / "attempt").exists())

    def test_cmake_list_separator_and_parent_traversal_paths_are_rejected(self):
        for destination in (self.outputs / "semi;colon", self.outputs / ".." / "attempt"):
            with self.assertRaises(ValueError):
                command_recipe(self.profile, self.source, destination, environ=self.environment)

    def test_configure_is_distinct_from_build_and_captures_effective_target(self):
        result = self.run_build("configure")
        self.assertEqual(result["status"], "configured")
        self.assertEqual(result["context"]["effective_target"], "test_board@1.0.0/chip/cpu0")
        self.assertEqual(result["context"]["sdk_path"], str(self.root / "SDK one"))
        self.assertEqual(result["context"]["status"], "captured")
        self.assertIsNone(result["context"]["cxx_compiler"])
        self.assertFalse(result["context"]["hardware_validated"])
        self.assertNotIn("elf", [item["kind"] for item in result["artifacts"]])

    def test_compile_success_has_hashed_elf_evidence_and_streamed_logs(self):
        logs = []
        result = self.run_build(emit=logs.append)
        self.assertEqual(result["status"], "built")
        artifact = next(item for item in result["artifacts"] if item["kind"] == "elf")
        self.assertEqual(artifact["sha256"], hashlib.sha256(b"synthetic ELF evidence").hexdigest())
        self.assertEqual(artifact["availability"], "live")
        self.assertIn("compile completed", logs)
        self.assertTrue((self.outputs / "attempt/CMakeCache.txt").exists())

    def test_incompatible_sample_stops_after_configure_with_a_clear_reason(self):
        logs = []
        evidence = {"status": "incompatible", "message": "Blinky requires an enabled led0 GPIO LED.", "details": []}
        result = execute_build(self.profile, self.source, self.outputs / "attempt", "build",
                               environ=self.environment, emit=logs.append,
                               after_configure=lambda output: evidence)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], evidence["message"])
        self.assertEqual(result["sample_compatibility"], evidence)
        self.assertTrue(result["context"]["configured"])
        self.assertNotIn("compile completed", logs)
        self.assertNotIn("elf", [item["kind"] for item in result["artifacts"]])

    def test_unverified_sample_keeps_its_warning_and_can_compile(self):
        evidence = {"status": "unknown", "message": "The input device requirement could not be verified.", "details": []}
        result = execute_build(self.profile, self.source, self.outputs / "attempt", "build",
                               environ=self.environment, after_configure=lambda output: evidence)
        self.assertEqual(result["status"], "built")
        self.assertEqual(result["sample_compatibility"], evidence)
        self.assertIn("sample-unverified", [item["code"] for item in result["diagnostics"]])

    def test_existing_output_is_not_reused_or_removed(self):
        output = self.outputs / "attempt"
        output.mkdir()
        evidence = output / "user-evidence"
        evidence.write_text("keep")
        with self.assertRaises(FileExistsError):
            self.run_build()
        self.assertEqual(evidence.read_text(), "keep")

    def test_symlinked_output_parent_cannot_redirect_build(self):
        real = self.root / "elsewhere"
        real.mkdir()
        self.outputs.rmdir()
        self.outputs.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_build()
        self.assertEqual(list(real.iterdir()), [])

    def test_configuration_failure_preserves_partial_outputs(self):
        result = self.run_build(mode="fail-configure")
        self.assertEqual(result["status"], "failed")
        self.assertIn("status 2", result["error"])
        self.assertFalse(result["context"]["configured"])
        self.assertTrue(result["artifacts"])
        self.assertTrue((self.outputs / "attempt").exists())

    def test_compile_failure_retains_successful_configuration_evidence(self):
        result = self.run_build(mode="fail-build")
        self.assertEqual(result["status"], "failed")
        self.assertIn("status 7", result["error"])
        self.assertTrue(result["context"]["configured"])
        self.assertNotIn("elf", [item["kind"] for item in result["artifacts"]])

    def test_cancel_stops_descendants_and_returns_partial_record(self):
        cancel = Event()
        children = []
        def emit(line):
            if line.startswith("child:"):
                children.append(int(line.split(":")[1]))
                cancel.set()
        result = self.run_build(mode="wait-child", cancel=cancel, emit=emit)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(children), 1)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                state = Path(f"/proc/{children[0]}/stat").read_text().split(")", 1)[1].split()[0]
            except FileNotFoundError:
                break
            if state == "Z":
                break
            time.sleep(0.01)
        else:
            self.fail("Cancelled build left a live child process")
        self.assertTrue(result["artifacts"])

    def test_cancel_after_pipe_closes_still_stops_the_process(self):
        cancel = Event()
        def emit(line):
            if line == "ready-to-cancel":
                cancel.set()
        result = self.run_build(mode="closed-pipe", cancel=cancel, emit=emit)
        self.assertEqual(result["status"], "cancelled")

    def test_log_and_time_limits_preserve_failed_attempts(self):
        with patch("zephyr_utilities.features.projects.builds.MAX_LOG_BYTES", 100):
            result = self.run_build(mode="large-output")
        self.assertEqual(result["status"], "failed")
        self.assertIn("log limit", result["error"])
        self.outputs = self.source / "other builds"
        self.outputs.mkdir()
        with patch("zephyr_utilities.features.projects.builds.BUILD_TIMEOUT", 0.1):
            result = self.run_build(mode="timeout")
        self.assertEqual(result["status"], "failed")
        self.assertIn("execution limit", result["error"])

    def test_context_captures_both_compiler_paths_from_cmake_cache(self):
        output = self.outputs / "cpp-context"
        output.mkdir()
        (output / "CMakeCache.txt").write_text(
            "CMAKE_C_COMPILER:FILEPATH=/SDK with spaces/bin/target-gcc\n"
            "CMAKE_CXX_COMPILER:FILEPATH=/SDK with spaces/bin/target-g++\n"
        )
        context, _, _ = capture_context(self.profile, self.source, output, configured=True)
        self.assertEqual(context["compiler"], "/SDK with spaces/bin/target-gcc")
        self.assertEqual(context["cxx_compiler"], "/SDK with spaces/bin/target-g++")

    def test_context_rejects_external_symlinks_and_malformed_build_info(self):
        output = self.outputs / "context"
        output.mkdir()
        external = self.root / "outside"
        external.mkdir()
        (external / ".config").write_text("private")
        (output / "zephyr").symlink_to(external, target_is_directory=True)
        (output / "build_info.yml").write_text("cmake: &value {board: *value}\n")
        context, artifacts, diagnostics = capture_context(self.profile, self.source, output, configured=True)
        self.assertEqual(context["status"], "partial")
        self.assertNotIn("kconfig", [item["kind"] for item in artifacts])
        self.assertTrue(any(item["code"] == "artifact-unavailable" for item in diagnostics))

    def test_special_artifact_does_not_block_context_capture(self):
        output = self.outputs / "fifo"
        output.mkdir()
        os.mkfifo(output / "build_info.yml")
        context, artifacts, diagnostics = capture_context(self.profile, self.source, output)
        self.assertEqual(context["status"], "partial")
        self.assertEqual(artifacts, [])
        self.assertTrue(diagnostics)
