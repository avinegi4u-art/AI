"""End-to-end OTP login flow over HTTP."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from httpx import AsyncClient

from marsool_auth.runtime import AuthRuntime
from marsool_auth.testing import RecordingOtpSender
from marsool_core.events.bus import InMemoryEventBus
from marsool_core.events.topics import EventType
from marsool_core.security.jwt import decode_access_token
from marsool_core.testing import TEST_JWT_SECRET

PHONE = "+971501234567"

RequestOtp = Callable[..., Awaitable[dict[str, Any]]]
Login = Callable[..., Awaitable[dict[str, Any]]]


async def test_otp_request_returns_challenge_metadata(
    request_otp: RequestOtp, otp_sender: RecordingOtpSender
) -> None:
    body = await request_otp()

    assert body["challenge_id"].startswith("otp_")
    assert body["phone"] == PHONE
    assert body["attempts_allowed"] == 5
    assert body["delivery_channel"] == "test"
    assert len(otp_sender.sent) == 1


async def test_local_format_phone_is_normalised(
    request_otp: RequestOtp, otp_sender: RecordingOtpSender
) -> None:
    body = await request_otp("050 123 4567")
    # The response and the delivery both use the canonical E.164 form, so a user who
    # types a local number and later an international one lands on the same account.
    assert body["phone"] == PHONE
    assert otp_sender.sent[0][0] == PHONE


async def test_login_issues_tokens_and_creates_the_account(login: Login) -> None:
    body = await login()

    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 900
    assert body["user"]["id"].startswith("usr_")
    assert body["user"]["role"] == "customer"
    assert body["user"]["phone"] == PHONE

    claims = decode_access_token(
        body["access_token"],
        issuer="marsool.auth",
        audience="marsool.api",
        secret=TEST_JWT_SECRET,
    )
    assert claims.subject == body["user"]["id"]
    assert claims.role.value == "customer"
    assert claims.session_id is not None


async def test_second_login_reuses_the_same_account(login: Login) -> None:
    first = await login()
    second = await login()
    assert first["user"]["id"] == second["user"]["id"]
    assert first["access_token"] != second["access_token"]


async def test_wrong_code_is_rejected_without_revealing_why(
    client: AsyncClient, request_otp: RequestOtp, otp_sender: RecordingOtpSender
) -> None:
    await request_otp()
    wrong_code = "000000" if otp_sender.latest_code_for(PHONE) != "000000" else "111111"

    response = await client.post("/auth/login", json={"phone": PHONE, "otp": wrong_code})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "otp_invalid"


async def test_login_for_unknown_phone_is_indistinguishable(client: AsyncClient) -> None:
    # No challenge was ever issued; the error must match the wrong-code error exactly so
    # the endpoint cannot be used to enumerate registered numbers.
    response = await client.post("/auth/login", json={"phone": PHONE, "otp": "123456"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "otp_invalid"


async def test_code_cannot_be_reused(
    client: AsyncClient, request_otp: RequestOtp, otp_sender: RecordingOtpSender
) -> None:
    await request_otp()
    code = otp_sender.latest_code_for(PHONE)

    first = await client.post("/auth/login", json={"phone": PHONE, "otp": code})
    assert first.status_code == 200

    replay = await client.post("/auth/login", json={"phone": PHONE, "otp": code})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "otp_invalid"


async def test_requesting_a_new_code_invalidates_the_previous_one(
    client: AsyncClient, request_otp: RequestOtp, otp_sender: RecordingOtpSender
) -> None:
    await request_otp()
    first_code = otp_sender.latest_code_for(PHONE)
    await request_otp()
    second_code = otp_sender.latest_code_for(PHONE)
    assert first_code != second_code

    stale = await client.post("/auth/login", json={"phone": PHONE, "otp": first_code})
    assert stale.status_code == 401

    fresh = await client.post("/auth/login", json={"phone": PHONE, "otp": second_code})
    assert fresh.status_code == 200


async def test_attempts_are_capped_per_challenge(
    client: AsyncClient, request_otp: RequestOtp, otp_sender: RecordingOtpSender
) -> None:
    await request_otp()
    real_code = otp_sender.latest_code_for(PHONE)
    wrong_code = "000000" if real_code != "000000" else "111111"

    for _ in range(5):
        response = await client.post("/auth/login", json={"phone": PHONE, "otp": wrong_code})
        assert response.json()["error"]["code"] == "otp_invalid"

    exhausted = await client.post("/auth/login", json={"phone": PHONE, "otp": wrong_code})
    assert exhausted.json()["error"]["code"] == "otp_attempts_exhausted"

    # The challenge is burned, so even the correct code no longer works.
    correct = await client.post("/auth/login", json={"phone": PHONE, "otp": real_code})
    assert correct.status_code == 401


async def test_otp_requests_are_rate_limited_per_phone(client: AsyncClient) -> None:
    for _ in range(5):
        assert (await client.post("/auth/otp", json={"phone": PHONE})).status_code == 201

    limited = await client.post("/auth/otp", json={"phone": PHONE})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1

    # A different number has its own bucket.
    assert (await client.post("/auth/otp", json={"phone": "+971509999999"})).status_code == 201


async def test_invalid_phone_is_a_validation_error(client: AsyncClient) -> None:
    response = await client.post("/auth/otp", json={"phone": "not-a-phone"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["field_errors"][0]["field"] == "phone"


async def test_non_numeric_otp_is_rejected_before_lookup(client: AsyncClient) -> None:
    response = await client.post("/auth/login", json={"phone": PHONE, "otp": "abcdef"})
    assert response.status_code == 422


async def test_login_publishes_registration_and_login_events(
    login: Login, runtime: AuthRuntime, event_bus: InMemoryEventBus
) -> None:
    body = await login()
    # Events are written to the outbox inside the login transaction; the relay publishes
    # them afterwards. Draining explicitly keeps the test deterministic.
    await runtime.outbox_relay.drain_once()

    published = {event.event_type for event in event_bus.published}
    assert EventType.USER_REGISTERED.value in published
    assert EventType.USER_LOGGED_IN.value in published
    assert EventType.NOTIFICATION_OTP_REQUESTED.value in published

    registered = event_bus.events_of_type(EventType.USER_REGISTERED.value)[0]
    assert registered.aggregate_id == body["user"]["id"]
    assert registered.data["phone"] == PHONE
    assert registered.aggregate_type == "User"


async def test_second_login_does_not_republish_registration(
    login: Login, runtime: AuthRuntime, event_bus: InMemoryEventBus
) -> None:
    await login()
    await login()
    await runtime.outbox_relay.drain_once()

    assert len(event_bus.events_of_type(EventType.USER_REGISTERED.value)) == 1
    assert len(event_bus.events_of_type(EventType.USER_LOGGED_IN.value)) == 2


async def test_events_carry_the_request_correlation_id(
    client: AsyncClient, runtime: AuthRuntime, event_bus: InMemoryEventBus
) -> None:
    await client.post(
        "/auth/otp", json={"phone": PHONE}, headers={"X-Correlation-Id": "corr_test_123"}
    )
    await runtime.outbox_relay.drain_once()

    otp_events = event_bus.events_of_type(EventType.NOTIFICATION_OTP_REQUESTED.value)
    assert otp_events[0].correlation_id == "corr_test_123"


async def test_failed_login_does_not_leave_an_outbox_row(
    client: AsyncClient, runtime: AuthRuntime, event_bus: InMemoryEventBus
) -> None:
    # The handler's transaction rolls back on error, and the outbox row is written in that
    # same transaction, so no event escapes for a request that failed.
    await client.post("/auth/login", json={"phone": PHONE, "otp": "123456"})
    await runtime.outbox_relay.drain_once()

    assert event_bus.events_of_type(EventType.USER_REGISTERED.value) == []
