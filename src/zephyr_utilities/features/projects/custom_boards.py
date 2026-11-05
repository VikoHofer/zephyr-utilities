"""Reviewed registration, reconnection and creation of local custom board roots."""

from copy import deepcopy
from pathlib import Path
import time
import unicodedata
import uuid

from .analyzer import digest
from .creation import _path, path_identity
from .custom_board_records import MAX_ROOTS
from .hardware import discover_hardware
from .records import identifier, now


class CustomBoardsService:
    def __init__(self, context, environments, creator):
        self.context = context
        self.environments = environments
        self.creator = creator
        self.store = environments.custom_boards
        self.plans = {}

    def _workspace(self, body, allowed):
        workspace = self.context.workspace.get()["path"]
        if not isinstance(body, dict) or set(body) - (set(allowed) | {"workspace"}):
            raise ValueError("Unknown custom board operation option.")
        if body.get("workspace") != workspace:
            raise ValueError("The selected workspace changed. Review custom boards in the current workspace.")
        return workspace

    def _idle(self, workspace):
        if any(job["status"] in {"queued", "running"} and job["metadata"].get("workspace") == workspace
               for job in self.context.jobs.list("projects")):
            raise ValueError("Wait for active project operations to finish, or cancel them, before changing custom boards.")

    @staticmethod
    def _name(value):
        if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 100 or
                any(unicodedata.category(character).startswith("C") for character in value)):
            raise ValueError("Give this board root a name of 1 to 100 readable characters.")
        return value.strip()

    def _root(self, workspace, value, snapshot):
        path = _path(value, "Custom board root")
        if not path.is_absolute():
            path = Path(workspace) / path
        if path == Path(workspace) or not path.is_relative_to(workspace):
            raise ValueError("Choose a custom board root inside the selected workspace.")
        if any(part.startswith(".") or part in {"build", "node_modules"} for part in path.relative_to(workspace).parts):
            raise ValueError("Choose a custom board root outside hidden, build and tool directories.")
        protected = [Path(__file__).resolve().parents[4]]
        if snapshot:
            protected += [Path(snapshot["zephyr"]["path"])] if snapshot.get("zephyr", {}).get("path") else []
            protected += [Path(item["path"]) for kind in ("sdks", "modules") for item in snapshot.get(kind, []) if item.get("path")]
        if any(path == item or path.is_relative_to(item) for item in protected):
            raise ValueError("Choose a custom board root outside Zephyr, dependency, SDK and utilities directories.")
        identity = path_identity(path)
        boards_identity = path_identity(path / "boards")
        return str(path), identity, boards_identity

    @staticmethod
    def _unique(record, records):
        for other in records:
            if other["id"] == record["id"]:
                continue
            first, second = Path(record["path"]), Path(other["path"])
            if first == second or first.is_relative_to(second) or second.is_relative_to(first):
                raise ValueError("This board root overlaps an existing registration. Update that registration instead.")
            if other["name"].casefold() == record["name"].casefold():
                raise ValueError("A custom board registration already has that name.")

    @staticmethod
    def _hardware(path, snapshot):
        roots = snapshot.get("roots", {}).get("socs", []) if snapshot else []
        roots = [*roots, {"path": path, "origin": "Custom board root"}]
        result = discover_hardware([{"path": path, "origin": "Registered custom board root"}], roots)
        diagnostics = [item["message"] for item in result["diagnostics"]]
        if not result["boards"]:
            diagnostics.append("No supported board definition was discovered. Check boards/**/board.yml and analyze the environment after fixing it.")
        return result["boards"], diagnostics

    def _dependencies(self, workspace, root):
        projects, diagnostics = self.creator.store.list(workspace, set())
        if diagnostics:
            raise ValueError("Saved application dependencies could not be read completely. Resolve record diagnostics first.")
        result = []
        for project in projects:
            for configuration in [{"id": None, "name": "Original", "profile": project["profile"]}, *project.get("configurations", [])]:
                profile = configuration["profile"]
                if any(item.get("path") == root for entries in profile.get("roots", {}).values() for item in entries):
                    result.append({"project_id": project["id"], "project_name": project["name"],
                                   "configuration_id": configuration["id"], "name": configuration["name"],
                                   "target": profile.get("target")})
        return result

    def current(self):
        from .board_scaffold import catalogue
        workspace = self.context.workspace.get()["path"]
        snapshot = self.environments.latest(workspace)
        _, entries, _ = self.store.observe(workspace)
        diagnostics = []
        for entry in entries:
            entry["boards"], entry["diagnostics"] = self._hardware(entry["path"], snapshot) if entry["availability"] == "available" else ([], [])
            try:
                entry["dependencies"] = self._dependencies(workspace, entry["path"])
            except ValueError as exc:
                entry["dependencies"] = []
                diagnostics.append(str(exc))
        jobs = [job for job in self.context.jobs.list("projects") if job["metadata"].get("workspace") == workspace and
                job["metadata"].get("operation") == "analyze"]
        return {"workspace": workspace, "registrations": entries, "snapshot": snapshot,
                "catalogue": catalogue(snapshot) if snapshot else {"references": [], "socs": []},
                "diagnostics": diagnostics, "job": jobs[0] if jobs else None}

    def preview(self, body):
        from .board_scaffold import preview_scaffold
        workspace = self._workspace(body, {"operation", "id", "name", "path", "vendor", "full_name", "reference_id", "soc"})
        operation = body.get("operation")
        if operation not in {"register", "update", "remove", "create"}:
            raise ValueError("Choose a supported custom board operation.")
        with self.creator.lock, self.store.lock:
            self._idle(workspace)
            state, _, signature = self.store.observe(workspace)
            snapshot = self.environments.latest(workspace)
            previous = None
            if operation in {"update", "remove"}:
                key = identifier(body.get("id"))
                previous = next((entry for entry in state["registrations"] if entry["id"] == key), None)
                if previous is None:
                    raise ValueError("This custom board registration is no longer available in this workspace.")
            elif "id" in body:
                raise ValueError("A new custom board registration cannot specify an existing identity.")
            dependencies = self._dependencies(workspace, previous["path"]) if previous else []
            plan = {"id": uuid.uuid4().hex, "workspace": workspace, "operation": operation,
                    "previous": previous, "expected": state, "registry_signature": signature,
                    "environment_id": snapshot.get("id") if snapshot else None,
                    "dependencies": dependencies, "files": [], "warnings": [], "diagnostics": [], "targets": [], "boards": []}
            if operation == "remove":
                if set(body) - {"workspace", "operation", "id"}:
                    raise ValueError("Removing a registration only accepts its identity.")
                plan.update(name=previous["name"], path=previous["path"], registration=None)
                plan["warnings"].append("Removing this registration leaves all board source files on disk. Analyze again and review affected application configurations.")
                plan["warnings"].append("Independent discovery paths, BOARD_ROOT or module settings may still include these boards. Remove those discovery sources separately if you want to stop using the root entirely.")
            else:
                if operation == "create":
                    scaffold = preview_scaffold(workspace, {key: value for key, value in body.items() if key not in {"workspace", "operation"}}, snapshot)
                    plan.update(scaffold=scaffold, files=scaffold["files"], warnings=scaffold.get("warnings", []), targets=scaffold.get("targets", []))
                    record = {"name": self._name(body.get("full_name") or body.get("name")), "path": scaffold["path"],
                              "provenance": scaffold.get("provenance")}
                else:
                    if set(body) - {"workspace", "operation", "id", "name", "path"}:
                        raise ValueError("Registering a board root accepts only its name and path.")
                    if not snapshot or not snapshot.get("zephyr", {}).get("path"):
                        raise ValueError("Analyze this workspace before registering a custom board root.")
                    path, identity, boards_identity = self._root(workspace, body.get("path"), snapshot)
                    record = {"name": self._name(body.get("name")), "path": path,
                              "directory_identity": identity, "boards_identity": boards_identity}
                    before = self._observation({**record, "workspace": workspace})
                    plan["boards"], plan["diagnostics"] = self._hardware(path, snapshot)
                    if self._observation({**record, "workspace": workspace}) != before:
                        raise ValueError("Custom board files changed while preparing the review. Review again.")
                    plan["source_observation"] = before
                    plan["targets"] = [target for board in plan["boards"] for target in board.get("targets", [])]
                record.update(id=previous["id"] if previous else uuid.uuid4().hex, workspace=workspace,
                              created_at=previous["created_at"] if previous else now(), updated_at=now())
                if previous and previous.get("provenance"):
                    record["provenance"] = deepcopy(previous["provenance"])
                self._unique(record, state["registrations"])
                if previous is None and len(state["registrations"]) >= MAX_ROOTS:
                    raise ValueError(f"At most {MAX_ROOTS} custom board roots can be registered in one workspace.")
                plan.update(registration=record, name=record["name"], path=record["path"])
            plan["warnings"].append("Analyze the environment after applying this change. Existing saved configurations keep their captured settings until explicitly edited.")
            current = time.monotonic()
            self.plans = {key: value for key, value in self.plans.items() if value["expires"] > current}
            if len(self.plans) >= 32:
                del self.plans[next(iter(self.plans))]
            self.plans[plan["id"]] = {"plan": deepcopy(plan), "expires": current + 1800}
            return {"plan": deepcopy(plan)}

    def _observation(self, record):
        _, identity, boards_identity = self._root(record["workspace"], record["path"], None)
        if identity != record["directory_identity"] or boards_identity != record["boards_identity"]:
            raise ValueError("The custom board directory changed during review. Review again.")
        from .analyzer import fingerprint, metadata_inputs
        paths, warnings = metadata_inputs([Path(record["path"]) / "boards", Path(record["path"]) / "soc"])
        value, _, more = fingerprint(paths)
        if warnings or more:
            raise ValueError("Custom board inputs could not be inspected completely. Use a narrower board root.")
        return digest({"files": value, "identity": path_identity(record["path"]),
                       "boards_identity": path_identity(Path(record["path"]) / "boards")})

    def apply(self, body):
        from .board_scaffold import apply_scaffold
        workspace = self._workspace(body, {"plan_id"})
        key = identifier(body.get("plan_id"))
        with self.creator.lock, self.store.lock:
            self._idle(workspace)
            saved = self.plans.get(key)
            if not saved or saved["expires"] <= time.monotonic() or saved["plan"]["workspace"] != workspace:
                raise ValueError("This custom board review expired or belongs to another workspace. Review again.")
            plan = self.plans.pop(key)["plan"]
            state, _, signature = self.store.observe(workspace)
            if state != plan["expected"] or signature != plan["registry_signature"]:
                raise ValueError("Custom board registrations or files changed since review. Review again.")
            snapshot = self.environments.latest(workspace)
            if plan["operation"] != "remove" and (snapshot.get("id") if snapshot else None) != plan["environment_id"]:
                raise ValueError("The analyzed environment changed since review. Review the custom board operation again.")
            if plan["previous"] and self._dependencies(workspace, plan["previous"]["path"]) != plan["dependencies"]:
                raise ValueError("Affected application configurations changed since review. Review again.")
            record = deepcopy(plan.get("registration"))
            if plan["operation"] == "create":
                publication = apply_scaffold(plan["scaffold"])
                record.update(directory_identity=publication["directory_identity"],
                              boards_identity=path_identity(Path(record["path"]) / "boards"))
            elif record and self._observation(record) != plan["source_observation"]:
                raise ValueError("The custom board files changed since review. Review again.")
            updated = deepcopy(state)
            updated["revision"] += 1
            updated["registrations"] = [entry for entry in state["registrations"] if entry["id"] != (plan["previous"] or {}).get("id")]
            if record:
                updated["registrations"].append(record)
            try:
                self.store.write(updated, state)
            except BaseException as exc:
                if plan["operation"] == "create":
                    raise ValueError(f"Board files were created at {record['path']}, but registration could not be saved. Use Register existing board root to reconnect these files. {exc}") from exc
                raise
            self.plans.clear()
            result = {"needs_analysis": True, "dependencies": plan["dependencies"]}
            if record:
                result["registration"] = record
            else:
                result["removed"] = plan["previous"]["id"]
            return result
