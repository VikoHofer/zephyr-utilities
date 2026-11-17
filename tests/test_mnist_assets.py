"""Synthetic independent asset installations; no trained model is stored in Git."""

from concurrent.futures import CancelledError
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from zephyr_utilities.features.projects.mnist_assets import MNISTAssets
from zephyr_utilities.features.projects.mnist_sample import MODULE_REVISION


def fixture(root):
    assets, module = root / "selected-assets", root / "selected-framework"
    model = b"\x08\0\0\0TFL3" + bytes(range(32))
    images = bytes(range(256)) * 91 + bytes(range(224))
    metadata = {"architecture": [784, 64, 10], "dtype": "float32", "model_bytes": len(model),
                "model_sha256": hashlib.sha256(model).hexdigest(),
                "images_sha256": hashlib.sha256(images).hexdigest(), "embedded_test_indices": list(range(30)),
                "embedded_correct": 30, "test_accuracy": 0.9722}
    files = {
        "model/mnist.tflite": model, "model/metadata.json": json.dumps(metadata).encode(),
        "model/model_metadata.hpp": ("// Synthetic metadata\n#pragma once\nnamespace mnist {\n"
                                     f'inline constexpr char kModelSha256[] = "{metadata["model_sha256"]}";\n'
                                     "inline constexpr float kTestAccuracy = 0.9722f;\n}\n").encode(),
        "data/test_images.bin": images,
        "data/test_samples.inc": ("// Synthetic scores\n" + "\n".join(
            f"{{{index}, {index % 10}, {index % 10}, {{" + ", ".join(
                "1.0f" if digit == index % 10 else "0.0f" for digit in range(10)) + "}},"
            for index in range(30))).encode(),
        "data/NOTICE.rst": b"Synthetic fixture. Yann LeCun; CC-BY-SA-3.0\n",
        "LICENSE": b"Apache License\nVersion 2.0\nSynthetic fixture, not license terms.\n",
    }
    for relative, content in files.items():
        path = assets / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    files = {
        ".git/HEAD": MODULE_REVISION + "\n",
        "zephyr/module.yml": "name: tflite-micro\nbuild:\n  cmake-ext: True\n  kconfig-ext: True\n",
        "LICENSE": "Apache License\nVersion 2.0\nSynthetic fixture.\n",
        "tensorflow/lite/micro/micro_interpreter.h": "// Synthetic framework header\n",
        "third_party/example/include/example.hpp": "// Synthetic dependency\n",
    }
    for relative, content in files.items():
        path = module / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return assets, module


class MNISTAssetsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="independent-mnist-assets-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.module = fixture(self.root)
        self.store = MNISTAssets(self.root / "managed/runtime")

    def test_status_never_installs_or_reads_the_legacy_application(self):
        self.assertEqual(self.store.status()["status"], "missing")
        self.assertFalse(self.store.root.exists())

    def test_install_survives_removing_every_source_and_copies_no_git_or_links(self):
        messages = []
        result = self.store.install(self.source, self.module, messages.append)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(messages)
        copied = self.store.path / "model/mnist.tflite"
        self.assertEqual(copied.read_bytes(), (self.source / "model/mnist.tflite").read_bytes())
        self.assertNotEqual(copied.stat().st_ino, (self.source / "model/mnist.tflite").stat().st_ino)
        installed = self.store.path / "framework/tflite-micro"
        self.assertFalse((installed / ".git").exists())
        self.assertEqual((installed / "REVISION").read_text().strip(), MODULE_REVISION)
        manifest = (self.store.path / "manifest.json").read_text()
        self.assertNotIn(str(self.source), manifest)
        self.assertNotIn(str(self.module), manifest)
        shutil.rmtree(self.source)
        shutil.rmtree(self.module)
        self.assertEqual(self.store.status(), result)

    def test_identical_install_is_idempotent_without_replacing_owned_files(self):
        first = self.store.install(self.source, self.module)
        before = (self.store.path / "manifest.json").stat()
        second = self.store.install(self.source, self.module)
        self.assertEqual(first, second)
        self.assertEqual(before.st_ino, (self.store.path / "manifest.json").stat().st_ino)
        self.assertEqual(before.st_mtime_ns, (self.store.path / "manifest.json").stat().st_mtime_ns)

    def test_different_valid_pack_cannot_replace_an_installed_runtime(self):
        first = self.store.install(self.source, self.module)
        selected = self.source / "data/test_samples.inc"
        selected.write_text(selected.read_text() + "\n// Different selected reference data\n")
        with self.assertRaisesRegex(ValueError, "different"):
            self.store.install(self.source, self.module)
        self.assertEqual(self.store.status(), first)

    def test_changed_installed_bytes_remain_visible_and_are_never_overwritten(self):
        self.store.install(self.source, self.module)
        selected = self.store.path / "framework/tflite-micro/tensorflow/lite/micro/micro_interpreter.h"
        selected.write_text("// Existing local edit\n")
        self.assertEqual(self.store.status()["status"], "invalid")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.store.install(self.source, self.module)
        self.assertEqual(selected.read_text(), "// Existing local edit\n")

    def test_added_git_metadata_cannot_redirect_managed_framework_identity(self):
        self.store.install(self.source, self.module)
        git = self.store.path / "framework/tflite-micro/.git"
        git.mkdir()
        (git / "HEAD").write_text("0" * 40 + "\n")
        self.assertEqual(self.store.status()["status"], "invalid")
        with self.assertRaisesRegex(ValueError, "Git database"):
            self.store.install(self.source, self.module)
        self.assertTrue(git.is_dir())

    def test_invalid_asset_identity_or_attribution_never_creates_an_installation(self):
        for relative in ("model/mnist.tflite", "data/test_images.bin", "data/NOTICE.rst", "LICENSE"):
            with self.subTest(relative=relative):
                path = self.source / relative
                previous = path.read_bytes()
                path.write_bytes(b"invalid")
                with self.assertRaises(ValueError):
                    self.store.install(self.source, self.module)
                self.assertFalse(self.store.path.exists())
                path.write_bytes(previous)

    def test_source_asset_and_framework_links_are_rejected(self):
        for root, relative in ((self.source, "model/mnist.tflite"),
                               (self.module, "tensorflow/lite/micro/micro_interpreter.h")):
            for kind in ("symbolic", "hard"):
                with self.subTest(relative=relative, kind=kind):
                    path = root / relative
                    saved = path.with_suffix(".saved")
                    path.rename(saved)
                    if kind == "symbolic":
                        path.symlink_to(saved)
                    else:
                        os.link(saved, path)
                    with self.assertRaises((ValueError, OSError)):
                        self.store.install(self.source, self.module)
                    self.assertFalse(self.store.path.exists())
                    path.unlink()
                    saved.rename(path)

    def test_a_linked_store_parent_is_not_followed(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (self.root / "managed").symlink_to(elsewhere, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.store.install(self.source, self.module)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_cancellation_before_or_during_copy_never_publishes_a_partial_install(self):
        for during in (False, True):
            with self.subTest(during=during):
                cancel = Event()
                if not during:
                    cancel.set()

                def emit(message):
                    if "framework" in message:
                        cancel.set()

                with self.assertRaises(CancelledError):
                    self.store.install(self.source, self.module, emit, cancel)
                self.assertFalse(self.store.path.exists())
                if self.store.root.exists():
                    self.assertEqual(list(self.store.root.iterdir()), [])

    def test_framework_changes_after_review_do_not_publish_and_preserve_the_source(self):
        selected = self.module / "tensorflow/lite/micro/micro_interpreter.h"

        def edit(message):
            if "framework" in message:
                selected.write_text("// Concurrent source edit\n")

        with self.assertRaisesRegex(ValueError, "changed"):
            self.store.install(self.source, self.module, edit)
        self.assertFalse(self.store.path.exists())
        self.assertEqual(selected.read_text(), "// Concurrent source edit\n")
        self.assertEqual(list(self.store.root.iterdir()), [])

    def test_framework_bytes_are_bounded_before_publication(self):
        with patch("zephyr_utilities.features.projects.mnist_assets._MAX_MODULE_FILE_BYTES", 5):
            with self.assertRaises(ValueError):
                self.store.install(self.source, self.module)
        self.assertFalse(self.store.path.exists())

    def test_wrong_or_missing_pinned_framework_revision_is_rejected(self):
        (self.module / ".git/HEAD").write_text("0" * 40 + "\n")
        with self.assertRaises(ValueError):
            self.store.install(self.source, self.module)
        self.assertFalse(self.store.path.exists())

    def test_a_managed_pack_can_provision_another_independent_install_without_git(self):
        first = self.store.install(self.source, self.module)
        shutil.rmtree(self.source)
        shutil.rmtree(self.module)
        other = MNISTAssets(self.root / "another-install")
        second = other.install(self.store.path, self.store.path / "framework/tflite-micro")
        self.assertEqual(second["status"], "ready")
        self.assertEqual(first["fingerprint"], second["fingerprint"])


if __name__ == "__main__":
    unittest.main()
