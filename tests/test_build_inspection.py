"""Read-only shared build observations, workspace boundaries and output freshness."""

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import yaml

from zephyr_utilities.core.builds import BuildInspectionService
from zephyr_utilities.core import builds as build_module


class BuildInspectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.build = self.make_build(self.workspace / "application/build")
        self.service = BuildInspectionService()

    def make_build(self, path, *, board="demo", qualifier="chip/cpu"):
        (path / "zephyr").mkdir(parents=True)
        (path / "CMakeCache.txt").write_text(
            f"ZEPHYR_BASE:PATH={self.workspace}/zephyr\n"
            f"APPLICATION_SOURCE_DIR:PATH={path.parent}\n"
            "BOARD:STRING=demo@2/chip/cpu\nCMAKE_C_COMPILER:FILEPATH=/example/sdk/bin/compiler\n"
        )
        info = {"version": "0.1.0", "cmake": {
            "board": {"name": board, "qualifiers": qualifier, "revision": "2"},
            "application": {"source-dir": str(path.parent)},
            "zephyr": {"zephyr-base": str(self.workspace / "zephyr"), "version": "4.4.2"},
            "toolchain": {"name": "zephyr", "path": "/example/sdk"},
            "kconfig": {"files": ["board_defconfig", "prj.conf"], "user-files": ["prj.conf"]},
            "devicetree": {"files": ["board.dts", "app.overlay"], "user-files": ["app.overlay"],
                           "extra-user-files": ["debug.overlay"], "include-dirs": ["include"], "bindings-dirs": ["bindings"]},
        }}
        (path / "build_info.yml").write_text(yaml.safe_dump(info))
        (path / "zephyr/zephyr.dts").write_text('/dts-v1/;\n/ { model = "Demo"; };\n')
        (path / "zephyr/.config").write_text("CONFIG_TEST=y\n")
        (path / "zephyr/zephyr.elf").write_bytes(b"\x7fELFsynthetic output")
        return path

    def inspect(self):
        return self.service.inspect(str(self.workspace), str(self.build))

    def test_observation_is_read_only_and_contains_qualified_context_and_text(self):
        before = {path: path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()}
        observation = self.inspect()
        domain = observation["domains"][0]
        self.assertEqual(observation["default_domain"], "application")
        self.assertEqual(domain["context"]["effective_target"], "demo@2/chip/cpu")
        self.assertEqual(domain["context"]["source_freshness"], "unknown")
        self.assertEqual(domain["context"]["configuration"]["files"], ["board_defconfig", "prj.conf"])
        self.assertEqual(domain["context"]["devicetree"]["files"], ["board.dts", "app.overlay"])
        self.assertEqual(domain["context"]["devicetree"]["extra_user_files"], ["debug.overlay"])
        output = self.service.read_text(self.workspace, observation["id"], "application", "resolved-dts")
        self.assertEqual(output["text"], (self.build / "zephyr/zephyr.dts").read_text())
        self.assertEqual(output["sha256"], hashlib.sha256(output["text"].encode()).hexdigest())
        self.assertEqual({path: path.read_bytes() for path in self.workspace.rglob("*") if path.is_file()}, before)

    def test_sysbuild_images_have_distinct_contexts_and_default(self):
        root = self.workspace / "sysbuild"
        self.make_build(root / "app", qualifier="chip/app")
        self.make_build(root / "radio", qualifier="chip/radio")
        (root / "domains.yaml").write_text(yaml.safe_dump({"default": "radio", "build_dir": str(root), "domains": [
            {"name": "app", "build_dir": "app"}, {"name": "radio", "build_dir": str(root / "radio")},
        ]}))
        result = self.service.inspect(self.workspace, root)
        self.assertEqual(result["default_domain"], "radio")
        self.assertEqual([domain["context"]["effective_target"] for domain in result["domains"]],
                         ["demo@2/chip/app", "demo@2/chip/radio"])
        output = self.service.read_text(self.workspace, result["id"], "radio", "resolved-dts")
        self.assertEqual(output["path"], str(root / "radio/zephyr/zephyr.dts"))
        self.assertEqual(result["artifacts"][0]["kind"], "domains")

    def test_changed_or_removed_outputs_keep_original_hashes(self):
        result = self.inspect()
        original = deepcopy(result["domains"][0])
        (self.build / "zephyr/zephyr.dts").write_text("/dts-v1/; / { changed; };\n")
        (self.build / "zephyr/zephyr.elf").unlink()
        checked = self.service.get(self.workspace, result["id"])["domains"][0]
        self.assertEqual(checked["freshness"], "changed")
        self.assertEqual(checked["context"], original["context"])
        for artifact in checked["artifacts"]:
            initial = next(item for item in original["artifacts"] if item["kind"] == artifact["kind"])
            self.assertEqual(artifact["sha256"], initial["sha256"])
        statuses = {item["kind"]: item["availability"] for item in checked["artifacts"]}
        self.assertEqual(statuses["resolved-dts"], "changed")
        self.assertEqual(statuses["elf"], "missing")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.read_text(self.workspace, result["id"], "application", "resolved-dts")
        fresh = self.inspect()
        self.assertNotEqual(fresh["id"], result["id"])
        self.assertNotEqual(fresh["domains"][0]["artifacts"][2]["sha256"], original["artifacts"][2]["sha256"])

    def test_changed_metadata_blocks_reading_unchanged_dts(self):
        result = self.inspect()
        with (self.build / "CMakeCache.txt").open("a") as stream:
            stream.write("BOARD:STRING=another\n")
        self.assertEqual(self.service.get(self.workspace, result["id"])["domains"][0]["freshness"], "changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.read_text(self.workspace, result["id"], "application", "resolved-dts")

    def test_added_metadata_requires_new_observation(self):
        (self.build / "build_info.yml").unlink()
        result = self.inspect()
        self.assertIsNone(result["domains"][0]["context"]["effective_target"])
        (self.build / "build_info.yml").write_text("cmake: {}\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.read_text(self.workspace, result["id"], "application", "resolved-dts")

    def test_missing_unrelated_elf_does_not_prevent_reading_unchanged_dts(self):
        result = self.inspect()
        (self.build / "zephyr/zephyr.elf").unlink()
        self.assertEqual(self.service.get(self.workspace, result["id"])["domains"][0]["freshness"], "unavailable")
        self.assertIn('model = "Demo"', self.service.read_text(self.workspace, result["id"], "application", "resolved-dts")["text"])

    def test_partial_and_malformed_metadata_does_not_invent_values(self):
        (self.build / "CMakeCache.txt").unlink()
        (self.build / "build_info.yml").write_text("cmake: [unterminated\n")
        result = self.inspect()["domains"][0]
        self.assertIsNone(result["context"]["effective_target"])
        self.assertIsNone(result["context"]["compiler"])
        self.assertTrue(result["diagnostics"])
        arbitrary = self.workspace / "cmake-only"
        arbitrary.mkdir()
        (arbitrary / "CMakeCache.txt").write_text("CMAKE_HOME_DIRECTORY:PATH=/example\n")
        with self.assertRaisesRegex(ValueError, "No readable Zephyr"):
            self.service.inspect(self.workspace, arbitrary)

    def test_workspace_and_selection_boundaries(self):
        result = self.inspect()
        other = self.workspace / "other"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "another workspace"):
            self.service.get(other, result["id"])
        for invalid in (self.workspace.parent, "../outside", self.workspace / "application/../application/build"):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                self.service.inspect(self.workspace, invalid)
        for kind in ("elf", "pickle", "../../private", [], {}):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.service.read_text(self.workspace, result["id"], "application", kind)
        with self.assertRaisesRegex(ValueError, "Choose an image"):
            self.service.read_text(self.workspace, result["id"], "unknown", "resolved-dts")
        with self.assertRaisesRegex(ValueError, "not available"):
            self.service.read_text(self.workspace, result["id"], "application", "map")

    def test_symlinked_files_and_directories_cannot_redirect_inspection(self):
        secret = self.workspace / "private.txt"
        secret.write_text("not a device tree")
        (self.build / "zephyr/zephyr.dts").unlink()
        (self.build / "zephyr/zephyr.dts").symlink_to(secret)
        result = self.inspect()["domains"][0]
        self.assertNotIn("resolved-dts", {item["kind"] for item in result["artifacts"]})
        self.assertTrue(result["diagnostics"])
        link = self.workspace / "redirected"
        link.symlink_to(self.build, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "build directory without symlinked"):
            self.service.inspect(self.workspace, link)
        (self.build / "zephyr/zephyr.dts").unlink()
        (self.build / "zephyr/zephyr.dts").write_text("/dts-v1/; / {};\n")
        observation = self.inspect()
        self.build.rename(self.build.with_name("original"))
        self.build.symlink_to(self.build.with_name("original"), target_is_directory=True)
        checked = self.service.get(self.workspace, observation["id"])
        self.assertTrue(all(item["availability"] == "unavailable" for item in checked["domains"][0]["artifacts"]))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.service.read_text(self.workspace, observation["id"], "application", "resolved-dts")

    def test_file_or_symlink_directory_selection_is_an_invalid_request(self):
        for name, target in (("file", self.build / "CMakeCache.txt"),
                             ("directory", self.build)):
            link = self.workspace / f"{name}-link"
            link.symlink_to(target, target_is_directory=name == "directory")
            with self.subTest(kind=name), self.assertRaisesRegex(ValueError, "build directory without symlinked"):
                self.service.inspect(self.workspace, link)
        with self.assertRaisesRegex(ValueError, "build directory without symlinked"):
            self.service.inspect(self.workspace, self.build / "CMakeCache.txt")

    def test_fifo_and_pickle_are_never_loaded(self):
        (self.build / "zephyr/edt.pickle").write_bytes(b"not executable input")
        os.mkfifo(self.build / "zephyr/zephyr.map")
        result = self.inspect()["domains"][0]
        self.assertNotIn("map", {item["kind"] for item in result["artifacts"]})
        self.assertNotIn("pickle", str(result))
        self.assertTrue(result["diagnostics"])

    def test_sysbuild_rejects_escape_duplicates_aliases_and_bad_manifests(self):
        root = self.workspace / "sysbuild"
        root.mkdir()
        cases = [
            {"domains": [{"name": "outside", "build_dir": str(self.build)}]},
            {"domains": [{"name": "a", "build_dir": "a"}, {"name": "a", "build_dir": "b"}]},
            {"domains": [{"name": "a", "build_dir": "a"}, {"name": "b", "build_dir": "a"}]},
            {"domains": [{"name": "parent", "build_dir": ".."}]},
            {"domains": []},
            {"domains": [{"name": [], "build_dir": "a"}]},
        ]
        for data in cases:
            (root / "domains.yaml").write_text(yaml.safe_dump(data))
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.service.inspect(self.workspace, root)
        for text in ("domains: [\n", "domains: &d [*d]\n", "!!python/object:os.system {}", "[]"):
            (root / "domains.yaml").write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.service.inspect(self.workspace, root)

    def test_missing_sysbuild_child_is_reported_and_not_invented(self):
        root = self.workspace / "sysbuild"
        self.make_build(root / "app")
        (root / "domains.yaml").write_text(yaml.safe_dump({"default": "missing", "domains": [
            {"name": "app", "build_dir": "app"}, {"name": "missing", "build_dir": "missing"},
        ]}))
        result = self.service.inspect(self.workspace, root)
        self.assertEqual([item["name"] for item in result["domains"]], ["app"])
        self.assertEqual(result["default_domain"], "app")
        self.assertTrue(result["diagnostics"])
        (root / "domains.yaml").write_text("domains: []\n")
        self.assertEqual(self.service.get(self.workspace, result["id"])["domains"][0]["freshness"], "changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.service.read_text(self.workspace, result["id"], "app", "resolved-dts")

    def test_output_limits_and_session_retention_are_bounded(self):
        with patch("zephyr_utilities.core.builds.MAX_ARTIFACT_BYTES", 4):
            result = self.inspect()["domains"][0]
        self.assertNotIn("resolved-dts", {item["kind"] for item in result["artifacts"]})
        self.assertTrue(result["diagnostics"])
        self.service = BuildInspectionService(max_observations=1)
        first, second = self.inspect(), self.inspect()
        with self.assertRaisesRegex(ValueError, "expired"):
            self.service.get(self.workspace, first["id"])
        returned = self.service.get(self.workspace, second["id"])
        returned["domains"][0]["context"]["board"] = "mutated"
        self.assertEqual(self.service.get(self.workspace, second["id"])["domains"][0]["context"]["board"], "demo")
        with patch("zephyr_utilities.core.builds.time.monotonic", return_value=10**20):
            with self.assertRaisesRegex(ValueError, "expired"):
                self.service.get(self.workspace, second["id"])

    def test_file_changed_during_read_is_not_served(self):
        result = self.inspect()
        original = os.fstat
        reads = 0

        def changed_info(descriptor):
            nonlocal reads
            info = original(descriptor)
            if stat.S_ISREG(info.st_mode):
                reads += 1
                if reads == 2:
                    (self.build / "CMakeCache.txt").write_text("changed during read\n")
                    info = original(descriptor)
            return info

        with patch("zephyr_utilities.core.builds.os.fstat", side_effect=changed_info):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.service.read_text(self.workspace, result["id"], "application", "resolved-dts")

    def test_metadata_change_while_other_outputs_are_captured_rejects_observation(self):
        original = self.service._capture

        def capture(workspace, path, kind, budget):
            result = original(workspace, path, kind, budget)
            if kind == "resolved-dts":
                (self.build / "build_info.yml").write_text("cmake: {}\n")
            return result

        with patch.object(self.service, "_capture", side_effect=capture):
            with self.assertRaisesRegex(ValueError, "metadata changed"):
                self.inspect()
        self.assertEqual(self.service.observations, {})

    def test_metadata_change_while_text_is_read_rejects_content(self):
        observation = self.inspect()
        original = build_module._read_file

        def read(workspace, path, budget, **options):
            result = original(workspace, path, budget, **options)
            if path.name == "zephyr.dts" and options.get("content"):
                (self.build / "CMakeCache.txt").write_text("BOARD:STRING=changed\n")
            return result

        with patch.object(build_module, "_read_file", side_effect=read):
            with self.assertRaisesRegex(ValueError, "metadata changed"):
                self.service.read_text(self.workspace, observation["id"], "application", "resolved-dts")
