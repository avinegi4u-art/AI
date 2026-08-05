"""Event envelope, topic registry and bus behaviour."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from marsool_core.context import bind_context
from marsool_core.events import (
    EventEnvelope,
    EventType,
    InMemoryEventBus,
    NoopEventBus,
    Topic,
    new_envelope,
    topic_for,
)


class _OrderCreatedData(BaseModel):
    order_id: str
    total_amount: str


def test_envelope_defaults_are_populated() -> None:
    envelope = new_envelope(
        event_type=EventType.ORDER_CREATED,
        aggregate_id="ord_777",
        aggregate_type="Order",
        data={"total_amount": "84.00"},
    )
    assert envelope.event_id.startswith("evt_")
    assert envelope.occurred_at.tzinfo is not None
    assert envelope.version == 1
    assert envelope.partition_key == "ord_777"


def test_envelope_inherits_ambient_correlation_id() -> None:
    with bind_context(correlation_id="corr_abc"):
        envelope = new_envelope(
            event_type=EventType.ORDER_CONFIRMED,
            aggregate_id="ord_777",
            aggregate_type="Order",
            data={},
        )
    assert envelope.correlation_id == "corr_abc"


def test_envelope_accepts_pydantic_payload() -> None:
    envelope = new_envelope(
        event_type=EventType.ORDER_CREATED,
        aggregate_id="ord_777",
        aggregate_type="Order",
        data=_OrderCreatedData(order_id="ord_777", total_amount="84.00"),
    )
    assert envelope.data == {"order_id": "ord_777", "total_amount": "84.00"}


def test_envelope_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        EventEnvelope(
            event_type="order.created",
            aggregate_id="ord_1",
            aggregate_type="Order",
            occurred_at=datetime(2026, 8, 5, 19, 0, 0),
        )


def test_envelope_rejects_unknown_fields() -> None:
    # ``extra="forbid"`` catches typos in event construction at the boundary.
    with pytest.raises(ValidationError):
        EventEnvelope(
            event_type="order.created",
            aggregate_id="ord_1",
            aggregate_type="Order",
            unexpected="field",
        )


def test_envelope_json_roundtrip() -> None:
    original = new_envelope(
        event_type=EventType.ORDER_DELIVERED,
        aggregate_id="ord_777",
        aggregate_type="Order",
        data={"delivered_at": datetime(2026, 8, 5, 19, 0, tzinfo=UTC).isoformat()},
    )
    assert EventEnvelope.model_validate(original.to_json_dict()) == original


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        (EventType.ORDER_CREATED, Topic.ORDER),
        (EventType.DISPATCH_ASSIGNED, Topic.DISPATCH),
        (EventType.PAYMENT_CAPTURED, Topic.PAYMENT),
        (EventType.TRACKING_ETA_UPDATED, Topic.TRACKING),
        (EventType.NOTIFICATION_ORDER_CONFIRMED, Topic.NOTIFICATION),
        (EventType.USER_REGISTERED, Topic.IDENTITY),
        (EventType.MENU_ITEM_UPDATED, Topic.CATALOG),
    ],
)
def test_every_event_type_maps_to_its_topic(event_type: EventType, expected: Topic) -> None:
    assert topic_for(event_type) is expected


def test_all_registered_event_types_resolve() -> None:
    # Contract guard: adding an event type without registering its topic prefix fails here
    # rather than at runtime in production.
    for event_type in EventType:
        assert isinstance(topic_for(event_type), Topic)


def test_unregistered_prefix_rejected() -> None:
    with pytest.raises(ValueError, match="no topic registered"):
        topic_for("billing.invoice_issued")


def test_dead_letter_topic_naming() -> None:
    assert Topic.ORDER.dead_letter == "order.events.dlq"


async def test_in_memory_bus_records_and_fans_out() -> None:
    bus = InMemoryEventBus()
    received: list[EventEnvelope] = []
    bus.subscribe(Topic.ORDER, lambda envelope: _append(received, envelope))

    envelope = new_envelope(
        event_type=EventType.ORDER_CREATED,
        aggregate_id="ord_777",
        aggregate_type="Order",
        data={},
    )
    await bus.publish(envelope)

    assert list(bus.published) == [envelope]
    assert received == [envelope]
    assert bus.events_of_type("order.created") == [envelope]
    assert bus.events_of_type("order.delivered") == []

    bus.clear()
    assert bus.published == ()


async def test_in_memory_bus_does_not_deliver_across_topics() -> None:
    bus = InMemoryEventBus()
    received: list[EventEnvelope] = []
    bus.subscribe(Topic.PAYMENT, lambda envelope: _append(received, envelope))
    await bus.publish(
        new_envelope(
            event_type=EventType.ORDER_CREATED,
            aggregate_id="ord_1",
            aggregate_type="Order",
            data={},
        )
    )
    assert received == []


async def test_in_memory_bus_propagates_handler_failures() -> None:
    bus = InMemoryEventBus()

    async def failing(_envelope: EventEnvelope) -> None:
        raise RuntimeError("consumer is broken")

    bus.subscribe(Topic.ORDER, failing)
    with pytest.raises(RuntimeError, match="consumer is broken"):
        await bus.publish(
            new_envelope(
                event_type=EventType.ORDER_CREATED,
                aggregate_id="ord_1",
                aggregate_type="Order",
                data={},
            )
        )


async def test_noop_bus_is_inert() -> None:
    bus = NoopEventBus()
    await bus.start()
    await bus.publish_all(
        [
            new_envelope(
                event_type=EventType.ORDER_CREATED,
                aggregate_id="ord_1",
                aggregate_type="Order",
                data={},
            )
        ]
    )
    await bus.stop()


async def _append(sink: list[EventEnvelope], envelope: EventEnvelope) -> None:
    sink.append(envelope)
