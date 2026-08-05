"""The event envelope shared by every topic.

A single envelope shape means consumers can route, log, deduplicate and dead-letter
messages without knowing anything about the payload. ``data`` is the only
event-type-specific part.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marsool_core.context import get_correlation_id
from marsool_core.ids import PREFIX_EVENT, new_id


class EventEnvelope(BaseModel):
    """Envelope wrapping every domain event published to the bus.

    Attributes:
        event_id: Unique event identifier; consumers use it for idempotency.
        event_type: Dotted type such as ``order.created``.
        occurred_at: When the state change happened (not when it was published).
        aggregate_id: Identifier of the aggregate that changed.
        aggregate_type: Aggregate class name, e.g. ``Order``.
        version: Schema version of ``data``; bump on breaking payload changes.
        correlation_id: Propagated request correlation ID for end-to-end tracing.
        causation_id: The ``event_id`` that caused this event, forming a causal chain.
        data: Event-specific payload.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(default_factory=lambda: new_id(PREFIX_EVENT))
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    aggregate_id: str
    aggregate_type: str
    version: int = Field(default=1, ge=1)
    correlation_id: str | None = None
    causation_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_timezone_aware_timestamp(self) -> Self:
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        return self

    @property
    def partition_key(self) -> str:
        """Kafka partition key.

        Keying by aggregate guarantees per-aggregate ordering, which the order state
        machine and dispatch assignment logic both depend on.
        """
        return self.aggregate_id

    def to_json_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict (ISO-8601 timestamps)."""
        return self.model_dump(mode="json")


def new_envelope(
    *,
    event_type: str,
    aggregate_id: str,
    aggregate_type: str,
    data: dict[str, Any] | BaseModel,
    version: int = 1,
    occurred_at: datetime | None = None,
    causation_id: str | None = None,
) -> EventEnvelope:
    """Build an envelope, inheriting the ambient correlation ID."""
    payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    return EventEnvelope(
        event_type=event_type,
        aggregate_id=aggregate_id,
        aggregate_type=aggregate_type,
        version=version,
        occurred_at=occurred_at or datetime.now(UTC),
        correlation_id=get_correlation_id(),
        causation_id=causation_id,
        data=payload,
    )
