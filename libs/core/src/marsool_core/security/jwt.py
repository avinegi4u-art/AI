"""JWT issuance and verification.

Access tokens are short-lived bearer credentials verified independently by the gateway
*and* by every service (defence in depth — a compromised gateway must not be able to
mint trust). Refresh tokens are long-lived, single-use and rotated on every use; the
JWT only carries the session pointer, while the authoritative revocation state lives in
the auth service database.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any, Final

import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from marsool_core.errors import AuthenticationError
from marsool_core.security.principal import Principal, Role

_LEEWAY_SECONDS: Final = 10


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class _BaseClaims(BaseModel):
    """Registered claims common to both token types."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    subject: str = Field(alias="sub")
    issuer: str = Field(alias="iss")
    audience: str = Field(alias="aud")
    issued_at: int = Field(alias="iat")
    expires_at: int = Field(alias="exp")
    jwt_id: str = Field(alias="jti")
    token_type: TokenType = Field(alias="typ")


class AccessTokenClaims(_BaseClaims):
    """Claims carried by an access token."""

    role: Role
    scopes: tuple[str, ...] = ()
    merchant_ids: tuple[str, ...] = ()
    session_id: str | None = Field(default=None, alias="sid")

    def to_principal(self) -> Principal:
        return Principal(
            id=self.subject,
            role=self.role,
            scopes=frozenset(self.scopes),
            merchant_ids=self.merchant_ids,
            session_id=self.session_id,
        )


class RefreshTokenClaims(_BaseClaims):
    """Claims carried by a refresh token."""

    session_id: str = Field(alias="sid")


def _signing_key(algorithm: str, *, secret: str, private_key: str | None) -> str:
    if algorithm == "HS256":
        return secret
    if not private_key:
        raise AuthenticationError(
            "RS256 signing requires a configured private key",
            code="jwt_key_missing",
            status_code=500,
        )
    return private_key


def _verification_key(algorithm: str, *, secret: str, public_key: str | None) -> str:
    if algorithm == "HS256":
        return secret
    if not public_key:
        raise AuthenticationError(
            "RS256 verification requires a configured public key",
            code="jwt_key_missing",
            status_code=500,
        )
    return public_key


def _encode(
    payload: dict[str, Any],
    *,
    algorithm: str,
    secret: str,
    private_key: str | None,
) -> str:
    key = _signing_key(algorithm, secret=secret, private_key=private_key)
    return jwt.encode(payload, key, algorithm=algorithm)


def encode_access_token(
    principal: Principal,
    *,
    jwt_id: str,
    ttl_seconds: int,
    issuer: str,
    audience: str,
    algorithm: str = "HS256",
    secret: str = "",
    private_key: str | None = None,
    now: int | None = None,
) -> tuple[str, int]:
    """Mint an access token. Returns ``(token, expires_at_epoch_seconds)``."""
    issued_at = now if now is not None else int(time.time())
    expires_at = issued_at + ttl_seconds
    payload: dict[str, Any] = {
        "sub": principal.id,
        "iss": issuer,
        "aud": audience,
        "iat": issued_at,
        "exp": expires_at,
        "jti": jwt_id,
        "typ": TokenType.ACCESS.value,
        "role": principal.role.value,
        "scopes": list(principal.scopes),
        "merchant_ids": list(principal.merchant_ids),
    }
    if principal.session_id:
        payload["sid"] = principal.session_id
    token = _encode(payload, algorithm=algorithm, secret=secret, private_key=private_key)
    return token, expires_at


def encode_refresh_token(
    *,
    subject: str,
    session_id: str,
    jwt_id: str,
    ttl_seconds: int,
    issuer: str,
    audience: str,
    algorithm: str = "HS256",
    secret: str = "",
    private_key: str | None = None,
    now: int | None = None,
) -> tuple[str, int]:
    """Mint a refresh token. Returns ``(token, expires_at_epoch_seconds)``."""
    issued_at = now if now is not None else int(time.time())
    expires_at = issued_at + ttl_seconds
    payload: dict[str, Any] = {
        "sub": subject,
        "iss": issuer,
        "aud": audience,
        "iat": issued_at,
        "exp": expires_at,
        "jti": jwt_id,
        "typ": TokenType.REFRESH.value,
        "sid": session_id,
    }
    token = _encode(payload, algorithm=algorithm, secret=secret, private_key=private_key)
    return token, expires_at


def _decode(
    token: str,
    *,
    expected_type: TokenType,
    issuer: str,
    audience: str,
    algorithm: str,
    secret: str,
    public_key: str | None,
) -> dict[str, Any]:
    key = _verification_key(algorithm, secret=secret, public_key=public_key)
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=[algorithm],
            issuer=issuer,
            audience=audience,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Token is invalid", code="token_invalid") from exc

    if payload.get("typ") != expected_type.value:
        raise AuthenticationError(
            f"Expected a {expected_type.value} token", code="token_wrong_type"
        )
    return payload


def decode_access_token(
    token: str,
    *,
    issuer: str,
    audience: str,
    algorithm: str = "HS256",
    secret: str = "",
    public_key: str | None = None,
) -> AccessTokenClaims:
    """Verify an access token and return its typed claims."""
    payload = _decode(
        token,
        expected_type=TokenType.ACCESS,
        issuer=issuer,
        audience=audience,
        algorithm=algorithm,
        secret=secret,
        public_key=public_key,
    )
    try:
        return AccessTokenClaims.model_validate(payload)
    except ValidationError as exc:
        raise AuthenticationError("Token claims are malformed", code="token_invalid") from exc


def decode_refresh_token(
    token: str,
    *,
    issuer: str,
    audience: str,
    algorithm: str = "HS256",
    secret: str = "",
    public_key: str | None = None,
) -> RefreshTokenClaims:
    """Verify a refresh token and return its typed claims."""
    payload = _decode(
        token,
        expected_type=TokenType.REFRESH,
        issuer=issuer,
        audience=audience,
        algorithm=algorithm,
        secret=secret,
        public_key=public_key,
    )
    try:
        return RefreshTokenClaims.model_validate(payload)
    except ValidationError as exc:
        raise AuthenticationError("Token claims are malformed", code="token_invalid") from exc
