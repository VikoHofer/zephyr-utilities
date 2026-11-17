"""Check the shared launcher from activated environments and arbitrary directories."""

import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from zephyr_utilities import cli


class LauncherTests(unittest.TestCase):
    def test_bare_command_and_explicit_memory_dispatch_to_same_handler(self):
        with patch.object(cli, "launch_application", return_value=0) as launch:
            self.assertEqual(cli.main([]), 0)
            self.assertEqual(cli.main(["memory"]), 0)
            first, second = [call.args[0] for call in launch.call_args_list]
        for attribute in ("root", "data_dir", "port", "no_browser"):
            self.assertEqual(getattr(first, attribute), getattr(second, attribute))
        self.assertEqual(first.entry_path, "/#/home")
        self.assertEqual(second.entry_path, "/#/memory/overview")

    def test_options_work_without_subcommand_and_on_either_side_of_it(self):
        variants = (
            ["--port", "9000", "--no-browser", "--root", "/tmp", "--data-dir", "/tmp/history"],
            ["memory", "--port", "9000", "--no-browser", "--root", "/tmp", "--data-dir", "/tmp/history"],
            ["--port", "9000", "--no-browser", "--root", "/tmp", "--data-dir", "/tmp/history", "memory"],
        )
        for argv in variants:
            with self.subTest(argv=argv), patch.object(cli, "launch_application", return_value=0) as launch:
                cli.main(argv)
                args = launch.call_args.args[0]
                self.assertEqual(args.port, 9000)
                self.assertTrue(args.no_browser)
                self.assertEqual(args.root, Path("/tmp"))
                self.assertEqual(args.data_dir, Path("/tmp/history"))

    def test_current_workspace_takes_precedence_over_venv_workspace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            current, environment = root / "current", root / "environment"
            for directory in (current / ".west", current / "app" / "build", environment / ".west"):
                directory.mkdir(parents=True)
            with patch.object(cli.Path, "cwd", return_value=current / "app" / "build"), \
                 patch.object(cli.sys, "prefix", str(environment / ".venv")), \
                 patch.object(cli.sys, "base_prefix", "/system"):
                self.assertEqual(cli.default_root(), current.resolve())

    def test_venv_workspace_is_used_when_started_elsewhere(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace, elsewhere = root / "workspace", root / "elsewhere"
            (workspace / ".west").mkdir(parents=True)
            elsewhere.mkdir()
            with patch.object(cli.Path, "cwd", return_value=elsewhere), \
                 patch.object(cli.sys, "prefix", str(workspace / ".venv")), \
                 patch.object(cli.sys, "base_prefix", "/system"):
                self.assertEqual(cli.default_root(), workspace.resolve())

    def test_unassociated_environment_falls_back_to_current_directory(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(cli.Path, "cwd", return_value=Path(temporary)), \
             patch.object(cli.sys, "prefix", "/system"), \
             patch.object(cli.sys, "base_prefix", "/system"):
            self.assertEqual(cli.default_root(), Path(temporary).resolve())

    def test_data_directory_is_independent_of_working_directory(self):
        with patch.dict(cli.os.environ, {"XDG_DATA_HOME": "/tmp/user-data"}), \
             patch.object(cli, "launch_application", return_value=0) as launch:
            for cwd in (Path("/tmp"), Path("/some/project")):
                with patch.object(cli.Path, "cwd", return_value=cwd):
                    cli.main([])
            for call in launch.call_args_list:
                self.assertEqual(call.args[0].data_dir, Path("/tmp/user-data/zephyr-utilities/memory"))

    def test_default_handler_starts_and_cleans_up_the_server(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            server = Mock()
            server.server_port = 12345
            server.app = SimpleNamespace(initial_path=str(directory),
                                         store=SimpleNamespace(root=directory), close=Mock())
            server.serve_forever.side_effect = KeyboardInterrupt
            args = argparse.Namespace(root=directory, data_dir=directory, port=0, no_browser=True, entry_path="/#/home")
            with patch("zephyr_utilities.app.create_server", return_value=server) as create, \
                 patch.object(cli.webbrowser, "open") as browser, \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.launch_application(args, argparse.ArgumentParser()), 0)
            create.assert_called_once_with(directory, directory, 0)
            server.serve_forever.assert_called_once()
            server.app.close.assert_called_once()
            server.server_close.assert_called_once()
            browser.assert_not_called()

    def test_mnist_installation_uses_selected_data_directory_before_shared_launch(self):
        from test_mnist_assets import fixture
        from zephyr_utilities.features.projects.mnist_assets import MNISTAssets

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets, framework = fixture(root)
            data = root / "chosen-data"
            server = Mock()
            server.server_port = 12345
            server.app.initial_path = str(root)
            server.serve_forever.side_effect = KeyboardInterrupt

            def start(*args):
                self.assertEqual(MNISTAssets(data / ".features/projects/tinyml-runtime").status()["status"], "ready")
                self.assertEqual(args[1], data)
                return server

            with patch("zephyr_utilities.app.create_server", side_effect=start) as create, \
                 contextlib.redirect_stdout(io.StringIO()):
                result = cli.main(["--root", str(root), "--data-dir", str(data), "--no-browser",
                                   "--install-mnist-assets", str(assets), "--tflm-source", str(framework)])
            self.assertEqual(result, 0)
            create.assert_called_once()
            server.app.close.assert_called_once()
            server.server_close.assert_called_once()

    def test_partial_mnist_installation_never_starts_the_application(self):
        with tempfile.TemporaryDirectory() as temporary:
            for option in ("--install-mnist-assets", "--tflm-source"):
                with self.subTest(option=option), patch("zephyr_utilities.app.create_server") as create, \
                     contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                    cli.main(["--root", temporary, option, temporary, "--no-browser"])
                self.assertEqual(raised.exception.code, 2)
                create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
