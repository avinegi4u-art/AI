"""Event consumption: routing, idempotency and the Kafka runtime.

Delivery is at-least-once, so consumers must be idempotent. Two mechanisms provide it:

1. Handlers are written to be naturally idempotent (upserts, state-machine guards).
2. A ``processed_events`` table records every ``(event_id, consumer_group)`` pair, so a
   redelivery is skipped instead of applied twice.

Handlers that keep failing are sent to the topic's dead-letter queue rather than
blocking the partition forever.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy import DateTime, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from marsool_core.events.envelope import EventEnvelope
from marsool_core.events.topics import Topic
from marsool_core.logging import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[EventEnvelope], Awaitable[None]]

DEFAULT_MAX_ATTEMPTS = 5


def build_processed_events_model(
    base: type[Any], *, table_name: str = "processed_events"
) -> type[Any]:
    """Create the consumer-side deduplication table for a service.

    ``base`` is the service's own ``DeclarativeBase`` subclass, so it cannot be typed
    more precisely than ``type[Any]``.
    """

    class ProcessedEvent(base):
        """Records events already applied by a consumer group."""

        __tablename__ = table_name
        __abstract__ = False

        event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
        consumer_group: Mapped[str] = mapped_column(String(80), primary_key=True)
        event_type: Mapped[str] = mapped_column(String(80), nullable=False)
        processed_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
        )

    return ProcessedEvent


class IdempotentConsumption:
    """Guards handlers against reprocessing an event.

    The claim is written in the same transaction as the handler's own writes, so either
    both land or neither does — a handler cannot be marked processed if it failed.
    """

    def __init__(self, model: type[Any], *, consumer_group: str) -> None:
        self._model = model
        self._consumer_group = consumer_group

    async def already_processed(self, session: AsyncSession, event_id: str) -> bool:
        result = await session.execute(
            select(self._model.event_id).where(
                self._model.event_id == event_id,
                self._model.consumer_group == self._consumer_group,
            )
        )
        return result.scalar_one_or_none() is not None

    def mark_processed(self, session: AsyncSession, envelope: EventEnvelope) -> None:
        session.add(
            self._model(
                event_id=envelope.event_id,
                consumer_group=self._consumer_group,
                event_type=envelope.event_type,
            )
        )


class EventRouter:
    """Dispatches envelopes to the handlers registered for their event type.

    Unregistered event types are ignored: a service subscribing to a topic will see
    events it does not care about, and that must not be an error.
    """

    def __init__(
        self,
        *,
        consumer_group: str,
        metrics: Any | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.consumer_group = consumer_group
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._metrics = metrics
        self._max_attempts = max_attempts
        self._dead_letters: list[tuple[EventEnvelope, str]] = []

    def on(self, event_type: str, handler: EventHandler) -> None:
        """Register ``handler`` for ``event_type``."""
        self._handlers[str(event_type)].append(handler)

    def handles(self, event_type: str) -> bool:
        return str(event_type) in self._handlers

    @property
    def dead_letters(self) -> list[tuple[EventEnvelope, str]]:
        """Events that exhausted their retry budget, with the failure reason."""
        return list(self._dead_letters)

    def _record(self, envelope: EventEnvelope, outcome: str) -> None:
        if self._metrics is not None:
            self._metrics.observe_event_consumed(envelope.event_type, outcome=outcome)

    async def dispatch(self, envelope: EventEnvelope) -> bool:
        """Invoke every handler for ``envelope``.

        Returns:
            True if a handler ran, False if the event type is not subscribed.

        Raises:
            Exception: Propagated from the handler so the caller can retry or commit.
        """
        handlers = self._handlers.get(envelope.event_type)
        if not handlers:
            self._record(envelope, "ignored")
            return False
        for handler in handlers:
            await handler(envelope)
        self._record(envelope, "success")
        return True

    async def dispatch_with_retry(
        self, envelope: EventEnvelope, *, base_delay_seconds: float = 0.1
    ) -> bool:
        """Dispatch with bounded exponential backoff, dead-lettering on exhaustion.

        Returns:
            True when a handler succeeded, False when the event was ignored or
            dead-lettered.
        """
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self.dispatch(envelope)
            except Exception as exc:  # noqa: BLE001 - one bad event must not stall the partition
                self._record(envelope, "failure")
                if attempt >= self._max_attempts:
                    reason = f"{type(exc).__name__}: {exc}"
                    self._dead_letters.append((envelope, reason))
                    logger.error(
                        "event_dead_lettered",
                        event_id=envelope.event_id,
                        event_type=envelope.event_type,
                        consumer_group=self.consumer_group,
                        attempts=attempt,
                        error=reason,
                    )
                    return False
                logger.warning(
                    "event_handler_failed_retrying",
                    event_id=envelope.event_id,
                    event_type=envelope.event_type,
                    attempt=attempt,
                    error=str(exc),
                )
                await asyncio.sleep(base_delay_seconds * 2 ** (attempt - 1))
        return False


class KafkaEventConsumer:
    """Kafka consumer runtime driving an :class:`EventRouter`.

    Offsets are committed manually after dispatch so that a crash mid-handler causes a
    redelivery (which the idempotency guard absorbs) rather than a lost event.
    """

    def __init__(
        self,
        *,
        router: EventRouter,
        topics: list[Topic],
        bootstrap_servers: str,
        dead_letter_publisher: Callable[[EventEnvelope, str], Awaitable[None]] | None = None,
    ) -> None:
        self._router = router
        self._topics = topics
        self._bootstrap_servers = bootstrap_servers
        self._dead_letter_publisher = dead_letter_publisher
        self._consumer: Any | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        # Lazy: aiokafka is an optional extra, needed only by the Kafka backend.
        from aiokafka import AIOKafkaConsumer  # noqa: PLC0415

        consumer = AIOKafkaConsumer(
            *(topic.value for topic in self._topics),
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._router.consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
        )
        await consumer.start()
        self._consumer = consumer
        logger.info(
            "kafka_consumer_started",
            group=self._router.consumer_group,
            topics=[topic.value for topic in self._topics],
        )

    async def stop(self) -> None:
        self._stopped.set()
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
            logger.info("kafka_consumer_stopped", group=self._router.consumer_group)

    async def run_forever(self) -> None:
        """Consume until :meth:`stop` is called."""
        if self._consumer is None:
            raise RuntimeError("KafkaEventConsumer.start() must be awaited before consuming")
        while not self._stopped.is_set():
            batch = await self._consumer.getmany(timeout_ms=1000, max_records=100)
            for records in batch.values():
                for record in records:
                    envelope = EventEnvelope.model_validate(orjson.loads(record.value))
                    dispatched = await self._router.dispatch_with_retry(envelope)
                    if not dispatched and self._dead_letter_publisher is not None:
                        for dead_envelope, reason in self._router.dead_letters:
                            await self._dead_letter_publisher(dead_envelope, reason)
            if batch:
                await self._consumer.commit()
