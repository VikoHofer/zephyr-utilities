#!/usr/bin/env python3
"""Run the repository's offline checks with one command."""

import argparse
import ast
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def run(command):
    print(f"\n> {' '.join(map(str, command))}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True, timeout=180)


def cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from cases(test)
        else:
            yield test


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Skip only tests that need loopback sockets")
    args = parser.parse_args(argv)
    try:
        run([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts", "zephyr-utils"])
        python_files = [ROOT / "zephyr-utils"]
        for directory in ("src", "tests", "scripts"):
            python_files.extend(sorted((ROOT / directory).rglob("*.py")))
        for path in python_files:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        print(f"Python syntax: {len(python_files)} files", flush=True)
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js 22+ is required for development checks (not for running the app).")
        major = int(subprocess.check_output([node, "--version"], text=True).strip().lstrip("v").split(".")[0])
        if major < 22:
            raise RuntimeError("Development checks require Node.js 22 or later.")
        javascript = sorted((ROOT / "src").rglob("*.js")) + sorted((ROOT / "tests").rglob("*.mjs"))
        for path in javascript:
            subprocess.run([node, "--check", str(path)], check=True, timeout=30)
        print(f"JavaScript syntax: {len(javascript)} files", flush=True)
        discovered = list(cases(unittest.defaultTestLoader.discover(str(ROOT / "tests"))))
        selected = [test for test in discovered if not (args.quick and getattr(test, "requires_loopback", False))]
        if args.quick:
            print(f"Quick mode: {len(discovered) - len(selected)} loopback tests excluded.", flush=True)
        result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
        frontend_tests = sorted((ROOT / "tests" / "frontend").glob("*.test.mjs"))
        if not frontend_tests:
            raise RuntimeError("No frontend tests found; the shared shell must have behavior checks.")
        run([node, "--test", *map(str, frontend_tests)])
        return 0 if result.wasSuccessful() else 1
    except (OSError, RuntimeError, SyntaxError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
