"""Application sources can be browsed before configuration, with bounded live identities."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.inspector.application_sources import ApplicationSourceNavigation


class ApplicationSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "apps/demo"
        self.application.mkdir(parents=True)
        self.write("CMakeLists.txt", "# Never executed\n")
        self.write("app.overlay", '#include "local.h"\n#include <zephyr/dt-bindings/demo.h>\n/ { demo {}; };\n')
        self.write("include/local.h", "#define LOCAL 1\n")
        self.write("prj.conf", "CONFIG_TEST=y\n")
        self.write("Kconfig.app", "config TEST\n    bool\n")
        self.write("boards/demo_defconfig", "CONFIG_BOARD_DEMO=y\n")
        self.write("dts/bindings/demo.yaml", "compatible: test,demo\ninclude: base.yaml\nproperties:\n  label:\n    type: string\n")
        self.write("sample.yaml", "tests: {}\n")
        self.zephyr = self.workspace / "zephyr"
        header = self.zephyr / "include/zephyr/dt-bindings/demo.h"
        header.parent.mkdir(parents=True)
        header.write_text("#define DEMO 1\n")
        binding = self.zephyr / "dts/bindings/base/base.yaml"
        binding.parent.mkdir(parents=True)
        binding.write_text("properties:\n  status:\n    type: string\n")
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.selection = {"workspace": str(self.workspace), "application_path": str(self.application)}

    def write(self, relative, text):
        path = self.application / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def call(self, suffix, body):
        return self.app.router.dispatch(Request("POST", "/api/devicetree/" + suffix, body=body)).data

    def catalog(self, **overrides):
        return self.call("application-sources", {**self.selection, **overrides})

    def document(self, catalog, file):
        return self.call("application-source-file", {**self.selection, "catalog_id": catalog["catalog_id"], "file_id": file["id"]})

    def test_unconfigured_sources_and_include_chains_do_not_run_tools_or_write_files(self):
        before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("subprocess.Popen", side_effect=AssertionError("Source browsing must not run tools")):
            catalog = self.catalog(application_path="apps/demo")
            self.assertEqual(catalog["application_path"], str(self.application))
            self.assertEqual(catalog["mode"], "application")
            self.assertEqual(catalog["source_freshness"], "unknown")
            self.assertFalse({"inspection_id", "domain", "tree", "effective_target"} & catalog.keys())
            self.assertEqual({file["name"] for file in catalog["files"]}, {"app.overlay", "prj.conf", "Kconfig.app", "demo_defconfig", "demo.yaml"})
            overlay = next(file for file in catalog["files"] if file["name"] == "app.overlay")
            opened = self.document(catalog, overlay)
            self.assertEqual(opened["mode"], "application")
            self.assertEqual([item["name"] for item in opened["includes"]], ["local.h", "zephyr/dt-bindings/demo.h"])
            for reference in opened["includes"]:
                self.assertEqual(len(reference["files"]), 1)
                self.assertTrue(self.document(catalog, reference["files"][0])["document"]["text"].startswith("#define"))
            binding = next(file for file in catalog["files"] if file["kind"] == "binding")
            opened = self.document(catalog, binding)
            self.assertEqual(opened["properties"], [{"name": "label", "type": "string"}])
            included = self.document(catalog, opened["includes"][0]["files"][0])
            self.assertEqual(included["properties"], [{"name": "status", "type": "string"}])
            self.assertEqual(self.app.context.jobs.list(), [])
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_discovery_skips_bookkeeping_environments_and_named_or_detected_build_outputs(self):
        excluded = [".git", ".hg", ".svn", ".west", ".venv", "venv", "__pycache__", "node_modules", "build", "build-debug", "build_release", "custom", "nested/other"]
        for folder in excluded:
            self.write(f"{folder}/generated.overlay", "must not be offered\n")
        self.write("custom/CMakeCache.txt", "cache")
        self.write("nested/other/build.ninja", "generated")
        outside = self.root / "outside.overlay"
        outside.write_text("outside")
        (self.application / "linked.overlay").symlink_to(outside)
        (self.application / "redirect").symlink_to(self.root, target_is_directory=True)
        catalog = self.catalog()
        self.assertNotIn("generated.overlay", [file["name"] for file in catalog["files"]])
        self.assertNotIn("linked.overlay", [file["name"] for file in catalog["files"]])
        self.assertTrue(any("CMakeCache.txt" in message for message in catalog["diagnostics"]))

    def test_invalid_roots_and_application_markers_are_rejected(self):
        (self.workspace / "alias").symlink_to(self.application, target_is_directory=True)
        for path in (str(self.workspace), str(self.root), "../escape", "apps/demo/../demo", "alias", "missing", "apps/demo/prj.conf", "bad\0path"):
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                self.catalog(application_path=path)
        marker = self.application / "CMakeLists.txt"
        marker.unlink()
        with self.assertRaisesRegex(ValueError, "CMakeLists"):
            self.catalog()
        marker.symlink_to(self.application / "prj.conf")
        with self.assertRaisesRegex(ValueError, "CMakeLists"):
            self.catalog()
        marker.unlink()
        marker.write_text("# source\n")
        for name in ("CMakeCache.txt", "build.ninja"):
            generated = self.write(name, "output")
            with self.subTest(marker=name), self.assertRaisesRegex(ValueError, "configured build directory"):
                self.catalog()
            generated.unlink()

    def test_binding_include_index_preserves_application_directory_exclusions(self):
        self.write("dts/bindings/demo.yaml", "include: generated.yaml\n")
        for folder in ("build", "build-debug", "build_release", "custom-cache", "custom-ninja"):
            self.write(f"dts/bindings/{folder}/generated.yaml", "properties:\n  generated:\n    type: string\n")
        self.write("dts/bindings/custom-cache/CMakeCache.txt", "generated")
        self.write("dts/bindings/custom-ninja/build.ninja", "generated")
        self.write("dts/bindings/ordinary/available.yaml", "properties: {}\n")
        catalog = self.catalog()
        binding = next(file for file in catalog["files"] if file["name"] == "demo.yaml")
        opened = self.document(catalog, binding)
        self.assertEqual(opened["includes"][0]["files"], [])
        self.assertTrue(any(file["name"] == "available.yaml" for file in catalog["files"]))

    def test_changed_source_or_application_marker_requires_refresh(self):
        catalog = self.catalog()
        overlay = next(file for file in catalog["files"] if file["name"] == "app.overlay")
        self.write("app.overlay", "/ { changed; };\n")
        with self.assertRaisesRegex(ValueError, "changed since"):
            self.document(catalog, overlay)
        fresh = self.catalog()
        fresh_overlay = next(file for file in fresh["files"] if file["name"] == "app.overlay")
        self.assertNotEqual(overlay["sha256"], fresh_overlay["sha256"])
        self.write("CMakeLists.txt", "# changed marker\n")
        with self.assertRaisesRegex(ValueError, "CMakeLists.txt changed"):
            self.document(fresh, fresh_overlay)

    def test_deleted_and_replaced_application_invalidates_catalogue(self):
        catalog = self.catalog()
        file = catalog["files"][0]
        moved = self.root / "previous"
        self.application.rename(moved)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.document(catalog, file)
        self.application.mkdir()
        self.write("CMakeLists.txt", "# Never executed\n")
        self.write("app.overlay", "/ {};\n")
        with self.assertRaisesRegex(ValueError, "replaced"):
            self.document(catalog, file)

    def test_catalogue_scope_and_expiration_are_enforced(self):
        catalog = self.catalog()
        body = {**self.selection, "catalog_id": catalog["catalog_id"], "file_id": catalog["files"][0]["id"]}
        for changes in ({"application_path": "apps/other"}, {"workspace": str(self.root)}, {"catalog_id": "bad"}, {"file_id": str(self.application / "prj.conf")}, {"inspection_id": "a" * 32}, {"file_id": []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.call("application-source-file", {**body, **changes})
        navigation = self.app.context.services["devicetree"].application_sources
        navigation.catalogs[catalog["catalog_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.document(catalog, catalog["files"][0])
        bounded = ApplicationSourceNavigation(max_catalogs=1)
        first = bounded.create(self.workspace, self.application)
        bounded.create(self.workspace, self.application)
        with self.assertRaisesRegex(ValueError, "expired"):
            bounded.get(self.workspace, self.application, first["catalog_id"])

    def test_scan_and_text_budgets_fail_or_report_partial_catalogues(self):
        for constant, limit in (("MAX_SCAN_FILES", 2), ("MAX_SCAN_DIRS", 1), ("MAX_SCAN_DEPTH", 0)):
            with self.subTest(constant=constant), patch(f"zephyr_utilities.features.inspector.application_sources.{constant}", limit), self.assertRaisesRegex(ValueError, "limit"):
                self.catalog()
        self.write("000.overlay", "").write_bytes(b"\xff" * 8)
        self.write("001.overlay", "ok")
        with patch("zephyr_utilities.features.inspector.sources.MAX_TOTAL_BYTES", 10):
            catalog = self.catalog()
        self.assertTrue(any("text limit" in message for message in catalog["diagnostics"]))
        self.assertEqual(len(catalog["files"]), 2)

    def test_absent_conventional_roots_and_malformed_bindings_keep_local_text_usable(self):
        self.zephyr.rename(self.workspace / "custom-checkout")
        self.write("dts/bindings/demo.yaml", "properties: [broken\n")
        catalog = self.catalog()
        self.assertFalse(any("Cannot search binding directory" in item for item in catalog["diagnostics"]))
        binding = next(file for file in catalog["files"] if file["kind"] == "binding")
        opened = self.document(catalog, binding)
        self.assertEqual(opened["document"]["text"], "properties: [broken\n")
        self.assertEqual(opened["properties"], [])
        self.assertTrue(opened["diagnostics"])


if __name__ == "__main__":
    unittest.main()
