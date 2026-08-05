"""Transactional outbox.

Publishing to Kafka inside a database transaction is not atomic: the broker call can
succeed while the transaction rolls back (phantom event) or vice versa (lost event).
Instead, services write events to an outbox table in the *same* transaction as the
state change, and a relay publishes them afterwards with at-least-once delivery.
Consumers deduplicate on ``event_id``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from marsool_core.db.session import rowcount
from marsool_core.db.types import StringEnum
from marsool_core.events.bus import EventBus
from marsool_core.events.envelope import EventEnvelope
from marsool_core.logging import get_logger

logger = get_logger(__name__)

MAX_PUBLISH_ATTEMPTS = 8


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PUBLISHED = "PUBLISHED"
    DEAD_LETTERED = "DEAD_LETTERED"


def build_outbox_model(base: type[Any], *, table_name: str = "event_outbox") -> type[Any]:
    """Create an outbox ORM model bound to a service's declarative base.

    ``base`` is the service's own ``DeclarativeBase`` subclass (each is bound to a
    different schema), so it cannot be typed more precisely than ``type[Any]``.

    Each service gets its own outbox table inside its own schema, so the relay only
    ever touches tables that service owns.
    """

    class EventOutbox(base):
        """Durable queue of domain events awaiting publication."""

        __tablename__ = table_name
        __abstract__ = False

        event_id: Mapped[str] = mapped_column(String(40), primary_key=True)
        event_type: Mapped[str] = mapped_column(String(80), nullable=False)
        aggregate_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
        aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
        version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
        correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
        causation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
        payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
        occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
        status: Mapped[OutboxStatus] = mapped_column(
            StringEnum(OutboxStatus),
            nullable=False,
            default=OutboxStatus.PENDING,
            index=True,
        )
        attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
        last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
        published_at: Mapped[datetime | None] = mapped_column(
            DateTime(timezone=True), nullable=True
        )
        created_at: Mapped[datetime] = mapped_column(
            DateTime(timezone=True),
            nullable=False,
            default=lambda: datetime.now(UTC),
            index=True,
        )

        def to_envelope(self) -> EventEnvelope:
            return EventEnvelope.model_validate(self.payload)

    return EventOutbox


class OutboxRepository:
    """Enqueues events into a service's outbox table."""

    def __init__(self, model: type[Any]) -> None:
        self._model = model

    def enqueue(self, session: AsyncSession, envelope: EventEnvelope) -> None:
        """Add an event to the outbox within the caller's transaction.

        Deliberately not ``async``: it only stages an ORM object, and the write happens
        when the caller's transaction commits. That is what makes the write atomic with
        the state change.
        """
        session.add(
            self._model(
                event_id=envelope.event_id,
                event_type=envelope.event_type,
                aggregate_id=envelope.aggregate_id,
                aggregate_type=envelope.aggregate_type,
                version=envelope.version,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                payload=envelope.to_json_dict(),
                occurred_at=envelope.occurred_at,
                status=OutboxStatus.PENDING,
            )
        )

    def enqueue_all(self, session: AsyncSession, envelopes: list[EventEnvelope]) -> None:
        for envelope in envelopes:
            self.enqueue(session, envelope)


class OutboxRelay:
    """Polls the outbox and publishes pending events to the bus.

    Rows are claimed with ``FOR UPDATE SKIP LOCKED`` so multiple replicas can run the
    relay concurrently without publishing the same event twice.
    """

    def __init__(
        self,
        *,
        model: type[Any],
        session_factory: Any,
        event_bus: EventBus,
        batch_size: int = 100,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._model = model
        self._session_factory = session_factory
        self._event_bus = event_bus
        self._batch_size = batch_size
        self._poll_interval = poll_interval_seconds
        self._stopped = asyncio.Event()

    async def drain_once(self) -> int:
        """Publish one batch of pending events. Returns the number published."""
        published = 0
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(self._model)
                        .where(self._model.status == OutboxStatus.PENDING)
                        .order_by(self._model.created_at)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                try:
                    await self._event_bus.publish(row.to_envelope())
                except Exception as exc:  # noqa: BLE001 - relay must survive one bad event
                    row.attempts += 1
                    row.last_error = f"{type(exc).__name__}: {exc}"
                    if row.attempts >= MAX_PUBLISH_ATTEMPTS:
                        row.status = OutboxStatus.DEAD_LETTERED
                        logger.error(
                            "outbox_event_dead_lettered",
                            event_id=row.event_id,
                            event_type=row.event_type,
                            attempts=row.attempts,
                            error=row.last_error,
                        )
                    else:
                        logger.warning(
                            "outbox_publish_failed",
                            event_id=row.event_id,
                            attempts=row.attempts,
                            error=row.last_error,
                        )
                else:
                    row.status = OutboxStatus.PUBLISHED
                    row.published_at = datetime.now(UTC)
                    published += 1
            await session.commit()
        return published

    async def run_forever(self) -> None:
        """Continuously drain the outbox until :meth:`stop` is called."""
        logger.info("outbox_relay_started", batch_size=self._batch_size)
        while not self._stopped.is_set():
            try:
                count = await self.drain_once()
            except Exception:
                logger.exception("outbox_relay_iteration_failed")
                count = 0
            if count == 0:
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=self._poll_interval)
                except TimeoutError:
                    continue
        logger.info("outbox_relay_stopped")

    def stop(self) -> None:
        self._stopped.set()

    async def requeue_dead_letters(self, event_ids: list[str]) -> int:
        """Reset dead-lettered events to PENDING after an operator fixes the cause."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(self._model)
                .where(
                    self._model.event_id.in_(event_ids),
                    self._model.status == OutboxStatus.DEAD_LETTERED,
                )
                .values(status=OutboxStatus.PENDING, attempts=0, last_error=None)
            )
            await session.commit()
            return rowcount(result)
