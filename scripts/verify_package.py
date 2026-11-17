#!/usr/bin/env python3
"""Build offline and verify runtime imports and browser assets from the wheel."""

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SMOKE = r'''
import importlib
import importlib.metadata
import pathlib
import re
import sys

installed = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(installed))
import zephyr_utilities
from zephyr_utilities.features import builtin_features
from zephyr_utilities.core.features import validate_features
from zephyr_utilities.cli import main

package = pathlib.Path(zephyr_utilities.__file__).resolve().parent
assert package.parent == installed, f"Wrong import location: {package}"
metadata = importlib.metadata.distribution("zephyr-utilities")
assert metadata.version == zephyr_utilities.__version__
assert any(e.name == "zephyr-utils" and e.value == "zephyr_utilities.cli:main"
           for e in metadata.entry_points)
for name in ("index.html", "shell.js", "shell.css", "shared/api.js", "shared/routes.js", "shared/interaction.js"):
    assert (package / "web" / name).is_file(), f"Missing shell asset: {name}"
for feature in validate_features(builtin_features()):
    if feature.frontend:
        for attr in ("module", "template", "stylesheet"):
            asset = feature.frontend.asset_dir / getattr(feature.frontend, attr)
            assert asset.is_file(), f"Missing feature asset: {asset}"
            assert asset.resolve().is_relative_to(package), f"External asset: {asset}"
for name in ("app", "core.http", "features.memory.snapshots", "memory.api", "memory.model",
             "features.projects.demo_contracts", "features.projects.demo_handoffs", "features.tinyml.mnist_protocol"):
    importlib.import_module("zephyr_utilities." + name)
for name in ("LICENSE-MNIST.txt", "NOTICE-MNIST.txt"):
    assert (package / "features" / "tinyml" / name).is_file(), f"Missing MNIST attribution: {name}"
template = package / "features" / "projects" / "mnist_template"
for name in ("src/main.cpp", "src/inference.cpp", "src/inference.hpp", "src/assets.cpp", "src/assets.hpp",
             "prj.conf", "boards/nucleo_h753zi.conf", "README.rst", "NOTICE.rst", "LICENSE"):
    asset = template / name
    assert asset.is_file(), f"Missing MNIST runtime template: {name}"
    assert asset.resolve().is_relative_to(package), f"External MNIST template: {asset}"
assert not any((template / name).exists() for name in ("model", "data", "CMakeLists.txt")), \
    "MNIST template unexpectedly bundles managed assets or an active CMake file."
managed_includes = {"model_metadata.hpp", "model.inc", "images.inc", "test_samples.inc"}
for source in (template / "src").iterdir():
    for include in re.findall(r'^\s*#\s*include\s*"([^"\n]+)"', source.read_text(encoding="utf-8"), re.MULTILINE):
        assert (template / "src" / include).is_file() or include in managed_includes, \
            f"Missing MNIST template runtime include: {include}"
try:
    main(["--help"])
except SystemExit as exc:
    assert exc.code == 0
print(f"Verified installed wheel {metadata.version}: imports, console entry point and runtime assets.")
'''


def main():
    try:
        with tempfile.TemporaryDirectory(prefix="zephyr-utils-wheel-") as directory:
            temporary = Path(directory)
            source = temporary / "source"
            source.mkdir()
            for name in ("pyproject.toml", "README.md", "MANIFEST.in"):
                shutil.copy2(ROOT / name, source / name)
            for pattern in ("LICENSE*", "LICENCE*", "COPYING*", "NOTICE*"):
                for path in ROOT.glob(pattern):
                    if path.is_file():
                        shutil.copy2(path, source / path.name)
            # An old build/lib or egg-info can otherwise silently retain removed files.
            shutil.copytree(ROOT / "src", source / "src",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
            subprocess.run([
                sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
                "--no-index", "--no-cache-dir", "--wheel-dir", str(temporary), str(source),
            ], check=True, cwd=source, timeout=120)
            wheels = list(temporary.glob("zephyr_utilities-*.whl"))
            if len(wheels) != 1:
                raise RuntimeError(f"Expected one project wheel, found {len(wheels)}")
            installed = temporary / "installed"
            with zipfile.ZipFile(wheels[0]) as archive:
                names = archive.namelist()
                if any(name.startswith(("tests/", "node_modules/", ".memory-explorer/")) for name in names):
                    raise RuntimeError("Development or captured data leaked into the wheel.")
                package = source / "src" / "zephyr_utilities"
                asset_roots = [package / "web", *(package / "features").glob("*/web"),
                               package / "features" / "projects" / "mnist_template"]
                expected = {path.relative_to(source / "src").as_posix()
                            for root in asset_roots for path in root.rglob("*") if path.is_file()}
                missing = expected.difference(names)
                if missing:
                    raise RuntimeError(f"Runtime assets missing from wheel: {', '.join(sorted(missing))}")
                archive.extractall(installed)
            subprocess.run([sys.executable, "-I", "-c", SMOKE, str(installed)],
                           check=True, cwd=temporary, timeout=30)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Package verification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
