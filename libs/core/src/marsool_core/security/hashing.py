"""Hashing helpers for OTP codes and opaque tokens.

OTP codes are short and low-entropy, so they are hashed with a memory-hard KDF
(scrypt) plus a per-record salt to make offline brute-force expensive. Refresh tokens
are 256-bit random values, so a single SHA-256 pass is sufficient and lets us index
the digest for constant-time lookup.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

_SCRYPT_N: Final = 2**14
_SCRYPT_R: Final = 8
_SCRYPT_P: Final = 1
_SCRYPT_DKLEN: Final = 32
_SALT_BYTES: Final = 16
_HASH_PREFIX: Final = "scrypt"


def hash_otp(code: str) -> str:
    """Return a salted scrypt digest of an OTP code, encoded as ``scrypt$salt$hash``."""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        code.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"{_HASH_PREFIX}${salt.hex()}${digest.hex()}"


def verify_otp(code: str, encoded: str) -> bool:
    """Verify an OTP code against an encoded digest in constant time."""
    try:
        scheme, salt_hex, digest_hex = encoded.split("$")
        if scheme != _HASH_PREFIX:
            return False
        expected = bytes.fromhex(digest_hex)
        candidate = hashlib.scrypt(
            code.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def hash_opaque_token(token: str) -> str:
    """Return a SHA-256 hex digest of a high-entropy opaque token.

    Refresh tokens are stored only as digests: a database leak must not yield usable
    session credentials.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """Compare two strings without leaking length-independent timing information."""
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
