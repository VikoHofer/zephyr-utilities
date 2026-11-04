"""Memory Explorer service and route registration, independent of the HTTP server."""

import csv
import io
import json
from pathlib import Path

from ...core.http import Response
from .compare import compare_snapshots
from .discovery import discover_builds
from .snapshots import SnapshotStore, inspect_selected_build


def validate_labels(body):
    name, notes = body.get("name", ""), body.get("notes", "")
    if not isinstance(name, str) or not isinstance(notes, str):
        raise ValueError("Snapshot name and notes must be text.")
    if len(name) > 160 or len(notes) > 8000:
        raise ValueError("Use at most 160 characters for the name and 8,000 for notes.")
    return name.strip(), notes.strip()


def snapshot_csv(snapshot):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["snapshot_id", "memory", "report_total_bytes", "path", "symbol",
                     "section", "address", "size_bytes"])

    def cell(value):
        text = str(value) if value is not None else ""
        return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else text

    for memory, report in snapshot["reports"].items():
        if report is None:
            continue
        for leaf in report["leaves"]:
            writer.writerow([snapshot["id"], memory.upper(), report["total_size"],
                             cell(leaf.get("path")), cell(leaf.get("name")),
                             cell(leaf.get("section")), cell(leaf.get("address")), leaf["size"]])
    return output.getvalue().encode("utf-8-sig")


class MemoryService:
    def __init__(self, context):
        self.context = context
        self.store = SnapshotStore(context.feature_data("memory"))

    def _selection(self, body):
        if not isinstance(body, dict) or set(body) != {"workspace", "inspection_id", "domain"}:
            raise ValueError("Choose a workspace, build inspection and image/domain.")
        workspace = self.context.workspace.get()["path"]
        if body["workspace"] != workspace:
            raise ValueError("The selected workspace changed. Open a build in the current workspace.")
        if not isinstance(body["domain"], str) or not body["domain"] or len(body["domain"]) > 256:
            raise ValueError("Choose an image/domain from this build inspection.")
        return self.context.builds.select_artifact(workspace, body["inspection_id"], body["domain"], "elf",
                                                  related_kinds=("kconfig", "map", "resolved-dts"))

    def inspection(self, request):
        selection = self._selection(request.body)
        build = inspect_selected_build(selection)
        return {"workspace": selection.workspace, "inspection_id": selection.inspection_id,
                "domain": selection.domain, "build": build,
                "elf": {key: selection.artifact[key] for key in ("path", "sha256")},
                "context": selection.context}

    def start_job(self, request):
        body = request.body
        selection = None
        if "selection" in body:
            if set(body) - {"build_path", "name", "notes", "selection"}:
                raise ValueError("Unknown inspected-build report options.")
            selection = self._selection(body["selection"])
        build_path = body.get("build_path")
        if not isinstance(build_path, str) or not build_path.strip():
            raise ValueError("Select a build directory first.")
        path = str(Path(build_path).expanduser().resolve())
        name, notes = validate_labels(body)
        if selection is not None:
            if path != selection.build_dir:
                raise ValueError("The build directory does not match the selected inspection.")
            build = inspect_selected_build(selection)
            if not build["ready"]:
                raise ValueError(build["error"])

        def capture(emit, cancel):
            options = {"selection": selection} if selection is not None else {}
            snapshot = self.store.capture(path, name, notes, emit=emit, cancel=cancel, **options)
            return {"snapshot_id": snapshot["id"]}

        job = self.context.jobs.submit("memory", capture, resource_key=f"build:{path}",
                                       metadata={"build_path": path})
        return Response(job, status=202)

    def rename(self, request):
        name, notes = validate_labels(request.body)
        if not name:
            raise ValueError("Enter a snapshot name.")
        return self.store.rename(request.params["id"], name, notes)

    def import_reports(self, request):
        name, notes = validate_labels(request.body)
        return self.store.import_reports(request.body.get("ram"), request.body.get("rom"), name, notes)

    def export(self, request):
        snapshot = self.store.load(request.params["id"])
        fmt = request.query_value("format", "json")
        if fmt == "json":
            data = json.dumps(snapshot, indent=2, allow_nan=False).encode()
            mime = "application/json; charset=utf-8"
        elif fmt == "csv":
            data, mime = snapshot_csv(snapshot), "text/csv; charset=utf-8"
        else:
            raise ValueError("Export format must be json or csv.")
        return Response(data, content_type=mime, filename=f"snapshot-{snapshot['id']}.{fmt}")


def register(router, context):
    service = MemoryService(context)
    context.services["memory"] = service
    router.add("POST", "/api/memory/inspection", service.inspection)
    routes = (
        ("GET", "/builds", lambda request: discover_builds(
            request.query_value("path", context.workspace.get()["path"]))),
        ("POST", "/jobs", service.start_job),
        ("GET", "/snapshots", lambda request: {"snapshots": service.store.list()}),
        ("GET", "/snapshots/:id", lambda request: service.store.load(request.params["id"])),
        ("PATCH", "/snapshots/:id", service.rename),
        ("DELETE", "/snapshots/:id", lambda request: service.store.delete(request.params["id"])),
        ("POST", "/import", service.import_reports),
        ("GET", "/compare", lambda request: compare_snapshots(
            service.store.load(request.query_value("baseline")),
            service.store.load(request.query_value("candidate")))),
        ("GET", "/snapshots/:id/export", service.export),
    )
    for method, suffix, handler in routes:
        router.add(method, "/api/memory" + suffix, handler)
        router.add(method, "/api" + suffix, handler)  # Original Memory Explorer API.
