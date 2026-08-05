"""Fixtures for the auth service test suite.

Tests run against real PostgreSQL and Redis: OTP expiry, row locking, refresh-token
uniqueness and rate limiting are all behaviours of the datastore, and a stubbed store
would verify the stub rather than the system.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_auth import SCHEMA
from marsool_auth.config import AuthSettings
from marsool_auth.main import build_service
from marsool_auth.models import Base
from marsool_auth.runtime import AuthRuntime
from marsool_auth.testing import RecordingOtpSender
from marsool_core.cache import build_redis
from marsool_core.events.bus import InMemoryEventBus
from marsool_core.testing import (
    TEST_REDIS_DSN,
    build_test_engine,
    drop_schema,
    provision_schema,
    truncate_all,
)

#: Default phone number used across the auth test suite.
PHONE = "+971501234567"


@pytest.fixture(scope="session")
def settings() -> AuthSettings:
    return AuthSettings()


@pytest.fixture(scope="session")
def engine() -> AsyncEngine:
    return build_test_engine()


@pytest.fixture(scope="session", autouse=True)
def schema() -> Iterator[None]:
    provision_schema(metadata=Base.metadata, schema=SCHEMA, create_postgis=False)
    yield
    drop_schema(schema=SCHEMA)


@pytest.fixture(autouse=True)
async def clean_tables(engine: AsyncEngine, schema: None) -> AsyncIterator[None]:
    """Start every test from an empty schema and a clean Redis database."""
    await truncate_all(engine, metadata=Base.metadata)
    client = build_redis(TEST_REDIS_DSN, service_name="marsool-auth-tests")
    await client.flushdb()
    await client.aclose()
    yield


@pytest.fixture
def otp_sender() -> RecordingOtpSender:
    return RecordingOtpSender()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = build_redis(TEST_REDIS_DSN, service_name="marsool-auth-tests")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def runtime(
    settings: AuthSettings,
    engine: AsyncEngine,
    redis_client: Redis,
    event_bus: InMemoryEventBus,
    otp_sender: RecordingOtpSender,
) -> AuthRuntime:
    return AuthRuntime(
        settings,
        engine=engine,
        redis=redis_client,
        event_bus=event_bus,
        otp_sender=otp_sender,
    )


@pytest.fixture
async def client(settings: AuthSettings, runtime: AuthRuntime) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the app, with the test runtime injected.

    The app is created without running its lifespan so the test's runtime (test engine,
    test Redis, in-memory bus) is used instead of production resources.
    """
    service = build_service(settings)
    service.app.state.runtime = runtime
    transport = ASGITransport(app=service.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://auth.test") as http_client:
        yield http_client


@pytest.fixture
async def client_factory(
    engine: AsyncEngine,
    redis_client: Redis,
    event_bus: InMemoryEventBus,
    otp_sender: RecordingOtpSender,
) -> AsyncIterator[Callable[..., Awaitable[AsyncClient]]]:
    """Build a client whose service uses overridden settings.

    Used by tests that need a different policy (higher rate limits, smaller session
    ceilings) than the defaults.
    """
    clients: list[AsyncClient] = []

    async def _build(**overrides: Any) -> AsyncClient:
        settings = AuthSettings(**overrides)
        runtime = AuthRuntime(
            settings,
            engine=engine,
            redis=redis_client,
            event_bus=event_bus,
            otp_sender=otp_sender,
        )
        service = build_service(settings)
        service.app.state.runtime = runtime
        http_client = AsyncClient(
            transport=ASGITransport(app=service.app, raise_app_exceptions=False),
            base_url="http://auth.test",
        )
        clients.append(http_client)
        return http_client

    try:
        yield _build
    finally:
        for http_client in clients:
            await http_client.aclose()


@pytest.fixture
def request_otp(
    client: AsyncClient,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Request an OTP and return the challenge payload, asserting success."""

    async def _request(phone: str = PHONE) -> dict[str, Any]:
        response = await client.post("/auth/otp", json={"phone": phone})
        assert response.status_code == 201, response.text
        return dict(response.json())

    return _request


@pytest.fixture
def login(
    client: AsyncClient,
    otp_sender: RecordingOtpSender,
    request_otp: Callable[..., Awaitable[dict[str, Any]]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Complete a full OTP login and return the token pair payload."""

    async def _login(phone: str = PHONE) -> dict[str, Any]:
        await request_otp(phone)
        response = await client.post(
            "/auth/login", json={"phone": phone, "otp": otp_sender.latest_code_for(phone)}
        )
        assert response.status_code == 200, response.text
        return dict(response.json())

    return _login
