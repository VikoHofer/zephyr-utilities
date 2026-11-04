"""Installed SDK inventory uses synthetic layouts and never invokes SDK scripts."""

from concurrent.futures import CancelledError
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects.sdk import discover_sdks


class SDKDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.workspace = self.home / "workspace"
        self.zephyr = self.workspace / "zephyr"
        self.zephyr.mkdir(parents=True)
        (self.zephyr / "SDK_VERSION").write_text("1.0.1\n")
        self.system_roots = patch("zephyr_utilities.features.projects.sdk.SYSTEM_SEARCH_ROOTS", ())
        self.system_roots.start()
        self.addCleanup(self.system_roots.stop)

    def file(self, path, contents="synthetic", executable=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        if executable:
            path.chmod(0o755)
        return path

    def sdk(self, name, version, layout="gnu", targets=("arm-zephyr-eabi",)):
        root = self.home / name
        self.file(root / "sdk_version", version)
        self.file(root / "cmake/Zephyr-sdkConfig.cmake", "# Synthetic package marker; never execute.\n")
        base = root / "gnu" if layout == "gnu" else root
        for target in targets:
            self.file(base / target / "bin" / f"{target}-gcc", executable=True)
        return root

    def scan(self, **kwargs):
        return discover_sdks(self.workspace, self.zephyr, home=self.home, environ=kwargs.pop("environ", {}), **kwargs)

    def codes(self, result):
        return {item["code"] for item in result["diagnostics"]}

    def test_modern_and_legacy_layouts_preserve_installed_vs_available_components(self):
        modern = self.sdk("zephyr-sdk-modern", "1.0.1")
        old = self.sdk("zephyr-sdk-old", "0.16.8", layout="root", targets=("riscv64-zephyr-elf",))
        self.file(modern / "sdk_gnu_toolchains", "arm-zephyr-eabi\nriscv64-zephyr-elf\n")
        self.file(old / "sdk_toolchains", "riscv64-zephyr-elf\narm-zephyr-eabi\n")
        self.file(modern / "hosttools/sysroots/x86_64-pokysdk-linux/usr/bin/dtc", executable=True)
        self.file(old / "sysroots/x86_64-pokysdk-linux/usr/bin/openocd", executable=True)
        result = self.scan()
        entries = {item["path"]: item for item in result["sdks"]}
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[str(modern)]["uninstalled_toolchains"], ["riscv64-zephyr-elf"])
        self.assertEqual(entries[str(modern)]["host_tools"], ["dtc"])
        self.assertEqual(entries[str(old)]["host_tools"], ["openocd"])
        self.assertEqual(entries[str(old)]["toolchains"][0]["name"], "riscv64-zephyr-elf")
        self.assertEqual(result["selection"]["recommended_version"], "1.0.1")
        self.assertIsNone(result["selection"]["effective_compiler"])
        self.assertTrue(all(item["compatibility"] == "unknown" for item in entries.values()))

    def test_equal_versions_and_registry_alias_do_not_collapse_installations(self):
        first = self.sdk("installation-a", "1.0.1")
        second = self.sdk("installation-b", "1.0.1", targets=("riscv64-zephyr-elf",))
        self.file(self.home / ".cmake/packages/Zephyr-sdk/a", str(first / "cmake") + "\n")
        alias = self.home / "sdk-alias"
        alias.symlink_to(first, target_is_directory=True)
        result = self.scan(sdk_paths=[str(first), str(second), str(alias)])
        self.assertEqual(len(result["sdks"]), 2)
        self.assertEqual(len({item["id"] for item in result["sdks"]}), 2)
        entry = next(item for item in result["sdks"] if item["path"] == str(first))
        self.assertIn("CMake package registry", entry["sources"])

    def test_requested_override_is_not_reported_as_effective_or_compatible(self):
        sdk = self.sdk("custom-sdk", "0.16.8", layout="root")
        result = self.scan(environ={"ZEPHYR_SDK_INSTALL_DIR": str(sdk), "ZEPHYR_TOOLCHAIN_VARIANT": "gnuarmemb"})
        self.assertEqual(result["selection"]["requested_sdk_path"], str(sdk))
        self.assertEqual(result["selection"]["requested_variant"], "gnuarmemb")
        self.assertIsNone(result["selection"]["effective_compiler"])
        self.assertEqual(result["sdks"][0]["compatibility"], "unknown")
        self.assertIn("sdk_other_toolchain", self.codes(result))

    def test_missing_sdk_and_invalid_explicit_path_leave_useful_diagnostics(self):
        result = self.scan(sdk_paths=[str(self.root / "missing")])
        self.assertEqual(result["sdks"], [])
        self.assertIn("sdk_path_invalid", self.codes(result))
        self.assertIn("sdk_not_found", self.codes(result))
        self.assertIn(str(self.root / "missing"), result["inputs"])

    def test_partial_installation_does_not_count_nonexecutables_or_setup_scripts(self):
        root = self.sdk("zephyr-sdk-partial", "1.0.1", targets=())
        self.file(root / "gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-gcc")
        self.file(root / "setup.sh", "raise if executed", executable=True)
        self.file(root / "sdk_gnu_toolchains", "arm-zephyr-eabi\n")
        (root / "hosttools").mkdir()
        result = self.scan()
        self.assertEqual(result["sdks"][0]["toolchains"], [])
        self.assertEqual(result["sdks"][0]["status"], "incomplete")
        self.assertEqual(result["sdks"][0]["uninstalled_toolchains"], ["arm-zephyr-eabi"])
        self.assertIn("sdk_no_compiler", self.codes(result))
        self.assertIn("sdk_host_tools_unverified", self.codes(result))

    def test_llvm_detection_requires_a_compiler(self):
        root = self.sdk("zephyr-sdk-llvm", "1.0.1", targets=())
        (root / "llvm").mkdir()
        self.assertEqual(self.scan()["sdks"][0]["status"], "incomplete")
        clang = self.file(root / "llvm/bin/clang", executable=True)
        entry = self.scan()["sdks"][0]
        self.assertEqual(entry["toolchains"], [{"name": "llvm", "compiler": str(clang),
                                              "path": str(root / "llvm"), "family": "LLVM"}])
        self.assertEqual(entry["status"], "discovered")

    def test_missing_cmake_package_and_changed_package_are_visible(self):
        root = self.sdk("zephyr-sdk-package", "1.0.1")
        before = self.scan()["sdks"][0]
        package = root / "cmake/Zephyr-sdkConfig.cmake"
        package.write_text("# Changed synthetic package marker")
        after = self.scan()["sdks"][0]
        self.assertNotEqual(before["fingerprint"], after["fingerprint"])
        self.assertIn(str(package), after["source_files"])
        package.unlink()
        result = self.scan()
        self.assertEqual(result["sdks"][0]["status"], "incomplete")
        self.assertIn("sdk_package_missing", self.codes(result))

    def test_unknown_and_malformed_versions_are_not_supported_installations(self):
        self.sdk("zephyr-sdk-future", "9.0.0")
        self.sdk("zephyr-sdk-invalid", "not-a-version\nanything")
        result = self.scan()
        self.assertEqual({item["status"] for item in result["sdks"]}, {"unsupported"})
        self.assertIn("sdk_version_invalid", self.codes(result))
        self.assertIn("sdk_version_unsupported", self.codes(result))

    def test_metadata_and_compiler_replacement_change_fingerprint(self):
        root = self.sdk("zephyr-sdk-change", "1.0.1")
        before = self.scan()["sdks"][0]
        self.file(root / "sdk_gnu_toolchains", "arm-zephyr-eabi\n")
        metadata_change = self.scan()["sdks"][0]
        self.assertNotEqual(before["fingerprint"], metadata_change["fingerprint"])
        compiler = Path(metadata_change["toolchains"][0]["compiler"])
        compiler.write_text("replacement executable")
        replacement = self.scan()["sdks"][0]
        self.assertNotEqual(metadata_change["fingerprint"], replacement["fingerprint"])

    def test_external_components_and_registry_symlinks_are_skipped(self):
        root = self.sdk("zephyr-sdk-links", "1.0.1", targets=())
        external = self.file(self.root / "external/bin/external-gcc", executable=True)
        (root / "gnu").mkdir()
        (root / "gnu/external").symlink_to(external.parent.parent, target_is_directory=True)
        registry = self.home / ".cmake/packages/Zephyr-sdk"
        registry.mkdir(parents=True)
        (registry / "entry").symlink_to(self.file(self.root / "pointer", str(root)))
        result = self.scan()
        self.assertEqual(result["sdks"][0]["toolchains"], [])
        self.assertIn("sdk_external_link", self.codes(result))
        self.assertIn("sdk_registry_link", self.codes(result))

    def test_internal_compiler_symlink_is_supported(self):
        root = self.sdk("zephyr-sdk-link", "1.0.1", targets=())
        real = self.file(root / "gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-gcc-real", executable=True)
        compiler = real.parent / "arm-zephyr-eabi-gcc"
        compiler.symlink_to(real.name)
        result = self.scan()
        self.assertEqual(result["sdks"][0]["toolchains"][0]["compiler"], str(compiler))

    def test_environment_prefix_and_relative_explicit_paths(self):
        root = self.root / "sdk-area/custom-sdk"
        self.file(root / "sdk_version", "1.0.1")
        self.file(root / "gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-gcc", executable=True)
        result = self.scan(environ={"CMAKE_PREFIX_PATH": str(root)})
        self.assertEqual(result["sdks"][0]["path"], str(root))
        result = self.scan(sdk_paths=["../../sdk-area/custom-sdk"])
        self.assertEqual(result["sdks"][0]["path"], str(root))

    def test_cancellation_and_scan_limits_are_explicit(self):
        event = Event()
        event.set()
        with self.assertRaises(CancelledError):
            self.scan(cancel=event)
        self.sdk("zephyr-sdk-a", "1.0.1")
        self.sdk("zephyr-sdk-b", "1.0.1")
        with patch("zephyr_utilities.features.projects.sdk.MAX_CANDIDATES", 1):
            result = self.scan()
        self.assertEqual(len(result["sdks"]), 1)
        self.assertIn("sdk_candidate_limit", self.codes(result))

    def test_oversized_metadata_and_registry_content_are_not_interpreted(self):
        root = self.sdk("zephyr-sdk-large", "1.0.1")
        self.file(root / "sdk_toolchains", "x" * 100)
        self.file(self.home / ".cmake/packages/Zephyr-sdk/entry", "${SHELL}\nexecute()")
        with patch("zephyr_utilities.features.projects.sdk.MAX_METADATA_BYTES", 64):
            result = self.scan()
        self.assertIn("sdk_metadata_limit", self.codes(result))
        self.assertIn("sdk_registry_invalid", self.codes(result))
        self.assertEqual(result["sdks"][0]["available_toolchains"], [])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO metadata regression requires POSIX")
    def test_fifo_metadata_is_rejected_without_waiting_for_a_writer(self):
        root = self.sdk("zephyr-sdk-fifo", "1.0.1")
        os.mkfifo(root / "sdk_toolchains")
        result = self.scan()
        self.assertEqual(len(result["sdks"]), 1)
        self.assertIn("sdk_metadata_invalid", self.codes(result))
        self.assertEqual(result["sdks"][0]["available_toolchains"], [])


if __name__ == "__main__":
    unittest.main()
