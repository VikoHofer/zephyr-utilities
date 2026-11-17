"""The real D0 hash-only descriptor stays bound to its qualified source inventory."""

import hashlib
import json
from pathlib import Path
import unittest

from zephyr_utilities.features.projects.demo_contracts import validate_descriptor


DOCS = Path(__file__).resolve().parents[1] / "docs"


class DemoD0ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.inventory = json.loads((DOCS / "tinyml-d0-source-inventory.json").read_text())
        self.descriptor = json.loads((DOCS / "tinyml-d0-tflm-descriptor.json").read_text())

    def test_real_descriptor_binds_reviewed_files_omissions_model_and_license(self):
        descriptor = validate_descriptor(self.descriptor)
        source = self.inventory["source"]
        observed = {item["path"]: item for item in source["files"]}
        expected = {path: item["sha256"] for path, item in observed.items()
                    if item["action"] in {"include", "transform"}}
        license_file = next(item for item in source["license_evidence"] if item["path"] == "LICENSE")
        expected["LICENSE"] = license_file["sha256"]
        self.assertEqual({item["path"]: item["sha256"] for item in descriptor["runtime"]["files"]}, expected)
        for item in descriptor["runtime"]["files"]:
            self.assertEqual(item["bytes"], license_file["bytes"] if item["path"] == "LICENSE"
                             else observed[item["path"]]["bytes"])
        self.assertEqual({item["path"] for item in descriptor["runtime"]["omitted"]},
                         {path for path, item in observed.items() if item["action"] == "omit"})
        self.assertEqual(descriptor["model"]["sha256"], observed[source["plan"]["model"]["path"]]["sha256"])
        self.assertEqual(descriptor["source"]["revision"], self.inventory["zephyr"]["git"]["head"])

    def test_capture_digest_is_reproducible_without_claiming_dependency_or_build_success(self):
        inventory = self.inventory
        basis = {"zephyr": inventory["zephyr"], "source": inventory["source"],
                 "manifest": inventory["dependencies"]["tflite_micro"]["manifest"]}
        digest = hashlib.sha256(json.dumps(basis, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=True).encode()).hexdigest()
        self.assertEqual(digest, self.descriptor["source"]["fingerprint"])
        self.assertEqual(inventory["qualification"]["status"], "source-qualified")
        self.assertEqual(inventory["dependencies"]["tflite_micro"]["installation"]["status"], "missing")
        self.assertEqual(inventory["source"]["plan"]["validation"]["build"], "not-checked")
        self.assertEqual(inventory["source"]["plan"]["validation"]["configuration"], "not-checked")
