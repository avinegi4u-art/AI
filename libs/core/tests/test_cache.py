"""Redis-backed cache, rate limiter and idempotency store."""

from __future__ import annotations

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError

from marsool_core.cache import (
    IdempotencyStore,
    JsonCache,
    RateLimiter,
    cache_key,
)
from marsool_core.errors import DependencyUnavailableError, RateLimitedError

pytestmark = pytest.mark.integration


class _BrokenRedis:
    """Redis stand-in that always fails, to exercise degradation paths."""

    def register_script(self, _script: str) -> object:
        async def _fail(*_args: object, **_kwargs: object) -> object:
            raise RedisError("connection refused")

        return _fail

    async def get(self, *_args: object) -> object:
        raise RedisError("connection refused")

    async def set(self, *_args: object, **_kwargs: object) -> object:
        raise RedisError("connection refused")

    async def delete(self, *_args: object) -> object:
        raise RedisError("connection refused")


def test_cache_key_is_namespaced() -> None:
    assert cache_key("catalog", "menu", "mch_1") == "marsool:catalog:menu:mch_1"


async def test_cache_roundtrip(redis_client: Redis) -> None:
    cache = JsonCache(redis_client, name="menu")
    key = cache_key("test", "menu", "mch_1")

    assert await cache.get(key) is None
    await cache.set(key, {"items": [1, 2, 3]}, ttl_seconds=60)
    assert await cache.get(key) == {"items": [1, 2, 3]}

    await cache.delete(key)
    assert await cache.get(key) is None


async def test_cache_discards_corrupt_entries(redis_client: Redis) -> None:
    cache = JsonCache(redis_client, name="menu")
    key = cache_key("test", "corrupt")
    await redis_client.set(key, b"not-json")

    assert await cache.get(key) is None
    # The poisoned entry is evicted so the next read repopulates cleanly.
    assert await redis_client.get(key) is None


async def test_cache_fails_open_when_redis_is_down() -> None:
    cache = JsonCache(_BrokenRedis(), name="menu")  # type: ignore[arg-type]
    assert await cache.get("any-key") is None
    await cache.set("any-key", {"a": 1}, ttl_seconds=60)  # must not raise


async def test_rate_limiter_allows_up_to_capacity_then_blocks(redis_client: Redis) -> None:
    limiter = RateLimiter(redis_client, capacity=5, window_seconds=60)

    for _ in range(5):
        assert (await limiter.check("usr_1")).allowed

    decision = await limiter.check("usr_1")
    assert not decision.allowed
    assert decision.retry_after_seconds >= 1


async def test_rate_limiter_buckets_are_per_identity(redis_client: Redis) -> None:
    limiter = RateLimiter(redis_client, capacity=2, window_seconds=60)
    assert (await limiter.check("usr_1")).allowed
    assert (await limiter.check("usr_1")).allowed
    assert not (await limiter.check("usr_1")).allowed
    # A different caller must be unaffected by the first caller's burst.
    assert (await limiter.check("usr_2")).allowed


async def test_rate_limiter_cost_is_applied(redis_client: Redis) -> None:
    limiter = RateLimiter(redis_client, capacity=10, window_seconds=60)
    assert (await limiter.check("usr_1", cost=9)).allowed
    assert not (await limiter.check("usr_1", cost=5)).allowed


async def test_enforce_raises_with_retry_hint(redis_client: Redis) -> None:
    limiter = RateLimiter(redis_client, capacity=1, window_seconds=60)
    await limiter.enforce("usr_1")
    with pytest.raises(RateLimitedError) as exc_info:
        await limiter.enforce("usr_1")
    assert exc_info.value.status_code == 429
    assert exc_info.value.retry_after_seconds >= 1


async def test_rate_limiter_fails_open_when_redis_is_down() -> None:
    limiter = RateLimiter(_BrokenRedis(), capacity=1, window_seconds=60)  # type: ignore[arg-type]
    # A limiter outage must not become a platform outage.
    assert (await limiter.check("usr_1")).allowed


@pytest.mark.parametrize(("capacity", "window"), [(0, 60), (5, 0)])
async def test_rate_limiter_rejects_invalid_configuration(
    redis_client: Redis, capacity: int, window: int
) -> None:
    with pytest.raises(ValueError, match=">= 1"):
        RateLimiter(redis_client, capacity=capacity, window_seconds=window)


async def test_idempotency_first_claim_wins(redis_client: Redis) -> None:
    store = IdempotencyStore(redis_client)
    assert await store.claim("orders", "key-1") is True
    assert await store.claim("orders", "key-1") is False


async def test_idempotency_replay_returns_stored_response(redis_client: Redis) -> None:
    store = IdempotencyStore(redis_client)
    await store.claim("orders", "key-1")
    # While in progress there is no stored response yet.
    assert await store.stored_response("orders", "key-1") is None

    await store.store_response("orders", "key-1", {"order_id": "ord_777"})
    assert await store.stored_response("orders", "key-1") == {"order_id": "ord_777"}


async def test_idempotency_release_allows_retry(redis_client: Redis) -> None:
    store = IdempotencyStore(redis_client)
    await store.claim("orders", "key-1")
    await store.release("orders", "key-1")
    assert await store.claim("orders", "key-1") is True


async def test_idempotency_fails_closed_when_redis_is_down() -> None:
    store = IdempotencyStore(_BrokenRedis())  # type: ignore[arg-type]
    # Unlike caching and rate limiting, replay protection must never fail open.
    with pytest.raises(DependencyUnavailableError):
        await store.claim("orders", "key-1")
    with pytest.raises(DependencyUnavailableError):
        await store.stored_response("orders", "key-1")
