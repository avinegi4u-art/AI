"""Domain events published by the merchant service."""

from __future__ import annotations

from marsool_core.events.envelope import EventEnvelope, new_envelope
from marsool_core.events.topics import EventType

AGGREGATE_TYPE = "Merchant"


def merchant_updated(*, merchant_id: str, changed_fields: list[str]) -> EventEnvelope:
    """Storefront details changed. Catalog and search caches use this to invalidate."""
    return new_envelope(
        event_type=EventType.MERCHANT_UPDATED,
        aggregate_id=merchant_id,
        aggregate_type=AGGREGATE_TYPE,
        data={"merchant_id": merchant_id, "changed_fields": changed_fields},
    )


def merchant_status_changed(
    *, merchant_id: str, accepting_orders: bool, status: str, reason: str | None
) -> EventEnvelope:
    """A merchant started or stopped accepting orders.

    Dispatch and the marketplace-health monitor consume this: a cluster of merchants
    pausing at once is an early signal of an operational problem.
    """
    return new_envelope(
        event_type=EventType.MERCHANT_STATUS_CHANGED,
        aggregate_id=merchant_id,
        aggregate_type=AGGREGATE_TYPE,
        data={
            "merchant_id": merchant_id,
            "accepting_orders": accepting_orders,
            "status": status,
            "reason": reason,
        },
    )


def merchant_hours_updated(*, merchant_id: str, window_count: int) -> EventEnvelope:
    """A merchant's weekly schedule was replaced."""
    return new_envelope(
        event_type=EventType.MERCHANT_HOURS_UPDATED,
        aggregate_id=merchant_id,
        aggregate_type=AGGREGATE_TYPE,
        data={"merchant_id": merchant_id, "window_count": window_count},
    )
