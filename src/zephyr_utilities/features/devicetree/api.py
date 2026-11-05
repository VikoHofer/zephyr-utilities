"""Editor context and in-memory draft routes through the authenticated router."""

from .drafts import DraftService
from .service import EditorService


def register(router, context):
    service = EditorService(context)
    context.services["devicetree_editor"] = service
    for suffix, method in (("catalog", service.catalog), ("open", service.open),
                           ("context", service.current), ("select", service.select),
                           ("document", service.document)):
        router.add("POST", f"/api/devicetree/editor/{suffix}",
                   lambda request, operation=method: operation(request.body))
    drafts = DraftService(service)
    context.services["devicetree_drafts"] = drafts
    for suffix in ("open", "get", "source", "command", "undo", "redo"):
        router.add("POST", f"/api/devicetree/editor/drafts/{suffix}",
                   lambda request, operation=getattr(drafts, suffix): operation(request.body))
