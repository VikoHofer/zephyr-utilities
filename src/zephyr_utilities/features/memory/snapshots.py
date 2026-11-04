"""Durable report captures with immutable evidence and editable annotations."""

from __future__ import annotations

from concurrent.futures import CancelledError
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import uuid

from .discovery import inspect_build, read_config
from .model import normalize_report
from .runner import file_hash, generate_reports


def _write_json(path: Path, value):
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _annotation(value, fallback="", maximum=160):
    if value is None:
        return fallback
    if not isinstance(value, str):
        raise ValueError("Snapshot names and notes must be text.")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"Snapshot text must be at most {maximum} characters.")
    return value or fallback


class SnapshotStore:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.objects = self.root / "objects"
        self.objects.mkdir(exist_ok=True, mode=0o700)
        self._lock = threading.RLock()
        self._active_builds = set()

    def _directory(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid snapshot identifier.")
        return self.root / identifier

    def _new(self, name, notes):
        now = datetime.now(timezone.utc)
        return {
            "schema_version": 1, "id": uuid.uuid4().hex,
            "name": _annotation(name, f"Memory snapshot {now:%Y-%m-%d %H:%M UTC}"),
            "notes": _annotation(notes, maximum=8000), "created_at": now.isoformat(),
            "build": None, "reports": {"ram": None, "rom": None}, "config": {},
            "metadata": {}, "warnings": [],
        }

    @staticmethod
    def _summary(snapshot):
        return {key: snapshot.get(key) for key in ("schema_version", "id", "name", "notes", "created_at", "build", "warnings")} | {
            "totals": {memory: report["total_size"] if report else None for memory, report in snapshot["reports"].items()}}

    def _publish(self, snapshot, staging):
        _write_json(staging / "snapshot.json", snapshot)
        _write_json(staging / "summary.json", self._summary(snapshot))
        os.replace(staging, self._directory(snapshot["id"]))
        return snapshot

    def _archive(self, path: Path, staging: Path) -> dict:
        captured = staging / ("capture-" + uuid.uuid4().hex)
        shutil.copyfile(path, captured)
        digest = file_hash(captured)
        destination = self.objects / digest
        try:
            os.link(captured, destination)
        except FileExistsError:
            pass
        size = captured.stat().st_size
        captured.unlink()
        return {"path": str(path), "sha256": digest, "size": size, "object": f"objects/{digest}"}

    def capture(self, build_path, name="", notes="", emit=None, cancel=None):
        build = inspect_build(build_path)
        if not build["ready"]:
            raise ValueError(build["error"])
        snapshot = self._new(name, notes)
        snapshot["build"] = build
        with self._lock:
            if build["path"] in self._active_builds:
                raise ValueError("A report job is already running for this build.")
            self._active_builds.add(build["path"])
        staging = None
        try:
            staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=self.root))
            if cancel is not None and cancel.is_set():
                raise CancelledError("Report generation was cancelled.")
            directory = Path(build["path"])
            elf = Path(build["elf_path"])
            sources = {
                "elf": elf,
                "config": directory / "zephyr" / ".config",
                "map": elf.with_suffix(".map"),
                "devicetree": directory / "zephyr" / "zephyr.dts",
                "build_info": directory / "build_info.yml",
                "cmake_cache": directory / "CMakeCache.txt",
            }
            sources = {key: path for key, path in sources.items() if path.is_file()}
            before = {key: file_hash(path) for key, path in sources.items()}
            if emit:
                emit("Capturing ELF and build metadata for a durable snapshot.")
            artifacts = {key: self._archive(path, staging) for key, path in sources.items()}
            if any(artifacts[key]["sha256"] != before[key] for key in sources):
                raise ValueError("Build artifacts changed while being captured. Retry once the build is idle.")
            result = generate_reports(build, staging / "raw", emit=emit, cancel=cancel)
            if any(not path.is_file() or file_hash(path) != before[key] for key, path in sources.items()):
                raise ValueError("Build artifacts changed during report generation. No snapshot was saved; retry once the build is idle.")
            prefixes = {build[key]: label for key, label in (("path", "OUTPUT_DIR"), ("zephyr_base", "ZEPHYR_BASE"), ("workspace", "WORKSPACE"), ("application", "APPLICATION")) if build.get(key)}
            snapshot["reports"] = {memory: normalize_report(raw, prefixes) for memory, raw in result["reports"].items()}
            if "config" in artifacts:
                snapshot["config"] = read_config(self.root / artifacts["config"]["object"])
            snapshot["metadata"] = {"source": "existing-build", "artifacts": artifacts,
                                    "artifact_hashes": before, "generator": result["generator"],
                                    "commands": result["commands"],
                                    "provenance": "Build metadata and artifacts captured at analysis time. Current checkout state does not prove the source revision of this ELF."}
            for memory, report in snapshot["reports"].items():
                snapshot["warnings"].extend(f"{memory.upper()}: {warning}" for warning in report.get("warnings", []))
            snapshot["warnings"].extend(result.get("warnings", []))
            if "fallback" in build.get("python_source", ""):
                snapshot["warnings"].append("The original build interpreter was not recorded; report generation used " + build["python_source"] + ".")
            shutil.copyfile(result["log_path"], staging / "generation.log")
            if cancel is not None and cancel.is_set():
                raise CancelledError("Report generation was cancelled.")
            return self._publish(snapshot, staging)
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
            with self._lock:
                self._active_builds.discard(build["path"])

    def import_reports(self, ram, rom, name="", notes=""):
        if ram is None and rom is None:
            raise ValueError("Select at least one RAM or ROM report to import.")
        snapshot = self._new(name, notes)
        snapshot["metadata"] = {"source": "import", "provenance": "Imported reports; original ELF and build identity are not verified."}
        snapshot["warnings"] = ["Imported reports have no verified build identity or physical memory capacities."]
        staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=self.root))
        try:
            raw_dir = staging / "raw"
            raw_dir.mkdir()
            for memory, raw in (("ram", ram), ("rom", rom)):
                if raw is not None:
                    snapshot["reports"][memory] = normalize_report(raw)
                    snapshot["warnings"].extend(f"{memory.upper()}: {warning}" for warning in snapshot["reports"][memory].get("warnings", []))
                    _write_json(raw_dir / f"{memory}.json", raw)
            return self._publish(snapshot, staging)
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def _read(self, identifier, filename):
        directory = self._directory(identifier)
        path = directory / filename
        if not path.is_file():
            raise FileNotFoundError("Snapshot not found.")
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            labels = directory / "annotations.json"
            if labels.is_file():
                snapshot.update(json.loads(labels.read_text(encoding="utf-8")))
            return snapshot
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Snapshot {identifier} could not be read: {exc}") from exc

    def load(self, identifier):
        with self._lock:
            return self._read(identifier, "snapshot.json")

    def list(self):
        with self._lock:
            snapshots = []
            for directory in self.root.iterdir():
                if directory.is_dir() and re.fullmatch(r"[a-f0-9]{32}", directory.name):
                    try:
                        snapshots.append(self._read(directory.name, "summary.json"))
                    except (ValueError, FileNotFoundError):
                        # An individual damaged snapshot must not hide the rest of history.
                        continue
        return sorted(snapshots, key=lambda item: item["created_at"], reverse=True)

    def delete(self, identifier):
        """Remove one capture and its annotations without touching shared artifact objects.

        Objects can be referenced by other snapshots or an in-progress capture, so
        deletion intentionally does not perform garbage collection. Original build
        files are outside the snapshot directory and are never modified here.
        """
        with self._lock:
            directory = self._directory(identifier)
            if directory.is_symlink():
                raise ValueError("Cannot delete a snapshot directory that is a symbolic link.")
            if not directory.is_dir():
                raise FileNotFoundError("Snapshot not found.")
            # rmtree removes nested links themselves, not their external targets.
            shutil.rmtree(directory)
        return {"id": identifier, "deleted": True}

    def rename(self, identifier, name, notes=""):
        with self._lock:
            snapshot = self.load(identifier)
            annotations = {"name": _annotation(name, snapshot["name"]), "notes": _annotation(notes, maximum=8000)}
            _write_json(self._directory(identifier) / "annotations.json", annotations)
            snapshot.update(annotations)
            return snapshot
