"""Read-only resolved Device Tree parsing and feature API boundaries."""

from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zephyr_utilities.core.http import Request, Router
from zephyr_utilities.features.devicetree.api import register
from zephyr_utilities.features.devicetree.parser import parse_resolved_dts


DTS = '''/dts-v1/;
reservation: /memreserve/ 0x10 0x20;
/* Generated text, not a source overlay. */
/ {
    model = "Demo; {board}";
    compatible = "vendor,demo", "vendor,soc";
    #address-cells = < 1 >;
    chosen { zephyr,console = &uart0; };
    soc {
        ranges;
        uart0: serial0: uart@1000 {
            compatible = "vendor,uart";
            status = "okay";
            reg = < 0x1000 0x100 >;
            bytes = [ 00 7f ff ];
            escaped = "quote \\" and slash \\\\";
            target = &{/soc/uart@1000};
            text = "<script>alert(1)</script>";
        };
    };
};
'''


class ResolvedTreeTests(unittest.TestCase):
    def test_nodes_labels_and_verbatim_properties_are_preserved(self):
        tree = parse_resolved_dts(DTS)
        self.assertEqual(tree["status"], "parsed", tree["diagnostics"])
        self.assertEqual([item["path"] for item in tree["nodes"]], ["/", "/chosen", "/soc", "/soc/uart@1000"])
        uart = tree["nodes"][-1]
        self.assertEqual(uart["parent"], "/soc")
        self.assertEqual(uart["labels"], ["uart0", "serial0"])
        self.assertEqual(uart["depth"], 2)
        properties = {item["name"]: item for item in uart["properties"]}
        self.assertEqual(properties["reg"]["value"], "< 0x1000 0x100 >")
        self.assertEqual(properties["target"]["value"], "&{/soc/uart@1000}")
        self.assertEqual(properties["text"]["value"], '"<script>alert(1)</script>"')
        self.assertTrue(tree["nodes"][2]["properties"][0]["boolean"])
        self.assertEqual(tree["reservations"][0]["labels"], ["reservation"])
        self.assertEqual(tree["reservations"][0]["value"], "0x10 0x20")

    def test_comments_strings_and_multiline_values_keep_resolved_line_numbers(self):
        text = '/dts-v1/;\n// ignored {\n/ {\n /* x; } */\n compatible = "vendor,one",\n              "vendor,two";\n};'
        tree = parse_resolved_dts(text)
        self.assertEqual(tree["status"], "parsed")
        self.assertEqual(tree["nodes"][0]["line"], 3)
        self.assertEqual(tree["nodes"][0]["properties"][0]["line"], 5)
        self.assertEqual(tree["nodes"][0]["properties"][0]["value"], '"vendor,one",\n              "vendor,two"')

    def test_unsupported_and_malformed_input_never_exposes_a_guessed_partial_tree(self):
        for text in ('/dts-v1/; / { good; /delete-node/ missing; };',
                     '/dts-v1/; / { value = /incbin/("file"); };',
                     '/dts-v1/; /include/ "file"',
                     '/dts-v1/; / { good; value = < 1; };',
                     '/dts-v1/; / { value = "unfinished; };',
                     '/dts-v1/; / { /* unfinished',
                     '/dts-v1/; / { child {}; child {}; };',
                     '/dts-v1/; / { repeat; repeat; };',
                     '/dts-v1/; &uart { status = "okay"; };',
                     '/dts-v1/;', ''):
            with self.subTest(text=text):
                tree = parse_resolved_dts(text)
                self.assertEqual(tree["status"], "unsupported")
                self.assertEqual(tree["nodes"], [])
                self.assertTrue(tree["diagnostics"])

    def test_structural_resource_limits_have_explicit_raw_text_fallback(self):
        limits = {"MAX_BYTES": 10, "MAX_TOKENS": 10, "MAX_NODES": 2, "MAX_PROPERTIES": 2, "MAX_DEPTH": 1}
        for name, limit in limits.items():
            with self.subTest(limit=name), patch(f"zephyr_utilities.features.devicetree.parser.{name}", limit):
                tree = parse_resolved_dts(DTS)
                self.assertEqual(tree["status"], "unsupported")
                self.assertEqual(tree["nodes"], [])
                self.assertIn("limit", tree["diagnostics"][0])


class DeviceTreeApiTests(unittest.TestCase):
    def setUp(self):
        self.workspace = "/workspace"
        self.identifier = "a" * 32
        self.domain = {"name": "app", "build_dir": "/workspace/build/app", "context": {"effective_target": "board/chip"},
                       "artifacts": [{"kind": "resolved-dts", "path": "/workspace/build/app/zephyr/zephyr.dts"}],
                       "diagnostics": [], "freshness": "unchanged"}
        self.observation = {"id": self.identifier, "workspace": self.workspace, "domains": [self.domain], "diagnostics": []}
        self.document = {"path": self.domain["artifacts"][0]["path"], "text": DTS,
                         "sha256": hashlib.sha256(DTS.encode()).hexdigest()}
        self.builds = Mock()
        self.builds.get.side_effect = lambda *args: deepcopy(self.observation)
        self.builds.read_text.side_effect = lambda *args: dict(self.document)
        self.context = SimpleNamespace(workspace=SimpleNamespace(get=lambda: {"path": self.workspace}),
                                       builds=self.builds, services={})
        self.router = Router()
        register(self.router, self.context)

    def call(self, body=None):
        if body is None:
            body = {"workspace": self.workspace, "inspection_id": self.identifier, "domain": "app"}
        return self.router.dispatch(Request("POST", "/api/devicetree/inspect", body=body)).data

    def test_shared_observation_and_domain_are_used_without_loading_pickle_or_running_tools(self):
        with patch("subprocess.Popen") as process, patch.object(Path, "write_text") as write:
            data = self.call()
            self.assertEqual(data["inspection"], self.observation)
            self.assertEqual(data["domain"], self.domain)
            self.assertEqual(data["document"], self.document)
            self.assertEqual(data["tree"]["status"], "parsed")
            process.assert_not_called()
            write.assert_not_called()
        self.builds.get.assert_called_once_with(self.workspace, self.identifier)
        self.builds.read_text.assert_called_once_with(self.workspace, self.identifier, "app", "resolved-dts")

    def test_workspace_and_request_validation_precede_build_reads(self):
        for body in ({}, {"workspace": "/other", "inspection_id": self.identifier, "domain": "app"},
                     {"workspace": self.workspace, "inspection_id": self.identifier, "domain": "app", "path": "/outside"},
                     {"workspace": self.workspace, "inspection_id": self.identifier, "domain": None}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.call(body)
        self.builds.get.assert_not_called()
        self.builds.read_text.assert_not_called()

    def test_unknown_domain_and_missing_dts_never_read_another_image(self):
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.call({"workspace": self.workspace, "inspection_id": self.identifier, "domain": "bootloader"})
        self.domain["artifacts"] = []
        with self.assertRaisesRegex(ValueError, "no resolved Device Tree"):
            self.call()
        self.builds.read_text.assert_not_called()

    def test_changed_or_missing_artifacts_propagate_inspection_error(self):
        self.builds.read_text.side_effect = ValueError("Artifact changed; inspect the build again.")
        with self.assertRaisesRegex(ValueError, "Artifact changed"):
            self.call()

    def test_unsupported_syntax_still_returns_exact_raw_document(self):
        self.document["text"] = '/dts-v1/; /include/ "not-read.dtsi"'
        data = self.call()
        self.assertEqual(data["tree"]["status"], "unsupported")
        self.assertEqual(data["document"]["text"], self.document["text"])
