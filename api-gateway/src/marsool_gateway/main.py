"""Gateway application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import FastAPI

from marsool_core.http.app import ServiceApp, create_app
from marsool_core.http.health import redis_readiness_check
from marsool_gateway.config import GatewaySettings, get_gateway_settings
from marsool_gateway.router import router
from marsool_gateway.runtime import GatewayRuntime

DESCRIPTION = """
The single entry point for every Marsool client.

Responsibilities, and nothing more:

- **Authentication** — verifies the bearer token and attaches the principal to the request.
- **Authorization boundary** — reads on merchant and menu paths are public so a customer can
  browse before signing in; everything else requires a token, and no write is ever public.
- **Rate limiting** — authenticated callers are limited per user id, anonymous traffic shares
  a tighter bucket keyed by client IP.
- **Routing** — forwards to the owning service. `/merchants/{id}/menu` and
  `/merchants/{id}/basket/validate` go to catalog even though they read as merchant
  sub-resources, because that is where the data lives.
- **Correlation** — generates a correlation ID for external callers and propagates it, so one
  request can be followed across every service it touches.

The gateway holds no business logic and no database, and every downstream service verifies
the token again for itself: a bypassed or compromised gateway must not be able to mint trust.

Per-service OpenAPI documents are served by the services themselves; this document only
describes the gateway's own health and metrics endpoints.
"""


def build_service(settings: GatewaySettings | None = None) -> ServiceApp:
    """Build the gateway application."""
    resolved = settings or get_gateway_settings()

    async def lifespan_hook(app: FastAPI) -> AsyncIterator[None]:
        runtime = GatewayRuntime(resolved)
        app.state.runtime = runtime
        try:
            yield
        finally:
            await runtime.stop()

    service = create_app(
        resolved,
        title="Marsool API Gateway",
        description=DESCRIPTION,
        routers=[router],
        tags_metadata=[{"name": "health", "description": "Liveness and readiness probes."}],
        lifespan_hook=lifespan_hook,
    )
    service.readiness_checks["redis"] = redis_readiness_check(
        lambda: service.app.state.runtime.redis
    )
    return service


app = build_service().app
