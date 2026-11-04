"""Advanced inspection and legacy read-only routes through the shared router."""


from .bindings import BindingNavigator
from .application_sources import ApplicationSourceNavigation
from .inspection import InspectionService
from .parser import parse_resolved_dts
from .sources import SourceNavigation


class DeviceTreeService:
    def __init__(self, context):
        self.context = context
        self.sources = SourceNavigation(context.builds)
        self.application_sources = ApplicationSourceNavigation()
        self.bindings = BindingNavigator()

    def _selection(self, body, extra=()):
        fields = {"workspace", "inspection_id", "domain", *extra}
        if not isinstance(body, dict) or set(body) != fields:
            raise ValueError("Choose a workspace, build inspection and image/domain.")
        workspace = self.context.workspace.get()["path"]
        if body["workspace"] != workspace:
            raise ValueError("The selected workspace changed. Open a build in the current workspace.")
        if not isinstance(body["domain"], str) or not body["domain"] or len(body["domain"]) > 256:
            raise ValueError("Choose an image/domain from this build inspection.")
        return workspace

    def inspect(self, body):
        workspace = self._selection(body)
        observation = self.context.builds.get(workspace, body["inspection_id"])
        domain = next((item for item in observation["domains"] if item["name"] == body["domain"]), None)
        if domain is None:
            raise ValueError("This image/domain does not belong to the selected build inspection.")
        if not any(item["kind"] == "resolved-dts" for item in domain["artifacts"]):
            raise ValueError("This image has no resolved Device Tree. Configure it, then open its build again in Build Inspector.")
        document = self.context.builds.read_text(workspace, observation["id"], domain["name"], "resolved-dts")
        return {"workspace": workspace, "inspection": observation, "domain": domain,
                "document": document, "tree": parse_resolved_dts(document["text"])}

    def source_catalog(self, body):
        workspace = self._selection(body)
        return self.sources.create(workspace, body["inspection_id"], body["domain"])

    def navigate_sources(self, body, operation):
        key = "node_path" if operation == "bindings" else "file_id"
        workspace = self._selection(body, ("catalog_id", key))
        if not isinstance(body[key], str) or not body[key] or len(body[key]) > 4096:
            raise ValueError("Choose a node or file from this source catalogue.")
        scope = self.sources.get(workspace, body["inspection_id"], body["domain"], body["catalog_id"])
        with scope.lock:
            if operation == "bindings":
                result = self.bindings.find(scope, body[key])
            elif scope.files.get(body[key], {}).get("kind") == "binding":
                result = self.bindings.document(scope, body[key])
            else:
                result = self.sources.document(scope, body[key])
        return {"workspace": workspace, "inspection_id": scope.inspection_id, "domain": scope.domain,
                "catalog_id": scope.catalog_id, **result}

    def _application_selection(self, body, extra=()):
        fields = {"workspace", "application_path", *extra}
        if not isinstance(body, dict) or set(body) != fields:
            raise ValueError("Choose a workspace and application source directory.")
        workspace = self.context.workspace.get()["path"]
        if body["workspace"] != workspace:
            raise ValueError("The selected workspace changed. Open an application in the current workspace.")
        if not isinstance(body["application_path"], str) or not body["application_path"] or len(body["application_path"]) > 4096:
            raise ValueError("Choose an application source directory inside the workspace.")
        return workspace

    def application_catalog(self, body):
        workspace = self._application_selection(body)
        return self.application_sources.create(workspace, body["application_path"])

    def application_document(self, body):
        workspace = self._application_selection(body, ("catalog_id", "file_id"))
        if not isinstance(body["file_id"], str) or not body["file_id"] or len(body["file_id"]) > 4096:
            raise ValueError("Choose a file from this application source catalogue.")
        scope = self.application_sources.get(workspace, body["application_path"], body["catalog_id"])
        with scope.lock:
            if scope.files.get(body["file_id"], {}).get("kind") == "binding":
                result = self.bindings.document(scope, body["file_id"])
            else:
                result = self.application_sources.document(scope, body["file_id"])
        return {**scope.selection(), "catalog_id": scope.catalog_id, **result}


def register(router, context):
    service = DeviceTreeService(context)
    context.services["inspector"] = service
    # Keep existing in-process and HTTP consumers on the same transient catalogues.
    context.services["devicetree"] = service
    inspection = InspectionService(context)
    for prefix in ("/api/inspector", "/api/projects"):
        router.add("POST", prefix + "/inspect", lambda request: inspection.inspect(request.body))
        router.add("GET", prefix + "/inspections/:id", lambda request: inspection.current(
            request.params["id"], request.query_value("workspace")))
        router.add("POST", prefix + "/inspection-text", lambda request: inspection.text(request.body))
    router.add("POST", "/api/inspector/devicetree", lambda request: service.inspect(request.body))
    router.add("POST", "/api/devicetree/inspect", lambda request: service.inspect(request.body))
    for prefix in ("/api/inspector", "/api/devicetree"):
        router.add("POST", prefix + "/sources", lambda request: service.source_catalog(request.body))
        router.add("POST", prefix + "/bindings", lambda request: service.navigate_sources(request.body, "bindings"))
        router.add("POST", prefix + "/source-file", lambda request: service.navigate_sources(request.body, "document"))
        router.add("POST", prefix + "/application-sources", lambda request: service.application_catalog(request.body))
        router.add("POST", prefix + "/application-source-file", lambda request: service.application_document(request.body))
