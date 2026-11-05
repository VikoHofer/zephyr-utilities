"""Reviewed overlay changes reject directory swaps without deleting foreign files."""

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from zephyr_utilities.features.projects.creation import path_identity
from zephyr_utilities.features.projects import reviewed_files


class ReviewedFileTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="reviewed-files-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.changes = [{"path": path, "before": before, "content": content,
                         "sha256": hashlib.sha256(content.encode()).hexdigest()}
                        for path, before, content in (
                            ("CMakeLists.txt", "# original\n", "# reviewed update\n"),
                            ("overlays/target.overlay", None, "/* new target */\n"))]

    def assert_directory_swap_is_preserved(self, *, existing, replacement):
        source = self.root / f"application-{existing}-{replacement}"
        source.mkdir()
        cmake = source / "CMakeLists.txt"
        cmake.write_text("# original\n")
        overlays = source / "overlays"
        if existing:
            overlays.mkdir()
            (overlays / "previous.overlay").write_text("/* original user overlay */\n")
        moved = source / "moved-overlays"
        foreign = self.root / f"foreign-{existing}-{replacement}"
        foreign.mkdir()
        (foreign / "keep.txt").write_text("outside source tree")
        real_exchange = reviewed_files._exchange
        swapped = False
        replacement_identity = None

        def exchange(*arguments):
            nonlocal swapped, replacement_identity
            if not swapped:
                swapped = True
                overlays.rename(moved)
                if replacement == "symlink":
                    overlays.symlink_to(foreign, target_is_directory=True)
                else:
                    overlays.mkdir()
                    if replacement == "populated":
                        (overlays / "keep.overlay").write_text("/* concurrent user file */\n")
                info = overlays.lstat()
                replacement_identity = info.st_dev, info.st_ino
            return real_exchange(*arguments)

        save = Mock(return_value={"saved": True})
        with patch.object(reviewed_files, "_exchange", side_effect=exchange):
            with self.assertRaisesRegex(ValueError, "overlay directory changed"):
                reviewed_files.apply_reviewed_files(source, path_identity(source), self.changes, save)
        save.assert_not_called()
        self.assertEqual(cmake.read_text(), "# original\n")
        self.assertFalse((moved / "target.overlay").exists())
        self.assertTrue(overlays.exists())
        info = overlays.lstat()
        self.assertEqual((info.st_dev, info.st_ino), replacement_identity)
        if replacement == "symlink":
            self.assertTrue(overlays.is_symlink())
        elif replacement == "populated":
            self.assertEqual((overlays / "keep.overlay").read_text(), "/* concurrent user file */\n")
        else:
            self.assertEqual(list(overlays.iterdir()), [])
        self.assertEqual((foreign / "keep.txt").read_text(), "outside source tree")
        if existing:
            self.assertEqual((moved / "previous.overlay").read_text(), "/* original user overlay */\n")
        self.assertEqual(list(source.glob(".zephyr-review-*")), [])

    def test_existing_overlay_directory_swap_rejects_save_and_preserves_replacement(self):
        for replacement in ("empty", "populated", "symlink"):
            with self.subTest(replacement=replacement):
                self.assert_directory_swap_is_preserved(existing=True, replacement=replacement)

    def test_new_overlay_directory_swap_does_not_remove_foreign_replacement_during_rollback(self):
        for replacement in ("empty", "populated", "symlink"):
            with self.subTest(replacement=replacement):
                self.assert_directory_swap_is_preserved(existing=False, replacement=replacement)

    def test_failed_save_removes_only_unchanged_created_directory_and_restores_cmake(self):
        source = self.root / "application"
        source.mkdir()
        cmake = source / "CMakeLists.txt"
        cmake.write_text("# original\n")
        save = Mock(side_effect=OSError("Storage failed"))
        with self.assertRaisesRegex(OSError, "Storage failed"):
            reviewed_files.apply_reviewed_files(source, path_identity(source), self.changes, save)
        self.assertEqual(cmake.read_text(), "# original\n")
        self.assertFalse((source / "overlays").exists())
        self.assertEqual(list(source.glob(".zephyr-review-*")), [])
