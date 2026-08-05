"""Catalog service runtime: owns long-lived resources."""

from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_catalog.config import CatalogSettings
from marsool_catalog.models import EventOutbox
from marsool_catalog.service import CatalogService
from marsool_core.cache import JsonCache, build_redis
from marsool_core.db.session import Database
from marsool_core.events.bus import EventBus, build_event_bus
from marsool_core.events.outbox import OutboxRelay, OutboxRepository
from marsool_core.logging import get_logger
from marsool_core.observability import ServiceMetrics

logger = get_logger(__name__)


class CatalogRuntime:
    """Long-lived resources for the catalog service."""

    def __init__(
        self,
        settings: CatalogSettings,
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
        self.menu_cache = JsonCache(self.redis, name="menu", metrics=metrics)
        self.catalog_service = CatalogService(
            settings=settings, outbox=self.outbox, menu_cache=self.menu_cache
        )
        self.outbox_relay = OutboxRelay(
            model=EventOutbox,
            session_factory=self.database.session_factory,
            event_bus=self.event_bus,
        )

    async def start(self) -> None:
        await self.event_bus.start()
        logger.info("catalog_runtime_started", service=self.settings.service_name)

    async def stop(self) -> None:
        self.outbox_relay.stop()
        await self.event_bus.stop()
        await self.redis.aclose()
        await self.database.dispose()
        logger.info("catalog_runtime_stopped", service=self.settings.service_name)
