"""Current binding navigation stays separate from build-proven binding selection."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import yaml

from zephyr_utilities.app import Application
from zephyr_utilities.core.http import Request
from zephyr_utilities.features.inspector.bindings import binding_mapping
import test_build_inspection_api as build_fixtures
import test_projects_api as http_fixtures


def prepare(root):
    build = build_fixtures.make_build(root)
    bindings = root / "zephyr-source/dts/bindings"
    (bindings / "serial").mkdir(parents=True)
    (bindings / "base").mkdir()
    (bindings / "serial/vendor,uart.yaml").write_text(
        'description: Example UART\ncompatible: test,uart\ninclude: controller.yaml\n'
        'properties:\n  reg:\n    required: true\n')
    (bindings / "serial/controller.yaml").write_text(
        'description: UART controller\nbus: uart\ninclude: base.yaml\n'
        'properties:\n  speed:\n    type: int\n    default: 9600\n    description: Serial speed\n')
    (bindings / "base/base.yaml").write_text('properties:\n  status:\n    type: string\n    enum: [okay, disabled]\n')
    info = yaml.safe_load((build / "build_info.yml").read_text())
    info["cmake"]["devicetree"]["bindings-dirs"] = [str(bindings)]
    (build / "build_info.yml").write_text(yaml.safe_dump(info))
    header = build / "zephyr/include/generated/zephyr/devicetree_generated.h"
    header.parent.mkdir(parents=True)
    header.write_text('/*\n * Devicetree node: /soc/serial@4000\n *\n'
                      ' * Binding (compatible = test,uart):\n'
                      ' *   $ZEPHYR_BASE/dts/bindings/serial/vendor,uart.yaml\n */\n')
    return build, bindings, header


class BindingNavigationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.build, self.bindings, self.header = prepare(self.workspace)
        self.app = Application(self.workspace, self.root / "data")
        self.addCleanup(self.app.close)
        self.observe()

    def observe(self):
        observed = self.app.context.builds.inspect(self.workspace, self.build)
        self.selection = {"workspace": str(self.workspace), "inspection_id": observed["id"], "domain": "application"}
        self.catalogue = None

    def call(self, suffix, body):
        return self.app.router.dispatch(Request("POST", "/api/devicetree/" + suffix, body=body)).data

    def catalogue_body(self):
        if self.catalogue is None:
            self.catalogue = self.call("sources", self.selection)
        return {**self.selection, "catalog_id": self.catalogue["catalog_id"]}

    def find(self, node_path="/soc/serial@4000"):
        return self.call("bindings", {**self.catalogue_body(), "node_path": node_path})

    def document(self, file):
        return self.call("source-file", {**self.catalogue_body(), "file_id": file["id"]})

    def test_recorded_binding_and_current_include_chain_are_read_only(self):
        before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        with patch("subprocess.Popen", side_effect=AssertionError("Navigation must not run tools")):
            result = self.find()
            self.assertEqual(result["compatibles"], ["test,uart"])
            self.assertEqual(result["node_path"], "/soc/serial@4000")
            self.assertEqual(len(result["bindings"]), 1)
            binding = result["bindings"][0]
            self.assertEqual(binding["evidence"], "recorded-binding")
            self.assertEqual(binding["file"]["path"], str(self.bindings / "serial/vendor,uart.yaml"))
            document = self.document(binding["file"])
            self.assertEqual(document["properties"], [{"name": "reg", "required": True}])
            included = self.document(document["includes"][0]["files"][0])
            self.assertEqual(included["properties"][0]["name"], "speed")
            self.assertIn("9600", included["properties"][0]["default"])
            base = self.document(included["includes"][0]["files"][0])
            self.assertEqual(base["properties"][0]["name"], "status")
            self.assertEqual(self.app.context.jobs.list(), [])
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_ordered_compatibles_keep_all_bus_and_duplicate_candidates(self):
        self.header.unlink()
        (self.build / "zephyr/zephyr.dts").write_text(
            '/dts-v1/; / { device { compatible = "test,specific", "test,uart"; }; };')
        (self.bindings / "specific.yaml").write_text('compatible: test,specific\non-bus: spi\n')
        (self.bindings / "duplicate.yaml").write_text('compatible: test,uart\non-bus: i2c\n')
        (self.bindings / "duplicate2.yaml").write_text('compatible: test,uart\non-bus: i2c\n')
        self.observe()
        result = self.find("/device")
        self.assertEqual([item["compatible"] for item in result["bindings"]], ["test,specific", "test,uart", "test,uart", "test,uart"])
        self.assertTrue(all(item["evidence"] == "compatible-candidate" for item in result["bindings"]))
        self.assertEqual([item["on_bus"] for item in result["bindings"]].count("i2c"), 2)
        self.assertTrue(any("not resolved" in item for item in result["diagnostics"]))

    def test_unknown_node_and_absent_compatible_do_not_guess_a_binding(self):
        with self.assertRaisesRegex(ValueError, "Choose a node"):
            self.find("/not-a-node")
        result = self.find("/soc")
        self.assertEqual(result["bindings"], [])
        self.assertTrue(any("child-binding" in item for item in result["diagnostics"]))

    def test_include_ambiguity_filters_and_cycles_remain_explicit_navigation(self):
        (self.bindings / "base/controller.yaml").write_text('include: vendor,uart.yaml\n')
        (self.bindings / "serial/vendor,uart.yaml").write_text(
            'compatible: test,uart\ninclude:\n  - name: controller.yaml\n    property-allowlist: [speed]\n'
            '  - ../escape.yaml\nchild-binding:\n  properties:\n    child:\n      type: int\n')
        binding = self.find()["bindings"][0]["file"]
        doc = self.document(binding)
        self.assertEqual(len(doc["includes"][0]["files"]), 2)
        self.assertIn("Ambiguous", doc["includes"][0]["note"])
        self.assertIn("filters", doc["includes"][0]["note"])
        self.assertEqual(doc["includes"][1]["files"], [])
        self.assertEqual(doc["properties"], [])
        self.assertTrue(any("child-binding" in item for item in doc["diagnostics"]))
        cycle = next(item for item in doc["includes"][0]["files"] if "/base/" in item["path"])
        back = self.document(cycle)["includes"][0]["files"][0]
        self.assertEqual(back["id"], binding["id"])

    def test_malformed_recorded_yaml_still_has_text_without_unsafe_construction(self):
        (self.bindings / "serial/vendor,uart.yaml").write_text('include: !include secret.yaml\n')
        result = self.find()
        file = result["bindings"][0]["file"]
        document = self.document(file)
        self.assertIn("!include", document["document"]["text"])
        self.assertEqual(document["properties"], [])
        self.assertTrue(document["diagnostics"])

    def test_yaml_parser_rejects_aliases_duplicate_keys_tags_and_limits(self):
        cases = ('compatible: one\ncompatible: two\n', 'a: &x [1]\nb: *x\n',
                 '!!python/object/apply:os.system ["false"]', 'properties: [', '- sequence',
                 'properties:\n  speed:\n    type: int\n    type: string\n')
        for text in cases:
            with self.subTest(text=text), self.assertRaises(ValueError):
                binding_mapping(text)
        with patch("zephyr_utilities.features.inspector.bindings.MAX_YAML_EVENTS", 3), self.assertRaisesRegex(ValueError, "limits"):
            binding_mapping('compatible: test,uart\nproperties: {}\n')
        with patch("zephyr_utilities.features.inspector.bindings.MAX_BINDING_BYTES", 2), self.assertRaisesRegex(ValueError, "limit"):
            binding_mapping('compatible: test,uart')

    def test_current_file_edits_require_explicit_source_catalogue_refresh(self):
        binding = self.find()["bindings"][0]["file"]
        original = Path(binding["path"]).read_text()
        Path(binding["path"]).write_text(original + '# a current source edit\n')
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.document(binding)
        self.catalogue = None
        current = self.find()["bindings"][0]["file"]
        self.assertNotEqual(current["id"], binding["id"])
        self.assertIn("current source edit", self.document(current)["document"]["text"])

    def test_indexed_candidate_cannot_silently_acquire_new_bytes(self):
        self.header.unlink()
        self.observe()
        self.find("/soc")  # Index current bindings without opening a candidate.
        target = self.bindings / "serial/vendor,uart.yaml"
        target.write_text(target.read_text() + '# changed since index\n')
        with self.assertRaisesRegex(ValueError, "binding changed"):
            self.find()

    def test_repeated_candidate_and_include_reads_recheck_previously_offered_files(self):
        self.header.unlink()
        self.observe()
        binding = self.find()["bindings"][0]["file"]
        included = self.document(binding)["includes"][0]["files"][0]
        target = Path(included["path"])
        target.write_text(target.read_text() + '# changed include\n')
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.document(binding)
        target = Path(binding["path"])
        target.write_text(target.read_text() + '# changed candidate\n')
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.find()

    def test_failed_text_reads_consume_the_binding_scan_budget(self):
        for name in ("a", "b", "c"):
            (self.bindings / f"{name}.yaml").write_bytes(b"\xff\xff\xff\xff")
        self.catalogue_body()
        service = self.app.context.services["devicetree"]
        scope = service.sources.get(**self.catalogue_body())
        with patch.object(scope.reader, "read_text", wraps=scope.reader.read_text) as read, patch(
                "zephyr_utilities.features.inspector.bindings.MAX_BINDING_BYTES", 4), patch(
                "zephyr_utilities.features.inspector.bindings.MAX_INDEX_BYTES", 8):
            index = service.bindings.index(scope)
        self.assertEqual(read.call_count, 2)
        self.assertTrue(any("reading limit" in message for message in index["diagnostics"]))

    def test_changed_build_outputs_or_metadata_invalidate_source_navigation(self):
        self.find()
        self.header.write_text(self.header.read_text() + '\n/* changed output */')
        with self.assertRaisesRegex(ValueError, "output changed"):
            self.find()
        self.observe()
        self.find()
        (self.build / "CMakeCache.txt").write_text('ZEPHYR_BASE:PATH=/changed\n')
        with self.assertRaisesRegex(ValueError, "context changed"):
            self.find()

    def test_outside_paths_and_symlink_roots_are_diagnostics_without_reads(self):
        outside = self.root / "external"
        outside.mkdir()
        (outside / "secret.yaml").write_text('compatible: test,uart\ndescription: should not be read\n')
        alias = self.workspace / "linked-bindings"
        alias.symlink_to(outside, target_is_directory=True)
        info = yaml.safe_load((self.build / "build_info.yml").read_text())
        info["cmake"]["devicetree"]["bindings-dirs"] = [str(outside), str(alias)]
        (self.build / "build_info.yml").write_text(yaml.safe_dump(info))
        self.header.write_text(f'/*\n * Devicetree node: /soc/serial@4000\n * Binding (compatible = test,uart):\n *   {outside}/secret.yaml\n */')
        self.observe()
        result = self.find()
        self.assertEqual(len(result["bindings"]), 1)
        self.assertEqual(result["bindings"][0]["file"]["availability"], "blocked")
        self.assertTrue(any("Cannot search" in item for item in result["diagnostics"]))
        self.assertNotIn("should not be read", json.dumps(result))

    def test_partial_index_limits_and_unsafe_document_ids_are_explicit(self):
        with patch("zephyr_utilities.features.inspector.bindings.MAX_INDEX_BYTES", 1):
            result = self.find()
        self.assertTrue(any("limit" in message for message in result["diagnostics"]))
        body = self.catalogue_body()
        for path, values in (("source-file", {"file_id": "/etc/passwd"}),
                             ("source-file", {"file_id": []}),
                             ("bindings", {"node_path": "/soc", "path": "/etc/passwd"})):
            with self.subTest(path=path, values=values), self.assertRaises(ValueError):
                self.call(path, {**body, **values})
        other = self.workspace / "other"
        other.mkdir()
        self.app.context.workspace.set(other)
        with self.assertRaisesRegex(ValueError, "workspace changed"):
            self.find()
        with self.assertRaisesRegex(ValueError, "another workspace"):
            self.call("bindings", {**body, "workspace": str(other), "node_path": "/soc"})


class SourceNavigationHttpTests(unittest.TestCase):
    requires_loopback = True
    setUp = http_fixtures.ProjectsHttpTests.setUp
    close = http_fixtures.ProjectsHttpTests.close
    request = http_fixtures.ProjectsHttpTests.request

    def test_source_routes_use_shared_authentication_and_file_id_allowlist(self):
        for suffix in ("sources", "bindings", "source-file"):
            with self.subTest(route=suffix):
                with self.assertRaises(HTTPError) as error:
                    self.request('/api/devicetree/' + suffix, method="POST", body={}, authorized=False)
                self.assertEqual(error.exception.code, 403)
                with self.assertRaises(HTTPError) as error:
                    self.request('/api/devicetree/' + suffix, method="POST", body={})
                self.assertEqual(error.exception.code, 400)
        build, _, _ = prepare(self.root)
        with self.request('/api/projects/inspect', method="POST", body={"workspace": str(self.root), "build_dir": str(build)}) as response:
            observation = json.load(response)
        selection = {"workspace": str(self.root), "inspection_id": observation["id"], "domain": "application"}
        with self.request('/api/devicetree/sources', method="POST", body=selection) as response:
            catalogue = json.load(response)
        body = {**selection, "catalog_id": catalogue["catalog_id"], "node_path": "/soc/serial@4000"}
        with self.request('/api/devicetree/bindings', method="POST", body=body) as response:
            binding = json.load(response)["bindings"][0]
        with self.request('/api/devicetree/source-file', method="POST", body={**selection, "catalog_id": catalogue["catalog_id"], "file_id": binding["file"]["id"]}) as response:
            self.assertIn('compatible: test,uart', json.load(response)["document"]["text"])
