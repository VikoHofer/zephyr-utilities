"""Project workflows use the shared authenticated router and job queue."""

from ...core.http import Response
from .service import ProjectsService
from .creator import CreatorService


def register(router, context):
    service = ProjectsService(context)
    context.services["projects"] = service
    router.add("GET", "/api/projects/environment", lambda request: service.current())
    router.add("POST", "/api/projects/analyze", lambda request: Response(service.start(request.body), status=202))
    creator = CreatorService(context, service.store)
    context.services["project_creator"] = creator
    router.add("GET", "/api/projects/creator", lambda request: creator.current())
    router.add("POST", "/api/projects/samples", lambda request: creator.samples(request.body))
    router.add("POST", "/api/projects/sample-check", lambda request: Response(creator.sample_check(request.body), status=202))
    router.add("POST", "/api/projects/preview", lambda request: creator.preview(request.body))
    router.add("POST", "/api/projects/create", lambda request: Response(creator.create(request.body), status=202))
    router.add("POST", "/api/projects/remove", lambda request: creator.remove_project(request.body))
    router.add("POST", "/api/projects/configurations", lambda request: creator.save_configuration(request.body))
    router.add("POST", "/api/projects/configurations/remove", lambda request: creator.remove_configuration(request.body))
    router.add("POST", "/api/projects/attempts/remove", lambda request: creator.remove_attempt(request.body))
    router.add("POST", "/api/projects/configuration-files", lambda request: creator.configuration_files(request.body))
    router.add("POST", "/api/projects/build-preview", lambda request: creator.build_preview(request.body))
    router.add("POST", "/api/projects/build", lambda request: Response(creator.build(request.body), status=202))
