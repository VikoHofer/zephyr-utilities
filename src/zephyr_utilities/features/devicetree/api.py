"""Read-only Device Tree inspection through the shared build observation service."""


from .parser import parse_resolved_dts


class DeviceTreeService:
    def __init__(self, context):
        self.context = context

    def inspect(self, body):
        fields = {"workspace", "inspection_id", "domain"}
        if not isinstance(body, dict) or set(body) != fields:
            raise ValueError("Choose a workspace, build inspection and image/domain.")
        workspace = self.context.workspace.get()["path"]
        if body["workspace"] != workspace:
            raise ValueError("The selected workspace changed. Open a build in the current workspace.")
        if not isinstance(body["domain"], str) or not body["domain"] or len(body["domain"]) > 256:
            raise ValueError("Choose an image/domain from this build inspection.")
        observation = self.context.builds.get(workspace, body["inspection_id"])
        domain = next((item for item in observation["domains"] if item["name"] == body["domain"]), None)
        if domain is None:
            raise ValueError("This image/domain does not belong to the selected build inspection.")
        if not any(item["kind"] == "resolved-dts" for item in domain["artifacts"]):
            raise ValueError("This image has no resolved Device Tree. Configure it, then inspect its build again in Project Creator.")
        document = self.context.builds.read_text(workspace, observation["id"], domain["name"], "resolved-dts")
        return {"workspace": workspace, "inspection": observation, "domain": domain,
                "document": document, "tree": parse_resolved_dts(document["text"])}


def register(router, context):
    service = DeviceTreeService(context)
    context.services["devicetree"] = service
    router.add("POST", "/api/devicetree/inspect", lambda request: service.inspect(request.body))
