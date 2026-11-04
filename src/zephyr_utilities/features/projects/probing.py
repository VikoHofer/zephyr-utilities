"""Bounded, cancellable queries to explicitly selected local tools."""

from concurrent.futures import CancelledError
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time


class ProbeError(ValueError):
    """A local inspection command failed without changing the workspace."""


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise CancelledError()


def run_probe(argv, *, cwd, environ, cancel=None, timeout=12, max_output=2 * 1024 * 1024):
    """Run a read-only argument array; bound stdout, time and descendant lifetime."""
    check_cancel(cancel)
    environment = dict(environ)
    environment.update(PYTHONDONTWRITEBYTECODE="1", GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    try:
        process = subprocess.Popen(list(map(str, argv)), cwd=str(Path(cwd)), env=environment,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=(os.name == "posix"))
    except OSError as exc:
        raise ProbeError(f"Cannot run {Path(argv[0]).name}: {exc.strerror or exc}") from exc
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                check_cancel(cancel)
                if time.monotonic() > deadline:
                    raise ProbeError(f"{Path(argv[0]).name} exceeded the {timeout}-second inspection limit.")
                for key, _ in selector.select(0.05):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > max_output:
                            raise ProbeError(f"{Path(argv[0]).name} exceeded the inspection output limit.")
        while process.poll() is None:
            check_cancel(cancel)
            if time.monotonic() > deadline:
                raise ProbeError(f"{Path(argv[0]).name} exceeded the {timeout}-second inspection limit.")
            try:
                process.wait(timeout=0.05)
            except subprocess.TimeoutExpired:
                pass
        text = output.decode("utf-8", errors="replace").strip()
        if process.returncode:
            raise ProbeError(f"{Path(argv[0]).name}: {text[-1800:] or 'inspection failed'}")
        return text
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"{Path(argv[0]).name} did not finish inspection.") from exc
    finally:
        # A failed/cancelled probe must not leave children or a pipe reader behind.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
