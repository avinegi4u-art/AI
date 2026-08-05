"""Topic and event-type registry.

This module is the single source of truth for the platform's event vocabulary. Every
event type is mapped to exactly one topic, and the mapping is asserted by a contract
test so a new event cannot be published to an unregistered topic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class Topic(StrEnum):
    """Kafka topics. One topic per bounded context keeps ordering guarantees local."""

    IDENTITY = "identity.events"
    MERCHANT = "merchant.events"
    CATALOG = "catalog.events"
    ORDER = "order.events"
    PRICING = "pricing.events"
    PAYMENT = "payment.events"
    DISPATCH = "dispatch.events"
    TRACKING = "tracking.events"
    NOTIFICATION = "notification.events"

    @property
    def dead_letter(self) -> str:
        """Dead-letter topic for messages that exhausted their retry budget."""
        return f"{self.value}.dlq"


class EventType(StrEnum):
    """Every domain event published by the platform."""

    # identity.events — owned by auth and user services
    USER_REGISTERED = "identity.user_registered"
    USER_PROFILE_UPDATED = "identity.user_profile_updated"
    USER_ADDRESS_ADDED = "identity.user_address_added"
    USER_ADDRESS_UPDATED = "identity.user_address_updated"
    USER_ADDRESS_DELETED = "identity.user_address_deleted"
    USER_LOGGED_IN = "identity.user_logged_in"
    USER_SESSION_REVOKED = "identity.user_session_revoked"

    # merchant.events
    MERCHANT_CREATED = "merchant.created"
    MERCHANT_UPDATED = "merchant.updated"
    MERCHANT_STATUS_CHANGED = "merchant.status_changed"
    MERCHANT_HOURS_UPDATED = "merchant.hours_updated"

    # catalog.events
    MENU_PUBLISHED = "catalog.menu_published"
    MENU_ITEM_CREATED = "catalog.item_created"
    MENU_ITEM_UPDATED = "catalog.item_updated"
    MENU_ITEM_AVAILABILITY_CHANGED = "catalog.item_availability_changed"

    # order.events
    ORDER_CREATED = "order.created"
    ORDER_CONFIRMED = "order.confirmed"
    ORDER_PREPARING = "order.preparing"
    ORDER_READY_FOR_PICKUP = "order.ready_for_pickup"
    ORDER_PICKED_UP = "order.picked_up"
    ORDER_IN_TRANSIT = "order.in_transit"
    ORDER_DELIVERED = "order.delivered"
    ORDER_CANCELLED = "order.cancelled"
    ORDER_FAILED = "order.failed"
    ORDER_REFUNDED = "order.refunded"

    # pricing.events
    PRICING_QUOTE_ISSUED = "pricing.quote_issued"
    PRICING_SURGE_UPDATED = "pricing.surge_updated"

    # payment.events
    PAYMENT_AUTHORIZED = "payment.authorized"
    PAYMENT_CAPTURED = "payment.captured"
    PAYMENT_FAILED = "payment.failed"
    PAYMENT_REFUNDED = "payment.refunded"

    # dispatch.events
    DISPATCH_ASSIGNMENT_REQUESTED = "dispatch.assignment_requested"
    DISPATCH_ASSIGNED = "dispatch.assigned"
    DISPATCH_REASSIGNED = "dispatch.reassigned"
    DISPATCH_ASSIGNMENT_REJECTED = "dispatch.assignment_rejected"
    DISPATCH_UNASSIGNED = "dispatch.unassigned"

    # tracking.events
    TRACKING_LOCATION_UPDATED = "tracking.location_updated"
    TRACKING_ETA_UPDATED = "tracking.eta_updated"

    # notification.events
    NOTIFICATION_OTP_REQUESTED = "notification.otp_requested"
    NOTIFICATION_ORDER_CONFIRMED = "notification.order_confirmed"
    NOTIFICATION_COURIER_ASSIGNED = "notification.courier_assigned"
    NOTIFICATION_ORDER_DELIVERED = "notification.order_delivered"
    NOTIFICATION_ORDER_CANCELLED = "notification.order_cancelled"


#: Prefix of each event type maps to the owning topic.
_PREFIX_TO_TOPIC: Final[dict[str, Topic]] = {
    "identity": Topic.IDENTITY,
    "merchant": Topic.MERCHANT,
    "catalog": Topic.CATALOG,
    "order": Topic.ORDER,
    "pricing": Topic.PRICING,
    "payment": Topic.PAYMENT,
    "dispatch": Topic.DISPATCH,
    "tracking": Topic.TRACKING,
    "notification": Topic.NOTIFICATION,
}


def topic_for(event_type: EventType | str) -> Topic:
    """Return the topic that owns ``event_type``.

    Raises:
        ValueError: If the event type's prefix is not a registered topic.
    """
    value = event_type.value if isinstance(event_type, EventType) else event_type
    prefix = value.split(".", 1)[0]
    try:
        return _PREFIX_TO_TOPIC[prefix]
    except KeyError as exc:
        raise ValueError(f"no topic registered for event type {value!r}") from exc
