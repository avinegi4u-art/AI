"""User service application entrypoint."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI

from marsool_core.http.app import ServiceApp, create_app
from marsool_core.http.health import database_readiness_check, redis_readiness_check
from marsool_user.config import UserSettings, get_user_settings
from marsool_user.router import router
from marsool_user.runtime import UserRuntime

DESCRIPTION = """
Profiles, delivery addresses and role-specific details for the Marsool platform.

Every endpoint is scoped to the authenticated caller. Addresses carry both scalar
coordinates and a PostGIS geography point, so serviceability checks, delivery pricing and
courier routing all read from the same authoritative location.

The profile row is created either by consuming `identity.user_registered` from the auth
service or just-in-time on the first authenticated request, whichever happens first.
"""

TAGS_METADATA = [
    {"name": "users", "description": "Profile, addresses, preferences and courier details."},
    {"name": "health", "description": "Liveness and readiness probes."},
]


def build_service(settings: UserSettings | None = None) -> ServiceApp:
    """Build the user service application."""
    resolved = settings or get_user_settings()

    async def lifespan_hook(app: FastAPI) -> AsyncIterator[None]:
        runtime = UserRuntime(resolved, metrics=app.state.metrics)
        app.state.runtime = runtime
        await runtime.start()
        relay_task = asyncio.create_task(runtime.outbox_relay.run_forever())
        try:
            yield
        finally:
            runtime.outbox_relay.stop()
            relay_task.cancel()
            await asyncio.gather(relay_task, return_exceptions=True)
            await runtime.stop()

    service = create_app(
        resolved,
        title="Marsool User Service",
        description=DESCRIPTION,
        routers=[router],
        tags_metadata=TAGS_METADATA,
        lifespan_hook=lifespan_hook,
    )
    service.readiness_checks["database"] = database_readiness_check(
        lambda: service.app.state.runtime.database.engine
    )
    service.readiness_checks["redis"] = redis_readiness_check(
        lambda: service.app.state.runtime.redis
    )
    return service


app = build_service().app
