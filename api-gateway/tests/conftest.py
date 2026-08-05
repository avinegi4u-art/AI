"""Fixtures for the gateway test suite.

Upstreams are stubbed with an ``httpx.MockTransport`` that records what the gateway sent.
That is exactly the surface under test: routing, header handling, auth and rate limiting.
Running four real services here would test them again, not the gateway.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis

from marsool_core.cache import build_redis
from marsool_core.security.principal import Principal, Role
from marsool_core.testing import TEST_REDIS_DSN, bearer_header, make_principal
from marsool_gateway.config import GatewaySettings
from marsool_gateway.main import build_service
from marsool_gateway.runtime import GatewayRuntime


@dataclass
class UpstreamRecorder:
    """Records the requests the gateway forwarded."""

    requests: list[httpx.Request] = field(default_factory=list)
    status_code: int = 200
    payload: dict[str, object] = field(default_factory=lambda: {"ok": True})
    response_headers: dict[str, str] = field(default_factory=dict)
    raise_error: Exception | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        return httpx.Response(
            self.status_code,
            content=json.dumps(self.payload).encode(),
            headers={"content-type": "application/json", **self.response_headers},
        )

    @property
    def last(self) -> httpx.Request:
        if not self.requests:
            raise AssertionError("the gateway did not forward any request upstream")
        return self.requests[-1]

    @property
    def forwarded_count(self) -> int:
        return len(self.requests)


@pytest.fixture(scope="session")
def settings() -> GatewaySettings:
    return GatewaySettings(
        auth_service_url="http://auth.internal",
        user_service_url="http://user.internal",
        merchant_service_url="http://merchant.internal",
        catalog_service_url="http://catalog.internal",
    )


@pytest.fixture
def upstream() -> UpstreamRecorder:
    return UpstreamRecorder()


@pytest.fixture(autouse=True)
def _flush_rate_limits() -> Iterator[None]:
    """Clear rate-limit buckets so one test's traffic cannot exhaust another's."""
    import asyncio  # noqa: PLC0415 - sync fixture bridging into async Redis

    async def _flush() -> None:
        client = build_redis(TEST_REDIS_DSN, service_name="marsool-gateway-tests")
        await client.flushdb()
        await client.aclose()

    asyncio.run(_flush())
    yield


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = build_redis(TEST_REDIS_DSN, service_name="marsool-gateway-tests")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def runtime(
    settings: GatewaySettings, redis_client: Redis, upstream: UpstreamRecorder
) -> GatewayRuntime:
    return GatewayRuntime(
        settings,
        redis=redis_client,
        upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle)),
    )


@pytest.fixture
async def client(settings: GatewaySettings, runtime: GatewayRuntime) -> AsyncIterator[AsyncClient]:
    service = build_service(settings)
    service.app.state.runtime = runtime
    transport = ASGITransport(app=service.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://gateway.test") as http_client:
        yield http_client


@pytest.fixture
def client_factory(
    settings: GatewaySettings, redis_client: Redis, upstream: UpstreamRecorder
) -> Callable[..., Awaitable[AsyncClient]]:
    """Build a gateway client with overridden settings."""
    clients: list[AsyncClient] = []

    async def _build(**overrides: object) -> AsyncClient:
        overridden = settings.model_copy(update=overrides)
        runtime = GatewayRuntime(
            overridden,
            redis=redis_client,
            upstream_client=httpx.AsyncClient(transport=httpx.MockTransport(upstream.handle)),
        )
        service = build_service(overridden)
        service.app.state.runtime = runtime
        http_client = AsyncClient(
            transport=ASGITransport(app=service.app, raise_app_exceptions=False),
            base_url="http://gateway.test",
        )
        clients.append(http_client)
        return http_client

    return _build


@pytest.fixture
def customer() -> Principal:
    return make_principal(role=Role.CUSTOMER)


@pytest.fixture
def auth_headers(customer: Principal) -> dict[str, str]:
    return bearer_header(customer)
