"""Read-only Device Tree inspection through the shared build observation service."""


from .bindings import BindingNavigator
from .parser import parse_resolved_dts
from .sources import SourceNavigation


class DeviceTreeService:
    def __init__(self, context):
        self.context = context
        self.sources = SourceNavigation(context.builds)
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
            raise ValueError("This image has no resolved Device Tree. Configure it, then inspect its build again in Project Creator.")
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


def register(router, context):
    service = DeviceTreeService(context)
    context.services["devicetree"] = service
    router.add("POST", "/api/devicetree/inspect", lambda request: service.inspect(request.body))
    router.add("POST", "/api/devicetree/sources", lambda request: service.source_catalog(request.body))
    router.add("POST", "/api/devicetree/bindings", lambda request: service.navigate_sources(request.body, "bindings"))
    router.add("POST", "/api/devicetree/source-file", lambda request: service.navigate_sources(request.body, "document"))
