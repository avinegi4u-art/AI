"""Fixtures for the merchant service test suite.

These tests run against real PostGIS. The whole point of the search path is that
proximity, polygon containment and timezone-aware opening hours are evaluated by the
database, so a stub would test nothing that ships.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import time

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_core.cache import build_redis
from marsool_core.events.bus import InMemoryEventBus
from marsool_core.security.principal import Principal, Role
from marsool_core.testing import (
    TEST_REDIS_DSN,
    build_test_engine,
    drop_schema,
    make_principal,
    provision_schema,
    session_factory_for,
    truncate_all,
)
from marsool_merchant import SCHEMA
from marsool_merchant.config import MerchantSettings
from marsool_merchant.deps import guards
from marsool_merchant.main import build_service
from marsool_merchant.models import Base, Merchant, ServiceArea
from marsool_merchant.runtime import MerchantRuntime
from marsool_merchant.testing import make_hours, make_merchant

MerchantFactory = Callable[..., Awaitable[Merchant]]


@pytest.fixture(scope="session")
def settings() -> MerchantSettings:
    return MerchantSettings()


@pytest.fixture(scope="session")
def engine() -> AsyncEngine:
    return build_test_engine()


@pytest.fixture(scope="session", autouse=True)
def schema() -> Iterator[None]:
    provision_schema(metadata=Base.metadata, schema=SCHEMA, create_postgis=True)
    yield
    drop_schema(schema=SCHEMA)


@pytest.fixture(autouse=True)
async def clean_tables(engine: AsyncEngine, schema: None) -> AsyncIterator[None]:
    await truncate_all(engine, metadata=Base.metadata)
    yield


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = build_redis(TEST_REDIS_DSN, service_name="marsool-merchant-tests")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def runtime(
    settings: MerchantSettings,
    engine: AsyncEngine,
    redis_client: Redis,
    event_bus: InMemoryEventBus,
) -> MerchantRuntime:
    return MerchantRuntime(settings, engine=engine, redis=redis_client, event_bus=event_bus)


@pytest.fixture
async def client(
    settings: MerchantSettings, runtime: MerchantRuntime
) -> AsyncIterator[AsyncClient]:
    """An unauthenticated client. Discovery endpoints are public."""
    service = build_service(settings)
    service.app.state.runtime = runtime
    transport = ASGITransport(app=service.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://merchant.test") as http_client:
        yield http_client


@pytest.fixture
async def client_as(
    settings: MerchantSettings, runtime: MerchantRuntime
) -> AsyncIterator[Callable[[Principal], Awaitable[AsyncClient]]]:
    """Factory for a client authenticated as an arbitrary principal."""
    clients: list[AsyncClient] = []

    async def _build(principal: Principal) -> AsyncClient:
        service = build_service(settings)
        service.app.state.runtime = runtime
        service.app.dependency_overrides[guards.verifier] = lambda: principal
        http_client = AsyncClient(
            transport=ASGITransport(app=service.app, raise_app_exceptions=False),
            base_url="http://merchant.test",
        )
        clients.append(http_client)
        return http_client

    try:
        yield _build
    finally:
        for http_client in clients:
            await http_client.aclose()


@pytest.fixture
def merchant_factory(engine: AsyncEngine) -> MerchantFactory:
    """Persist a merchant, optionally with opening hours and a service-area polygon."""

    async def _create(
        *,
        hours: list[tuple[int, time, time]] | None = None,
        service_area_wkt: str | None = None,
        **kwargs: object,
    ) -> Merchant:
        async with session_factory_for(engine)() as session:
            merchant = make_merchant(**kwargs)  # type: ignore[arg-type]
            session.add(merchant)
            await session.flush()
            if hours:
                session.add_all(make_hours(merchant.id, hours))
            if service_area_wkt:
                session.add(
                    ServiceArea(
                        merchant_id=merchant.id,
                        name="primary",
                        boundary=func.ST_GeogFromText(f"SRID=4326;{service_area_wkt}"),
                        is_active=True,
                    )
                )
            await session.commit()
            await session.refresh(merchant)
            return merchant

    return _create


@pytest.fixture
def merchant_staff_for() -> Callable[[str], Principal]:
    """Build a merchant-staff principal scoped to one merchant."""

    def _principal(merchant_id: str) -> Principal:
        return make_principal(role=Role.MERCHANT, merchant_ids=(merchant_id,))

    return _principal
