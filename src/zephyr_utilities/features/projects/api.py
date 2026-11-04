"""Project workflows use the shared authenticated router and job queue."""

from ...core.http import Response
from .service import ProjectsService
from .creator import CreatorService
from .inspection import InspectionService


def register(router, context):
    service = ProjectsService(context)
    context.services["projects"] = service
    router.add("GET", "/api/projects/environment", lambda request: service.current())
    router.add("POST", "/api/projects/analyze", lambda request: Response(service.start(request.body), status=202))
    creator = CreatorService(context, service.store)
    context.services["project_creator"] = creator
    router.add("GET", "/api/projects/creator", lambda request: creator.current())
    router.add("POST", "/api/projects/preview", lambda request: creator.preview(request.body))
    router.add("POST", "/api/projects/create", lambda request: Response(creator.create(request.body), status=202))
    router.add("POST", "/api/projects/build-preview", lambda request: creator.build_preview(request.body))
    router.add("POST", "/api/projects/build", lambda request: Response(creator.build(request.body), status=202))
    inspection = InspectionService(context)
    router.add("POST", "/api/projects/inspect", lambda request: inspection.inspect(request.body))
    router.add("GET", "/api/projects/inspections/:id", lambda request: inspection.current(
        request.params["id"], request.query_value("workspace")))
    router.add("POST", "/api/projects/inspection-text", lambda request: inspection.text(request.body))
