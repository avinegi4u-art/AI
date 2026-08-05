"""Event-driven messaging: envelope, topic registry, bus implementations, outbox."""

from marsool_core.events.bus import (
    EventBus,
    EventHandler,
    InMemoryEventBus,
    NoopEventBus,
    build_event_bus,
)
from marsool_core.events.consumer import (
    EventRouter,
    IdempotentConsumption,
    build_processed_events_model,
)
from marsool_core.events.envelope import EventEnvelope, new_envelope
from marsool_core.events.outbox import OutboxRelay, OutboxRepository, build_outbox_model
from marsool_core.events.topics import EventType, Topic, topic_for

__all__ = [
    "EventBus",
    "EventEnvelope",
    "EventHandler",
    "EventRouter",
    "EventType",
    "IdempotentConsumption",
    "InMemoryEventBus",
    "NoopEventBus",
    "OutboxRelay",
    "OutboxRepository",
    "Topic",
    "build_event_bus",
    "build_outbox_model",
    "build_processed_events_model",
    "new_envelope",
    "topic_for",
]
