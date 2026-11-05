"""Reviewed project creation and explicit build jobs within the shared application."""

from copy import deepcopy
from functools import wraps
import os
from pathlib import Path
from threading import RLock
import time
import uuid

from .analyzer import analyze_environment
from .builds import command_recipe, execute_build
from .build_workflow import BuildWorkflow, annotate_outputs
from .creation import apply_project, path_identity, preview_project, select_profile, validate_identity
from .execution_identity import execution_guard
from .fragments import discover_fragment_files, validate_fragment_files
from .probing import check_cancel
from .records import ProjectStore, configuration_name, identifier, now
from .samples import preview_sample, sample_catalog
from .sample_compatibility import check_sample
from .sample_check_identity import sample_input_guard
from .overlay_usage import overlay_usage
from .application_inputs import source_inputs as source_inputs
from .configurations import ConfigurationWorkflow
from .default_overlay import project_default_overlay
from .project_lifecycle import ProjectLifecycle
from .templates import template_catalog


def _locked(method):
    """Serialize metadata removal with preview creation and job submission."""
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    return call


class CreatorService(ConfigurationWorkflow, ProjectLifecycle, BuildWorkflow):
    def __init__(self, context, environment_store):
        self.context = context
        self.environments = environment_store
        self.store = ProjectStore(context.feature_data("projects") / "creator")
        self.plans = {}
        self.sample_checks = {}
        self.lock = RLock()

    def _execute_build(self, *args, **kwargs):
        return execute_build(*args, **kwargs)

    def _workspace(self, body, fields):
        if not isinstance(body, dict) or set(body) - (set(fields) | {"workspace"}):
            raise ValueError("Unknown project operation option.")
        workspace = self.context.workspace.get()["path"]
        if body.get("workspace") != workspace:
            raise ValueError("The selected workspace changed. Review this operation in the current workspace.")
        return workspace

    def _remember(self, kind, plan, environment):
        with self.lock:
            current = time.monotonic()
            self.plans = {key: value for key, value in self.plans.items() if value["expires"] > current}
            if len(self.plans) >= 32:
                del self.plans[next(iter(self.plans))]
            self.plans[plan["id"]] = {"kind": kind, "plan": deepcopy(plan), "environment": dict(environment),
                                       "expires": current + 1800}
        return {"plan": deepcopy(plan)}

    def _consume(self, kind, plan_id, workspace):
        identifier(plan_id)
        with self.lock:
            saved = self.plans.get(plan_id)
            if not saved or saved["kind"] != kind or saved["expires"] <= time.monotonic():
                raise ValueError("This preview expired or was already used. Preview the operation again.")
            if saved["plan"]["workspace"] != workspace:
                raise ValueError("This preview belongs to another workspace.")
            return self.plans.pop(plan_id)

    @_locked
    def current(self):
        return self._current(self.context.workspace.get()["path"])

    def _current(self, workspace):
        jobs = [job for job in self.context.jobs.list("projects")
                if job["metadata"].get("workspace") == workspace and job["metadata"].get("operation") not in {"analyze", "sample-check"}]
        active = {job["metadata"].get("attempt_id") for job in jobs if job["status"] in {"queued", "running"}}
        stored_projects, diagnostics = self.store.list(workspace, active)
        projects, unavailable = [], []
        live_paths = set()
        for project in stored_projects:
            reason = None
            try:
                identity = path_identity(project["path"])
                if identity != project.get("directory_identity") or project["path"] in live_paths:
                    reason = ("replaced", "The saved application folder was replaced or recreated at this location.")
            except (FileNotFoundError, NotADirectoryError):
                reason = ("missing", "The saved application folder was deleted or moved from this location.")
            except ValueError:
                reason = ("replaced", "The saved application path no longer refers to its original directory.")
            except OSError as exc:
                diagnostics.append(f"Cannot check application directory {project['path']}: {exc}")
            annotate_outputs(project, self.store)
            self._project_overlay_reports(project)
            if reason:
                project.update(availability=reason[0], availability_reason=reason[1])
                unavailable.append(project)
            else:
                live_paths.add(project["path"])
                projects.append(project)
        project_ids = {project["id"] for project in projects}
        attempt_ids = {attempt["id"] for project in projects for attempt in project["attempts"]}

        def visible_job(job):
            if job["status"] in {"queued", "running"}:
                return True
            metadata = job["metadata"]
            if metadata.get("attempt_id"):
                return metadata["attempt_id"] in attempt_ids
            if metadata.get("project_id"):
                return metadata["project_id"] in project_ids
            created = (job.get("result") or {}).get("project")
            return not created or created.get("id") in project_ids

        jobs = [job for job in jobs if visible_job(job)]
        snapshot = self.environments.latest(workspace)
        if snapshot:
            snapshot["freshness"] = "saved-observation"
        return {"workspace": workspace, "snapshot": snapshot, "projects": projects,
                "unavailable_projects": unavailable, "jobs": jobs,
                "diagnostics": diagnostics, "templates": template_catalog()}

    @staticmethod
    def _project_overlay_reports(project):
        """Expose historical selection without treating it as current filesystem state."""
        attempts = project.get("attempts", [])
        reports = {}
        for attempt in attempts:
            profile = attempt.get("profile") or project.get("initial_profile", project["profile"])
            source = attempt.get("context", {}).get("source_dir") or attempt.get("recipe", {}).get("cwd") or project["path"]
            report = overlay_usage(profile, source,
                files=attempt.get("sources", {}).get("files", project.get("files", [])),
                context=attempt.get("context"), default_overlay=attempt.get("default_overlay") or project_default_overlay(project, profile))
            report["evidence"] = {"kind": "attempt", "attempt_id": attempt["id"],
                "checked_at": attempt.get("finished_at") or attempt.get("started_at"),
                "configuration_name": attempt.get("configuration", {}).get("name", "Original"),
                "note": "Captured for this attempt. Configure again to confirm selection after changing application or board files."}
            reports[attempt["id"]] = report
        project["overlay_usage_by_attempt"] = reports
        selected = {}
        for configuration in [{"id": "", "name": "Original", "profile": project["profile"]}, *project.get("configurations", [])]:
            profile = configuration["profile"]
            matching = next((attempt for attempt in attempts
                if (attempt.get("configuration", {}).get("id") or "") == configuration["id"]
                and attempt.get("profile") == profile and reports[attempt["id"]]["status"] == "confirmed"
                and attempt.get("context", {}).get("source_dir", project["path"]) == project["path"]), None)
            if matching:
                selected[configuration["id"]] = deepcopy(reports[matching["id"]])
            else:
                report = overlay_usage(profile, project["path"], files=project.get("registered_sources", {}).get("files", project.get("files", [])),
                                       default_overlay=project_default_overlay(project, profile))
                report["evidence"] = {"kind": "creation", "configuration_name": configuration["name"],
                    "note": "Based on the last reviewed application file list. Review Configure to inspect current files and confirm overlay selection."}
                selected[configuration["id"]] = report
        project["overlay_usage_by_configuration"] = selected

    def _require_idle(self, project, workspace):
        for job in self.context.jobs.list("projects"):
            metadata = job["metadata"]
            if (job["status"] in {"queued", "running"} and metadata.get("workspace") == workspace and
                    (metadata.get("project_id") == project["id"] or metadata.get("destination") == project["path"])):
                raise ValueError("An operation is using this application. Wait for it to finish or cancel it before removing records.")

    def _discard_previews(self, project_id, configuration_id=None):
        self.plans = {key: value for key, value in self.plans.items()
                      if value["plan"].get("project_id") != project_id or
                      (configuration_id is not None and value["plan"].get("configuration", {}).get("id") != configuration_id)}

    @_locked
    def remove_project(self, body):
        workspace = self._workspace(body, {"project_id"})
        project = self.store.project(body.get("project_id"), workspace)
        self._require_idle(project, workspace)
        current = self._current(workspace)
        if any(item["id"] == project["id"] for item in current["projects"]):
            if any(item["path"] == project["path"] and item.get("directory_identity") == project.get("directory_identity")
                   for item in current["unavailable_projects"]):
                raise ValueError("Remove the older unavailable saved entries for this location first, so they cannot reappear as the current application.")
        self.store.remove_project(project["id"], workspace)
        self._discard_previews(project["id"])
        return {"removed": project["id"]}

    @_locked
    def remove_configuration(self, body):
        workspace = self._workspace(body, {"project_id", "configuration_id"})
        project = self.store.project(body.get("project_id"), workspace)
        configuration_id = identifier(body.get("configuration_id"))
        self._require_idle(project, workspace)
        self.store.remove_configuration(configuration_id, project["id"], workspace)
        self._discard_previews(project["id"], configuration_id)
        return {"removed": configuration_id}

    @_locked
    def remove_attempt(self, body):
        workspace = self._workspace(body, {"project_id", "attempt_id"})
        project = self.store.project(body.get("project_id"), workspace)
        attempt_id = identifier(body.get("attempt_id"))
        self._require_idle(project, workspace)
        attempt = self.store.attempt(attempt_id, project["id"], workspace)
        self.store._validate_removal(attempt)
        self.retain_attempt_output(project, attempt)
        self.store.remove_attempt(attempt_id, project["id"], workspace)
        return {"removed": attempt_id}

    def samples(self, body):
        workspace = self._workspace(body, set())
        snapshot = self.environments.latest(workspace)
        zephyr = snapshot.get("zephyr") if isinstance(snapshot, dict) else None
        if not isinstance(zephyr, dict) or not isinstance(zephyr.get("path"), str) or not zephyr["path"]:
            raise ValueError("Analyze this workspace before choosing a local sample.")
        return {"workspace": workspace, **sample_catalog(snapshot)}

    def preview(self, body):
        workspace = self._workspace(body, {"name", "destination", "board_id", "target", "sdk_id", "template_id",
                                           "sample_id", "sample_fingerprint", "sample_check_id"})
        if "sample_check_id" in body and body.get("template_id") != "local-sample":
            raise ValueError("Sample compatibility checks only apply to local samples.")
        snapshot = self.environments.latest(workspace)
        if snapshot is None:
            raise ValueError("Analyze this workspace before creating an application.")
        environment = dict(os.environ)
        plan = preview_project(workspace, {key: value for key, value in body.items() if key not in {"workspace", "sample_check_id"}}, snapshot,
                               recipe_factory=lambda profile, source, build: command_recipe(profile, source, build,
                                                                                           environ=environment))
        plan["profile"]["execution_fingerprint"] = execution_guard(snapshot, plan["profile"])
        if "sample" in plan:
            plan["sample_compatibility"] = self._sample_result(body, plan, snapshot)
        checked_overlays = plan.get("sample_compatibility", {}).get("overlay_usage")
        if isinstance(checked_overlays, dict) and checked_overlays.get("status") == "confirmed":
            plan["overlay_usage"] = deepcopy(checked_overlays)
            plan["overlay_usage"]["evidence"] = {"kind": "compatibility-check",
                "checked_at": plan["sample_compatibility"].get("checked_at"),
                "note": "Confirmed by configuring an identical temporary sample copy with this target."}
        else:
            plan["overlay_usage"] = overlay_usage(plan["profile"], plan["destination"], files=plan["files"],
                                                   default_overlay=plan.get("default_overlay"))
            plan["overlay_usage"]["evidence"] = {"kind": "creation", "note": "Expected selection; Configure confirms the actual inputs."}
        return self._remember("create", plan, environment)

    @staticmethod
    def _sample_selection(body, snapshot):
        return {key: body[key] for key in ("workspace", "sample_id", "sample_fingerprint", "board_id", "target", "sdk_id")} | {
            "environment_fingerprint": snapshot["fingerprint"]}

    def _sample_result(self, body, plan, snapshot):
        selection = self._sample_selection(body, snapshot)
        check_id = body.get("sample_check_id")
        if "sample_check_id" in body:
            identifier(check_id)
        with self.lock:
            self.sample_checks = {key: value for key, value in self.sample_checks.items() if value["expires"] > time.monotonic()}
            saved = self.sample_checks.get(check_id) if check_id else next((value for value in reversed(list(self.sample_checks.values()))
                if value["selection"] == selection and value["sample_fingerprint"] == plan["sample"]["fingerprint"]
                and value["guard"] == plan["profile"]["execution_fingerprint"]), None)
            if check_id and (not saved or saved["selection"] != selection or
                             saved["sample_fingerprint"] != plan["sample"]["fingerprint"] or
                             saved["guard"] != plan["profile"]["execution_fingerprint"]):
                raise ValueError("This sample compatibility check expired or its inputs changed. Check compatibility again.")
            saved = deepcopy(saved)
        if saved:
            try:
                current_inputs = sample_input_guard(plan["profile"])
            except (OSError, ValueError):
                current_inputs = None
            if current_inputs != saved["input_guard"]:
                if check_id:
                    raise ValueError("This sample compatibility check expired or its inputs changed. Check compatibility again.")
                saved = None
            else:
                plan["sample_input_fingerprint"] = current_inputs
        result = deepcopy(saved["result"]) if saved else {
            **selection, "status": "unknown", "message": "Board compatibility has not been checked.",
            "details": ["Use Check compatibility to configure a temporary copy and inspect this board's resolved hardware requirements."]}
        if result["status"] == "incompatible":
            raise ValueError(result["message"])
        if plan["sample"].get("adaptation"):
            result["adaptation"] = deepcopy(plan["sample"]["adaptation"])
        return result

    def sample_check(self, body):
        workspace = self._workspace(body, {"sample_id", "sample_fingerprint", "board_id", "target", "sdk_id"})
        body = deepcopy(body)
        snapshot = self.environments.latest(workspace)
        profile = select_profile(workspace, body, snapshot)
        environment = dict(os.environ)
        profile["execution_fingerprint"] = execution_guard(snapshot, profile)
        recipe = {"cwd": workspace, "display_configure": "", "display_build": ""}
        sample = preview_sample(snapshot, body.get("sample_id"), recipe, profile=profile)
        fingerprint = sample["provenance"].get("source_fingerprint", sample["provenance"]["fingerprint"])
        if body.get("sample_fingerprint") != fingerprint:
            raise ValueError("The local sample changed after selection. Refresh samples before checking compatibility.")
        selection = self._sample_selection(body, snapshot)
        check_id = uuid.uuid4().hex

        def run(emit, cancel):
            self._recheck(profile, environment, emit, cancel)
            current = preview_sample(snapshot, body["sample_id"], recipe, profile=profile, cancel=cancel)
            if current != sample:
                raise ValueError("The local sample changed before the compatibility check. Refresh samples and check again.")
            input_guard = sample_input_guard(profile, cancel=cancel)
            result = check_sample(profile, sample["files"], body["sample_id"],
                                  root=self.context.feature_data("projects") / "sample-checks",
                                  default_overlay=sample["provenance"].get("default_overlay"),
                                  environ=environment, emit=emit, cancel=cancel)
            self._recheck(profile, environment, emit, cancel)
            if preview_sample(snapshot, body["sample_id"], recipe, profile=profile, cancel=cancel) != sample:
                raise ValueError("The local sample changed during the compatibility check. Refresh samples and check again.")
            if sample_input_guard(profile, cancel=cancel) != input_guard:
                raise ValueError("The board configuration inputs changed during the compatibility check. Check compatibility again.")
            check_cancel(cancel)
            result = {**result, **selection, "checked_at": now()}
            if sample["provenance"].get("adaptation"):
                result["adaptation"] = deepcopy(sample["provenance"]["adaptation"])
            with self.lock:
                self.sample_checks = {key: value for key, value in self.sample_checks.items() if value["expires"] > time.monotonic()}
                if len(self.sample_checks) >= 32:
                    del self.sample_checks[next(iter(self.sample_checks))]
                self.sample_checks[check_id] = {"selection": selection, "result": deepcopy(result),
                    "guard": profile["execution_fingerprint"], "sample_fingerprint": sample["provenance"]["fingerprint"],
                    "input_guard": input_guard, "expires": time.monotonic() + 1800}
            return {"check_id": check_id, "compatibility": result}

        return self.context.jobs.submit("projects", run, resource_key=f"sample-check:{workspace}",
            metadata={"workspace": workspace, "operation": "sample-check", "view": "create", "selection": selection})

    def _recheck(self, profile, environment, emit, cancel):
        emit("Rechecking the selected environment before this operation…")
        previous = self.environments.latest(profile["workspace"])
        if previous and previous.get("status") == "stale":
            raise ValueError("The environment changed since selection. Analyze again and edit the build configuration, or preview a new application.")
        options = self.environments.analysis_options(profile["workspace"], profile["analysis_options"])
        result = analyze_environment(profile["workspace"], options, environ=environment,
                                     emit=emit, cancel=cancel, previous=previous)
        if result["status"] == "stale" or execution_guard(result, profile, cancel=cancel) != profile.get("execution_fingerprint"):
            raise ValueError("The environment changed since selection. Analyze again and edit the build configuration, or preview a new application.")
        check_cancel(cancel)

    @_locked
    def create(self, body):
        workspace = self._workspace(body, {"plan_id"})
        saved = self._consume("create", body.get("plan_id"), workspace)
        plan, environment = saved["plan"], saved["environment"]

        def run(emit, cancel):
            self._recheck(plan["profile"], environment, emit, cancel)
            if plan.get("sample_input_fingerprint") and sample_input_guard(plan["profile"], cancel=cancel) != plan["sample_input_fingerprint"]:
                raise ValueError("Sample compatibility inputs changed after preview. Check compatibility and preview again.")
            emit(f"Creating the reviewed application at {plan['destination']}…")
            project = apply_project(plan, cancel=cancel)
            try:
                self.store.save_project(project)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Application files were created at {project['path']}, but its profile could not be saved: {exc}") from exc
            emit("Application created. Configure and build are separate actions.")
            return {"project": project}

        return self.context.jobs.submit("projects", run, resource_key=f"create:{plan['destination']}",
                                        metadata={"workspace": workspace, "operation": "create", "view": "create",
                                                  "destination": plan["destination"], "plan_id": plan["id"]})

    def configuration_files(self, body):
        workspace = self._workspace(body, {"project_id"})
        project = self.store.project(body.get("project_id"), workspace)
        validate_identity(project["path"], project["directory_identity"])
        catalogue = discover_fragment_files(project["path"])
        validate_identity(project["path"], project["directory_identity"])
        return {"workspace": workspace, "project_id": project["id"], **catalogue}

    @_locked
    def save_configuration(self, body):
        workspace = self._workspace(body, {"project_id", "name", "board_id", "target", "sdk_id", "fragments"})
        name = configuration_name(body.get("name"))
        project = self.store.project(body.get("project_id"), workspace)
        validate_identity(project["path"], project["directory_identity"])
        snapshot = self.environments.latest(workspace)
        profile = select_profile(workspace, body, snapshot)
        if "fragments" in body:
            profile["fragments"] = validate_fragment_files(project["path"], body["fragments"])
            profile["schema_version"] = 2
        profile["execution_fingerprint"] = execution_guard(snapshot, profile)
        command_recipe(profile, project["path"], Path(project["path"]) / "build" / "manual")
        configuration = {"schema_version": 1, "id": uuid.uuid4().hex, "project_id": project["id"],
                         "workspace": workspace, "name": name, "created_at": now(), "profile": profile}
        validate_identity(project["path"], project["directory_identity"])
        self.store.save_configuration(configuration)
        return {"configuration": deepcopy(configuration)}
