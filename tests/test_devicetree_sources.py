"""Current source navigation preserves output identity and confines all reads."""

from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from zephyr_utilities.core.builds import BuildInspectionService
from zephyr_utilities.core.source_files import WorkspaceSourceReader
from zephyr_utilities.features.inspector.sources import SourceNavigation


class WorkspaceSourceReaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.reader = WorkspaceSourceReader(self.workspace)

    def test_text_reads_preserve_bytes_and_report_content_identity(self):
        path = self.workspace / "board.dts"
        content = '/dts-v1/; / { model = "Demo"; };\n'
        path.write_text(content)
        result = self.reader.read_text(path)
        self.assertEqual(result, {"path": str(path), "text": content,
                                  "sha256": hashlib.sha256(content.encode()).hexdigest()})
        self.assertEqual(path.read_text(), content)

    def test_read_boundaries_reject_traversal_links_binary_and_oversized_files(self):
        outside = self.root / "outside.dts"
        outside.write_text("secret")
        (self.workspace / "link.dts").symlink_to(outside)
        (self.workspace / "linked").symlink_to(self.root, target_is_directory=True)
        for path in (outside, "../outside.dts", "one/../two.dts", "bad\0.dts", "link.dts", "linked/outside.dts"):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.reader.read_text(path)
        path = self.workspace / "test.dts"
        for content in (b"\xff", b"zero\0byte"):
            path.write_bytes(content)
            with self.assertRaises(ValueError):
                self.reader.read_text(path)
        path.write_text("too long")
        with self.assertRaisesRegex(ValueError, "size limit"):
            self.reader.read_text(path, limit=2)

    def test_directory_scan_is_sorted_bounded_and_does_not_follow_links(self):
        folder = self.workspace / "bindings"
        folder.mkdir()
        (folder / "z.yaml").write_text("compatible: test,z")
        (folder / "a.yml").write_text("compatible: test,a")
        (folder / "ignore.txt").write_text("ignored")
        (folder / "redirect.yaml").symlink_to(self.root / "outside.yaml")
        (folder / "child").mkdir()
        (folder / "child/b.yaml").write_text("compatible: test,b")
        (folder / "child/cycle").symlink_to(folder, target_is_directory=True)
        result = self.reader.list_files(folder, {".yaml", ".yml"})
        self.assertEqual([str(path.relative_to(folder)) for path in result], ["a.yml", "child/b.yaml", "z.yaml"])
        with self.assertRaisesRegex(ValueError, "limit"):
            self.reader.list_files(folder, {".yaml"}, max_files=1)
        with self.assertRaisesRegex(ValueError, "limit"):
            self.reader.list_files(folder, {".yaml"}, max_dirs=1)
        with self.assertRaises(ValueError):
            self.reader.list_files(self.root, {".yaml"})

    def test_replaced_workspace_is_not_the_original_read_scope(self):
        path = self.workspace / "board.dts"
        path.write_text("old")
        self.workspace.rename(self.root / "previous")
        self.workspace.mkdir()
        path.write_text("new")
        with self.assertRaisesRegex(ValueError, "workspace directory changed"):
            self.reader.read_text(path)

    def test_scan_filters_skip_large_output_directories_before_enumerating_them(self):
        app = self.workspace / "app"
        app.mkdir()
        (app / "Kconfig.board").write_text("config BOARD\n")
        (app / "board_defconfig").write_text("CONFIG_BOARD=y\n")
        (app / "build-release").mkdir()
        (app / "build-release/skip.overlay").write_text("generated")
        output = app / "custom-output"
        output.mkdir()
        (output / "CMakeCache.txt").write_text("generated")
        for index in range(20):
            (output / f"{index}.overlay").write_text("generated")
        found = self.reader.list_files(app, {".overlay"}, prefixes=("Kconfig",), endings=("_defconfig",),
                                         exclude_prefixes=("build-",), stop_names={"CMakeCache.txt"},
                                         max_files=5, max_dirs=3)
        self.assertEqual([path.name for path in found], ["Kconfig.board", "board_defconfig"])

    def test_directory_identity_is_confined_and_does_not_follow_links(self):
        app = self.workspace / "app"
        app.mkdir()
        identity = self.reader.directory_identity(app)
        app.rename(self.workspace / "previous")
        app.mkdir()
        self.assertNotEqual(identity, self.reader.directory_identity(app))
        (self.workspace / "alias").symlink_to(app, target_is_directory=True)
        for path in (self.workspace / "alias", self.root, "../outside"):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.reader.directory_identity(path)


class SourceNavigationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.application.mkdir(parents=True)
        self.board = self.application / "board.dts"
        self.board.write_text('/dts-v1/; / { model = "Demo"; };\n')
        self.overlay = self.application / "app.overlay"
        self.overlay.write_text('/ { chosen {}; };\n')
        self.config = self.application / "prj.conf"
        self.config.write_text("CONFIG_DEMO=y\n")
        self.include = self.application / "include"
        self.include.mkdir()
        self.chip = self.include / "chip.dtsi"
        self.chip.write_text('/ { soc {}; };\n')
        self.build = self.application / "build"
        (self.build / "zephyr").mkdir(parents=True)
        (self.build / "CMakeCache.txt").write_text(f"ZEPHYR_BASE:PATH={self.workspace}/zephyr\n")
        self.info = {"cmake": {"application": {"source-dir": str(self.application)},
                               "board": {"name": "demo", "qualifiers": "chip"},
                               "devicetree": {"files": [str(self.board), str(self.overlay)],
                                              "user-files": [str(self.overlay)], "include-dirs": [str(self.include)]},
                               "kconfig": {"files": [str(self.config)]}}}
        self.write_info()
        (self.build / "zephyr/zephyr.dts").write_text('/dts-v1/; / { model = "Demo"; };\n')
        (self.build / "zephyr/zephyr.dts.pre").write_text(f'# 0 "<built-in>"\n# 1 "{self.chip}" 1\n/ {{}};\n')
        (self.build / "zephyr/include/generated/zephyr").mkdir(parents=True)
        (self.build / "zephyr/include/generated/zephyr/devicetree_generated.h").write_text("/* generated */\n")
        self.builds = BuildInspectionService()
        self.navigation = SourceNavigation(self.builds)

    def write_info(self):
        (self.build / "build_info.yml").write_text(yaml.safe_dump(self.info))

    def open(self):
        observation = self.builds.inspect(self.workspace, self.build)
        payload = self.navigation.create(self.workspace, observation["id"], "application")
        return payload, self.navigation.get(self.workspace, observation["id"], "application", payload["catalog_id"])

    def file(self, scope, path):
        return next(item for item in scope.files.values() if item["path"] == str(path))

    def test_catalog_includes_recorded_inputs_and_preprocessor_sources_without_tools_or_writes(self):
        before = {path: path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()}
        with patch("subprocess.Popen", side_effect=AssertionError("Source navigation must not run tools")):
            payload, scope = self.open()
            result = self.navigation.document(scope, self.file(scope, self.board)["id"])
        self.assertEqual(payload["source_freshness"], "unknown")
        self.assertEqual({item["path"] for item in payload["files"]}, {str(self.board), str(self.overlay), str(self.config), str(self.chip)})
        self.assertEqual(self.file(scope, self.overlay)["origins"], ["Recorded Device Tree input", "Explicit overlay"])
        self.assertEqual(self.file(scope, self.config)["kind"], "configuration")
        self.assertEqual(self.file(scope, self.chip)["origins"], ["Recorded preprocessor input"])
        self.assertEqual(result["document"]["text"], self.board.read_text())
        self.assertEqual(before, {path: path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()})

    def test_missing_external_unsupported_and_symlink_sources_remain_disabled(self):
        outside = self.root / "private.dts"
        outside.write_text("not read")
        linked = self.application / "linked.dtsi"
        linked.symlink_to(self.chip)
        missing = self.application / "missing.overlay"
        unsupported = self.application / "data.bin"
        unsupported.write_bytes(b"not read")
        self.info["cmake"]["devicetree"]["files"] += [str(outside), str(linked), str(missing), str(unsupported), "../escape.dts"]
        self.write_info()
        _, scope = self.open()
        for path, availability in ((outside, "blocked"), (linked, "blocked"), (missing, "missing"), (unsupported, "unsupported")):
            item = self.file(scope, path)
            self.assertEqual(item["availability"], availability)
            with self.assertRaises(ValueError):
                scope.read(item["id"])
        self.assertTrue(any("parent traversal" in item.get("reason", "") for item in scope.files.values()))

    def test_source_changes_require_catalog_refresh_but_do_not_claim_build_freshness(self):
        payload, scope = self.open()
        item = self.file(scope, self.board)
        previous = item["sha256"]
        self.board.write_text("/dts-v1/; / { updated; };\n")
        with self.assertRaisesRegex(ValueError, "changed since the catalog"):
            scope.read(item["id"])
        refreshed = self.navigation.create(self.workspace, payload["inspection_id"], "application")
        self.assertNotEqual(refreshed["catalog_id"], payload["catalog_id"])
        self.assertEqual(refreshed["source_freshness"], "unknown")
        new = next(item for item in refreshed["files"] if item["path"] == str(self.board))
        self.assertNotEqual(new["sha256"], previous)

    def test_output_changes_and_new_optional_outputs_invalidate_navigation(self):
        for relative in ("zephyr/zephyr.dts", "zephyr/zephyr.dts.pre", "zephyr/include/generated/zephyr/devicetree_generated.h", "CMakeCache.txt"):
            path = self.build / relative
            original = path.read_bytes()
            payload, scope = self.open()
            path.write_bytes(original + b"\nchanged")
            with self.subTest(path=relative), self.assertRaises(ValueError):
                self.navigation.get(self.workspace, payload["inspection_id"], "application", payload["catalog_id"])
            with self.subTest(read=relative), self.assertRaises(ValueError):
                scope.read(self.file(scope, self.board)["id"])
            path.write_bytes(original)
        optional = self.build / "zephyr/zephyr.dts.pre"
        optional.unlink()
        payload, _ = self.open()
        optional.write_text("new output")
        with self.assertRaises(ValueError):
            self.navigation.get(self.workspace, payload["inspection_id"], "application", payload["catalog_id"])

    def test_catalog_and_original_observation_expiry_and_image_boundaries(self):
        payload, scope = self.open()
        for workspace, inspection_id, domain in ((self.root, payload["inspection_id"], "application"),
                                                 (self.workspace, "b" * 32, "application"),
                                                 (self.workspace, payload["inspection_id"], "other")):
            with self.assertRaisesRegex(ValueError, "another workspace or image"):
                self.navigation.get(workspace, inspection_id, domain, payload["catalog_id"])
        with self.assertRaisesRegex(ValueError, "Choose a file"):
            scope.read(str(self.board))
        self.builds.observations[payload["inspection_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "expired"):
            self.navigation.get(self.workspace, payload["inspection_id"], "application", payload["catalog_id"])
        payload, _ = self.open()
        self.navigation.catalogs[payload["catalog_id"]]["expires"] = 0
        with self.assertRaisesRegex(ValueError, "source catalog expired"):
            self.navigation.get(self.workspace, payload["inspection_id"], "application", payload["catalog_id"])

    def test_literal_include_navigation_preserves_ambiguity_and_ignores_comments_strings_and_macros(self):
        (self.application / "chip.dtsi").write_text("/ { local {}; };\n")
        self.board.write_text('''/* #include "comment.dtsi" */
// /include/ "comment2.dtsi"
/ { text = "/include/ "; other = "not-an-include.dtsi"; };
#if UNKNOWN_CONDITION
#include "chip.dtsi"
#endif
#include HEADER_MACRO
/include/ "chip.dtsi"
#include "missing.dtsi"
#include "../outside.dtsi"
''')
        _, scope = self.open()
        result = self.navigation.document(scope, self.file(scope, self.board)["id"])
        self.assertEqual([item["name"] for item in result["includes"]], ["chip.dtsi", "missing.dtsi", "../outside.dtsi"])
        candidates = result["includes"][0]
        self.assertEqual({item["path"] for item in candidates["files"]}, {str(self.application / "chip.dtsi"), str(self.chip)})
        self.assertIn("Multiple", candidates["note"])
        self.assertIn("Macro-based", result["diagnostics"][0])
        self.assertEqual(result["includes"][1]["files"], [])
        self.assertIn("parent traversal", result["includes"][2]["note"])
        self.assertFalse(any(item["path"].endswith("missing.dtsi") for item in scope.files.values()))

    def test_repeated_include_requests_do_not_grow_catalog_or_replace_original_hash(self):
        self.board.write_text('#include "chip.dtsi"\n')
        _, scope = self.open()
        file_id = self.file(scope, self.board)["id"]
        first = self.navigation.document(scope, file_id)
        before = deepcopy(scope.files)
        second = self.navigation.document(scope, file_id)
        self.assertEqual(first, second)
        self.assertEqual(scope.files, before)
        self.chip.write_text("changed")
        item = scope.add_file(self.chip, "source", "another reference")
        self.assertEqual(item["sha256"], self.file(scope, self.chip)["sha256"])
        with self.assertRaisesRegex(ValueError, "changed since"):
            scope.read(item["id"])

    def test_catalog_file_limit_is_partial_and_explicit(self):
        with patch("zephyr_utilities.features.inspector.sources.MAX_FILES", 2):
            payload, _ = self.open()
        self.assertEqual(len(payload["files"]), 2)
        self.assertIn("file limit", payload["diagnostics"][0])

    def test_failed_text_reads_consume_admission_budget(self):
        self.info["cmake"]["devicetree"]["files"] = []
        self.info["cmake"]["devicetree"]["user-files"] = []
        self.info["cmake"]["kconfig"]["files"] = []
        self.write_info()
        (self.build / "zephyr/zephyr.dts.pre").unlink()
        _, scope = self.open()
        bad = self.application / "bad.dtsi"
        bad.write_bytes(b"\xff" * 8)
        good = self.application / "good.dtsi"
        good.write_text("ok")
        extra = self.application / "extra.dtsi"
        extra.write_text("extra")
        with patch("zephyr_utilities.features.inspector.sources.MAX_TOTAL_BYTES", 10):
            self.assertEqual(scope.add_file(bad, "source", "test")["availability"], "blocked")
            self.assertEqual(scope.add_file(good, "source", "test")["availability"], "available")
            with self.assertRaisesRegex(ValueError, "text limit"):
                scope.add_file(extra, "source", "test")

    def test_catalog_eviction_requires_explicit_refresh(self):
        self.navigation = SourceNavigation(self.builds, max_catalogs=1)
        first, _ = self.open()
        self.open()
        with self.assertRaisesRegex(ValueError, "expired"):
            self.navigation.get(self.workspace, first["inspection_id"], "application", first["catalog_id"])
