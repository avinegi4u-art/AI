"""Event-driven messaging: envelope, topic registry, bus implementations, outbox."""

from marsool_core.events.bus import (
    EventBus,
    EventHandler,
    InMemoryEventBus,
    NoopEventBus,
    build_event_bus,
)
from marsool_core.events.envelope import EventEnvelope, new_envelope
from marsool_core.events.topics import EventType, Topic, topic_for

__all__ = [
    "EventBus",
    "EventEnvelope",
    "EventHandler",
    "EventType",
    "InMemoryEventBus",
    "NoopEventBus",
    "Topic",
    "build_event_bus",
    "new_envelope",
    "topic_for",
]
