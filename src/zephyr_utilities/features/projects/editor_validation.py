"""Selected-environment Configure adapter for an exact saved editor candidate."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from threading import RLock
import time

from ...core.source_files import WorkspaceSourceReader
from .application_inputs import source_inputs
from .builds import _artifact
from .creation import path_identity, validate_identity
from .fragments import fragment_path, _read_fragment
from .probing import check_cancel
from .records import identifier
from .sample_check_identity import sample_input_guard


LIMITATIONS = [
    "Unsaved candidate staging is unsupported. Save the exact overlay, then review Configure. "
    "Configuration uses the original source layout, preserving removals and relative includes.",
    "Configure executes selected application and toolchain code in a fresh managed output directory. "
    "It does not compile or flash firmware.",
    "Input coverage is partial: selected source trees, application files and tools are checked, "
    "but arbitrary dynamic CMake inputs and the full tool runtime are not captured. "
    "A successful Configure is not a complete freshness or hardware validation claim.",
]
ADAPTER_FILES = ("cmake/modules/dts.cmake", "scripts/dts/gen_defines.py",
                 "scripts/dts/python-devicetree/src/devicetree/dtlib.py")
EVIDENCE_FILES = {"cmake-cache": "CMakeCache.txt", "build-info": "build_info.yml",
                  "resolved-dts": "zephyr/zephyr.dts", "preprocessed-dts": "zephyr/zephyr.dts.pre",
                  "devicetree-dependencies": "zephyr/zephyr.dts.d"}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _tool_observation(profile):
    records = []
    tools = {item["name"]: item for item in profile.get("tools", []) if isinstance(item, dict) and "name" in item}
    names = ["cmake", "ninja", "Python"]
    if tools.get("dtc", {}).get("status") == "discovered":
        names.append("dtc")
    for name in names:
        selected = tools.get(name, {})
        if selected.get("status") != "discovered":
            raise ValueError(f"Configuration validation needs the selected {name}. Analyze the environment again.")
        if not isinstance(selected.get("path"), str):
            raise ValueError(f"The selected {name} has no supported absolute executable path.")
        path = Path(selected["path"])
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(f"The selected {name} has no supported absolute executable path.")
        resolved = path.resolve(strict=True)
        descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or not before.st_mode & 0o111 or before.st_size > 64 * 1024 * 1024:
                raise ValueError(f"The selected {name} is unavailable or exceeds the 64 MiB executable limit.")
            digest, size = hashlib.sha256(), 0
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > 64 * 1024 * 1024:
                        raise ValueError(f"The selected {name} grew beyond the executable limit.")
                    digest.update(chunk)
            if (_identity(before) != _identity(os.fstat(descriptor)) or
                    _identity(before) != _identity(resolved.stat()) or path.resolve(strict=True) != resolved):
                raise ValueError(f"The selected {name} changed during inspection.")
            records.append({"name": name, "path": str(path), "resolved_path": str(resolved),
                            "identity": _identity(before), "sha256": digest.hexdigest()})
        finally:
            os.close(descriptor)
    return records


def _candidate(workspace, source, candidate):
    if not isinstance(candidate, dict) or set(candidate) != {"path", "text", "sha256"}:
        raise ValueError("Validation requires one exact candidate path, text and hash.")
    text = candidate["text"]
    if not isinstance(text, str) or "\x00" in text or len(text.encode("utf-8")) > 256 * 1024:
        raise ValueError("The overlay candidate must be bounded UTF-8 source.")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != candidate["sha256"]:
        raise ValueError("The candidate text does not match its hash.")
    reader = WorkspaceSourceReader(workspace)
    path = reader.path(candidate["path"])
    if not path.is_relative_to(source):
        raise ValueError("Validate an application overlay, not a generated output or external file.")
    fragment_path(path.relative_to(source).as_posix(), "overlays")
    before = _identity(path.lstat())
    parents = path_identity(path.parent)
    document = _read_fragment(reader, path)
    if before != _identity(path.lstat()) or parents != path_identity(path.parent):
        raise ValueError("The saved overlay changed during validation inspection.")
    if document["text"] != text or document["sha256"] != candidate["sha256"]:
        raise ValueError("Unsaved candidate validation is unsupported. Save the exact overlay before Configure.")
    return {"path": str(path), "sha256": candidate["sha256"], "identity": before, "ancestors": parents}


def _diagnostics(logs, candidate, source, messages=()):
    """Link only this captured overlay; imported paths remain plain diagnostic text."""
    result, seen = [], set()
    for item in list(messages) + list(logs):
        message = item.get("message", "") if isinstance(item, dict) else str(item)
        message = message[:2000]
        if not message or message in seen:
            continue
        match = re.search(r"(?P<path>(?:/|[A-Za-z0-9_.])[\w /.+@-]*\.overlay):(?P<line>\d+)"
                          r"(?::(?P<column>\d+)|\s+\(column (?P<dtcolumn>\d+)\))?", message)
        if match is None and item not in messages and not re.search(r"\b(error|warning|failed)\b", message, re.I):
            continue
        severity = item.get("severity") if isinstance(item, dict) else None
        diagnostic = {"message": message, "severity": severity if severity in {"warning", "error", "info"} else
                      "warning" if "warning" in message.lower() else "error"}
        if match:
            raw = match.group("path").strip()
            # A tool prefix can precede the absolute path; accept only the exact
            # candidate spelling or its exact application-relative spelling.
            allowed = (candidate["path"], str(Path(candidate["path"]).relative_to(source)))
            matched = next((path for path in allowed if raw == path or raw.endswith(" " + path)), None)
            line = int(match.group("line"))
            column = int(match.group("column") or match.group("dtcolumn") or 1)
            if matched and 0 < line <= candidate["text"].count("\n") + 1 and 0 < column <= 256 * 1024:
                diagnostic.update(path=candidate["path"], line=line, column=column, sha256=candidate["sha256"])
        result.append(diagnostic)
        seen.add(message)
        if len(result) >= 100:
            break
    return result


def _outputs_current(attempt):
    """Recheck captured evidence without accepting generated files as edit inputs."""
    output = Path(attempt["build_dir"])
    artifacts = [item for item in attempt.get("artifacts", []) if item.get("kind") in EVIDENCE_FILES]
    if not artifacts or not attempt.get("output_identity"):
        return "unknown"
    try:
        validate_identity(output, attempt["output_identity"])
        for saved in artifacts:
            path = output / EVIDENCE_FILES[saved["kind"]]
            if saved.get("path") != str(path):
                return "stale"
            observed, _ = _artifact(path, output, saved["kind"])
            if observed["sha256"] != saved.get("sha256") or observed["size"] != saved.get("size"):
                return "stale"
    except (OSError, ValueError):
        return "stale"
    return "checked" if len({item["kind"] for item in artifacts}) == len(EVIDENCE_FILES) else "unknown"


def _effective_context(profile, source, captured):
    keys = ("source_dir", "application_source_dir", "requested_target", "effective_target", "board", "revision",
            "qualifiers", "zephyr_base", "sdk_path", "toolchain")
    evidence = {key: captured.get(key) for key in keys}
    base, *qualifiers = profile["target"].split("/")
    board, _, revision = base.partition("@")
    expected = {"source_dir": source, "application_source_dir": source, "requested_target": profile["target"],
                "board": board, "zephyr_base": profile["zephyr"]["path"], "sdk_path": profile["sdk"]["path"],
                "toolchain": "zephyr"}
    if any(evidence.get(key) is not None and evidence[key] != value for key, value in expected.items()):
        return False, evidence
    if evidence["qualifiers"] is not None and evidence["qualifiers"].lstrip("/") != "/".join(qualifiers):
        return False, evidence
    if revision and evidence["revision"] is not None and evidence["revision"] != revision:
        return False, evidence
    if any(evidence.get(key) is None for key in expected) or (qualifiers and evidence["qualifiers"] is None) or (revision and evidence["revision"] is None):
        return None, evidence
    return True, evidence


class ProjectsValidationProvider:
    """Bounded session plans with durable configuration evidence in Projects attempts."""

    def __init__(self, context, creator):
        self.context, self.creator = context, creator
        self.plans = {}
        self.lock = RLock()

    def _get(self, plan_id):
        identifier(plan_id)
        record = self.plans.get(plan_id)
        if record is None or not record.get("job_id") and record["expires"] <= time.monotonic():
            raise ValueError("This validation review expired. Review Configure again.")
        return record

    def _profile(self, selection):
        workspace, project_id = selection["workspace"], selection["project"]["id"]
        project = self.creator.store.project(project_id, workspace)
        configuration_id = selection["configuration"]["id"]
        selected = project if configuration_id is None else self.creator.store.configuration(configuration_id, project_id, workspace)
        if (project.get("revision", 0) != selection["project"]["revision"] or
                selected.get("profile_revision" if configuration_id is None else "revision", 0) != selection["configuration"]["revision"] or
                _fingerprint(selected["profile"]) != selection["configuration"]["profile_fingerprint"]):
            raise ValueError("The selected project or configuration changed. Review Configure again.")
        validate_identity(project["path"], project["directory_identity"])
        profile = selected["profile"]
        for kind in ("zephyr", "sdk"):
            entry = profile.get(kind)
            path = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(path, str) or not Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError(f"The selected {kind} has no supported absolute directory path.")
        if not isinstance(profile.get("tools"), list):
            raise ValueError("The selected environment has no supported tool catalogue.")
        return profile

    def _inputs(self, selection, candidate, *, cancel=None):
        profile = self._profile(selection)
        source = Path(selection["application"]["path"])
        observed = _candidate(selection["workspace"], source, candidate)
        check_cancel(cancel)
        result = {"schema_version": 1, "application": source_inputs(source, cancel=cancel),
                  "source_trees": sample_input_guard(profile, cancel=cancel), "tools": _tool_observation(profile),
                  "candidate": observed, "profile": _fingerprint(profile),
                  "environment_directories": {kind: path_identity(profile[kind]["path"]) for kind in ("zephyr", "sdk")},
                  "application_identity": path_identity(source), "workspace_identity": path_identity(selection["workspace"])}
        if _candidate(selection["workspace"], source, candidate) != observed:
            raise ValueError("The saved overlay changed while inspecting validation inputs.")
        check_cancel(cancel)
        return result

    def review(self, selection, candidate):
        with self.lock, self.creator.lock:
            self.context.project_context.revalidate(selection)
            profile = self._profile(selection)
            zephyr = Path(profile["zephyr"]["path"])
            for relative in ADAPTER_FILES:
                path = zephyr / relative
                if not path.is_file():
                    raise ValueError(f"The selected Zephyr checkout lacks the supported Configure adapter input: {relative}")
            inputs = self._inputs(selection, candidate)
            response = self.creator.build_preview({"workspace": selection["workspace"], "project_id": selection["project"]["id"],
                "configuration_id": selection["configuration"]["id"], "operation": "configure"})
            build = response["plan"]
            if build["sources"] != inputs["application"]:
                raise ValueError("Application inputs changed while reviewing validation. Review again.")
            plan = {"schema_version": 1, "id": build["id"], "mode": "saved-source", "workspace": selection["workspace"],
                    "application": deepcopy(selection["application"]), "project": deepcopy(selection["project"]),
                    "configuration": deepcopy(selection["configuration"]), "target": deepcopy(selection["target"]),
                    "environment": deepcopy(selection["environment"]), "candidate": deepcopy(candidate),
                    "selection_fingerprint": _fingerprint({key: selection[key] for key in
                        ("workspace", "application", "project", "configuration", "target", "fragments", "roots")}),
                    "input_fingerprint": _fingerprint(inputs), "recipe": deepcopy(build["recipe"]),
                    "build_dir": build["build_dir"], "overlay_usage": deepcopy(build["overlay_usage"]),
                    "coverage": "partial", "limitations": list(LIMITATIONS),
                    "capabilities": {"adapter": "zephyr-cmake-saved-source-v1", "configure": True, "staging": False,
                                     "compile": False, "diagnostic_links": "selected-overlay"}}
            if len(self.plans) >= 32:
                available = [key for key, value in self.plans.items() if not value.get("job_id") or
                             self._job(value).get("status") not in {"queued", "running"}]
                if not available:
                    raise ValueError("All retained validation plans are active. Wait for a configuration to finish.")
                del self.plans[available[0]]
            self.plans[plan["id"]] = {"plan": plan, "selection": deepcopy(selection), "inputs": inputs,
                                     "expires": time.monotonic() + 1800}
            return {"plan": deepcopy(plan)}

    def _job(self, record):
        if not record.get("job_id"):
            return None
        try:
            return self.context.jobs.get(record["job_id"])
        except FileNotFoundError:
            # Shared job retention can expire before durable attempt evidence.
            return {"id": record["job_id"], "status": "unavailable", "logs": []}

    def _evidence(self, record, phase, attempt, cancel):
        plan = record["plan"]
        try:
            current = self._inputs(record["selection"], plan["candidate"], cancel=cancel)
            changed = current != record["inputs"]
        except (OSError, ValueError) as exc:
            if phase == "before":
                raise
            changed, current = True, None
            reason = str(exc)
        else:
            reason = "Captured inputs changed." if changed else LIMITATIONS[2]
        if phase == "before" and changed:
            raise ValueError("Validation inputs changed after review. Review Configure again.")
        tree = attempt.get("context", {}).get("devicetree", {})
        used = plan["candidate"]["path"] in tree.get("dependencies", []) and tree.get("dependency_coverage") == "complete"
        matched, effective = _effective_context(attempt["profile"], plan["application"]["path"], attempt.get("context", {}))
        return {"schema_version": 1, "plan_id": plan["id"], "mode": "saved-source", "candidate": deepcopy(plan["candidate"]),
                "input_fingerprint": plan["input_fingerprint"], "observed_fingerprint": _fingerprint(current) if current else None,
                "selection_fingerprint": plan["selection_fingerprint"], "coverage": "partial", "freshness": "stale" if changed else "unknown",
                "overlay_used": used, "context_matches": matched, "effective_context": effective,
                "message": reason, "adapter": plan["capabilities"]["adapter"]}

    def submit(self, plan_id):
        with self.lock, self.creator.lock:
            record = self._get(plan_id)
            plan = record["plan"]
            if plan["workspace"] != self.context.workspace.get()["path"]:
                raise ValueError("The selected workspace changed. Review Configure in the current workspace.")
            if record.get("job_id"):
                return {"plan_id": plan_id, "job": self._job(record)}
            self.context.project_context.revalidate(record["selection"])
            if self._inputs(record["selection"], plan["candidate"]) != record["inputs"]:
                raise ValueError("Validation inputs changed after review. Review Configure again.")
            job = self.creator.build({"workspace": plan["workspace"], "build_plan_id": plan_id},
                validation_guard=lambda phase, attempt, cancel: self._evidence(record, phase, attempt, cancel))
            record["job_id"] = job["id"]
            return {"plan_id": plan_id, "job": job}

    def result(self, plan_id):
        with self.lock:
            record = self._get(plan_id)
            plan, job = record["plan"], self._job(record)
            response = {"plan_id": plan_id, "job": job, "status": job["status"] if job else "ready",
                        "candidate": deepcopy(plan["candidate"]), "input_fingerprint": plan["input_fingerprint"],
                        "freshness": "unknown", "coverage": "partial", "stale": False, "overlay_used": False,
                        "configured": False, "diagnostics": [], "artifacts": [], "message": "Configuration has not run."}
            if job is None or job["status"] in {"queued", "running"}:
                if job:
                    response["message"] = "Configuration is " + job["status"] + "."
                return response
            try:
                attempt = self.creator.store.attempt(plan_id, plan["project"]["id"], plan["workspace"])
            except (OSError, ValueError):
                response.update(message="Configuration was cancelled before an attempt was recorded." if job["status"] == "cancelled" else
                                job.get("error", "The captured configuration attempt is unavailable."), handoff=self.handoff(plan_id))
                return response
            evidence = attempt.get("editor_validation", {})
            try:
                changed = self._inputs(record["selection"], plan["candidate"]) != record["inputs"]
            except (OSError, ValueError):
                changed = True
            output_freshness = _outputs_current(attempt)
            stale = changed or output_freshness == "stale" or evidence.get("freshness") == "stale" or attempt.get("context", {}).get("application_freshness") == "stale"
            configured = attempt.get("context", {}).get("configured") is True
            used = evidence.get("overlay_used") is True
            matched = evidence.get("context_matches")
            status = "stale" if stale else "cancelled" if attempt["status"] == "cancelled" else "failed" if attempt["status"] == "failed" else "inconclusive"
            message = ("Inputs or generated evidence changed; these results do not validate the current candidate." if stale else
                       "Configuration was cancelled." if status == "cancelled" else
                       attempt.get("error", "Configuration failed. Inspect the diagnostics and log.") if status == "failed" else
                       "Configure completed with the saved candidate and selected target. Overall input freshness remains unknown because coverage is partial." if configured and used and matched is True else
                       "Configure completed, but captured metadata does not prove the selected target/environment and overlay use.")
            messages = list(attempt.get("diagnostics", []))
            if attempt.get("error"):
                messages.append(attempt["error"])
            if configured and matched is not True:
                messages.append({"message": "Effective configuration differs from the reviewed selection." if matched is False else
                                 "Effective target or environment metadata is incomplete; validation is inconclusive.", "severity": "warning"})
            response.update(status=status, stale=stale, freshness="stale" if stale else "unknown", configured=configured,
                            context_matches=matched, effective_context=deepcopy(evidence.get("effective_context", {})),
                            overlay_used=used, output_freshness=output_freshness, message=message, attempt_id=plan_id, artifacts=deepcopy(attempt.get("artifacts", [])),
                            diagnostics=_diagnostics(attempt.get("logs", []), plan["candidate"], Path(plan["application"]["path"]), messages),
                            handoff=self.handoff(plan_id))
            return response

    def cancel(self, plan_id):
        with self.lock:
            record = self._get(plan_id)
            if not record.get("job_id"):
                raise ValueError("This configuration has not been submitted.")
            return {"plan_id": plan_id, "job": self.context.jobs.cancel(record["job_id"])}

    def handoff(self, plan_id):
        with self.lock:
            record = self._get(plan_id)
            plan = record["plan"]
            configuration = plan["configuration"]["id"] or "original"
            return {"feature": "projects", "view": f"applications/{plan['project']['id']}/{configuration}/{plan_id}",
                    "workspace": plan["workspace"], "project_id": plan["project"]["id"],
                    "configuration_id": plan["configuration"]["id"], "attempt_id": plan_id, "job_id": record.get("job_id")}
