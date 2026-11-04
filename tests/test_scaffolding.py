"""Check safe feature generation without modifying the actual registry or features."""

import ast
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "new_feature.py"
SPEC = importlib.util.spec_from_file_location("new_feature", SCRIPT)
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


class ScaffoldingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.features = self.root / "src" / "zephyr_utilities" / "features"
        self.features.mkdir(parents=True)
        (self.features / "__init__.py").write_text("# Existing explicit registry\n", encoding="utf-8")

    def test_generates_a_planned_feature_and_leaves_registration_explicit(self):
        target = generator.scaffold("telemetry", 'Telemetry "preview" <local>', root=self.root)
        for path in target.glob("*.py"):
            ast.parse(path.read_text())
        namespace = {"__package__": "zephyr_utilities.features.telemetry"}
        exec(compile((target / "feature.py").read_text(), str(target / "feature.py"), "exec"), namespace)
        feature = namespace["FEATURE"]
        self.assertEqual(feature.id, "telemetry")
        self.assertEqual(feature.status, "planned")
        self.assertIsNone(feature.frontend)
        self.assertIsNone(feature.register)
        self.assertIn("&lt;local&gt;", (target / "web" / "view.html").read_text())
        self.assertEqual((self.features / "__init__.py").read_text(), "# Existing explicit registry\n")
        self.assertFalse(list(self.features.glob(".telemetry-*")))

    def test_rejects_invalid_paths_and_reserved_identifiers_without_writes(self):
        for feature_id in ("../outside", "two/tools", "Upper", "two-tools", "class", "home", "shell", "overview", ""):
            with self.subTest(feature_id=feature_id), self.assertRaises(ValueError):
                generator.scaffold(feature_id, "Test", root=self.root)
        self.assertEqual([path.name for path in self.features.iterdir()], ["__init__.py"])

    def test_existing_feature_is_not_overwritten(self):
        target = generator.scaffold("telemetry", "Telemetry", root=self.root)
        evidence = target / "keep.txt"
        evidence.write_text("existing work")
        with self.assertRaises(ValueError):
            generator.scaffold("telemetry", "Replacement", root=self.root)
        self.assertEqual(evidence.read_text(), "existing work")

    def test_missing_or_multiline_title_is_rejected(self):
        for title in ("", " ", "Two\nlines", "Null\x00value"):
            with self.subTest(title=title), self.assertRaises(ValueError):
                generator.scaffold("telemetry", title, root=self.root)
