"""JWT issuance/verification, hashing, and the role model."""

from __future__ import annotations

import time

import jwt
import pytest

from marsool_core.errors import AuthenticationError
from marsool_core.ids import new_id
from marsool_core.security import (
    Principal,
    Role,
    decode_access_token,
    decode_refresh_token,
    encode_access_token,
    encode_refresh_token,
    hash_opaque_token,
    hash_otp,
    verify_otp,
)

ISSUER = "marsool.auth"
AUDIENCE = "marsool.api"
SECRET = "unit-test-secret-key-0123456789abcdef"


@pytest.fixture
def principal() -> Principal:
    return Principal(
        id=new_id("usr"),
        role=Role.MERCHANT,
        scopes=frozenset({"catalog:write"}),
        merchant_ids=("mch_01J9Z8XQF3K7M2P4R6T8V0W1Y3",),
        session_id="ses_1",
    )


def _issue(principal: Principal, **overrides: object) -> str:
    kwargs: dict[str, object] = {
        "jwt_id": new_id("jti"),
        "ttl_seconds": 900,
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "secret": SECRET,
    }
    kwargs.update(overrides)
    token, _ = encode_access_token(principal, **kwargs)  # type: ignore[arg-type]
    return token


def test_access_token_roundtrip_preserves_principal(principal: Principal) -> None:
    claims = decode_access_token(
        _issue(principal), issuer=ISSUER, audience=AUDIENCE, secret=SECRET
    )
    assert claims.to_principal() == principal


def test_access_token_expiry_is_reported(principal: Principal) -> None:
    now = int(time.time())
    _, expires_at = encode_access_token(
        principal,
        jwt_id=new_id("jti"),
        ttl_seconds=300,
        issuer=ISSUER,
        audience=AUDIENCE,
        secret=SECRET,
        now=now,
    )
    assert expires_at == now + 300


def test_expired_token_rejected(principal: Principal) -> None:
    token = _issue(principal, ttl_seconds=1, now=int(time.time()) - 3600)
    with pytest.raises(AuthenticationError) as exc_info:
        decode_access_token(token, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    assert exc_info.value.code == "token_expired"


def test_token_signed_with_another_secret_rejected(principal: Principal) -> None:
    token = _issue(principal, secret="another-secret-key-0123456789abcdef")
    with pytest.raises(AuthenticationError) as exc_info:
        decode_access_token(token, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    assert exc_info.value.code == "token_invalid"


@pytest.mark.parametrize(
    ("issuer", "audience"),
    [("evil.issuer", AUDIENCE), (ISSUER, "some.other.audience")],
)
def test_issuer_and_audience_are_enforced(
    principal: Principal, issuer: str, audience: str
) -> None:
    token = _issue(principal, issuer=issuer, audience=audience)
    with pytest.raises(AuthenticationError):
        decode_access_token(token, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)


def test_refresh_token_cannot_be_used_as_access_token() -> None:
    token, _ = encode_refresh_token(
        subject=new_id("usr"),
        session_id="ses_1",
        jwt_id=new_id("jti"),
        ttl_seconds=3600,
        issuer=ISSUER,
        audience=AUDIENCE,
        secret=SECRET,
    )
    with pytest.raises(AuthenticationError) as exc_info:
        decode_access_token(token, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    assert exc_info.value.code == "token_wrong_type"


def test_access_token_cannot_be_used_as_refresh_token(principal: Principal) -> None:
    with pytest.raises(AuthenticationError) as exc_info:
        decode_refresh_token(_issue(principal), issuer=ISSUER, audience=AUDIENCE, secret=SECRET)
    assert exc_info.value.code == "token_wrong_type"


def test_unsigned_token_rejected(principal: Principal) -> None:
    # An 'alg: none' token must never be accepted, even though the claims look valid.
    forged = jwt.encode(
        {
            "sub": principal.id,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(time.time()),
            "exp": int(time.time()) + 900,
            "jti": "forged",
            "typ": "access",
            "role": "admin",
        },
        key="",
        algorithm="none",
    )
    with pytest.raises(AuthenticationError):
        decode_access_token(forged, issuer=ISSUER, audience=AUDIENCE, secret=SECRET)


def test_rs256_requires_configured_keys(principal: Principal) -> None:
    with pytest.raises(AuthenticationError) as exc_info:
        encode_access_token(
            principal,
            jwt_id=new_id("jti"),
            ttl_seconds=900,
            issuer=ISSUER,
            audience=AUDIENCE,
            algorithm="RS256",
        )
    assert exc_info.value.code == "jwt_key_missing"


def test_otp_hash_is_salted_and_verifiable() -> None:
    first, second = hash_otp("123456"), hash_otp("123456")
    assert first != second, "each hash must use a fresh salt"
    assert verify_otp("123456", first)
    assert not verify_otp("654321", first)


@pytest.mark.parametrize("encoded", ["", "garbage", "bcrypt$aa$bb", "scrypt$zz$zz"])
def test_verify_otp_rejects_malformed_hashes(encoded: str) -> None:
    assert not verify_otp("123456", encoded)


def test_opaque_token_hash_is_deterministic() -> None:
    assert hash_opaque_token("abc") == hash_opaque_token("abc")
    assert hash_opaque_token("abc") != hash_opaque_token("abd")
    assert len(hash_opaque_token("abc")) == 64


def test_role_authorization_helpers() -> None:
    merchant = Principal(id="usr_1", role=Role.MERCHANT, merchant_ids=("mch_1",))
    admin = Principal(id="usr_2", role=Role.ADMIN)
    agent = Principal(id="svc_1", role=Role.SERVICE, scopes=frozenset({"catalog:read"}))

    assert merchant.can_act_for_merchant("mch_1")
    assert not merchant.can_act_for_merchant("mch_2")
    assert admin.can_act_for_merchant("mch_2"), "admins may administer any merchant"
    assert admin.has_scope("anything"), "admins bypass scope checks"
    assert agent.has_scope("catalog:read")
    assert not agent.has_scope("catalog:write")
    assert not agent.role.is_human
