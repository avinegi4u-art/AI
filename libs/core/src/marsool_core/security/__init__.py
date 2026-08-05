"""Authentication and authorization primitives."""

from marsool_core.security.hashing import (
    constant_time_equals,
    hash_opaque_token,
    hash_otp,
    verify_otp,
)
from marsool_core.security.jwt import (
    AccessTokenClaims,
    RefreshTokenClaims,
    TokenType,
    decode_access_token,
    decode_refresh_token,
    encode_access_token,
    encode_refresh_token,
)
from marsool_core.security.principal import Principal, Role

__all__ = [
    "AccessTokenClaims",
    "Principal",
    "RefreshTokenClaims",
    "Role",
    "TokenType",
    "constant_time_equals",
    "decode_access_token",
    "decode_refresh_token",
    "encode_access_token",
    "encode_refresh_token",
    "hash_opaque_token",
    "hash_otp",
    "verify_otp",
]
