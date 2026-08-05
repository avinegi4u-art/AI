"""Domain events published by the catalog service."""

from __future__ import annotations

from marsool_core.events.envelope import EventEnvelope, new_envelope
from marsool_core.events.topics import EventType


def item_availability_changed(
    *, item_id: str, merchant_id: str, is_available: bool, reason: str | None
) -> EventEnvelope:
    """An item went in or out of stock.

    Consumed by analytics (to measure lost demand) and by the merchant ops agent, which
    watches for items that keep selling out and suggests prep-time or menu changes.
    """
    return new_envelope(
        event_type=EventType.MENU_ITEM_AVAILABILITY_CHANGED,
        aggregate_id=item_id,
        aggregate_type="MenuItem",
        data={
            "item_id": item_id,
            "merchant_id": merchant_id,
            "is_available": is_available,
            "reason": reason,
        },
    )


def item_updated(*, item_id: str, merchant_id: str, changed_fields: list[str]) -> EventEnvelope:
    """An item's details changed."""
    return new_envelope(
        event_type=EventType.MENU_ITEM_UPDATED,
        aggregate_id=item_id,
        aggregate_type="MenuItem",
        data={
            "item_id": item_id,
            "merchant_id": merchant_id,
            "changed_fields": changed_fields,
        },
    )
