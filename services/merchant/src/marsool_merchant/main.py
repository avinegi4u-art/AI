"""Merchant service application entrypoint."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI

from marsool_core.http.app import ServiceApp, create_app
from marsool_core.http.health import database_readiness_check, redis_readiness_check
from marsool_merchant.config import MerchantSettings, get_merchant_settings
from marsool_merchant.router import router
from marsool_merchant.runtime import MerchantRuntime

DESCRIPTION = """
Merchant discovery and storefront management for the Marsool platform.

`GET /merchants` answers the first question a customer asks — "what can I order from right
now?" — and does it in one indexed PostGIS query:

- **Proximity**: `ST_DWithin` against a GiST-indexed `geography` column, ranked by distance.
- **Coverage**: the customer's point must fall inside one of the merchant's service-area
  polygons, or within its delivery radius when it has none. Real coverage is not a circle.
- **Open now**: opening windows are stored as local wall-clock times and compared in the
  merchant's own timezone, including windows that run past midnight.

`GET /merchants/{id}/serviceability` is the pre-checkout gate used by order creation.
"""

TAGS_METADATA = [
    {
        "name": "merchants",
        "description": "Geospatial discovery, storefronts, opening hours and serviceability.",
    },
    {"name": "health", "description": "Liveness and readiness probes."},
]


def build_service(settings: MerchantSettings | None = None) -> ServiceApp:
    """Build the merchant service application."""
    resolved = settings or get_merchant_settings()

    async def lifespan_hook(app: FastAPI) -> AsyncIterator[None]:
        runtime = MerchantRuntime(resolved, metrics=app.state.metrics)
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
        title="Marsool Merchant Service",
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
