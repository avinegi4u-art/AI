"""Event bus abstraction and implementations.

Application code depends only on the :class:`EventBus` protocol. Local development and
tests use :class:`InMemoryEventBus` (synchronous, inspectable, no broker); staging and
production use the Kafka implementation. Swapping backends is a configuration change.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, runtime_checkable

from marsool_core.config import ServiceSettings
from marsool_core.events.envelope import EventEnvelope
from marsool_core.events.topics import Topic, topic_for
from marsool_core.logging import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[EventEnvelope], Awaitable[None]]


@runtime_checkable
class EventBus(Protocol):
    """Publishes domain events. Implementations must be safe for concurrent use."""

    async def publish(self, envelope: EventEnvelope) -> None:
        """Publish a single event to the topic that owns its event type."""
        ...

    async def publish_all(self, envelopes: Sequence[EventEnvelope]) -> None:
        """Publish a batch of events."""
        ...

    async def start(self) -> None:
        """Acquire resources (connections, background tasks)."""
        ...

    async def stop(self) -> None:
        """Flush and release resources."""
        ...


class NoopEventBus:
    """Discards events. Used in unit tests that do not assert on publishing."""

    async def publish(self, envelope: EventEnvelope) -> None:
        return None

    async def publish_all(self, envelopes: Sequence[EventEnvelope]) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class InMemoryEventBus:
    """In-process bus that records published events and invokes local subscribers.

    Handler failures are logged and re-raised so that tests surface broken consumers
    instead of silently passing.
    """

    def __init__(self) -> None:
        self._published: list[EventEnvelope] = []
        self._subscribers: dict[Topic, list[EventHandler]] = defaultdict(list)

    @property
    def published(self) -> Sequence[EventEnvelope]:
        """All events published so far, in order."""
        return tuple(self._published)

    def events_of_type(self, event_type: str) -> list[EventEnvelope]:
        """Return published events matching ``event_type``."""
        return [event for event in self._published if event.event_type == event_type]

    def clear(self) -> None:
        self._published.clear()

    def subscribe(self, topic: Topic, handler: EventHandler) -> None:
        """Register a local handler for ``topic``."""
        self._subscribers[topic].append(handler)

    async def publish(self, envelope: EventEnvelope) -> None:
        topic = topic_for(envelope.event_type)
        self._published.append(envelope)
        logger.debug(
            "event_published",
            topic=topic.value,
            event_type=envelope.event_type,
            event_id=envelope.event_id,
            aggregate_id=envelope.aggregate_id,
        )
        for handler in self._subscribers[topic]:
            await handler(envelope)

    async def publish_all(self, envelopes: Sequence[EventEnvelope]) -> None:
        for envelope in envelopes:
            await self.publish(envelope)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class KafkaEventBus:
    """Kafka-backed event bus.

    ``aiokafka`` is imported lazily so that services running with the in-memory backend
    (local dev, unit tests, CI) do not need the dependency installed.
    """

    def __init__(
        self,
        *,
        bootstrap_servers: str,
        client_id: str,
        acks: str = "all",
        linger_ms: int = 5,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._client_id = client_id
        self._acks = acks
        self._linger_ms = linger_ms
        self._producer: object | None = None

    async def start(self) -> None:
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            client_id=self._client_id,
            acks=self._acks,
            linger_ms=self._linger_ms,
            enable_idempotence=True,
            value_serializer=lambda payload: payload,
        )
        await producer.start()
        self._producer = producer
        logger.info("kafka_producer_started", bootstrap_servers=self._bootstrap_servers)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.stop()  # type: ignore[attr-defined]
            self._producer = None
            logger.info("kafka_producer_stopped")

    async def publish(self, envelope: EventEnvelope) -> None:
        import orjson

        if self._producer is None:
            raise RuntimeError("KafkaEventBus.start() must be awaited before publishing")
        topic = topic_for(envelope.event_type)
        await self._producer.send_and_wait(  # type: ignore[attr-defined]
            topic.value,
            value=orjson.dumps(envelope.to_json_dict()),
            key=envelope.partition_key.encode("utf-8"),
            headers=[
                ("event_type", envelope.event_type.encode("utf-8")),
                ("event_id", envelope.event_id.encode("utf-8")),
                ("correlation_id", (envelope.correlation_id or "").encode("utf-8")),
            ],
        )

    async def publish_all(self, envelopes: Sequence[EventEnvelope]) -> None:
        for envelope in envelopes:
            await self.publish(envelope)


def build_event_bus(settings: ServiceSettings) -> EventBus:
    """Construct the event bus configured for this service."""
    backend = settings.event_bus_backend
    if backend == "kafka":
        return KafkaEventBus(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            client_id=settings.kafka_client_id or settings.service_name,
        )
    if backend == "noop":
        return NoopEventBus()
    return InMemoryEventBus()
