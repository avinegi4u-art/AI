"""Redis-backed caching, rate limiting and idempotency.

All three concerns share one Redis connection pool. Every operation degrades gracefully:
if Redis is unavailable, caching misses and rate limiting fails *open* (logged), because
a cache outage must not take the ordering flow down. Idempotency is the exception — it
fails closed, since replaying a payment or order creation is worse than a 503.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Final

import orjson
from redis.asyncio import Redis
from redis.exceptions import RedisError

from marsool_core.errors import DependencyUnavailableError, RateLimitedError
from marsool_core.logging import get_logger

logger = get_logger(__name__)

_KEY_NAMESPACE: Final = "marsool"

# Token-bucket rate limiter. Implemented in Lua so the read-modify-write is atomic
# under concurrency; separate GET/SET calls would let bursts slip through.
_RATE_LIMIT_LUA: Final = """
local tokens_key = KEYS[1]
local timestamp_key = KEYS[2]
local capacity = tonumber(ARGV[1])
local refill_per_second = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local requested = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local tokens = tonumber(redis.call('get', tokens_key))
local last_refill = tonumber(redis.call('get', timestamp_key))
if tokens == nil then
  tokens = capacity
  last_refill = now
end

local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_per_second)

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end

redis.call('setex', tokens_key, ttl, tokens)
redis.call('setex', timestamp_key, ttl, now)

local retry_after = 0
if allowed == 0 and refill_per_second > 0 then
  retry_after = math.ceil((requested - tokens) / refill_per_second)
end
return {allowed, tokens, retry_after}
"""


def build_redis(dsn: str, *, service_name: str) -> Redis:
    """Create a Redis client with sane timeouts for request-path use."""
    return Redis.from_url(
        dsn,
        decode_responses=False,
        socket_timeout=1.0,
        socket_connect_timeout=1.0,
        health_check_interval=30,
        client_name=service_name,
    )


def cache_key(*parts: str | int) -> str:
    """Build a namespaced cache key, e.g. ``marsool:catalog:menu:mch_123``."""
    return ":".join([_KEY_NAMESPACE, *(str(part) for part in parts)])


class JsonCache:
    """A small JSON cache with fail-open semantics."""

    def __init__(self, redis: Redis, *, name: str, metrics: Any | None = None) -> None:
        self._redis = redis
        self._name = name
        self._metrics = metrics

    def _record(self, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.observe_cache(self._name, outcome=outcome)

    async def get(self, key: str) -> Any | None:
        """Return the cached value, or None on a miss or Redis failure."""
        try:
            raw = await self._redis.get(key)
        except RedisError as exc:
            logger.warning("cache_unavailable", cache=self._name, key=key, error=str(exc))
            self._record("error")
            return None
        if raw is None:
            self._record("miss")
            return None
        self._record("hit")
        try:
            return orjson.loads(raw)
        except orjson.JSONDecodeError:
            # A poisoned entry (format change, partial write) should not break callers.
            logger.warning("cache_entry_corrupt", cache=self._name, key=key)
            await self.delete(key)
            return None

    async def set(self, key: str, value: Any, *, ttl_seconds: int) -> None:
        """Cache ``value`` under ``key``; failures are logged and ignored."""
        try:
            await self._redis.set(key, orjson.dumps(value), ex=ttl_seconds)
        except RedisError as exc:
            logger.warning("cache_write_failed", cache=self._name, key=key, error=str(exc))

    async def delete(self, *keys: str) -> None:
        """Invalidate one or more keys."""
        if not keys:
            return
        try:
            await self._redis.delete(*keys)
        except RedisError as exc:
            logger.warning("cache_delete_failed", cache=self._name, error=str(exc))


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of a rate limit check."""

    allowed: bool
    remaining_tokens: float
    retry_after_seconds: int


class RateLimiter:
    """Distributed token-bucket rate limiter.

    Fails open on Redis errors: rejecting all traffic because the limiter is down would
    turn a cache outage into a full outage.
    """

    def __init__(self, redis: Redis, *, capacity: int, window_seconds: int) -> None:
        if capacity < 1 or window_seconds < 1:
            raise ValueError("capacity and window_seconds must be >= 1")
        self._redis = redis
        self._capacity = capacity
        self._refill_per_second = capacity / window_seconds
        # Keep idle buckets around for two windows so a returning caller is not
        # immediately granted a fresh full bucket.
        self._ttl = window_seconds * 2
        self._script = redis.register_script(_RATE_LIMIT_LUA)

    async def check(self, identity: str, *, cost: int = 1) -> RateLimitDecision:
        """Consume ``cost`` tokens for ``identity``."""
        now = time.time()
        try:
            allowed, tokens, retry_after = await self._script(
                keys=[
                    cache_key("ratelimit", identity, "t"),
                    cache_key("ratelimit", identity, "ts"),
                ],
                args=[self._capacity, self._refill_per_second, now, cost, self._ttl],
            )
        except RedisError as exc:
            logger.warning("rate_limiter_unavailable", identity=identity, error=str(exc))
            return RateLimitDecision(True, float(self._capacity), 0)
        return RateLimitDecision(
            allowed=bool(int(allowed)),
            remaining_tokens=float(tokens),
            retry_after_seconds=int(retry_after),
        )

    async def enforce(self, identity: str, *, cost: int = 1) -> RateLimitDecision:
        """Consume tokens and raise :class:`RateLimitedError` when the bucket is empty."""
        decision = await self.check(identity, cost=cost)
        if not decision.allowed:
            raise RateLimitedError(
                "Too many requests; please slow down",
                retry_after_seconds=max(decision.retry_after_seconds, 1),
            )
        return decision


