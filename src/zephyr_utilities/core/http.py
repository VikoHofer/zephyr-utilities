"""Authenticated loopback transport and feature-neutral request routing."""

from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler
import json
import re
import secrets
from urllib.parse import parse_qs, unquote, urlsplit

MAX_BODY = 64 * 1024 * 1024


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: dict = field(default_factory=dict)
    params: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)

    def query_value(self, name, default=""):
        return self.query.get(name, [default])[0]


@dataclass(frozen=True)
class Response:
    data: object
    status: int = 200
    content_type: str | None = None
    filename: str | None = None


class MethodNotAllowed(ValueError):
    pass


class Router:
    def __init__(self):
        self._routes = []
        self._identities = set()

    def add(self, method, path, handler):
        """Register an API path; :name segments are passed through Request.params."""
        if not path.startswith("/api/") or "?" in path or not callable(handler):
            raise ValueError("Routes require an /api/ path and a callable handler.")
        method = method.upper()
        segments = path.strip("/").split("/")
        names = [part[1:] for part in segments if part.startswith(":")]
        if len(names) != len(set(names)) or any(not name.isidentifier() for name in names):
            raise ValueError("Route parameters must have unique valid names.")
        identity = (method, tuple(":" if part.startswith(":") else part for part in segments))
        if identity in self._identities:
            raise ValueError(f"Duplicate route: {method} {path}")
        pattern = "/" + "/".join(f"(?P<{part[1:]}>[^/]+)" if part.startswith(":")
                                  else re.escape(part) for part in segments)
        self._identities.add(identity)
        self._routes.append((method, re.compile(pattern + "$"), handler))

    def dispatch(self, request):
        path_found = False
        for method, pattern, handler in self._routes:
            match = pattern.fullmatch(request.path)
            if match:
                path_found = True
                if method == request.method:
                    value = handler(Request(request.method, request.path, request.query,
                                            match.groupdict(), request.body))
                    return value if isinstance(value, Response) else Response(value)
        if path_found:
            raise MethodNotAllowed("HTTP method is not supported for this endpoint.")
        raise FileNotFoundError("API endpoint not found.")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def send_content(self, response):
        data = response.data
        content_type = response.content_type or "application/json; charset=utf-8"
        if response.content_type is None:
            data = json.dumps(data, ensure_ascii=True, allow_nan=False).encode()
        elif isinstance(data, str):
            data = data.encode()
        if not isinstance(data, bytes):
            raise TypeError("A non-JSON response must contain bytes or text.")
        if response.filename and any(character in response.filename for character in '\r\n"'):
            raise ValueError("Invalid download filename.")
        self.send_response(response.status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        if response.filename:
            self.send_header("Content-Disposition", f'attachment; filename="{response.filename}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, status, value):
        self.send_content(Response(value, status))

    def body(self):
        if self.headers.get("Transfer-Encoding"):
            raise ValueError("Chunked request bodies are not supported.")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Send JSON with Content-Type: application/json.")
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= MAX_BODY:
            raise ValueError("Request body must be between 1 byte and 64 MiB.")
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise ValueError("Incomplete request body.")

        def reject_constant(value):
            raise ValueError(f"Invalid JSON number: {value}")

        value = json.loads(raw, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError("The request body must be a JSON object.")
        return value

    def dispatch(self, method):
        app = self.server.app
        try:
            host = self.headers.get("Host", "")
            if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
                self.send_json(403, {"error": "Only the local application origin is allowed."})
                self.close_connection = True
                return
            origin = self.headers.get("Origin")
            if ((origin is not None and origin != f"http://{host}") or
                    self.headers.get("Sec-Fetch-Site") == "cross-site"):
                self.send_json(403, {"error": "Cross-origin requests are not allowed."})
                self.close_connection = True
                return
            url = urlsplit(self.path)
            path = unquote(url.path)
            if path == "/api/session" and method == "GET":
                self.send_json(200, app.session())
                return
            if path.startswith("/api/"):
                if not secrets.compare_digest(self.headers.get("X-Session-Token", ""), app.token):
                    self.send_json(403, {"error": "Session expired. Reload this page."})
                    self.close_connection = True
                    return
                body = self.body() if method in {"POST", "PATCH", "PUT", "DELETE"} else {}
                response = app.router.dispatch(Request(method, path, parse_qs(url.query), body=body))
            elif method == "GET":
                response = app.asset(path)
            else:
                raise FileNotFoundError("Page not found.")
            self.send_content(response)
        except MethodNotAllowed as exc:
            self.send_json(405, {"error": str(exc)})
        except FileNotFoundError as exc:
            self.send_json(404, {"error": str(exc)})
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            self.close_connection = True
            self.send_json(400, {"error": str(exc) or "Invalid request."})
        except PermissionError as exc:
            self.send_json(403, {"error": f"Permission denied: {exc.filename or 'requested resource'}"})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            self.close_connection = True
            self.send_json(500, {"error": f"Unable to complete request: {exc}"})
