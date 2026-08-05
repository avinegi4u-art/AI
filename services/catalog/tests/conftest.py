"""Fixtures for the catalog service test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_catalog import SCHEMA
from marsool_catalog.config import CatalogSettings
from marsool_catalog.deps import guards
from marsool_catalog.main import build_service
from marsool_catalog.models import Base
from marsool_catalog.runtime import CatalogRuntime
from marsool_catalog.testing import seed_menu as seed_menu_data
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

MERCHANT_ID = "mch_01J9Z8XQF3K7M2P4R6T8V0W1Y3"
OTHER_MERCHANT_ID = "mch_01J9Z8XQF3K7M2P4R6T8V0W1Y9"


@pytest.fixture(scope="session")
def settings() -> CatalogSettings:
    return CatalogSettings()


@pytest.fixture(scope="session")
def engine() -> AsyncEngine:
    return build_test_engine()


@pytest.fixture(scope="session", autouse=True)
def schema() -> Iterator[None]:
    provision_schema(metadata=Base.metadata, schema=SCHEMA, create_postgis=False)
    yield
    drop_schema(schema=SCHEMA)


@pytest.fixture(autouse=True)
async def clean_state(engine: AsyncEngine, schema: None) -> AsyncIterator[None]:
    """Reset tables and the menu cache between tests."""
    await truncate_all(engine, metadata=Base.metadata)
    client = build_redis(TEST_REDIS_DSN, service_name="marsool-catalog-tests")
    await client.flushdb()
    await client.aclose()
    yield


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = build_redis(TEST_REDIS_DSN, service_name="marsool-catalog-tests")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def runtime(
    settings: CatalogSettings,
    engine: AsyncEngine,
    redis_client: Redis,
    event_bus: InMemoryEventBus,
) -> CatalogRuntime:
    return CatalogRuntime(settings, engine=engine, redis=redis_client, event_bus=event_bus)


@pytest.fixture
def merchant_staff() -> Principal:
    return make_principal(role=Role.MERCHANT, merchant_ids=(MERCHANT_ID,))


@pytest.fixture
async def client(settings: CatalogSettings, runtime: CatalogRuntime) -> AsyncIterator[AsyncClient]:
    """A client authenticated as a customer. Menu reads are public regardless."""
    service = build_service(settings)
    service.app.state.runtime = runtime
    service.app.dependency_overrides[guards.verifier] = lambda: make_principal(role=Role.CUSTOMER)
    transport = ASGITransport(app=service.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://catalog.test") as http_client:
        yield http_client


@pytest.fixture
async def anonymous_client(
    settings: CatalogSettings, runtime: CatalogRuntime
) -> AsyncIterator[AsyncClient]:
    """A client with real authentication in place and no credentials supplied."""
    service = build_service(settings)
    service.app.state.runtime = runtime
    transport = ASGITransport(app=service.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://catalog.test") as http_client:
        yield http_client


@pytest.fixture
async def client_as(
    settings: CatalogSettings, runtime: CatalogRuntime
) -> AsyncIterator[Callable[[Principal], Awaitable[AsyncClient]]]:
    """Factory for a client authenticated as an arbitrary principal."""
    clients: list[AsyncClient] = []

    async def _build(principal: Principal) -> AsyncClient:
        service = build_service(settings)
        service.app.state.runtime = runtime
        service.app.dependency_overrides[guards.verifier] = lambda: principal
        http_client = AsyncClient(
            transport=ASGITransport(app=service.app, raise_app_exceptions=False),
            base_url="http://catalog.test",
        )
        clients.append(http_client)
        return http_client

    try:
        yield _build
    finally:
        for http_client in clients:
            await http_client.aclose()


@pytest.fixture
def seed_menu(engine: AsyncEngine) -> Callable[..., Awaitable[dict[str, str]]]:
    """Persist the sample Emirati menu for a merchant and return its id lookup.

    Keys are of the form ``"item:Karak Chai"`` and ``"option:Large"``, so tests refer to
    catalogue rows by name rather than by generated identifier.
    """

    async def _seed(merchant_id: str = MERCHANT_ID) -> dict[str, str]:
        async with session_factory_for(engine)() as session:
            lookup = await seed_menu_data(session, merchant_id=merchant_id)
            await session.commit()
            return lookup

    return _seed
