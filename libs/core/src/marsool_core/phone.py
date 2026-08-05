"""E.164 phone number normalisation.

Phone numbers are the primary login identifier, so they must be stored in exactly one
canonical form — otherwise ``+971501234567`` and ``0501234567`` become two accounts.

A hand-rolled validator is used instead of a full libphonenumber dependency: we only
need E.164 structural validation plus local-format handling for our launch markets.
The market table is the extension point when new countries go live.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_E164_PATTERN: Final = re.compile(r"^\+[1-9]\d{7,14}$")
_NON_DIGITS: Final = re.compile(r"[^\d+]")


@dataclass(frozen=True, slots=True)
class Market:
    """A launch market's dialling rules."""

    country_code: str
    calling_code: str
    national_number_length: int
    trunk_prefix: str = "0"


# Countries where local-format input is accepted and normalised.
MARKETS: Final[dict[str, Market]] = {
    "AE": Market(country_code="AE", calling_code="971", national_number_length=9),
    "SA": Market(country_code="SA", calling_code="966", national_number_length=9),
}

DEFAULT_MARKET: Final = "AE"


class InvalidPhoneNumberError(ValueError):
    """Raised when a phone number cannot be normalised to valid E.164."""


def normalize_phone(raw: str, *, default_market: str = DEFAULT_MARKET) -> str:
    """Normalise a phone number to E.164.

    Accepts international format, and local format for known markets:

    >>> normalize_phone("+971 50 123 4567")
    '+971501234567'
    >>> normalize_phone("050 123 4567")
    '+971501234567'
    >>> normalize_phone("971501234567")
    '+971501234567'

    Raises:
        InvalidPhoneNumberError: When the input cannot be normalised.
    """
    if not raw or not raw.strip():
        raise InvalidPhoneNumberError("Phone number is required")

    stripped = _NON_DIGITS.sub("", raw.strip())
    # A '+' is only meaningful in the leading position.
    digits = stripped.replace("+", "")
    cleaned = f"+{digits}" if stripped.startswith("+") else digits

    market = MARKETS.get(default_market.upper())
    if market is None:
        raise InvalidPhoneNumberError(f"Unsupported market: {default_market!r}")

    if not cleaned.startswith("+"):
        trunk_length = len(market.trunk_prefix)
        local_length = market.national_number_length + trunk_length
        if cleaned.startswith(market.trunk_prefix) and len(cleaned) == local_length:
            # Local format: strip the trunk prefix and prepend the calling code.
            cleaned = f"+{market.calling_code}{cleaned[trunk_length:]}"
        elif cleaned.startswith(market.calling_code):
            cleaned = f"+{cleaned}"
        elif len(cleaned) == market.national_number_length:
            cleaned = f"+{market.calling_code}{cleaned}"
        else:
            raise InvalidPhoneNumberError(f"Cannot infer country code for {raw!r}")

    if not _E164_PATTERN.match(cleaned):
        raise InvalidPhoneNumberError(f"Not a valid E.164 phone number: {raw!r}")
    return cleaned


def is_valid_phone(raw: str, *, default_market: str = DEFAULT_MARKET) -> bool:
    """Return True when ``raw`` can be normalised to valid E.164."""
    try:
        normalize_phone(raw, default_market=default_market)
    except InvalidPhoneNumberError:
        return False
    return True


def mask_phone(phone: str) -> str:
    """Mask a phone number for logs and support UIs.

    >>> mask_phone("+971501234567")
    '+9715****4567'
    """
    if len(phone) <= 8:
        return "*" * len(phone)
    return f"{phone[:5]}****{phone[-4:]}"
