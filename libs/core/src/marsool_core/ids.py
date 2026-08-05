"""Prefixed, lexicographically sortable public identifiers.

Every aggregate exposes an ID of the form ``<prefix>_<ulid>``, e.g. ``ord_01J9Z8...``.
The ULID payload is time-ordered, which keeps B-tree index inserts append-only and
makes IDs debuggable (you can read the creation time out of them).

See ``docs/architecture/adr/0002-prefixed-ulid-identifiers.md`` for the trade-offs.
"""

from __future__ import annotations

import os
import time
from typing import Final

_CROCKFORD: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_INDEX: Final = {char: value for value, char in enumerate(_CROCKFORD)}
_ULID_LENGTH: Final = 26
_TIMESTAMP_BITS: Final = 48
_RANDOM_BITS: Final = 80

# Canonical prefixes. Keeping them in one place prevents two services from
# inventing different prefixes for the same aggregate.
PREFIX_USER: Final = "usr"
PREFIX_ADDRESS: Final = "adr"
PREFIX_MERCHANT: Final = "mch"
PREFIX_MENU: Final = "menu"
PREFIX_CATEGORY: Final = "cat"
PREFIX_MENU_ITEM: Final = "itm"
PREFIX_OPTION_GROUP: Final = "opg"
PREFIX_OPTION: Final = "opt"
PREFIX_ORDER: Final = "ord"
PREFIX_ORDER_ITEM: Final = "oit"
PREFIX_ASSIGNMENT: Final = "asg"
PREFIX_PAYMENT_INTENT: Final = "pi"
PREFIX_QUOTE: Final = "qte"
PREFIX_EVENT: Final = "evt"
PREFIX_OTP: Final = "otp"
PREFIX_REFRESH_TOKEN: Final = "rt"  # noqa: S105 - an ID prefix, not a credential
PREFIX_SERVICE_AREA: Final = "sza"


def _encode_crockford(value: int, length: int) -> str:
    chars = [""] * length
    for position in range(length - 1, -1, -1):
        chars[position] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_ulid(*, now_ms: int | None = None) -> str:
    """Return a 26-character Crockford base32 ULID."""
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    if not 0 <= timestamp < (1 << _TIMESTAMP_BITS):
        raise ValueError("timestamp out of ULID range")
    randomness = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
    return _encode_crockford((timestamp << _RANDOM_BITS) | randomness, _ULID_LENGTH)


def new_id(prefix: str, *, now_ms: int | None = None) -> str:
    """Return a namespaced public identifier such as ``usr_01J9Z8...``."""
    if not prefix or "_" in prefix:
        raise ValueError(f"invalid id prefix: {prefix!r}")
    return f"{prefix}_{new_ulid(now_ms=now_ms)}"


def parse_id(value: str) -> tuple[str, str]:
    """Split a public identifier into ``(prefix, ulid)``, validating the payload."""
    prefix, separator, ulid = value.partition("_")
    if not separator or not prefix:
        raise ValueError(f"malformed identifier: {value!r}")
    if len(ulid) != _ULID_LENGTH or any(char not in _CROCKFORD_INDEX for char in ulid):
        raise ValueError(f"malformed ULID payload in identifier: {value!r}")
    return prefix, ulid


def timestamp_ms(value: str) -> int:
    """Extract the creation timestamp (epoch milliseconds) from a public identifier."""
    _, ulid = parse_id(value)
    decoded = 0
    for char in ulid:
        decoded = (decoded << 5) | _CROCKFORD_INDEX[char]
    return decoded >> _RANDOM_BITS


def has_prefix(value: str, prefix: str) -> bool:
    """Return True when ``value`` is a well-formed identifier with the given prefix."""
    try:
        return parse_id(value)[0] == prefix
    except ValueError:
        return False
