"""Reviewed fresh/incremental build execution and owned output cleanup."""

from concurrent.futures import CancelledError
from copy import deepcopy
from functools import wraps
import hashlib
import os
from pathlib import Path
import uuid

from .application_inputs import source_inputs
from .builds import command_recipe, capture_context
from .build_outputs import inventory_output, managed_path, remove_output
from .creation import open_directory, path_identity, validate_identity
from .default_overlay import project_default_overlay
from .fragments import profile_fragments, validate_fragment_files
from .metadata import read_regular
from .overlay_usage import overlay_usage
from .probing import check_cancel
from .records import identifier, now
from .sample_compatibility import inspect_sample_requirements


def _workflow_locked(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


def _owned_path(project, record):
    owner = identifier(record.get("output_owner_id") or record.get("id"))
    value = record.get("path") if "path" in record else record.get("build_dir")
    path = managed_path(value)
    if path != Path(project["path"]) / "build" / owner or record.get("source_dir", project["path"]) != project["path"]:
        raise ValueError("The recorded output does not belong to this application's managed build directory.")
    recipe = record.get("recipe")
    if "build_dir" in record and recipe is None:
        raise ValueError("The legacy attempt has no captured source/output recipe to establish ownership.")
    if recipe is not None:
        configure = recipe.get("configure", []) if isinstance(recipe, dict) else []
        if not isinstance(configure, list) or "-S" not in configure or configure.index("-S") + 1 >= len(configure) or configure[configure.index("-S") + 1] != project["path"]:
            raise ValueError("The captured output recipe does not identify this application's source directory.")
        if "-B" not in configure or configure.index("-B") + 1 >= len(configure) or configure[configure.index("-B") + 1] != str(path):
            raise ValueError("The captured output recipe does not identify this managed build directory.")
    return path


def _output_entries(project, store):
    validate_identity(project["path"], project["directory_identity"])
    attempts = project.get("attempts", [])
    records = {record["id"]: deepcopy(record) for record in store.outputs(project["id"], project["workspace"])}
    for attempt in reversed(attempts):
        owner = attempt.get("output_owner_id") or attempt["id"]
        if owner not in records:
            if attempt.get("output_owner_id") and not attempt.get("output_identity"):
                # A new attempt may fail before it reserves any output. It must
                # never adopt a directory that someone inserted at its planned path.
                continue
            try:
                path = _owned_path(project, attempt)
            except (TypeError, ValueError):
                continue
            records[owner] = {"schema_version": 1, "id": owner, "project_id": project["id"], "workspace": project["workspace"],
                "path": str(path), "source_dir": project["path"], "directory_identity": attempt.get("output_identity"),
                "state": "available", "latest_attempt_id": attempt["id"], "legacy": not bool(attempt.get("output_owner_id")),
                "_derived": True, "created_at": attempt.get("started_at")}
        elif records[owner].get("_derived"):
            records[owner]["latest_attempt_id"] = attempt["id"]
    result = []
    for record in records.values():
        related = [attempt for attempt in attempts if attempt.get("build_dir") == record.get("path")]
        latest = next((attempt for attempt in related if attempt["id"] == record.get("latest_attempt_id")), None)
        state, reason, clean = "unmanaged", "Output ownership could not be verified.", False
        try:
            path = _owned_path(project, record)
            identity = path_identity(path)
            if record.get("state") == "cleaned":
                reason = "A new directory appeared at a cleaned output path; it is not managed by this record."
            elif record.get("directory_identity") is not None and identity != record["directory_identity"]:
                reason = "The output directory was replaced or redirected."
            else:
                state, reason, clean = "available", "Managed build outputs are available.", True
                if record.get("cleanup_error"):
                    reason = "Cleanup stopped before completion; review the remaining output entries before trying again."
        except FileNotFoundError:
            state = "cleaned" if record.get("state") == "cleaned" else "missing"
            reason = "Build outputs were cleaned; captured attempt history is retained." if state == "cleaned" else "The build output directory is no longer available."
        except (OSError, TypeError, ValueError) as exc:
            reason = str(exc)
        reusable = bool(state == "available" and not record.get("cleanup_error") and record.get("directory_identity") and latest and latest.get("status") in {"configured", "built"}
                        and latest.get("context", {}).get("configured") is True and latest.get("output_identity") == record["directory_identity"])
        if reusable:
            configuration_id = latest.get("configuration", {}).get("id")
            configuration = next((item for item in project.get("configurations", []) if item["id"] == configuration_id), None) if configuration_id else project
            revision = configuration.get("revision" if configuration_id else "profile_revision", 0) if configuration else None
            reusable = bool(configuration and latest.get("profile") == configuration["profile"] and latest.get("configuration_revision", 0) == revision)
        result.append({"id": record["id"], "path": record.get("path"), "state": state, "reason": reason,
            "latest_attempt_id": record.get("latest_attempt_id"), "attempt_ids": [attempt["id"] for attempt in related],
            "reuse_allowed": reusable, "clean_allowed": clean, "legacy": bool(record.get("legacy")), "_record": record})
    return result


def annotate_outputs(project, store):
    """Derive live output labels without rewriting captured attempts or artifacts."""
    try:
        outputs = _output_entries(project, store)
        project["build_outputs"] = [{key: value for key, value in output.items() if key != "_record"} for output in outputs]
    except (OSError, ValueError) as exc:
        project["build_outputs"] = []
        project["build_output_diagnostics"] = [str(exc)]
        outputs = []
    for attempt in project.get("attempts", []):
        output = next((item for item in outputs if attempt["id"] in item["attempt_ids"]), None)
        state = output["state"] if output else "unmanaged"
        if output and state == "available" and output["latest_attempt_id"] != attempt["id"]:
            state = "superseded"
        attempt.update(output_state=state, output_reason=output["reason"] if output else "No managed output ownership is available.",
                       reuse_allowed=bool(output and output["reuse_allowed"] and state == "available"),
                       clean_allowed=bool(output and output["clean_allowed"]), output_id=output["id"] if output else None)
        if state == "superseded":
            attempt["output_reason"] = "A later attempt reused this directory; this attempt's captured evidence is historical."
    return project


class BuildWorkflow:
    def retain_attempt_output(self, project, attempt):
        """Keep a validated legacy output manageable when its history entry is removed."""
        owner = attempt.get("output_owner_id") or attempt["id"]
        if any(record["id"] == owner for record in self.store.outputs(project["id"], project["workspace"])):
            return
        if attempt.get("output_owner_id") and not attempt.get("output_identity"):
            return
        try:
            validate_identity(project["path"], project["directory_identity"])
            path = _owned_path(project, attempt)
            identity = path_identity(path)
            if attempt.get("output_identity") and identity != attempt["output_identity"]:
                return
        except (OSError, TypeError, ValueError):
            return
        self.store.save_output({"schema_version": 1, "id": owner, "project_id": project["id"],
            "workspace": project["workspace"], "path": str(path), "source_dir": project["path"],
            "directory_identity": identity, "state": "available", "latest_attempt_id": attempt["id"],
            "created_at": attempt.get("started_at") or now(), "updated_at": now(), "legacy": not bool(attempt.get("output_owner_id"))})

    def _output_project(self, project):
        projects, errors = self.store.list(project["workspace"])
        if errors:
            raise ValueError("Saved build records cannot be read completely. Resolve their diagnostics before managing outputs.")
        return next(item for item in projects if item["id"] == project["id"])

    def _reuse_guard(self, project, attempt, profile, revision):
        if attempt.get("profile") != profile or attempt.get("status") not in {"configured", "built"}:
            raise ValueError("Reuse requires a successful configured or built attempt with the same saved profile. Use a fresh build.")
        if attempt.get("configuration_revision", 0) != revision:
            raise ValueError("The saved configuration was edited after this output was configured. Use a fresh build for its new revision.")
        path = _owned_path(project, attempt)
        if not attempt.get("output_identity"):
            raise ValueError("This older attempt has no captured output directory identity. Use a fresh build first.")
        validate_identity(path, attempt["output_identity"])
        outputs = _output_entries(self._output_project(project), self.store)
        output = next((item for item in outputs if item["path"] == str(path)), None)
        if not output or not output["reuse_allowed"] or output["latest_attempt_id"] != attempt["id"]:
            raise ValueError("Only the latest successful attempt for an available managed output can be reused.")
        captured = attempt.get("context", {})
        if captured.get("configured") is not True or captured.get("source_dir") != project["path"] or captured.get("application_source_dir") != project["path"]:
            raise ValueError("The saved configuration does not prove this output belongs to the application. Use a fresh build.")
        requested = profile["target"].split("/")
        requested_board, _, requested_revision = requested[0].partition("@")
        if (captured.get("board") != requested_board or (captured.get("qualifiers") or "").lstrip("/") != "/".join(requested[1:]) or
                (requested_revision and captured.get("revision") != requested_revision)):
            raise ValueError("The effective configured board does not match the selected target. Use a fresh build.")
        context, artifacts, _ = capture_context(profile, project["path"], path, configured=True)
        hashes = {item["kind"]: item["sha256"] for item in artifacts if item["kind"] in {"cmake-cache", "build-info"}}
        saved = {item["kind"]: item["sha256"] for item in attempt.get("artifacts", []) if item.get("kind") in {"cmake-cache", "build-info"}}
        if set(hashes) != {"cmake-cache", "build-info"} or hashes != saved:
            raise ValueError("The configured CMake cache or build information changed. Review a fresh build instead of reusing stale outputs.")
        keys = ("application_source_dir", "effective_target", "cached_board", "zephyr_base", "sdk_path", "toolchain", "compiler", "cxx_compiler")
        if (any(context.get(key) != captured.get(key) for key in keys) or context.get("sdk_path") != profile["sdk"]["path"] or
                context.get("zephyr_base") != profile["zephyr"]["path"] or context.get("toolchain") != "zephyr"):
            raise ValueError("The configured source, board or toolchain no longer matches the captured build. Use a fresh build.")
        return {"output_identity": attempt["output_identity"], "hashes": hashes, "latest_attempt_id": attempt["id"]}

    @_workflow_locked
    def build_preview(self, body):
        workspace = self._workspace(body, {"project_id", "operation", "configuration_id", "reuse_attempt_id"})
        operation = body.get("operation")
        if operation not in {"configure", "build"}:
            raise ValueError("Choose configure or build.")
        project = self.store.project(body.get("project_id"), workspace)
        self._require_idle(project, workspace)
        configuration_id = body.get("configuration_id")
        if configuration_id is None or configuration_id == "":
            profile = deepcopy(project["profile"])
            configuration = {"id": None, "name": "Original"}
            revision = project.get("profile_revision", 0)
        else:
            saved = self.store.configuration(configuration_id, project["id"], workspace)
            profile = deepcopy(saved["profile"])
            configuration = {"id": saved["id"], "name": saved["name"]}
            revision = saved.get("revision", 0)
        source = Path(project["path"])
        validate_identity(source, project["directory_identity"])
        validate_fragment_files(source, profile_fragments(profile))
        sources = source_inputs(source)
        parent = source / "build"
        parent_identity = path_identity(parent) if parent.exists() or parent.is_symlink() else None
        plan_id = uuid.uuid4().hex
        output = parent / plan_id
        reuse_attempt_id = body.get("reuse_attempt_id")
        reuse_guard = None
        owner = plan_id
        if reuse_attempt_id is not None:
            previous = self.store.attempt(reuse_attempt_id, project["id"], workspace)
            reuse_guard = self._reuse_guard(project, previous, profile, revision)
            output = Path(previous["build_dir"])
            owner = previous.get("output_owner_id") or previous["id"]
        environment = dict(os.environ)
        recipe = command_recipe(profile, source, output, environ=environment)
        plan = {"schema_version": 1, "id": plan_id, "workspace": workspace, "project_id": project["id"],
                "operation": operation, "build_dir": str(output), "recipe": recipe, "sources": sources,
                "parent_identity": parent_identity, "created_at": now(), "profile": profile,
                "configuration": configuration, "reuse": reuse_guard is not None, "reuse_attempt_id": reuse_attempt_id,
                "reuse_guard": reuse_guard, "output_owner_id": owner, "configuration_revision": revision}
        selected_overlay = project_default_overlay(project, profile)
        if selected_overlay:
            plan["default_overlay"] = selected_overlay
        overlay_files = deepcopy(sources["files"])
        cmake = next((item for item in overlay_files if item["path"] == "CMakeLists.txt"), None)
        if cmake is not None:
            try:
                data = read_regular(source / "CMakeLists.txt", 1024 * 1024)
                if hashlib.sha256(data).hexdigest() == cmake["sha256"]:
                    cmake["content"] = data.decode("utf-8")
            except (OSError, ValueError):
                pass
        plan["overlay_usage"] = overlay_usage(profile, source, files=overlay_files,
                                               default_overlay=selected_overlay)
        plan["overlay_usage"]["evidence"] = {"kind": "build-preview", "configuration_name": configuration["name"],
            "note": "Expected from the current application files and selection. This Configure/Build will confirm the actual inputs."}
        if project.get("sample"):
            adaptation = project["sample"].get("adaptation")
            if adaptation and adaptation.get("target") != profile["target"]:
                raise ValueError("This sample's RGB LED adaptation belongs to another board target. Create a new sample for the selected target.")
            plan["sample_compatibility"] = {"status": "unknown",
                "message": "Sample hardware requirements will be checked during Configure/Build.",
                "details": ["The current application's resolved Device Tree is checked before compilation."]}
        return self._remember("build", plan, environment)

    def _prepare_output(self, project, plan, *, cancel=None):
        source = Path(project["path"])
        validate_identity(source, project["directory_identity"])
        if source_inputs(source, cancel=cancel) != plan["sources"]:
            raise ValueError("Application files changed since preview. Review the build command again.")
        parent = source / "build"
        if plan["parent_identity"] is not None:
            validate_identity(parent, plan["parent_identity"])
        else:
            # Do not accept a build parent inserted between preview and submission.
            fd = open_directory(source, project["directory_identity"])
            try:
                os.mkdir("build", mode=0o755, dir_fd=fd)
            except FileExistsError as exc:
                raise ValueError("The build directory appeared after preview. Review the command again.") from exc
            finally:
                os.close(fd)
            validate_identity(source, project["directory_identity"])
            path_identity(parent)
        if plan["reuse"]:
            attempt = self.store.attempt(plan["reuse_attempt_id"], project["id"], project["workspace"])
            if self._reuse_guard(project, attempt, plan["profile"], plan["configuration_revision"]) != plan["reuse_guard"]:
                raise ValueError("The reusable output changed after preview. Review the build again.")

    @_workflow_locked
    def build(self, body):
        workspace = self._workspace(body, {"build_plan_id"})
        saved = self._consume("build", body.get("build_plan_id"), workspace)
        plan, environment = saved["plan"], saved["environment"]
        project = self.store.project(plan["project_id"], workspace)
        self._require_idle(project, workspace)

        def run(emit, cancel):
            logs = []

            def report(message):
                logs.append(str(message)[:2000])
                del logs[:-500]
                emit(message)

            attempt = {"schema_version": 1, "id": plan["id"], "project_id": project["id"], "workspace": workspace,
                       "operation": plan["operation"], "status": "running", "started_at": now(),
                       "build_dir": plan["build_dir"], "recipe": plan["recipe"], "sources": plan["sources"],
                       "profile": deepcopy(plan["profile"]), "configuration": deepcopy(plan["configuration"]),
                       "context": {}, "artifacts": [], "diagnostics": [], "output_owner_id": plan["output_owner_id"],
                       "reuse_attempt_id": plan["reuse_attempt_id"], "reuse": plan["reuse"],
                       "configuration_revision": plan["configuration_revision"]}
            if plan.get("default_overlay"):
                attempt["default_overlay"] = deepcopy(plan["default_overlay"])
            self.store.save_attempt(attempt)
            try:
                self._recheck(plan["profile"], environment, report, cancel)
                self._prepare_output(project, plan, cancel=cancel)
                check_cancel(cancel)
                def output_ready(identity):
                    attempt["output_identity"] = deepcopy(identity)
                    self.store.save_attempt(attempt)
                    old = next((record for record in self.store.outputs(project["id"], workspace)
                                if record["id"] == plan["output_owner_id"]), {})
                    self.store.save_output({"schema_version": 1, "id": plan["output_owner_id"], "project_id": project["id"],
                        "workspace": workspace, "path": plan["build_dir"], "source_dir": project["path"],
                        "directory_identity": deepcopy(identity), "state": "available", "latest_attempt_id": plan["id"],
                        "created_at": old.get("created_at") or now(), "updated_at": now()})

                options = {"on_output_ready": output_ready}
                if plan["reuse"]:
                    options["reuse_identity"] = plan["reuse_guard"]["output_identity"]
                if project.get("sample"):
                    def inspect_sample(build_dir):
                        source = Path(project["path"]) / "src/main.c"
                        try:
                            data = read_regular(source, 1024 * 1024)
                        except (OSError, ValueError):
                            data = None
                        expected = next((item["sha256"] for item in project["files"] if item["path"] == "src/main.c"), None)
                        if data is None or hashlib.sha256(data).hexdigest() != expected:
                            return {"status": "unknown", "message": "The sample source has changed; its original hardware requirements cannot be applied automatically.",
                                    "details": ["Review the current application source and configuration before using it on hardware."]}
                        files = [{"path": "src/main.c", "content": data.decode("utf-8", errors="replace")}]
                        return inspect_sample_requirements(plan["profile"], files, project["sample"]["id"],
                                                           build_dir, source_dir=project["path"], emit=report, cancel=cancel)
                    options["after_configure"] = inspect_sample
                result = self._execute_build(plan["profile"], project["path"], plan["build_dir"], plan["operation"],
                                       environ=environment, emit=report, cancel=cancel, **options)
                attempt.update(result)
                try:
                    changed = source_inputs(project["path"]) != plan["sources"]
                except (OSError, ValueError):
                    changed = True
                attempt.setdefault("context", {})["application_freshness"] = "stale" if changed else "checked-at-finish"
                if changed:
                    attempt.setdefault("diagnostics", []).append("Application inputs changed during execution; artifacts describe this attempt only.")
            except CancelledError:
                attempt.update(status="cancelled", error="Operation cancelled.")
            except Exception as exc:
                attempt.update(status="failed", error=str(exc) or type(exc).__name__)
            attempt.update(logs=logs, finished_at=now())
            self.store.save_attempt(attempt)
            if attempt["status"] == "cancelled":
                raise CancelledError()
            if attempt["status"] == "failed":
                raise ValueError(attempt.get("error", "Build failed; inspect the saved attempt."))
            return {"attempt": attempt}

        return self.context.jobs.submit("projects", run, resource_key=f"project:{project['id']}",
                                        metadata={"workspace": workspace, "operation": plan["operation"], "view": "applications",
                                                  "project_id": project["id"], "attempt_id": plan["id"]})

    @_workflow_locked
    def outputs_preview(self, body):
        workspace = self._workspace(body, {"project_id", "attempt_id", "output_id"})
        if (body.get("attempt_id") is None) == (body.get("output_id") is None):
            raise ValueError("Select exactly one build attempt or managed output for cleanup.")
        project = self.store.project(body.get("project_id"), workspace)
        self._require_idle(project, workspace)
        validate_identity(project["path"], project["directory_identity"])
        attempt_id = body.get("attempt_id")
        if attempt_id is not None:
            attempt = self.store.attempt(attempt_id, project["id"], workspace)
            _owned_path(project, attempt)
            output_id = attempt.get("output_owner_id") or attempt["id"]
        else:
            output_id = identifier(body.get("output_id"))
        output = next((item for item in _output_entries(self._output_project(project), self.store) if item["id"] == output_id), None)
        if output is None or not output["clean_allowed"]:
            raise ValueError(output["reason"] if output else "This application has no matching managed output.")
        inventory = inventory_output(output["path"], identity=output["_record"].get("directory_identity"))
        plan = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": workspace, "project_id": project["id"],
                "attempt_id": attempt_id, "output_id": output_id, "output_owner_id": output_id,
                "build_dir": output["path"], "output_identity": inventory["identity"],
                "shared_attempt_ids": output["attempt_ids"], "latest_attempt_id": output["latest_attempt_id"],
                "created_at": now(), **{key: inventory[key] for key in ("bytes", "file_count", "directory_count", "symlink_count")},
                "inventory_digest": inventory["fingerprint"], "_inventory": inventory, "_record": output["_record"]}
        response = self._remember("outputs-clean", plan, {})
        response["plan"].pop("_inventory")
        response["plan"].pop("_record")
        return response

    @_workflow_locked
    def outputs_clean(self, body):
        workspace = self._workspace(body, {"plan_id"})
        saved = self._consume("outputs-clean", body.get("plan_id"), workspace)
        plan = saved["plan"]
        project = self.store.project(plan["project_id"], workspace)
        self._require_idle(project, workspace)

        def run(emit, cancel):
            validate_identity(project["path"], project["directory_identity"])
            outputs = _output_entries(self._output_project(project), self.store)
            output = next((item for item in outputs if item["id"] == plan["output_id"]), None)
            if not output or not output["clean_allowed"] or output["_record"] != plan["_record"]:
                raise ValueError("Managed output ownership changed after review. Preview cleanup again.")
            try:
                counts = remove_output(plan["build_dir"], plan["_inventory"], cancel=cancel, emit=emit)
            except ValueError as exc:
                if str(exc).startswith("Cleanup stopped"):
                    partial = {**plan["_record"], "directory_identity": plan["output_identity"],
                               "cleanup_error": str(exc)[:2000], "updated_at": now()}
                    partial.pop("legacy", None)
                    partial.pop("_derived", None)
                    self.store.save_output(partial)
                raise
            record = {**plan["_record"], "directory_identity": plan["output_identity"], "state": "cleaned", "cleaned_at": now(), "updated_at": now()}
            record.pop("legacy", None)
            record.pop("_derived", None)
            record.pop("cleanup_error", None)
            try:
                self.store.save_output(record)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Build outputs were removed, but their cleanup status could not be saved: {exc}") from exc
            with self.lock:
                self._discard_previews(project["id"])
            emit("Build outputs removed. Application sources and captured attempt history were retained.")
            return {"cleaned": plan["build_dir"], "output_id": plan["output_id"], "attempt_ids": plan["shared_attempt_ids"], **counts}

        return self.context.jobs.submit("projects", run, resource_key=f"project:{project['id']}", metadata={
            "workspace": workspace, "operation": "clean-output", "view": "applications", "project_id": project["id"],
            "output_id": plan["output_id"]})
