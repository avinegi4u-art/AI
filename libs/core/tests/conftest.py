"""Fixtures for the core library test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_core.cache import build_redis
from marsool_core.testing import TEST_REDIS_DSN, build_test_engine


@pytest.fixture(scope="session")
def engine() -> AsyncEngine:
    return build_test_engine()


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = build_redis(TEST_REDIS_DSN, service_name="marsool-core-tests")
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()
