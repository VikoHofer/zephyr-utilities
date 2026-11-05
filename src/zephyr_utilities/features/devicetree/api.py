"""Editor, durable draft and reviewed Save routes through the authenticated router."""

from .drafts import DraftService
from .publication import OverlayPublisher
from .saving import SavingService
from .service import EditorService


def register(router, context):
    service = EditorService(context)
    context.services["devicetree_editor"] = service
    for suffix, method in (("catalog", service.catalog), ("open", service.open),
                           ("context", service.current), ("select", service.select),
                           ("document", service.document)):
        router.add("POST", f"/api/devicetree/editor/{suffix}",
                   lambda request, operation=method: operation(request.body))
    storage = context.feature_data("devicetree")
    publisher = OverlayPublisher(storage)
    drafts = DraftService(service, storage=storage, publisher=publisher)
    context.services["devicetree_drafts"] = drafts
    for suffix in ("open", "get", "source", "command", "undo", "redo"):
        router.add("POST", f"/api/devicetree/editor/drafts/{suffix}",
                   lambda request, operation=getattr(drafts, suffix): operation(request.body))
    saving = SavingService(drafts, publisher)
    context.services["devicetree_saving"] = saving
    for suffix in ("review", "save", "compare", "reload"):
        router.add("POST", f"/api/devicetree/editor/drafts/{suffix}",
                   lambda request, operation=getattr(saving, suffix): operation(request.body))
    for suffix in ("list", "read", "restore", "discard"):
        router.add("POST", f"/api/devicetree/editor/drafts/recovery/{suffix}",
                   lambda request, operation=getattr(saving, "recovery_" + suffix): operation(request.body))
