"""Workspace metadata and subprocess boundaries use isolated synthetic environments."""

from concurrent.futures import CancelledError
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event, Timer
import time
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects.probing import ProbeError, run_probe
from zephyr_utilities.features.projects.workspace import inspect_workspace


class _Probe:
    def __init__(self, manifest_path, projects=(), manifest_error=None, missing=()):
        self.manifest_path = manifest_path
        self.projects = list(projects)
        self.manifest_error = manifest_error
        self.missing = missing
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if "-c" in argv:
            if self.manifest_error:
                raise ProbeError(self.manifest_error)
            return json.dumps({"manifest_path": str(self.manifest_path), "projects": self.projects})
        tool = "west" if "west" in argv else Path(argv[0]).name
        if tool in self.missing:
            raise ProbeError(f"{tool} unavailable")
        if "rev-parse" in argv:
            return "a" * 40
        if "--porcelain" in argv:
            return ""
        return f"{tool} synthetic-version"


class WorkspaceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.zephyr = self.checkout(self.workspace / "zephyr")
        self.manifest = self.zephyr / "west.yml"
        self.file(self.workspace / ".west/config", "[manifest]\npath = zephyr\n")
        self.file(self.manifest, "manifest:\n  projects: []\n")

    def file(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def checkout(self, path):
        self.file(path / "VERSION", "VERSION_MAJOR = 4\nVERSION_MINOR = 4\nPATCHLEVEL = 2\nEXTRAVERSION =\n")
        self.file(path / "SDK_VERSION", "1.0.1\n")
        (path / "scripts").mkdir()
        return path

    def project(self, name, *, active=True, root_settings=None, exists=True):
        path = self.workspace / name
        if exists:
            path.mkdir(parents=True)
            if root_settings is not None:
                self.file(path / "zephyr/module.yml", "build:\n  settings:\n" + root_settings)
        return {"name": name, "path": str(path), "active": active, "cloned": exists, "revision": "v1"}

    def scan(self, *, selected=None, options=None, projects=(), environ=None, manifest_error=None, missing=()):
        probe = _Probe(self.manifest, projects, manifest_error, missing)
        with patch("zephyr_utilities.features.projects.workspace.shutil.which",
                   side_effect=lambda name, path=None: f"/synthetic-tools/{name}"):
            result = inspect_workspace(selected or self.workspace, options or {}, environ=environ or {}, probe=probe)
        return result, probe

    def codes(self, result):
        return {item["code"] for item in result["diagnostics"]}

    def test_workspace_resolution_from_an_application_preserves_venv_path(self):
        application = self.workspace / "applications/app"
        application.mkdir(parents=True)
        venv_python = self.workspace / ".venv/bin/python"
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(sys.executable)
        result, probe = self.scan(selected=application)
        self.assertEqual(result["workspace"]["path"], str(application))
        self.assertEqual(result["workspace"]["west_topdir"], str(self.workspace))
        self.assertEqual(result["zephyr"]["path"], str(self.zephyr))
        self.assertEqual(result["python"], str(venv_python))
        self.assertEqual(probe.calls[0][0][0], str(venv_python))
        self.assertTrue(all(call[1]["cwd"] == application for call in probe.calls))
        self.assertEqual(result["environment"]["PATH"].split(os.pathsep)[0], str(venv_python.parent))

    def test_explicit_base_wins_over_environment_and_does_not_fallback_when_missing(self):
        alternative = self.checkout(self.root / "custom-zephyr")
        result, _ = self.scan(options={"zephyr_base": str(alternative)}, environ={"ZEPHYR_BASE": str(self.zephyr)})
        self.assertEqual(result["zephyr"]["path"], str(alternative))
        self.assertEqual(result["roots"]["boards"][-1]["path"], str(alternative))
        missing, _ = self.scan(options={"zephyr_base": str(self.root / "missing")})
        self.assertIsNone(missing["zephyr"]["path"])
        self.assertIn("zephyr-unavailable", self.codes(missing))

    def test_unresolved_imports_do_not_activate_fallback_module_roots(self):
        project = self.project("custom", root_settings="    board_root: .\n    soc_root: .\n")
        self.file(self.manifest, "manifest:\n  projects:\n    - name: custom\n      import: true\n")
        result, _ = self.scan(manifest_error="Imported manifest is unavailable")
        self.assertFalse(result["workspace"]["manifest_resolved"])
        self.assertEqual(result["modules"][0]["status"], "unresolved")
        self.assertIsNone(result["modules"][0]["active"])
        self.assertNotIn(project["path"], [item["path"] for item in result["roots"]["boards"]])
        self.assertIn("manifest-unresolved", self.codes(result))

    def test_active_module_roots_and_explicit_root_order(self):
        project = self.project("custom", root_settings="    board_root: hardware\n    soc_root: hardware\n    dts_root: .\n    snippet_root: .\n")
        inactive = self.project("inactive", active=False, root_settings="    board_root: .\n")
        explicit = self.root / "custom hardware"
        environment = self.root / "environment hardware"
        result, _ = self.scan(projects=[project, inactive], options={"board_roots": [str(explicit)]},
                              environ={"BOARD_ROOT": str(environment)})
        roots = result["roots"]["boards"]
        self.assertEqual([item["path"] for item in roots],
                         [str(explicit), str(environment), str(self.workspace / "custom/hardware"), str(self.zephyr)])
        self.assertEqual(roots[2]["origin"], "module:custom")
        self.assertEqual(len(result["modules"]), 1)
        self.assertTrue(result["modules"][0]["is_module"])

    def test_replacement_and_extra_modules_do_not_retain_manifest_roots(self):
        manifest_project = self.project("manifest-module", root_settings="    board_root: .\n")
        replacement = self.project("replacement", root_settings="    board_root: .\n")
        extra = self.project("extra", root_settings="    soc_root: .\n")
        result, _ = self.scan(projects=[manifest_project], environ={
            "ZEPHYR_MODULES": replacement["path"], "EXTRA_ZEPHYR_MODULES": extra["path"],
        })
        self.assertEqual({item["name"] for item in result["modules"]}, {"replacement", "extra"})
        self.assertEqual(result["roots"]["boards"][0]["path"], replacement["path"])
        self.assertEqual(result["roots"]["socs"][0]["path"], extra["path"])
        self.assertNotIn(manifest_project["path"], [item["path"] for item in result["roots"]["boards"]])

    def test_missing_projects_and_west_are_partial_results(self):
        missing = self.project("uncloned", exists=False)
        result, _ = self.scan(projects=[missing], missing=("west",))
        self.assertEqual(result["modules"][0]["status"], "missing")
        self.assertIn("project-missing", self.codes(result))
        self.assertIn("tool-unavailable", self.codes(result))
        west = next(tool for tool in result["tools"] if tool["name"] == "west")
        self.assertEqual(west["status"], "unavailable")
        self.assertEqual(result["zephyr"]["version"], "4.4.2")

    def test_malformed_module_is_not_partially_admitted(self):
        project = self.project("malformed", root_settings="    board_root: .\n    soc_root: [not, a, path]\n")
        result, _ = self.scan(projects=[project])
        self.assertEqual(result["modules"][0]["status"], "unsupported")
        self.assertIn("module-metadata", self.codes(result))
        self.assertNotIn(project["path"], [item["path"] for item in result["roots"]["boards"]])

    def test_malformed_module_dependencies_do_not_reach_dependency_traversal(self):
        project = self.project("bad-dependencies")
        for value in ("7", "a-module", "[a-module, {invalid: mapping}]", "null"):
            with self.subTest(value=value):
                self.file(Path(project["path"]) / "zephyr/module.yml",
                          f"build:\n  depends: {value}\n  settings:\n    board_root: .\n")
                result, _ = self.scan(projects=[project])
                module = result["modules"][0]
                self.assertEqual(module["status"], "unsupported")
                self.assertEqual(module["dependencies"], [])
                self.assertIn("module-metadata", self.codes(result))
                self.assertNotIn(project["path"], [item["path"] for item in result["roots"]["boards"]])

    def test_missing_module_dependency_has_actionable_diagnostic(self):
        project = self.project("needs-driver")
        self.file(Path(project["path"]) / "zephyr/module.yml", "build:\n  depends: [unavailable-driver]\n")
        result, _ = self.scan(projects=[project])
        message = next(item for item in result["diagnostics"] if item["code"] == "module-dependency-missing")
        self.assertIn("unavailable-driver", message["message"])
        self.assertEqual(message["path"], project["path"])
        self.assertEqual(result["modules"][0]["dependencies"], ["unavailable-driver"])

    def test_dependency_cycle_uses_declared_module_names_and_terminates(self):
        first = self.project("first-repository")
        second = self.project("second-repository")
        self.file(Path(first["path"]) / "zephyr/module.yml", "name: alpha\nbuild:\n  depends: [beta]\n")
        self.file(Path(second["path"]) / "zephyr/module.yml", "name: beta\nbuild:\n  depends: [alpha]\n")
        result, _ = self.scan(projects=[first, second])
        self.assertIn("module-dependency-cycle", self.codes(result))
        self.assertNotIn("module-dependency-missing", self.codes(result))
        self.assertEqual({item["name"] for item in result["modules"]}, {"alpha", "beta"})

    def test_bad_manifest_and_version_produce_diagnostics_without_crashing(self):
        self.file(self.manifest, "manifest: [incorrect-shape]\n")
        self.file(self.zephyr / "VERSION", "VERSION_MAJOR = invalid\n")
        result, _ = self.scan(manifest_error="west cannot resolve manifest")
        self.assertIn("manifest-invalid", self.codes(result))
        self.assertIn("zephyr-version", self.codes(result))
        self.assertEqual(result["modules"], [])
        self.assertIsNone(result["zephyr"]["version"])

    def test_oversized_and_nontext_sdk_version_leave_partial_workspace_information(self):
        sdk_version = self.zephyr / "SDK_VERSION"
        for content in (b"x" * 256, b"\xff\xfe"):
            with self.subTest(content=content[:8]):
                sdk_version.write_bytes(content)
                with patch("zephyr_utilities.features.projects.workspace.MAX_METADATA", 128):
                    result, _ = self.scan()
                self.assertEqual(result["zephyr"]["path"], str(self.zephyr))
                self.assertIsNone(result["zephyr"]["recommended_sdk"])
                self.assertTrue(any(item.get("path") == str(sdk_version) for item in result["diagnostics"]))

    def test_excess_explicit_modules_report_truncated_inventory(self):
        first = self.project("first", root_settings="    board_root: .\n")
        second = self.project("second", root_settings="    soc_root: .\n")
        with patch("zephyr_utilities.features.projects.workspace.MAX_PROJECTS", 1):
            result, _ = self.scan(environ={"ZEPHYR_MODULES": first["path"], "EXTRA_ZEPHYR_MODULES": second["path"]})
        self.assertEqual(len(result["modules"]), 1)
        self.assertTrue(any("limit" in item["code"] for item in result["diagnostics"]))

    def test_nonwest_checkout_and_local_roots_can_be_inspected(self):
        standalone = self.checkout(self.root / "standalone")
        (standalone / "boards").mkdir()
        result, probe = self.scan(selected=standalone)
        self.assertIsNone(result["workspace"]["west_topdir"])
        self.assertIn("no-west-workspace", self.codes(result))
        self.assertEqual(result["roots"]["boards"][0]["path"], str(standalone))
        self.assertFalse(any("-c" in argv for argv, _ in probe.calls))

    def test_precancelled_inspection_does_not_invoke_tools(self):
        event = Event()
        event.set()
        probe = _Probe(self.manifest)
        with self.assertRaises(CancelledError):
            inspect_workspace(self.workspace, {}, environ={}, cancel=event, probe=probe)
        self.assertEqual(probe.calls, [])


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_python(self, code, **kwargs):
        return run_probe([sys.executable, "-I", "-B", "-c", code], cwd=self.directory,
                         environ={"PATH": os.defpath}, **kwargs)

    def test_arguments_working_directory_and_readonly_environment_are_preserved(self):
        result = self.run_python("import json,os; print(json.dumps([os.getcwd(),os.environ['PYTHONDONTWRITEBYTECODE'],os.environ['GIT_OPTIONAL_LOCKS']]))")
        self.assertEqual(json.loads(result), [str(self.directory), "1", "0"])

    def test_error_output_is_retained_and_output_is_bounded(self):
        with self.assertRaisesRegex(ProbeError, "synthetic useful failure"):
            self.run_python("import sys; print('synthetic useful failure'); sys.exit(3)")
        with self.assertRaisesRegex(ProbeError, "output limit"):
            self.run_python("print('x'*65536)", max_output=128)

    def test_timeout_and_precancellation_are_bounded(self):
        start = time.monotonic()
        with self.assertRaises(ProbeError):
            self.run_python("import time; time.sleep(4)", timeout=0.05)
        self.assertLess(time.monotonic() - start, 1.5)
        event = Event()
        event.set()
        with self.assertRaises(CancelledError):
            self.run_python("raise RuntimeError('must not start')", cancel=event)

    def test_cancellation_after_output_closes_still_stops_process(self):
        event = Event()
        timer = Timer(0.15, event.set)
        timer.start()
        self.addCleanup(timer.cancel)
        start = time.monotonic()
        with self.assertRaises(CancelledError):
            self.run_python("import os,time; os.close(1); os.close(2); time.sleep(4)", cancel=event, timeout=2)
        self.assertLess(time.monotonic() - start, 1.5)


if __name__ == "__main__":
    unittest.main()
