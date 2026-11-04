"""Run Zephyr's existing-ELF footprint generator without invoking a build."""

from __future__ import annotations

from concurrent.futures import CancelledError
import hashlib
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stop(process):
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=3)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=3)


def _run(command: list[str], cwd: str, emit=None, cancel=None, timeout=300) -> str:
    if cancel is not None and cancel.is_set():
        raise CancelledError("Report generation was cancelled.")
    environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                   start_new_session=os.name == "posix")
    except OSError as exc:
        raise ValueError(f"Cannot start the build's report generator: {exc}") from exc
    messages = queue.Queue()

    def read_output():
        try:
            for line in process.stdout:
                messages.put(line.rstrip("\r\n")[:16000])
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    lines = []
    output_finished = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise CancelledError("Report generation was cancelled.")
            if time.monotonic() > deadline:
                raise ValueError(f"Report generation exceeded {timeout} seconds. The process was stopped.")
            try:
                line = messages.get(timeout=0.1)
            except queue.Empty:
                if output_finished and process.poll() is not None:
                    break
                continue
            if line is None:
                output_finished = True
                if process.poll() is not None:
                    break
                continue
            lines.append(line)
            if len(lines) > 1000:
                lines.pop(0)
            if emit:
                emit(line)
        process.wait(timeout=max(1, deadline - time.monotonic()))
        output = "\n".join(lines)
        if process.returncode:
            if "no DWARF" in output or "DWARF information" in output:
                raise ValueError("This ELF has no usable DWARF debug information. Select the unstripped build ELF; source-level reports cannot be generated from this artifact.")
            if "ModuleNotFoundError" in output or "ImportError" in output:
                raise ValueError("The build's Python environment is missing report dependencies. Install the Zephyr footprint requirements in that environment, then retry. " + "\n".join(lines[-3:]))
            raise ValueError(f"Zephyr report generation failed (exit {process.returncode}). " + "\n".join(lines[-8:]))
        return output
    finally:
        _stop(process)
        reader.join(timeout=1)
        process.stdout.close()


def generate_reports(build: dict, directory: Path, emit=None, cancel=None) -> dict:
    """Generate into a private directory; no files are written to the build."""
    script = Path(build["zephyr_base"]) / "scripts" / "footprint" / "size_report"
    executable = build["python"]
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    logs = []
    unclassified_symbols = set()
    other_warnings = set()
    log_path = directory / "generation.log"

    def record(message):
        if message.startswith("WARN: Symbol") and "is not in RAM or ROM" in message:
            unclassified_symbols.add(message)
        elif message.startswith(("WARN:", "WARNING:")):
            other_warnings.add(message)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(message + "\n")
        logs.append(message)
        if len(logs) > 1000:
            logs.pop(0)
        if emit:
            emit(message)

    record("Checking the selected build's report generator.")
    help_text = _run([executable, str(script), "--help"], build["path"], cancel=cancel, timeout=30)
    required = ("--kernel", "--zephyrbase", "--output", "--json")
    if not all(option in help_text for option in required):
        raise ValueError("This Zephyr report generator does not support the required JSON/existing-ELF interface. Import previously generated RAM/ROM JSON instead.")
    commands = []
    reports = {}
    generator_hash = file_hash(script)
    for memory in ("ram", "rom"):
        record(f"Generating {memory.upper()} report from the existing ELF.")
        command = [executable, str(script), "--kernel", build["elf_path"],
                   "--zephyrbase", build["zephyr_base"], "--output", build["path"],
                   "--json", str(directory / f"{memory}.json")]
        if "--workspace" in help_text and build.get("workspace"):
            command.extend(["--workspace", build["workspace"]])
        if "--quiet" in help_text:
            command.append("--quiet")
        command.append(memory)
        commands.append(command)
        _run(command, build["path"], emit=record, cancel=cancel)
        report_path = directory / f"{memory}.json"
        if not report_path.is_file():
            raise ValueError(f"The report generator did not produce {memory}.json.")
        try:
            reports[memory] = json.loads(report_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"The generated {memory.upper()} report is not valid JSON.") from exc
    if file_hash(script) != generator_hash:
        raise ValueError("The Zephyr report generator changed during analysis. Retry after checkout changes have finished.")
    record("Both reports generated successfully. The firmware was not rebuilt.")
    warnings = []
    if unclassified_symbols:
        warnings.append(
            f"Zephyr reported {len(unclassified_symbols)} symbols outside RAM/ROM classification. "
            "They are excluded from these report totals; see generation.log for details."
        )
    if other_warnings:
        warnings.append(f"Zephyr emitted {len(other_warnings)} additional report warnings; see generation.log for details.")
    return {"reports": reports, "logs": logs, "commands": commands,
            "warnings": warnings, "log_path": str(log_path),
            "generator": {"path": str(script), "sha256": generator_hash}}
