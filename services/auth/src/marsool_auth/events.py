"""Domain events published by the auth service."""

from __future__ import annotations

from datetime import datetime

from marsool_core.events.envelope import EventEnvelope, new_envelope
from marsool_core.events.topics import EventType
from marsool_core.security.principal import Role

AGGREGATE_TYPE = "User"


def user_registered(
    *, principal_id: str, phone: str, role: Role, registered_at: datetime
) -> EventEnvelope:
    """First successful login for a phone number created a new account.

    The user service consumes this to provision the profile row.
    """
    return new_envelope(
        event_type=EventType.USER_REGISTERED,
        aggregate_id=principal_id,
        aggregate_type=AGGREGATE_TYPE,
        data={
            "user_id": principal_id,
            "phone": phone,
            "role": role.value,
            "registered_at": registered_at.isoformat(),
        },
    )


def user_logged_in(
    *, principal_id: str, session_id: str, role: Role, logged_in_at: datetime
) -> EventEnvelope:
    """A session was established. Consumed by analytics and fraud monitoring."""
    return new_envelope(
        event_type=EventType.USER_LOGGED_IN,
        aggregate_id=principal_id,
        aggregate_type=AGGREGATE_TYPE,
        data={
            "user_id": principal_id,
            "session_id": session_id,
            "role": role.value,
            "logged_in_at": logged_in_at.isoformat(),
        },
    )


def session_revoked(
    *, principal_id: str, session_ids: list[str], reason: str, revoked_at: datetime
) -> EventEnvelope:
    """One or more sessions were revoked, by logout or by reuse detection."""
    return new_envelope(
        event_type=EventType.USER_SESSION_REVOKED,
        aggregate_id=principal_id,
        aggregate_type=AGGREGATE_TYPE,
        data={
            "user_id": principal_id,
            "session_ids": session_ids,
            "reason": reason,
            "revoked_at": revoked_at.isoformat(),
        },
    )


def otp_requested(
    *, phone: str, challenge_id: str, code: str, ttl_seconds: int, channel: str
) -> EventEnvelope:
    """An OTP needs delivering.

    The notification service consumes this and performs the actual send. The code is
    carried in the payload, so this topic must be treated as sensitive: short retention,
    restricted ACLs, and no mirroring into the analytics lake.
    """
    return new_envelope(
        event_type=EventType.NOTIFICATION_OTP_REQUESTED,
        aggregate_id=challenge_id,
        aggregate_type="OtpChallenge",
        data={
            "phone": phone,
            "challenge_id": challenge_id,
            "code": code,
            "ttl_seconds": ttl_seconds,
            "channel": channel,
        },
    )
