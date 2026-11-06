"""Editor, durable draft and reviewed Save routes through the authenticated router."""

from .drafts import DraftService
from .publication import OverlayPublisher
from .saving import SavingService
from .service import EditorService
from .validation import ValidationService
from .binding_service import BindingService
from .peripherals import PeripheralService
from .pins import PinService
from .modules import ModuleService
from .layout import LayoutService


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
    bindings = BindingService(drafts)
    drafts.bindings = bindings
    context.services["devicetree_bindings"] = bindings
    router.add("POST", "/api/devicetree/editor/drafts/bindings", lambda request: bindings.inspect(request.body))
    peripherals = PeripheralService(drafts)
    drafts.peripherals = peripherals
    context.services["devicetree_peripherals"] = peripherals
    router.add("POST", "/api/devicetree/editor/drafts/peripherals", lambda request: peripherals.inspect(request.body))
    pins = PinService(drafts)
    drafts.pins = pins
    context.services["devicetree_pins"] = pins
    router.add("POST", "/api/devicetree/editor/drafts/pins", lambda request: pins.inspect(request.body))
    modules = ModuleService(drafts)
    drafts.modules = modules
    context.services["devicetree_modules"] = modules
    router.add("POST", "/api/devicetree/editor/drafts/modules", lambda request: modules.inspect(request.body))
    layout = LayoutService(drafts, modules, storage)
    context.services["devicetree_layout"] = layout
    router.add("POST", "/api/devicetree/editor/drafts/module-layout", lambda request: layout.read(request.body))
    router.add("POST", "/api/devicetree/editor/drafts/module-layout/save", lambda request: layout.save(request.body))
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
    validation = ValidationService(drafts)
    context.services["devicetree_validation"] = validation
    for suffix in ("review", "submit", "status", "cancel", "handoff"):
        router.add("POST", f"/api/devicetree/editor/drafts/validation/{suffix}",
                   lambda request, operation=getattr(validation, suffix): operation(request.body))
