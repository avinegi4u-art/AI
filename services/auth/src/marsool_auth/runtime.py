"""Auth service runtime: owns long-lived resources.

One :class:`AuthRuntime` is created during application startup and stored on
``app.state.runtime``; dependencies read it from the request. Tests construct a runtime
bound to the test engine, so there is no module-level mutable state to reset.
"""

from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_auth import CONSUMER_GROUP
from marsool_auth.config import AuthSettings
from marsool_auth.consumers import build_router
from marsool_auth.models import EventOutbox, ProcessedEvent
from marsool_auth.otp import LoggingOtpSender, OtpSender
from marsool_auth.service import AuthService
from marsool_core.cache import AttemptCounter, RateLimiter, build_redis
from marsool_core.db.session import Database
from marsool_core.events.bus import EventBus, build_event_bus
from marsool_core.events.consumer import EventRouter, IdempotentConsumption
from marsool_core.events.outbox import OutboxRelay, OutboxRepository
from marsool_core.logging import get_logger
from marsool_core.observability import ServiceMetrics

logger = get_logger(__name__)


class AuthRuntime:
    """Long-lived resources for the auth service."""

    def __init__(
        self,
        settings: AuthSettings,
        *,
        engine: AsyncEngine | None = None,
        redis: Redis | None = None,
        event_bus: EventBus | None = None,
        otp_sender: OtpSender | None = None,
        metrics: ServiceMetrics | None = None,
    ) -> None:
        self.settings = settings
        self.database = Database(settings, engine=engine)
        self.redis = redis or build_redis(
            str(settings.redis_dsn), service_name=settings.service_name
        )
        self.event_bus = event_bus or build_event_bus(settings)
        self.metrics = metrics
        self.outbox = OutboxRepository(EventOutbox)

        self.otp_rate_limiter = RateLimiter(
            self.redis,
            capacity=settings.otp_requests_per_hour,
            window_seconds=3600,
        )
        self.otp_attempt_counter = AttemptCounter(
            self.redis,
            namespace="otp",
            limit=settings.otp_max_attempts,
            ttl_seconds=settings.otp_ttl_seconds,
        )
        self.auth_service = AuthService(
            settings=settings,
            otp_sender=otp_sender or LoggingOtpSender(),
            outbox=self.outbox,
            session_factory=self.database.session_factory,
            attempt_counter=self.otp_attempt_counter,
            rate_limiter=self.otp_rate_limiter,
        )
        self.outbox_relay = OutboxRelay(
            model=EventOutbox,
            session_factory=self.database.session_factory,
            event_bus=self.event_bus,
        )
        self.event_router: EventRouter = build_router(
            session_factory=self.database.session_factory,
            idempotency=IdempotentConsumption(ProcessedEvent, consumer_group=CONSUMER_GROUP),
            consumer_group=CONSUMER_GROUP,
            metrics=metrics,
        )

    async def start(self) -> None:
        await self.event_bus.start()
        logger.info("auth_runtime_started", service=self.settings.service_name)

    async def stop(self) -> None:
        self.outbox_relay.stop()
        await self.event_bus.stop()
        await self.redis.aclose()
        await self.database.dispose()
        logger.info("auth_runtime_stopped", service=self.settings.service_name)
