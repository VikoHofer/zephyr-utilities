"""Read-only existing-build requests over the shared inspection service."""


class InspectionService:
    def __init__(self, context):
        self.context = context

    def _workspace(self, body, fields):
        if not isinstance(body, dict) or set(body) - (set(fields) | {"workspace"}):
            raise ValueError("Unknown build inspection option.")
        workspace = self.context.workspace.get()["path"]
        if body.get("workspace") != workspace:
            raise ValueError("The selected workspace changed. Open the build in the current workspace.")
        return workspace

    def inspect(self, body):
        workspace = self._workspace(body, {"build_dir"})
        return self.context.builds.inspect(workspace, body.get("build_dir"))

    def current(self, observation_id, workspace):
        selected = self._workspace({"workspace": workspace}, ())
        return self.context.builds.get(selected, observation_id)

    def text(self, body):
        workspace = self._workspace(body, {"inspection_id", "domain", "kind"})
        return self.context.builds.read_text(workspace, body.get("inspection_id"),
                                            body.get("domain"), body.get("kind"))
