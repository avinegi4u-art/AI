"""Auth service application entrypoint."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI

from marsool_auth.config import AuthSettings, get_auth_settings
from marsool_auth.router import router
from marsool_auth.runtime import AuthRuntime
from marsool_core.http.app import ServiceApp, create_app
from marsool_core.http.health import database_readiness_check, redis_readiness_check

DESCRIPTION = """
Phone/OTP authentication and token lifecycle for the Marsool platform.

This is the only service that mints tokens; every other service verifies them
independently against the same issuer, audience and signing key.

**Flow**

1. `POST /auth/otp` — request a one-time password (rate limited per phone number).
2. `POST /auth/login` — exchange the phone number and code for an access/refresh pair.
   The account is created on first successful login.
3. `POST /auth/refresh` — rotate the refresh token. Tokens are single-use; replaying a
   rotated token revokes the whole session.
4. `POST /auth/logout` — revoke one session or all sessions.
"""

TAGS_METADATA = [
    {"name": "auth", "description": "OTP challenges, login, token rotation and sessions."},
    {"name": "health", "description": "Liveness and readiness probes."},
]


def build_service(settings: AuthSettings | None = None) -> ServiceApp:
    """Build the auth service application."""
    resolved = settings or get_auth_settings()

    async def lifespan_hook(app: FastAPI) -> AsyncIterator[None]:
        runtime = AuthRuntime(resolved, metrics=app.state.metrics)
        app.state.runtime = runtime
        await runtime.start()
        # The relay drains the outbox in-process. It moves to its own deployment once
        # event volume justifies scaling publication independently of the API.
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
        title="Marsool Auth Service",
        description=DESCRIPTION,
        routers=[router],
        tags_metadata=TAGS_METADATA,
        lifespan_hook=lifespan_hook,
    )
    # Registered after construction because the checks need the runtime, which only
    # exists once the lifespan has run. The health router holds this dict by reference.
    service.readiness_checks["database"] = database_readiness_check(
        lambda: service.app.state.runtime.database.engine
    )
    service.readiness_checks["redis"] = redis_readiness_check(
        lambda: service.app.state.runtime.redis
    )
    return service


app = build_service().app
