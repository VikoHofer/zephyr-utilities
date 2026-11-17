"""Shared launcher for local Zephyr workspace utilities."""

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__


def default_root():
    """Prefer the current west workspace, then the launcher's virtual environment."""
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".west").is_dir():
            return candidate
    # sys.prefix retains the virtualenv identity even if its Python is a symlink.
    if sys.prefix != sys.base_prefix:
        workspace = Path(sys.prefix).parent
        if (workspace / ".west").is_dir():
            return workspace.resolve()
    return cwd


def build_parser():
    # Suppressed defaults preserve options placed before an explicit subcommand.
    launch = argparse.ArgumentParser(add_help=False)
    launch.add_argument("--root", type=Path, default=argparse.SUPPRESS,
                        help="Initial project or build directory (default: detected west workspace)")
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    data_dir = data_home / "zephyr-utilities" / "memory"
    launch.add_argument("--data-dir", type=Path, default=argparse.SUPPRESS,
                        help=f"Persistent snapshot directory (default: {data_dir})")
    launch.add_argument("--port", type=int, default=argparse.SUPPRESS,
                        help="Loopback HTTP port; use 0 to choose a free port (default: 8765)")
    launch.add_argument("--no-browser", action="store_true", default=argparse.SUPPRESS,
                        help="Print URL without opening a browser")
    launch.add_argument("--install-mnist-assets", type=Path, default=argparse.SUPPRESS, metavar="PACK",
                        help="Install an independent MNIST model/image asset pack before starting the application")
    launch.add_argument("--tflm-source", type=Path, default=argparse.SUPPRESS, metavar="FRAMEWORK",
                        help="Pinned TensorFlow Lite Micro directory to install with --install-mnist-assets")
    parser = argparse.ArgumentParser(
        description="Zephyr Utilities — one local application for your workspace tools.",
        parents=[launch],
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.set_defaults(handler=launch_application, entry_path="/#/home")
    commands = parser.add_subparsers(dest="command", title="tools")
    memory = commands.add_parser("memory", help="Open the shared application directly in Memory Explorer",
                                 parents=[launch])
    memory.set_defaults(handler=launch_application, entry_path="/#/memory/overview")
    # Features register with the app, not separate processes or launch handlers.
    return parser, data_dir


def main(argv=None):
    parser, data_dir = build_parser()
    args = parser.parse_args(argv)
    defaults = {"root": default_root, "data_dir": lambda: data_dir,
                "port": lambda: 8765, "no_browser": lambda: False}
    for name, value in defaults.items():
        if not hasattr(args, name):
            setattr(args, name, value())
    return args.handler(args, parser)


def launch_application(args, parser):
    if not args.root.expanduser().is_dir():
        parser.error(f"Initial directory does not exist: {args.root}")
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535.")
    assets_source = getattr(args, "install_mnist_assets", None)
    framework_source = getattr(args, "tflm_source", None)
    if (assets_source is None) != (framework_source is None):
        parser.error("--install-mnist-assets and --tflm-source must be supplied together.")
    try:
        if assets_source is not None:
            from .features.projects.mnist_assets import MNISTAssets
            assets = MNISTAssets(args.data_dir.expanduser().absolute() / ".features/projects/tinyml-runtime")
            result = assets.install(assets_source.expanduser().absolute(), framework_source.expanduser().absolute(), emit=print)
            print(f"MNIST runtime assets installed independently: {result['path']}", flush=True)
        from .app import create_server
        server = create_server(args.root.expanduser(), args.data_dir.expanduser(), args.port)
    except ModuleNotFoundError as exc:
        print(f"Missing dependency: {exc.name}. Install this project with 'python -m pip install -e .' "
              "or use your Zephyr Python environment.", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"Unable to start Zephyr Utilities: {exc}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_port}{args.entry_path}"
    print(f"Zephyr Utilities {__version__}\nLocal: {url}\n"
          f"Workspace: {server.app.initial_path}\n"
          f"Data directory: {args.data_dir.resolve()}\nPress Ctrl+C to stop.", flush=True)
    if not args.no_browser:
        opener = threading.Timer(0.3, webbrowser.open, args=(url,))
        opener.daemon = True
        opener.start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nStopping Zephyr Utilities.", flush=True)
    finally:
        server.app.close()
        server.server_close()
    return 0
