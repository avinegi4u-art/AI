"""OTP generation and delivery.

Delivery is behind a small protocol so the SMS provider (Unifonic/Twilio in the UAE) can
be swapped without touching the login flow, and so local development and tests need no
external provider.
"""

from __future__ import annotations

import secrets
from typing import Protocol

from marsool_core.logging import get_logger
from marsool_core.phone import mask_phone

logger = get_logger(__name__)


def generate_otp(length: int) -> str:
    """Generate a numeric OTP using a cryptographically secure RNG.

    ``secrets.randbelow`` is used rather than ``random`` so codes are not predictable
    from previously observed values.
    """
    if not 4 <= length <= 8:
        raise ValueError("OTP length must be between 4 and 8 digits")
    upper_bound = 10**length
    return str(secrets.randbelow(upper_bound)).zfill(length)


class OtpSender(Protocol):
    """Delivers an OTP code to a phone number."""

    channel: str

    async def send(self, *, phone: str, code: str, ttl_seconds: int) -> None:
        """Deliver ``code`` to ``phone``."""
        ...


class LoggingOtpSender:
    """Logs the OTP instead of sending an SMS.

    Used in local development and tests. The code is logged at INFO with the phone
    masked; this sender must never be configured in production (the service refuses to
    echo codes in production responses, and deployments wire a real provider).
    """

    channel = "log"

    async def send(self, *, phone: str, code: str, ttl_seconds: int) -> None:
        logger.info(
            "otp_delivered_via_log",
            phone=mask_phone(phone),
            ttl_seconds=ttl_seconds,
            code_length=len(code),
        )


class SmsOtpSender:
    """Sends the OTP over SMS through the configured provider.

    The concrete provider integration lands with the notification service (Step 4); this
    class exists so the wiring and the interface are settled now.
    """

    channel = "sms"

    def __init__(self, *, provider_name: str) -> None:
        self.provider_name = provider_name

    async def send(self, *, phone: str, code: str, ttl_seconds: int) -> None:
        raise NotImplementedError(
            "SMS delivery is provided by the notification service; "
            "configure MARSOOL_OTP_SENDER=log until it is deployed"
        )
