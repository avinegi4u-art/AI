"""Gateway runtime: owns the upstream client, Redis and the rate limiter."""

from __future__ import annotations

import httpx
from redis.asyncio import Redis

from marsool_core.cache import RateLimiter, build_redis
from marsool_core.logging import get_logger
from marsool_gateway.config import GatewaySettings
from marsool_gateway.proxy import build_upstream_client

logger = get_logger(__name__)


class GatewayRuntime:
    """Long-lived resources for the gateway."""

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        redis: Redis | None = None,
        upstream_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.redis = redis or build_redis(
            str(settings.redis_dsn), service_name=settings.service_name
        )
        self.upstream_client = upstream_client or build_upstream_client(
            connect_timeout=settings.upstream_connect_timeout_seconds,
            read_timeout=settings.upstream_read_timeout_seconds,
            max_connections=settings.upstream_max_connections,
        )
        # Two buckets with different capacities: an authenticated user gets their own
        # allowance, while anonymous callers share one keyed by IP, which is coarser and
        # therefore tighter.
        self.user_rate_limiter = RateLimiter(
            self.redis,
            capacity=settings.rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )
        self.anonymous_rate_limiter = RateLimiter(
            self.redis,
            capacity=settings.anonymous_rate_limit_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )

    async def stop(self) -> None:
        await self.upstream_client.aclose()
        await self.redis.aclose()
        logger.info("gateway_runtime_stopped", service=self.settings.service_name)
