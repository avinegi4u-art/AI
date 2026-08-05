"""User service runtime: owns long-lived resources."""

from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_core.cache import build_redis
from marsool_core.db.session import Database
from marsool_core.events.bus import EventBus, build_event_bus
from marsool_core.events.consumer import EventRouter, IdempotentConsumption
from marsool_core.events.outbox import OutboxRelay, OutboxRepository
from marsool_core.logging import get_logger
from marsool_core.observability import ServiceMetrics
from marsool_user import CONSUMER_GROUP
from marsool_user.config import UserSettings
from marsool_user.consumers import build_router
from marsool_user.models import EventOutbox, ProcessedEvent
from marsool_user.service import UserService

logger = get_logger(__name__)


class UserRuntime:
    """Long-lived resources for the user service."""

    def __init__(
        self,
        settings: UserSettings,
        *,
        engine: AsyncEngine | None = None,
        redis: Redis | None = None,
        event_bus: EventBus | None = None,
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
        self.user_service = UserService(settings=settings, outbox=self.outbox)
        self.outbox_relay = OutboxRelay(
            model=EventOutbox,
            session_factory=self.database.session_factory,
            event_bus=self.event_bus,
        )
        self.event_router: EventRouter = build_router(
            session_factory=self.database.session_factory,
            idempotency=IdempotentConsumption(ProcessedEvent, consumer_group=CONSUMER_GROUP),
            consumer_group=CONSUMER_GROUP,
            user_service=self.user_service,
            metrics=metrics,
        )

    async def start(self) -> None:
        await self.event_bus.start()
        logger.info("user_runtime_started", service=self.settings.service_name)

    async def stop(self) -> None:
        self.outbox_relay.stop()
        await self.event_bus.stop()
        await self.redis.aclose()
        await self.database.dispose()
        logger.info("user_runtime_stopped", service=self.settings.service_name)
