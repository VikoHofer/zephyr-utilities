"""Reviewed project creation and explicit build jobs within the shared application."""

from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
from threading import RLock
import time
import uuid

from .analyzer import analyze_environment, digest
from .builds import command_recipe, execute_build
from .creation import apply_project, open_directory, path_identity, preview_project, select_profile, validate_identity
from .execution_identity import execution_guard
from .fragments import discover_fragment_files, profile_fragments, validate_fragment_files
from .metadata import read_regular
from .probing import check_cancel
from .records import ProjectStore, configuration_name, identifier, now


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


class CreatorService:
    def __init__(self, context, environment_store):
        self.context = context
        self.environments = environment_store
        self.store = ProjectStore(context.feature_data("projects") / "creator")
        self.plans = {}
        self.lock = RLock()

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

    def current(self):
        workspace = self.context.workspace.get()["path"]
        jobs = [job for job in self.context.jobs.list("projects")
                if job["metadata"].get("workspace") == workspace and job["metadata"].get("operation") != "analyze"]
        active = {job["metadata"].get("attempt_id") for job in jobs if job["status"] in {"queued", "running"}}
        stored_projects, diagnostics = self.store.list(workspace, active)
        projects = []
        for project in stored_projects:
            try:
                if not stat.S_ISDIR(Path(project["path"]).stat().st_mode):
                    continue
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError as exc:
                diagnostics.append(f"Cannot check application directory {project['path']}: {exc}")
            projects.append(project)
        snapshot = self.environments.latest(workspace)
        if snapshot:
            snapshot["freshness"] = "saved-observation"
        return {"workspace": workspace, "snapshot": snapshot, "projects": projects, "jobs": jobs,
                "diagnostics": diagnostics}

    def preview(self, body):
        workspace = self._workspace(body, {"name", "destination", "board_id", "target", "sdk_id"})
        snapshot = self.environments.latest(workspace)
        if snapshot is None:
            raise ValueError("Analyze this workspace before creating an application.")
        environment = dict(os.environ)
        plan = preview_project(workspace, {key: value for key, value in body.items() if key != "workspace"}, snapshot,
                               recipe_factory=lambda profile, source, build: command_recipe(profile, source, build,
                                                                                           environ=environment))
        plan["profile"]["execution_fingerprint"] = execution_guard(snapshot, plan["profile"])
        return self._remember("create", plan, environment)

    def _recheck(self, profile, environment, emit, cancel):
        emit("Rechecking the selected environment before this operation…")
        previous = self.environments.latest(profile["workspace"])
        result = analyze_environment(profile["workspace"], profile["analysis_options"], environ=environment,
                                     emit=emit, cancel=cancel, previous=previous)
        if result["status"] == "stale" or execution_guard(result, profile, cancel=cancel) != profile.get("execution_fingerprint"):
            raise ValueError("The environment changed since selection. Analyze again and save a new build configuration, or preview a new application.")
        check_cancel(cancel)

    def create(self, body):
        workspace = self._workspace(body, {"plan_id"})
        saved = self._consume("create", body.get("plan_id"), workspace)
        plan, environment = saved["plan"], saved["environment"]

        def run(emit, cancel):
            self._recheck(plan["profile"], environment, emit, cancel)
            emit(f"Creating the reviewed application at {plan['destination']}…")
            project = apply_project(plan, cancel=cancel)
            try:
                self.store.save_project(project)
            except (OSError, ValueError) as exc:
                raise ValueError(f"Application files were created at {project['path']}, but its profile could not be saved: {exc}") from exc
            emit("Application created. Configure and build are separate actions.")
            return {"project": project}

        return self.context.jobs.submit("projects", run, resource_key=f"create:{plan['destination']}",
                                        metadata={"workspace": workspace, "operation": "create", "view": "create", "plan_id": plan["id"]})

    def configuration_files(self, body):
        workspace = self._workspace(body, {"project_id"})
        project = self.store.project(body.get("project_id"), workspace)
        validate_identity(project["path"], project["directory_identity"])
        catalogue = discover_fragment_files(project["path"])
        validate_identity(project["path"], project["directory_identity"])
        return {"workspace": workspace, "project_id": project["id"], **catalogue}

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

    def build_preview(self, body):
        workspace = self._workspace(body, {"project_id", "operation", "configuration_id"})
        operation = body.get("operation")
        if operation not in {"configure", "build"}:
            raise ValueError("Choose configure or build.")
        project = self.store.project(body.get("project_id"), workspace)
        configuration_id = body.get("configuration_id")
        if configuration_id is None or configuration_id == "":
            profile = deepcopy(project["profile"])
            configuration = {"id": None, "name": "Original"}
        else:
            saved = self.store.configuration(configuration_id, project["id"], workspace)
            profile = deepcopy(saved["profile"])
            configuration = {"id": saved["id"], "name": saved["name"]}
        source = Path(project["path"])
        validate_identity(source, project["directory_identity"])
        validate_fragment_files(source, profile_fragments(profile))
        sources = source_inputs(source)
        parent = source / "build"
        parent_identity = path_identity(parent) if parent.exists() or parent.is_symlink() else None
        plan_id = uuid.uuid4().hex
        output = parent / plan_id
        environment = dict(os.environ)
        recipe = command_recipe(profile, source, output, environ=environment)
        plan = {"schema_version": 1, "id": plan_id, "workspace": workspace, "project_id": project["id"],
                "operation": operation, "build_dir": str(output), "recipe": recipe, "sources": sources,
                "parent_identity": parent_identity, "created_at": now(), "profile": profile,
                "configuration": configuration}
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

    def build(self, body):
        workspace = self._workspace(body, {"build_plan_id"})
        saved = self._consume("build", body.get("build_plan_id"), workspace)
        plan, environment = saved["plan"], saved["environment"]
        project = self.store.project(plan["project_id"], workspace)

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
                       "context": {}, "artifacts": [], "diagnostics": []}
            self.store.save_attempt(attempt)
            try:
                self._recheck(plan["profile"], environment, report, cancel)
                self._prepare_output(project, plan, cancel=cancel)
                check_cancel(cancel)
                result = execute_build(plan["profile"], project["path"], plan["build_dir"], plan["operation"],
                                       environ=environment, emit=report, cancel=cancel)
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
