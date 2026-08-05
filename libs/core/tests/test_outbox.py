"""Transactional outbox behaviour against a real PostgreSQL instance."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase

from marsool_core.db.base import metadata_for_schema
from marsool_core.events import EventType, InMemoryEventBus, new_envelope
from marsool_core.events.envelope import EventEnvelope
from marsool_core.events.outbox import (
    MAX_PUBLISH_ATTEMPTS,
    OutboxRelay,
    OutboxRepository,
    OutboxStatus,
    build_outbox_model,
)
from marsool_core.testing import session_factory_for, temporary_schema

SCHEMA = "core_outbox_test"


class _Base(DeclarativeBase):
    metadata = metadata_for_schema(SCHEMA)


EventOutbox = build_outbox_model(_Base)
repository = OutboxRepository(EventOutbox)

pytestmark = pytest.mark.integration


@pytest.fixture
async def schema(engine: AsyncEngine) -> AsyncIterator[None]:
    async with temporary_schema(
        engine, metadata=_Base.metadata, schema=SCHEMA, create_postgis=False
    ):
        yield


def _envelope(order_id: str = "ord_777") -> EventEnvelope:
    return new_envelope(
        event_type=EventType.ORDER_CREATED,
        aggregate_id=order_id,
        aggregate_type="Order",
        data={"total_amount": "84.00"},
    )


async def _rows(engine: AsyncEngine) -> Sequence[object]:
    async with session_factory_for(engine)() as session:
        return (await session.execute(select(EventOutbox))).scalars().all()


async def test_rollback_discards_the_event(engine: AsyncEngine, schema: None) -> None:
    # The point of the outbox: a failed transaction must not leave a phantom event.
    factory = session_factory_for(engine)
    async with factory() as session:
        repository.enqueue(session, _envelope())
        await session.rollback()
    assert await _rows(engine) == []


async def test_commit_persists_the_event(engine: AsyncEngine, schema: None) -> None:
    envelope = _envelope()
    factory = session_factory_for(engine)
    async with factory() as session:
        repository.enqueue(session, envelope)
        await session.commit()

    rows = await _rows(engine)
    assert len(rows) == 1
    row = rows[0]
    assert row.status is OutboxStatus.PENDING  # type: ignore[attr-defined]
    assert row.to_envelope() == envelope  # type: ignore[attr-defined]


async def test_relay_publishes_pending_events_once(engine: AsyncEngine, schema: None) -> None:
    factory = session_factory_for(engine)
    async with factory() as session:
        repository.enqueue_all(session, [_envelope("ord_1"), _envelope("ord_2")])
        await session.commit()

    bus = InMemoryEventBus()
    relay = OutboxRelay(model=EventOutbox, session_factory=factory, event_bus=bus)

    assert await relay.drain_once() == 2
    # A second pass must be a no-op: published rows are not republished.
    assert await relay.drain_once() == 0
    assert len(bus.published) == 2
    assert {row.status for row in await _rows(engine)} == {OutboxStatus.PUBLISHED}  # type: ignore[attr-defined]


async def test_relay_retries_then_dead_letters(engine: AsyncEngine, schema: None) -> None:
    factory = session_factory_for(engine)
    async with factory() as session:
        repository.enqueue(session, _envelope())
        await session.commit()

    class _BrokenBus(InMemoryEventBus):
        async def publish(self, envelope: EventEnvelope) -> None:
            raise RuntimeError("broker unreachable")

    relay = OutboxRelay(model=EventOutbox, session_factory=factory, event_bus=_BrokenBus())

    for attempt in range(1, MAX_PUBLISH_ATTEMPTS):
        assert await relay.drain_once() == 0
        row = (await _rows(engine))[0]
        assert row.attempts == attempt  # type: ignore[attr-defined]
        assert row.status is OutboxStatus.PENDING  # type: ignore[attr-defined]

    await relay.drain_once()
    row = (await _rows(engine))[0]
    assert row.status is OutboxStatus.DEAD_LETTERED  # type: ignore[attr-defined]
    assert "broker unreachable" in row.last_error  # type: ignore[attr-defined]


async def test_dead_letters_can_be_requeued_and_published(
    engine: AsyncEngine, schema: None
) -> None:
    envelope = _envelope()
    factory = session_factory_for(engine)
    async with factory() as session:
        repository.enqueue(session, envelope)
        await session.commit()

    class _BrokenBus(InMemoryEventBus):
        async def publish(self, _envelope: EventEnvelope) -> None:
            raise RuntimeError("broker unreachable")

    broken_relay = OutboxRelay(model=EventOutbox, session_factory=factory, event_bus=_BrokenBus())
    for _ in range(MAX_PUBLISH_ATTEMPTS):
        await broken_relay.drain_once()

    healthy_bus = InMemoryEventBus()
    healthy_relay = OutboxRelay(
        model=EventOutbox, session_factory=factory, event_bus=healthy_bus
    )
    assert await healthy_relay.requeue_dead_letters([envelope.event_id]) == 1
    assert await healthy_relay.drain_once() == 1
    assert [event.event_id for event in healthy_bus.published] == [envelope.event_id]


async def test_relay_respects_batch_size(engine: AsyncEngine, schema: None) -> None:
    factory = session_factory_for(engine)
    async with factory() as session:
        repository.enqueue_all(session, [_envelope(f"ord_{index}") for index in range(5)])
        await session.commit()

    bus = InMemoryEventBus()
    relay = OutboxRelay(model=EventOutbox, session_factory=factory, event_bus=bus, batch_size=2)
    assert await relay.drain_once() == 2
    assert await relay.drain_once() == 2
    assert await relay.drain_once() == 1
