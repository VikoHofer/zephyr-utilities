"""Explicit UART drawing actions share the application's authenticated router."""

from ...core.http import Response
from .uart_service import UARTService


def register(router, context):
    service = UARTService(context)
    context.services["tinyml_uart"] = service
    router.add("GET", "/api/tinyml/ports", lambda request: service.ports(request.query_value("workspace")))
    router.add("POST", "/api/tinyml/prepare", lambda request: service.prepare(request.body))
    router.add("POST", "/api/tinyml/open", lambda request: Response(service.open(request.body), status=202))
    router.add("POST", "/api/tinyml/close", lambda request: Response(service.close_connection(request.body), status=202))
    router.add("POST", "/api/tinyml/infer", lambda request: Response(service.infer(request.body), status=202))