class AttemptCounter:
    """Fixed-window counter for bounded retry budgets, such as OTP verification.

    Why Redis rather than a database column: the counter must survive the rollback of the
    request that failed. A request-scoped database transaction is rolled back when the
    handler raises, which would discard an incremented column and leave the retry budget
    permanently at zero used — the exact bug this class exists to avoid.

    Fails **closed**: if Redis is unavailable the budget is reported as exhausted. An
    unbounded number of guesses against a six-digit code is a credential-stuffing hole,
    so refusing logins is the safer failure mode here (unlike caching and rate limiting,
    which fail open).
    """

    def __init__(
        self, redis: Redis, *, namespace: str, limit: int, ttl_seconds: int
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._redis = redis
        self._namespace = namespace
        self._limit = limit
        self._ttl = ttl_seconds

    @property
    def limit(self) -> int:
        return self._limit

    def _key(self, identity: str) -> str:
        return cache_key("attempts", self._namespace, identity)

    async def register_failure(self, identity: str) -> int:
        """Record one failed attempt and return the running total."""
        key = self._key(identity)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, self._ttl)
                count, _ = await pipe.execute()
        except RedisError as exc:
            logger.warning("attempt_counter_unavailable", namespace=self._namespace, error=str(exc))
            return self._limit
        return int(count)

    async def is_exhausted(self, identity: str) -> bool:
        """Return True when ``identity`` has used its whole budget."""
        try:
            raw = await self._redis.get(self._key(identity))
        except RedisError as exc:
            logger.warning("attempt_counter_unavailable", namespace=self._namespace, error=str(exc))
            return True
        return raw is not None and int(raw) >= self._limit

    async def reset(self, identity: str) -> None:
        """Clear the budget, e.g. after a successful verification."""
        try:
            await self._redis.delete(self._key(identity))
        except RedisError as exc:
            logger.warning(
                "attempt_counter_reset_failed", namespace=self._namespace, error=str(exc)
            )


class IdempotencyStore:
    """Stores responses for ``Idempotency-Key`` replay protection.

    Fails closed: if Redis is unreachable we cannot prove a request is not a replay, so
    mutating endpoints return 503 rather than risk a duplicate order or charge.
    """

    def __init__(self, redis: Redis, *, ttl_seconds: int = 86_400) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def _key(scope: str, key: str) -> str:
        return cache_key("idempotency", scope, key)

    async def claim(self, scope: str, key: str) -> bool:
        """Attempt to claim ``key``. Returns True if this is the first use."""
        try:
            claimed = await self._redis.set(
                self._key(scope, key), b"__in_progress__", nx=True, ex=self._ttl
            )
        except RedisError as exc:
            raise DependencyUnavailableError(
                "Cannot verify request idempotency", code="idempotency_unavailable"
            ) from exc
        return bool(claimed)

    async def stored_response(self, scope: str, key: str) -> Any | None:
        """Return the stored response for a replayed key, or None if still in progress."""
        try:
            raw = await self._redis.get(self._key(scope, key))
        except RedisError as exc:
            raise DependencyUnavailableError(
                "Cannot verify request idempotency", code="idempotency_unavailable"
            ) from exc
        if raw is None or raw == b"__in_progress__":
            return None
        return orjson.loads(raw)

    async def store_response(self, scope: str, key: str, response: Any) -> None:
        """Persist the response so replays return the original result."""
        try:
            await self._redis.set(self._key(scope, key), orjson.dumps(response), ex=self._ttl)
        except RedisError as exc:
            logger.warning("idempotency_store_failed", scope=scope, error=str(exc))

    async def release(self, scope: str, key: str) -> None:
        """Release a claim so a failed request can be retried with the same key."""
        try:
            await self._redis.delete(self._key(scope, key))
        except RedisError as exc:
            logger.warning("idempotency_release_failed", scope=scope, error=str(exc))
