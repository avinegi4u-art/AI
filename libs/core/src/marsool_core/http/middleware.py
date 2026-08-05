"""HTTP middleware: correlation IDs, access logging, security headers."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from marsool_core.context import (
    CORRELATION_ID_HEADER,
    REQUEST_ID_HEADER,
    bind_context,
)
from marsool_core.ids import new_ulid
from marsool_core.logging import get_logger

logger = get_logger("http.access")

RequestResponder = Callable[[Request], Awaitable[Response]]

# Paths excluded from access logging: probes and metrics scrapes would otherwise
# dominate the log volume without adding signal.
_UNLOGGED_PATHS = frozenset({"/health", "/health/live", "/health/ready", "/metrics"})


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagates a correlation ID across the whole call graph.

    The gateway generates the ID for external callers; downstream services reuse the
    inbound header. Each hop also gets its own request ID so a single logical request
    can be broken down per service.
    """

    async def dispatch(self, request: Request, call_next: RequestResponder) -> Response:
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or new_ulid()
        request_id = new_ulid()
        with bind_context(correlation_id=correlation_id, request_id=request_id):
            request.state.correlation_id = correlation_id
            request.state.request_id = request_id
            response = await call_next(request)
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Emits one structured log line per request with latency and status."""

    async def dispatch(self, request: Request, call_next: RequestResponder) -> Response:
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.exception(
                "http_request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
            )
            raise
        duration_ms = (time.perf_counter() - started) * 1000
        if request.url.path not in _UNLOGGED_PATHS:
            logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                # The route template (not the raw path) keeps log cardinality bounded.
                route=getattr(request.scope.get("route"), "path", request.url.path),
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
                client_ip=request.client.host if request.client else None,
            )
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.2f}"
        return response


class SecurityHeadersMiddleware:
    """Adds baseline security headers to every response.

    Implemented as raw ASGI middleware rather than ``BaseHTTPMiddleware`` so it also
    covers streaming and WebSocket-adjacent responses without buffering.
    """

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self._app = app
        self._headers: list[tuple[bytes, bytes]] = [
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"cross-origin-opener-policy", b"same-origin"),
            (b"permissions-policy", b"geolocation=(self), microphone=(), camera=()"),
        ]
        if hsts:
            self._headers.append(
                (b"strict-transport-security", b"max-age=31536000; includeSubDomains")
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                existing = {name.lower() for name, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    header for header in self._headers if header[0] not in existing
                ]
            await send(message)

        await self._app(scope, receive, send_with_headers)
