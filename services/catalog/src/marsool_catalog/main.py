"""Catalog service application entrypoint."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI

from marsool_catalog.config import CatalogSettings, get_catalog_settings
from marsool_catalog.router import catalog_router, merchant_router
from marsool_catalog.runtime import CatalogRuntime
from marsool_core.http.app import ServiceApp, create_app
from marsool_core.http.health import database_readiness_check, redis_readiness_check

DESCRIPTION = """
Menus, items and basket validation for the Marsool platform.

`GET /merchants/{id}/menu` returns the whole nested menu in one round trip and is cached in
Redis, invalidated on any item change so a sold-out item disappears immediately.

`POST /merchants/{id}/basket/validate` is the authority on what a valid basket line is:
which option groups are required, how many options each allows, and what the configured
line costs. Order creation delegates here rather than reimplementing the rules, so the
order service cannot accept a line the catalogue would reject.
"""

TAGS_METADATA = [
    {
        "name": "catalog",
        "description": "Menus, items, option groups and basket validation.",
    },
    {"name": "health", "description": "Liveness and readiness probes."},
]


def build_service(settings: CatalogSettings | None = None) -> ServiceApp:
    """Build the catalog service application."""
    resolved = settings or get_catalog_settings()

    async def lifespan_hook(app: FastAPI) -> AsyncIterator[None]:
        runtime = CatalogRuntime(resolved, metrics=app.state.metrics)
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
        title="Marsool Catalog Service",
        description=DESCRIPTION,
        routers=[merchant_router, catalog_router],
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
