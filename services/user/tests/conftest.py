"""Fixtures for the user service test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
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
    truncate_all,
)
from marsool_user import SCHEMA
from marsool_user.config import UserSettings
from marsool_user.deps import guards
from marsool_user.main import build_service
from marsool_user.models import Base
from marsool_user.runtime import UserRuntime


@pytest.fixture(scope="session")
def settings() -> UserSettings:
    return UserSettings()


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
    client = build_redis(TEST_REDIS_DSN, service_name="marsool-user-tests")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def customer() -> Principal:
    return make_principal(role=Role.CUSTOMER)


@pytest.fixture
async def runtime(
    settings: UserSettings,
    engine: AsyncEngine,
    redis_client: Redis,
    event_bus: InMemoryEventBus,
) -> UserRuntime:
    return UserRuntime(settings, engine=engine, redis=redis_client, event_bus=event_bus)


@pytest.fixture
async def client(
    settings: UserSettings, runtime: UserRuntime, customer: Principal
) -> AsyncIterator[AsyncClient]:
    """A client authenticated as ``customer``.

    Authentication itself is covered by the core and auth suites, so it is stubbed here
    with a dependency override; these tests are about user-domain behaviour.
    """
    service = build_service(settings)
    service.app.state.runtime = runtime
    service.app.dependency_overrides[guards.verifier] = lambda: customer
    transport = ASGITransport(app=service.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://user.test") as http_client:
        yield http_client


@pytest.fixture
async def anonymous_client(
    settings: UserSettings, runtime: UserRuntime
) -> AsyncIterator[AsyncClient]:
    """A client with real authentication in place and no credentials supplied."""
    service = build_service(settings)
    service.app.state.runtime = runtime
    transport = ASGITransport(app=service.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://user.test") as http_client:
        yield http_client


@pytest.fixture
async def client_as(
    settings: UserSettings, runtime: UserRuntime
) -> AsyncIterator[object]:
    """Factory for a client authenticated as an arbitrary principal."""
    clients: list[AsyncClient] = []

    async def _build(principal: Principal) -> AsyncClient:
        service = build_service(settings)
        service.app.state.runtime = runtime
        service.app.dependency_overrides[guards.verifier] = lambda: principal
        http_client = AsyncClient(
            transport=ASGITransport(app=service.app, raise_app_exceptions=False),
            base_url="http://user.test",
        )
        clients.append(http_client)
        return http_client

    try:
        yield _build
    finally:
        for http_client in clients:
            await http_client.aclose()
