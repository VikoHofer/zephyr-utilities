"""Environment analysis uses the shared authenticated router and job queue."""

from ...core.http import Response
from .service import ProjectsService


def register(router, context):
    service = ProjectsService(context)
    context.services["projects"] = service
    router.add("GET", "/api/projects/environment", lambda request: service.current())
    router.add("POST", "/api/projects/analyze", lambda request: Response(service.start(request.body), status=202))
