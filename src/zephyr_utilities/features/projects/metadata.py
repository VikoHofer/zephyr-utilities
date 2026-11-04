"""Bounded regular-file reads for local metadata and stored observations."""

import os
from pathlib import Path
import stat


def read_regular(path, limit):
    path = Path(path)
    # Nonblocking open avoids hanging on a FIFO substituted between discovery and reading.
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Metadata must be a regular file: {path}")
        if info.st_size > limit:
            raise ValueError(f"Metadata exceeds the {limit}-byte read limit: {path}")
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError(f"Metadata exceeds the {limit}-byte read limit: {path}")
        return content
