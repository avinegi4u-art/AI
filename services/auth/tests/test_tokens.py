"""Refresh-token rotation, reuse detection, logout and session listing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_auth.models import AuthPrincipal, PrincipalStatus, RefreshToken
from marsool_auth.runtime import AuthRuntime
from marsool_auth.testing import RecordingOtpSender
from marsool_core.events.bus import InMemoryEventBus
from marsool_core.events.topics import EventType
from marsool_core.security.jwt import encode_refresh_token
from marsool_core.testing import TEST_JWT_SECRET, session_factory_for

PHONE = "+971501234567"

Login = Callable[..., Awaitable[dict[str, Any]]]
ClientFactory = Callable[..., Awaitable[AsyncClient]]



async def test_refresh_returns_a_new_pair(
    client: AsyncClient, login: Login
) -> None:
    initial = await login()

    response = await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})

    assert response.status_code == 200, response.text
    rotated = response.json()
    assert rotated["refresh_token"] != initial["refresh_token"]
    assert rotated["user"]["id"] == initial["user"]["id"]


async def test_rotated_token_cannot_be_used_again(
    client: AsyncClient, login: Login
) -> None:
    initial = await login()
    await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})

    replay = await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})

    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "token_reused"


async def test_reuse_detection_revokes_the_whole_session(
    client: AsyncClient, login: Login
) -> None:
    initial = await login()
    rotated = (
        await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})
    ).json()

    # An attacker replays the stolen (already-rotated) token.
    await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})

    # The legitimate holder's current token is now dead too: theft becomes a detected
    # incident and forces re-authentication rather than granting silent access.
    victim = await client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert victim.status_code == 401
    assert victim.json()["error"]["code"] == "token_reused"


async def test_reuse_detection_emits_a_revocation_event(
    client: AsyncClient, login: Login, runtime: AuthRuntime, event_bus: InMemoryEventBus
) -> None:
    initial = await login()
    await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})
    await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})
    await runtime.outbox_relay.drain_once()

    revocations = event_bus.events_of_type(EventType.USER_SESSION_REVOKED.value)
    assert [event.data["reason"] for event in revocations] == ["reuse_detected"]


async def test_rotation_chain_is_recorded(
    client: AsyncClient, login: Login, engine: AsyncEngine
) -> None:
    initial = await login()
    await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})

    async with session_factory_for(engine)() as session:
        tokens = (
            (await session.execute(select(RefreshToken).order_by(RefreshToken.created_at)))
            .scalars()
            .all()
        )

    assert len(tokens) == 2
    old, new = tokens
    assert old.revoked_reason == "rotated"
    assert old.replaced_by_id == new.id
    assert new.revoked_at is None
    # Rotation stays inside one session, so sessions are not multiplied by refreshes.
    assert old.session_id == new.session_id


async def test_refresh_tokens_are_only_stored_as_digests(
    client: AsyncClient, login: Login, engine: AsyncEngine
) -> None:
    initial = await login()

    async with session_factory_for(engine)() as session:
        stored = (await session.execute(select(RefreshToken))).scalars().all()

    assert len(stored) == 1
    assert stored[0].token_hash != initial["refresh_token"]
    assert len(stored[0].token_hash) == 64


async def test_access_token_is_rejected_by_refresh(
    client: AsyncClient, login: Login
) -> None:
    initial = await login()
    response = await client.post("/auth/refresh", json={"refresh_token": initial["access_token"]})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_wrong_type"


async def test_unknown_but_well_formed_refresh_token_is_rejected(client: AsyncClient) -> None:
    # Correctly signed but never issued: the database is the source of truth for
    # sessions, so a valid signature alone must not grant access.
    forged, _ = encode_refresh_token(
        subject="usr_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
        session_id="ses_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
        jwt_id="rt_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
        ttl_seconds=3600,
        issuer="marsool.auth",
        audience="marsool.api",
        secret=TEST_JWT_SECRET,
    )
    response = await client.post("/auth/refresh", json={"refresh_token": forged})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_invalid"


async def test_expired_refresh_token_is_rejected(
    client: AsyncClient, login: Login, engine: AsyncEngine
) -> None:
    initial = await login()
    async with session_factory_for(engine)() as session:
        stored = (await session.execute(select(RefreshToken))).scalar_one()
        stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    response = await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


async def test_suspended_account_cannot_refresh(
    client: AsyncClient, login: Login, engine: AsyncEngine
) -> None:
    initial = await login()
    async with session_factory_for(engine)() as session:
        principal_record = (await session.execute(select(AuthPrincipal))).scalar_one()
        principal_record.status = PrincipalStatus.SUSPENDED
        await session.commit()

    response = await client.post("/auth/refresh", json={"refresh_token": initial["refresh_token"]})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_not_active"


async def test_logout_revokes_the_session(
    client: AsyncClient, login: Login
) -> None:
    initial = await login()
    headers = {"Authorization": f"Bearer {initial['access_token']}"}

    response = await client.post(
        "/auth/logout", json={"refresh_token": initial["refresh_token"]}, headers=headers
    )
    assert response.status_code == 204

    refreshed = await client.post(
        "/auth/refresh", json={"refresh_token": initial["refresh_token"]}
    )
    assert refreshed.status_code == 401


async def test_logout_is_idempotent(client: AsyncClient, login: Login) -> None:
    initial = await login()
    headers = {"Authorization": f"Bearer {initial['access_token']}"}
    payload = {"refresh_token": initial["refresh_token"]}

    assert (await client.post("/auth/logout", json=payload, headers=headers)).status_code == 204
    assert (await client.post("/auth/logout", json=payload, headers=headers)).status_code == 204


async def test_logout_requires_authentication(
    client: AsyncClient, login: Login
) -> None:
    initial = await login()
    response = await client.post("/auth/logout", json={"refresh_token": initial["refresh_token"]})
    assert response.status_code == 401


async def test_logout_rejects_another_users_token(
    client: AsyncClient, login: Login
) -> None:
    victim = await login()
    attacker = await login("+971509999999")

    response = await client.post(
        "/auth/logout",
        json={"refresh_token": victim["refresh_token"]},
        headers={"Authorization": f"Bearer {attacker['access_token']}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "token_owner_mismatch"


async def test_logout_all_revokes_every_session(
    client: AsyncClient, login: Login
) -> None:
    first = await login()
    second = await login()

    response = await client.post(
        "/auth/logout",
        json={"all_sessions": True},
        headers={"Authorization": f"Bearer {second['access_token']}"},
    )
    assert response.status_code == 204

    for session_tokens in (first, second):
        refreshed = await client.post(
            "/auth/refresh", json={"refresh_token": session_tokens["refresh_token"]}
        )
        assert refreshed.status_code == 401


async def test_logout_requires_a_target(
    client: AsyncClient, login: Login
) -> None:
    initial = await login()
    response = await client.post(
        "/auth/logout",
        json={},
        headers={"Authorization": f"Bearer {initial['access_token']}"},
    )
    assert response.status_code == 422


async def test_sessions_endpoint_lists_active_sessions(
    client: AsyncClient, login: Login
) -> None:
    await login()
    current = await login()

    response = await client.get(
        "/auth/sessions", headers={"Authorization": f"Bearer {current['access_token']}"}
    )

    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 2
    assert sum(1 for item in sessions if item["is_current"]) == 1


@pytest.mark.parametrize("payload", [{}, {"refresh_token": "short"}])
async def test_refresh_validates_its_payload(client: AsyncClient, payload: dict) -> None:
    assert (await client.post("/auth/refresh", json=payload)).status_code == 422


async def test_session_ceiling_revokes_oldest_sessions(
    client_factory: ClientFactory,
    otp_sender: RecordingOtpSender,
    engine: AsyncEngine,
) -> None:
    # A small ceiling and a relaxed OTP request limit keep the test focused on the
    # ceiling rather than on rate limiting.
    max_sessions = 3
    client = await client_factory(max_active_sessions=max_sessions, otp_requests_per_hour=50)

    for _ in range(max_sessions + 2):
        await client.post("/auth/otp", json={"phone": PHONE})
        response = await client.post(
            "/auth/login", json={"phone": PHONE, "otp": otp_sender.latest_code_for(PHONE)}
        )
        assert response.status_code == 200, response.text

    async with session_factory_for(engine)() as session:
        active = (
            (await session.execute(select(RefreshToken).where(RefreshToken.revoked_at.is_(None))))
            .scalars()
            .all()
        )
        limited = (
            (
                await session.execute(
                    select(RefreshToken).where(RefreshToken.revoked_reason == "session_limit")
                )
            )
            .scalars()
            .all()
        )

    assert len(active) == max_sessions
    assert len(limited) == 2
