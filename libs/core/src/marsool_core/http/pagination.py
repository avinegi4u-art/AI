"""Opaque cursor encoding for keyset pagination.

Cursors are base64url-encoded JSON of the sort key values. They are opaque to clients
(so we can change the sort key without breaking them) but not secret — never put
authorization-relevant data in a cursor.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

import orjson

from marsool_core.errors import BadRequestError


def encode_cursor(values: dict[str, Any]) -> str:
    """Encode sort-key values into an opaque cursor string."""
    return base64.urlsafe_b64encode(orjson.dumps(values)).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode an opaque cursor.

    Raises:
        BadRequestError: When the cursor is not decodable, so a tampered or truncated
            cursor becomes a 400 instead of a 500.
    """
    padding = "=" * (-len(cursor) % 4)
    try:
        payload = orjson.loads(base64.urlsafe_b64decode(cursor + padding))
    except (binascii.Error, orjson.JSONDecodeError, ValueError) as exc:
        raise BadRequestError("Malformed pagination cursor", code="invalid_cursor") from exc
    if not isinstance(payload, dict):
        raise BadRequestError("Malformed pagination cursor", code="invalid_cursor")
    return payload
