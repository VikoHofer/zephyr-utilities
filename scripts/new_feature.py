#!/usr/bin/env python3
"""Create a planned feature skeleton without changing the app's explicit registry."""

import argparse
import html
import keyword
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zephyr_utilities.core.features import RESERVED_FEATURE_IDS


def scaffold(feature_id, title, *, root=ROOT):
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", feature_id) or keyword.iskeyword(feature_id):
        raise ValueError("Use a lowercase Python identifier, for example 'telemetry' or 'runtime_metrics'.")
    if feature_id in RESERVED_FEATURE_IDS:
        raise ValueError(f"'{feature_id}' is reserved by the application shell.")
    if not isinstance(title, str) or not title.strip() or any(ord(char) < 32 for char in title):
        raise ValueError("Provide a nonempty, single-line English title.")
    title = title.strip()
    parent = Path(root) / "src" / "zephyr_utilities" / "features"
    if not parent.is_dir():
        raise ValueError("Run this script in a Zephyr Utilities repository.")
    destination = parent / feature_id
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"Feature already exists: {destination}. Extend it instead of overwriting it.")
    content = {
        "__init__.py": f'"""Feature package: {feature_id}."""\n',
        "feature.py": (
            '"""Planned feature; register executable components only when implemented."""\n\n'
            'from ...core.features import FeatureDefinition\n\n'
            'FEATURE = FeatureDefinition(\n'
            f'    id={feature_id!r},\n    title={title!r},\n'
            "    description='Define the workflow and acceptance cases before enabling this feature.',\n"
            '    status="planned",\n)\n'
        ),
        "README.md": (
            f"# {title}\n\nStatus: planned. This skeleton does not implement a user workflow.\n\n"
            "Define inputs, outputs, persistence, failure states and acceptance cases here.\n"
            "Keep services and routes in this package; use shared core workspace and jobs.\n\n"
            "Use shared theme tokens and visible action feedback from the UI guidelines.\n"
            "Keep requested results in view; passive updates must preserve focus and scroll.\n\n"
            "Follow [Adding a feature](../../../../docs/adding-a-feature.md). Import `FEATURE`\n"
            "explicitly in `features/__init__.py` and add it to `builtin_features()`.\n"
            "When the workflow is implemented, attach a `FrontendDefinition` and registration\n"
            "callback, set `status=\"available\"`, and add behavior and package checks.\n"
        ),
        "web/view.html": (
            f'<section class="{feature_id}-feature" aria-label="{html.escape(title, quote=True)}">\n'
            f'  <h1>{html.escape(title)}</h1>\n'
            '  <p data-status>This feature is being developed.</p>\n</section>\n'
        ),
        "web/style.css": f".{feature_id}-feature {{ padding: 1.5rem; }}\n",
        "web/index.js": (
            '// The shell owns routing; scope all DOM work to root and clean up on dispose.\n'
            'export function mount(root, context) {\n'
            '  const controller = new AbortController();\n'
            '  return {\n'
            '    navigate(view = "overview") {\n'
            '      context.state.view = view;\n'
            '      root.dataset.view = view;\n'
            '    },\n'
            '    dispose() { controller.abort(); },\n'
            '  };\n}\n'
        ),
    }
    # Exclusive creation also prevents two contributors generating the same ID at once.
    destination.mkdir()
    try:
        for relative, value in content.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feature_id", help="Stable lowercase identifier, e.g. telemetry")
    parser.add_argument("--title", required=True, help="English title shown in the tool catalog")
    args = parser.parse_args(argv)
    try:
        destination = scaffold(args.feature_id, args.title)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"Created planned feature: {destination}\n"
          "Next: implement its workflow and explicitly register FEATURE in features/__init__.py.\n"
          "See docs/adding-a-feature.md for the backend and frontend contracts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
