"""Bounded application source identities shared by creator workflows."""

import hashlib
import os
from pathlib import Path

from .analyzer import digest
from .creation import path_identity
from .metadata import read_regular
from .probing import check_cancel


def source_inputs(source, *, cancel=None):
    """Hash application inputs; build outputs and Git bookkeeping are excluded."""
    source = Path(source)
    path_identity(source)
    records, size = [], 0
    for count, (current, directories, files) in enumerate(os.walk(source, followlinks=False)):
        check_cancel(cancel)
        if count >= 5000:
            raise ValueError("Application inputs exceed the supported directory count.")
        base = Path(current)
        directories[:] = sorted(name for name in directories if base != source or name not in {".git", "build"})
        if len(base.relative_to(source).parts) > 16:
            raise ValueError("Application inputs exceed the supported directory depth.")
        for name in directories + files:
            if (base / name).is_symlink():
                raise ValueError("Application input symlinks are not supported in this first creator release.")
        for name in sorted(files):
            path = base / name
            data = read_regular(path, 4 * 1024 * 1024)
            size += len(data)
            if len(records) >= 2000 or size > 32 * 1024 * 1024:
                raise ValueError("Application inputs exceed the supported file/size limits.")
            records.append({"path": str(path.relative_to(source)), "sha256": hashlib.sha256(data).hexdigest(),
                            "size": len(data)})
    return {"fingerprint": digest(records), "files": records, "coverage": "application-files-excluding-build-and-git"}
