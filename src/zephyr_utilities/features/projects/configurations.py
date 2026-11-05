"""Reviewed edits to saved build choices and optional exact-target overlay files."""

from copy import deepcopy
import hashlib
import os
from pathlib import Path
import uuid

from .application_inputs import source_inputs
from .analyzer import digest
from .builds import command_recipe
from .creation import select_profile, validate_identity
from .default_overlay import extend_default_overlays, project_default_overlay, project_default_overlays
from .execution_identity import execution_guard
from .fragments import profile_fragments, validate_fragment_files
from .metadata import read_regular
from .records import configuration_name, now
from .reviewed_files import apply_reviewed_files


class ConfigurationWorkflow:
    def configuration_preview(self, body):
        with self.lock:
            workspace = self._workspace(body, {"project_id", "configuration_id", "name", "board_id", "target", "sdk_id",
                                                "fragments", "create_default_overlay", "expected_revision"})
            project = self.store.project(body.get("project_id"), workspace)
            self._require_idle(project, workspace)
            validate_identity(project["path"], project["directory_identity"])
            previous = None
            if "configuration_id" not in body:
                mode, configuration_id, revision = "create", uuid.uuid4().hex, 0
                base = project["profile"]
            elif body["configuration_id"] in ("", None):
                mode, configuration_id, revision = "original", None, project.get("profile_revision", 0)
                base = project["profile"]
            else:
                previous = self.store.configuration(body["configuration_id"], project["id"], workspace)
                mode, configuration_id, revision = "update", previous["id"], previous.get("revision", 0)
                base = previous["profile"]
            if "expected_revision" in body and (type(body["expected_revision"]) is not int or body["expected_revision"] != revision):
                raise ValueError("This configuration changed. Refresh applications and review its current version.")
            if mode == "original":
                name = body.get("name", "Original")
                if name != "Original":
                    raise ValueError("The Original configuration keeps its reserved name.")
            else:
                name = configuration_name(body.get("name", previous.get("name") if previous else None))
            create_overlay = body.get("create_default_overlay", False)
            if not isinstance(create_overlay, bool):
                raise ValueError("Choose whether to create a default overlay.")
            snapshot = self.environments.latest(workspace)
            selection = {"board_id": body.get("board_id", base["board_id"]), "target": body.get("target", base["target"]),
                         "sdk_id": body.get("sdk_id", base["sdk"]["id"])}
            profile = select_profile(workspace, selection, snapshot)
            profile["fragments"] = validate_fragment_files(project["path"], body.get("fragments", profile_fragments(base)))
            profile["schema_version"] = 2
            profile["execution_fingerprint"] = execution_guard(snapshot, profile)
            sources = source_inputs(project["path"])
            changes, warnings = [], []
            overlay = project_default_overlay(project, profile)
            if create_overlay and overlay:
                if not any(item["path"] == overlay["path"] for item in sources["files"]):
                    raise ValueError("This target's registered default overlay is missing. Restore it before changing the configuration.")
                warnings.append("This target already has a default overlay. Its current contents will be retained.")
            elif create_overlay:
                cmake = read_regular(Path(project["path"]) / "CMakeLists.txt", 1024 * 1024).decode("utf-8")
                contents = {item["path"]: "" for item in sources["files"]}
                contents["CMakeLists.txt"] = cmake
                updated, overlay = extend_default_overlays(contents, profile["target"], project_default_overlays(project))
                changes = [{"path": path, "before": cmake if path == "CMakeLists.txt" else None,
                            "content": updated[path], "sha256": hashlib.sha256(updated[path].encode()).hexdigest()}
                           for path in ("CMakeLists.txt", overlay["path"])]
                if profile["fragments"]["overlays"]["mode"] == "replace":
                    warnings.append("Replace mode excludes the new default overlay unless you select it explicitly afterward.")
            if mode == "original":
                configuration = {"id": None, "name": "Original", "profile": profile, "revision": revision + 1}
            else:
                configuration = {**(deepcopy(previous) if previous else {}), "schema_version": 2, "id": configuration_id,
                    "project_id": project["id"], "workspace": workspace, "name": name, "profile": profile,
                    "revision": revision + 1 if previous else 0, "created_at": previous["created_at"] if previous else now(),
                    "updated_at": now()}
                self.store.validate_configuration_change(configuration, previous)
            if overlay:
                configuration["default_overlay"] = deepcopy(overlay)
            elif "default_overlay" in configuration:
                del configuration["default_overlay"]
            recipe = command_recipe(profile, project["path"], Path(project["path"]) / "build" / "manual")
            validate_identity(project["path"], project["directory_identity"])
            if source_inputs(project["path"]) != sources:
                raise ValueError("Application files changed while preparing the review. Review again.")
            plan = {"schema_version": 1, "id": uuid.uuid4().hex, "workspace": workspace, "project_id": project["id"],
                    "mode": mode, "configuration": configuration, "profile": profile, "expected_revision": revision,
                    "previous_configuration": previous, "project": project, "sources": sources, "files": changes,
                    "default_overlay": overlay, "warnings": warnings, "recipe": recipe, "created_at": now()}
            return self._remember("configuration", plan, dict(os.environ))

    def configuration_apply(self, body):
        with self.lock:
            workspace = self._workspace(body, {"plan_id"})
            saved = self._consume("configuration", body.get("plan_id"), workspace)
            plan = saved["plan"]
            project = self.store.project(plan["project_id"], workspace)
            self._require_idle(project, workspace)
            if project != plan["project"]:
                raise ValueError("The saved application changed since review. Review the configuration again.")
            validate_identity(project["path"], project["directory_identity"])
            if source_inputs(project["path"]) != plan["sources"]:
                raise ValueError("Application files changed since review. Review the configuration again.")
            snapshot = self.environments.latest(workspace)
            if execution_guard(snapshot, plan["profile"]) != plan["profile"]["execution_fingerprint"]:
                raise ValueError("The environment changed since review. Analyze and review this configuration again.")
            updated_project = deepcopy(project)
            configuration = deepcopy(plan["configuration"])
            if plan["mode"] == "original":
                updated_project.setdefault("initial_profile", deepcopy(project["profile"]))
                updated_project["profile"] = deepcopy(plan["profile"])
                updated_project["profile_revision"] = configuration["revision"]
            if plan["files"]:
                defaults = project_default_overlays(project)
                defaults.append(deepcopy(plan["default_overlay"]))
                updated_project["default_overlays"] = defaults
            if updated_project != project:
                updated_project.update(schema_version=2, revision=project.get("revision", 0) + 1, updated_at=now())

            def save():
                with self.store.lock:
                    if self.store.project(project["id"], workspace) != project:
                        raise ValueError("The saved application changed since review. Review again.")
                    if plan["files"]:
                        files = {item["path"]: deepcopy(item) for item in plan["sources"]["files"]}
                        for item in plan["files"]:
                            files[item["path"]] = {"path": item["path"], "sha256": item["sha256"], "size": len(item["content"].encode())}
                        files = sorted(files.values(), key=lambda item: item["path"])
                        updated_project["registered_sources"] = {**plan["sources"], "files": files, "fingerprint": digest(files)}
                    if plan["mode"] != "original":
                        self.store.validate_configuration_change(configuration, plan["previous_configuration"])
                        if plan["previous_configuration"]:
                            self.store.update_configuration(configuration, plan["previous_configuration"])
                        else:
                            self.store.save_configuration(configuration)
                    try:
                        if updated_project != project:
                            self.store.update_project(updated_project, project)
                    except BaseException:
                        if plan["mode"] != "original":
                            path = self.store.root / "configurations" / f"{configuration['id']}.json"
                            if self.store._read(path) == configuration:
                                if plan["previous_configuration"]:
                                    self.store._write(path, plan["previous_configuration"])
                                else:
                                    path.unlink()
                        raise
                    return {"configuration": configuration, "project": deepcopy(updated_project)}

            result = apply_reviewed_files(project["path"], project["directory_identity"], plan["files"], save)
            self._discard_previews(project["id"])
            return result
