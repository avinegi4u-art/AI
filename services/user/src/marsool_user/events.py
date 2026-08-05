"""Domain events published by the user service."""

from __future__ import annotations

from marsool_core.events.envelope import EventEnvelope, new_envelope
from marsool_core.events.topics import EventType

AGGREGATE_TYPE = "User"


def profile_updated(
    *, user_id: str, name: str | None, email: str | None, locale: str, dietary_tags: list[str]
) -> EventEnvelope:
    """A user changed their profile.

    Auth consumes this to refresh its denormalised display name; analytics and the
    recommendation pipeline consume it for personalisation features.
    """
    return new_envelope(
        event_type=EventType.USER_PROFILE_UPDATED,
        aggregate_id=user_id,
        aggregate_type=AGGREGATE_TYPE,
        data={
            "user_id": user_id,
            "name": name,
            "email": email,
            "locale": locale,
            "dietary_tags": dietary_tags,
        },
    )


def address_added(
    *,
    user_id: str,
    address_id: str,
    latitude: str,
    longitude: str,
    city: str,
    is_default: bool,
) -> EventEnvelope:
    """A delivery address was added. Consumed by analytics for coverage planning."""
    return new_envelope(
        event_type=EventType.USER_ADDRESS_ADDED,
        aggregate_id=user_id,
        aggregate_type=AGGREGATE_TYPE,
        data={
            "user_id": user_id,
            "address_id": address_id,
            "latitude": latitude,
            "longitude": longitude,
            "city": city,
            "is_default": is_default,
        },
    )


def address_updated(*, user_id: str, address_id: str, changed_fields: list[str]) -> EventEnvelope:
    """A delivery address changed.

    Only the changed field *names* travel with the event: an address is personal data, and
    a topic consumed by analytics should not carry more of it than consumers need.
    """
    return new_envelope(
        event_type=EventType.USER_ADDRESS_UPDATED,
        aggregate_id=user_id,
        aggregate_type=AGGREGATE_TYPE,
        data={
            "user_id": user_id,
            "address_id": address_id,
            "changed_fields": changed_fields,
        },
    )


def address_deleted(*, user_id: str, address_id: str) -> EventEnvelope:
    """A delivery address was removed."""
    return new_envelope(
        event_type=EventType.USER_ADDRESS_DELETED,
        aggregate_id=user_id,
        aggregate_type=AGGREGATE_TYPE,
        data={"user_id": user_id, "address_id": address_id},
    )
