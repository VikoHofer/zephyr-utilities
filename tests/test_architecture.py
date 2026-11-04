"""Keep feature work independent as the suite grows."""

import ast
import importlib.util
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "zephyr_utilities"


def imports(path):
    relative = path.relative_to(PACKAGE).with_suffix("")
    parts = ("zephyr_utilities", *relative.parts)
    package = ".".join(parts[:-1])
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            module = importlib.util.resolve_name(prefix, package) if node.level else prefix
            yield module
            yield from (f"{module}.{alias.name}" for alias in node.names)


class ArchitectureTests(unittest.TestCase):
    def test_core_does_not_depend_on_feature_implementations_or_composition(self):
        prohibited = {"features", "memory", "app", "cli"}
        for path in (PACKAGE / "core").rglob("*.py"):
            for dependency in imports(path):
                parts = dependency.split(".")
                with self.subTest(path=path.name, dependency=dependency):
                    self.assertFalse(len(parts) > 1 and parts[0] == "zephyr_utilities" and parts[1] in prohibited)

    def test_features_do_not_import_each_other_or_legacy_memory(self):
        for path in (PACKAGE / "features").rglob("*.py"):
            relative = path.relative_to(PACKAGE / "features")
            if len(relative.parts) < 2:
                continue  # The explicit registry is the composition boundary.
            feature = relative.parts[0]
            for dependency in imports(path):
                parts = dependency.split(".")
                with self.subTest(path=str(path.relative_to(PACKAGE)), dependency=dependency):
                    self.assertFalse(dependency == "zephyr_utilities.memory" or
                                     dependency.startswith("zephyr_utilities.memory."))
                    self.assertFalse(dependency == "zephyr_utilities.features",
                                     "Feature modules must not depend on the application registry.")
                    self.assertFalse(len(parts) > 1 and parts[0] == "zephyr_utilities" and
                                     parts[1] in {"app", "cli"},
                                     "Features must not depend on the composition root or launcher.")
                    if len(parts) >= 3 and parts[:2] == ["zephyr_utilities", "features"]:
                        self.assertEqual(parts[2], feature, "Use a public shared core interface instead.")
