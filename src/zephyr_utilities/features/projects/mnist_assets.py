"""Explicit offline installation of independently owned MNIST runtime assets."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from threading import RLock
import uuid

from .creation import _rename_noreplace, open_directory, path_identity, validate_identity
from .probing import check_cancel


ASSET_FILES = (
    "model/mnist.tflite", "model/metadata.json", "model/model_metadata.hpp",
    "data/test_images.bin", "data/test_samples.inc", "data/NOTICE.rst", "LICENSE",
)
_MAX_MODULE_FILES = 20000
_MAX_MODULE_BYTES = 80 * 1024 * 1024
_MAX_MODULE_FILE_BYTES = 4 * 1024 * 1024


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _record(path, content):
    return {"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _read(directory, name, limit):
    from .mnist_sample import _read_at

    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("MNIST installation inputs must be regular files without links.")
    return _read_at(directory, name, limit)


def _assets(source):
    identity = path_identity(source)
    result = {}
    for relative in ASSET_FILES:
        parent = source / Path(relative).parent
        with_directory = open_directory(parent)
        try:
            result[relative] = _read(with_directory, Path(relative).name, 1024 * 1024)
        finally:
            os.close(with_directory)
    validate_identity(source, identity)
    return result


def _validate_assets(contents):
    try:
        metadata = json.loads(contents["model/metadata.json"])
        model, images = contents["model/mnist.tflite"], contents["data/test_images.bin"]
        valid = (isinstance(metadata, dict) and len(model) >= 8 and model[4:8] == b"TFL3" and
                 metadata.get("model_sha256") == hashlib.sha256(model).hexdigest() and
                 metadata.get("model_bytes") == len(model) and
                 metadata.get("images_sha256") == hashlib.sha256(images).hexdigest() and
                 len(images) == 30 * 28 * 28 and metadata.get("architecture") == [784, 64, 10] and
                 metadata.get("dtype") == "float32")
        indices = metadata.get("embedded_test_indices") if isinstance(metadata, dict) else None
        valid = (valid and isinstance(indices, list) and len(indices) == 30 and
                 all(type(index) is int and index >= 0 for index in indices) and len(set(indices)) == 30)
        license_text = contents["LICENSE"].decode("utf-8")
        attribution = contents["data/NOTICE.rst"].decode("utf-8")
        for path in ASSET_FILES:
            if path not in {"model/mnist.tflite", "data/test_images.bin"}:
                text = contents[path].decode("utf-8")
                if any(ord(character) < 32 and character not in "\r\n\t" for character in text):
                    valid = False
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError("The MNIST asset pack metadata is invalid.") from exc
    if (not valid or "Apache License" not in license_text or "Version 2.0" not in license_text or
            "CC-BY-SA-3.0" not in attribution or "LeCun" not in attribution):
        raise ValueError("MNIST model/images must match their metadata and retain their license and attribution.")
    from .mnist_sample import TEMPLATE_ROOT, _capture_files, _runtime_inputs, _runtime_metadata, _text

    headers, _ = _capture_files(TEMPLATE_ROOT, ("src/assets.hpp", "src/inference.hpp"))
    text_contents = {path: _text(content, path) for path, content in {**contents, **headers}.items()
                     if path not in {"model/mnist.tflite", "data/test_images.bin"}}
    _runtime_metadata(text_contents, metadata)
    _runtime_inputs(text_contents)


def _write(directory, relative, content):
    """Create only absent files below an installer-owned staging descriptor."""
    descriptor = os.dup(directory)
    try:
        parts = Path(relative).parts
        for part in parts[:-1]:
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        target = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        try:
            offset = 0
            while offset < len(content):
                offset += os.write(target, content[offset:])
            os.fsync(target)
        finally:
            os.close(target)
    finally:
        os.close(descriptor)


def _tree(source, *, cancel=None, output=None):
    """Capture bounded framework bytes, never its Git database or links."""
    from .mnist_sample import _signature

    identity = path_identity(source)
    records, total, count = [], 0, 0

    def visit(directory, prefix, depth):
        nonlocal total, count
        check_cancel(cancel)
        if depth > 24:
            raise ValueError("The MNIST framework directory exceeds the supported depth.")
        before = os.fstat(directory)
        for name in sorted(os.listdir(directory)):
            if not prefix and name in {".git", "REVISION"}:
                continue
            if any(ord(character) < 32 for character in name):
                raise ValueError("The MNIST framework has an unsupported file name.")
            count += 1
            if count > _MAX_MODULE_FILES:
                raise ValueError("The MNIST framework exceeds the supported file count.")
            relative = f"{prefix}/{name}" if prefix else name
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    if _signature(os.fstat(child)) != _signature(info):
                        raise ValueError("The MNIST framework changed during installation.")
                    visit(child, relative, depth + 1)
                finally:
                    os.close(child)
            else:
                content = _read(directory, name, _MAX_MODULE_FILE_BYTES)
                total += len(content)
                if total > _MAX_MODULE_BYTES:
                    raise ValueError("The MNIST framework exceeds the supported byte limit.")
                records.append(_record(relative, content))
                if output is not None:
                    _write(output, f"framework/tflite-micro/{relative}", content)
        if _signature(os.fstat(directory)) != _signature(before):
            raise ValueError("The MNIST framework changed during installation.")

    descriptor = open_directory(source, identity)
    try:
        visit(descriptor, "", 0)
    finally:
        os.close(descriptor)
    validate_identity(source, identity)
    return records


def _manifest(assets, framework, revision):
    manifest = {"schema_version": 1, "runtime": "mnist-v1", "module_revision": revision,
                "assets": [_record(path, assets[path]) for path in sorted(assets)], "framework": framework}
    return {**manifest, "fingerprint": _digest(manifest)}


def _ensure_store(path):
    if (not path.is_absolute() or ".." in path.parts or
            any(ord(character) < 32 or character == ";" for character in str(path))):
        raise ValueError("The managed MNIST store requires an absolute local directory.")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)


class MNISTAssets:
    """An explicit, immutable managed installation; ordinary reads never install."""

    def __init__(self, root):
        self.root = Path(root).expanduser()
        self.path = self.root / "mnist-v1"
        self.lock = RLock()

    def _verified(self, path, cancel=None):
        descriptor = open_directory(path)
        try:
            raw = _read(descriptor, "manifest.json", 4 * 1024 * 1024)
        finally:
            os.close(descriptor)
        try:
            saved = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise ValueError("The installed MNIST runtime manifest is invalid.") from exc
        from .mnist_sample import MODULE_REVISION

        assets = _assets(path)
        _validate_assets(assets)
        framework = path / "framework/tflite-micro"
        if (framework / ".git").exists() or (framework / ".git").is_symlink():
            raise ValueError("The managed MNIST framework must not contain a Git database.")
        descriptor = open_directory(framework)
        try:
            revision = _read(descriptor, "REVISION", 100).decode("ascii").strip()
        finally:
            os.close(descriptor)
        if revision != MODULE_REVISION:
            raise ValueError("The installed MNIST framework revision is unsupported.")
        observed = _manifest(assets, _tree(framework, cancel=cancel), revision)
        if saved != observed:
            raise ValueError("The installed MNIST runtime changed; its existing files were preserved.")
        return observed

    def status(self):
        """Report actual managed bytes without consulting an application folder."""
        try:
            manifest = self._verified(self.path)
            return {"schema_version": 1, "status": "ready", "path": str(self.path), "reason": "",
                    "fingerprint": manifest["fingerprint"]}
        except FileNotFoundError:
            exists = self.path.exists() or self.path.is_symlink()
            return {"schema_version": 1, "status": "invalid" if exists else "missing", "path": str(self.path),
                    "reason": "The installed MNIST runtime is incomplete." if exists else "Install the MNIST runtime asset pack first."}
        except (OSError, ValueError, UnicodeError) as exc:
            return {"schema_version": 1, "status": "invalid", "path": str(self.path), "reason": str(exc)}

    def install(self, asset_source, module_source, emit=None, cancel=None):
        """Copy selected local inputs once; never download or replace an installation."""
        emit = emit or (lambda message: None)
        source, module_source = Path(asset_source).expanduser(), Path(module_source).expanduser()
        from .mnist_sample import MODULE_REVISION, _module

        with self.lock:
            check_cancel(cancel)
            assets = _assets(source)
            _validate_assets(assets)
            module = _module(module_source, cancel)
            framework = _tree(module_source, cancel=cancel)
            expected = _manifest(assets, framework, MODULE_REVISION)
            if self.path.exists() or self.path.is_symlink():
                if self._verified(self.path, cancel) != expected:
                    raise ValueError("A different MNIST runtime is already installed; existing files were preserved.")
                emit("The same independent MNIST runtime is already installed.")
                return self.status()
            _ensure_store(self.root)
            identity = path_identity(self.root)
            parent = open_directory(self.root, identity)
            stage_name = f".mnist-install-{uuid.uuid4().hex}"
            stage = self.root / stage_name
            directory, owned, published = None, None, False
            try:
                os.mkdir(stage_name, 0o700, dir_fd=parent)
                owned = os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
                directory = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                if (os.fstat(directory).st_dev, os.fstat(directory).st_ino) != (owned.st_dev, owned.st_ino):
                    raise ValueError("The MNIST staging directory changed during installation.")
                emit("Copying MNIST model and reference assets into managed storage…")
                for relative, content in assets.items():
                    check_cancel(cancel)
                    _write(directory, relative, content)
                emit("Copying the pinned TensorFlow Lite Micro framework…")
                if _tree(module_source, cancel=cancel, output=directory) != framework:
                    raise ValueError("The MNIST framework changed during installation.")
                _write(directory, "framework/tflite-micro/REVISION", (MODULE_REVISION + "\n").encode())
                _write(directory, "manifest.json", (json.dumps(expected, sort_keys=True, indent=2) + "\n").encode())
                if _assets(source) != assets or _module(module_source, cancel) != module:
                    raise ValueError("MNIST installation inputs changed; no runtime was installed.")
                if self._verified(stage, cancel) != expected:
                    raise ValueError("The staged MNIST runtime changed during installation.")
                check_cancel(cancel)
                validate_identity(self.root, identity)
                _rename_noreplace(parent, stage_name, self.path.name)
                published = True
                emit("Independent MNIST runtime installed.")
                return self.status()
            finally:
                if directory is not None:
                    os.close(directory)
                os.close(parent)
                if not published and owned is not None:
                    try:
                        observed = stage.lstat()
                        if (observed.st_dev, observed.st_ino) == (owned.st_dev, owned.st_ino):
                            shutil.rmtree(stage)
                    except FileNotFoundError:
                        pass
