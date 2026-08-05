"""Profile reads, updates and just-in-time provisioning."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_core.events.bus import InMemoryEventBus
from marsool_core.events.topics import EventType
from marsool_core.security.principal import Principal, Role
from marsool_core.testing import make_principal, session_factory_for
from marsool_user.models import CourierProfile, User, UserStatus
from marsool_user.runtime import UserRuntime

ClientAs = Callable[[Principal], Awaitable[AsyncClient]]


async def test_first_request_provisions_the_profile(
    client: AsyncClient, customer: Principal
) -> None:
    # Auth has issued a token but the registration event may not have been consumed yet;
    # the caller must still get their profile rather than a 404.
    response = await client.get("/users/me")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == customer.id
    assert body["role"] == "customer"
    assert body["status"] == "ACTIVE"
    assert body["locale"] == "en-AE"
    assert body["dietary_tags"] == []


async def test_provisioning_is_idempotent(client: AsyncClient, engine: AsyncEngine) -> None:
    await client.get("/users/me")
    await client.get("/users/me")

    async with session_factory_for(engine)() as session:
        users = (await session.execute(select(User))).scalars().all()
    assert len(users) == 1


async def test_courier_gets_a_courier_profile_on_provisioning(
    client_as: ClientAs, engine: AsyncEngine
) -> None:
    courier = make_principal(role=Role.COURIER)
    client = await client_as(courier)

    body = (await client.get("/users/me")).json()

    assert body["courier_profile"]["status"] == "OFFLINE"
    assert body["courier_profile"]["vehicle"] == "MOTORCYCLE"
    assert body["courier_profile"]["max_concurrent_orders"] == 3
    async with session_factory_for(engine)() as session:
        profiles = (await session.execute(select(CourierProfile))).scalars().all()
    assert len(profiles) == 1


async def test_customer_has_no_courier_profile(client: AsyncClient) -> None:
    assert (await client.get("/users/me")).json()["courier_profile"] is None


async def test_update_profile_applies_only_supplied_fields(client: AsyncClient) -> None:
    await client.get("/users/me")

    first = await client.put("/users/me", json={"name": "Layla Al Mansouri"})
    assert first.status_code == 200, first.text
    assert first.json()["name"] == "Layla Al Mansouri"

    second = await client.put("/users/me", json={"email": "layla@example.ae"})
    assert second.json()["email"] == "layla@example.ae"
    # The earlier name must survive a later partial update.
    assert second.json()["name"] == "Layla Al Mansouri"


async def test_update_publishes_a_profile_event(
    client: AsyncClient, runtime: UserRuntime, event_bus: InMemoryEventBus
) -> None:
    await client.get("/users/me")
    await client.put("/users/me", json={"name": "Layla", "dietary_tags": ["halal", "vegan"]})
    await runtime.outbox_relay.drain_once()

    events = event_bus.events_of_type(EventType.USER_PROFILE_UPDATED.value)
    assert len(events) == 1
    assert events[0].data["name"] == "Layla"
    assert events[0].data["dietary_tags"] == ["halal", "vegan"]


async def test_no_op_update_publishes_nothing(
    client: AsyncClient, runtime: UserRuntime, event_bus: InMemoryEventBus
) -> None:
    await client.get("/users/me")
    response = await client.put("/users/me", json={})
    assert response.status_code == 200
    await runtime.outbox_relay.drain_once()

    assert event_bus.events_of_type(EventType.USER_PROFILE_UPDATED.value) == []


async def test_dietary_tags_are_normalised_and_deduplicated(client: AsyncClient) -> None:
    await client.get("/users/me")
    response = await client.put(
        "/users/me", json={"dietary_tags": ["Halal", " halal ", "VEGAN"]}
    )
    assert response.json()["dietary_tags"] == ["halal", "vegan"]


async def test_unknown_dietary_tag_is_rejected(client: AsyncClient) -> None:
    await client.get("/users/me")
    response = await client.put("/users/me", json={"dietary_tags": ["carnivore"]})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["field_errors"][0]["field"] == "dietary_tags"
    assert "carnivore" in body["error"]["field_errors"][0]["message"]


async def test_invalid_email_is_rejected(client: AsyncClient) -> None:
    await client.get("/users/me")
    response = await client.put("/users/me", json={"email": "not-an-email"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [{"phone": "+971509999999"}, {"role": "admin"}, {"id": "usr_other"}, {"status": "SUSPENDED"}],
)
async def test_protected_fields_cannot_be_set_through_the_profile_endpoint(
    client: AsyncClient, payload: dict, engine: AsyncEngine, customer: Principal
) -> None:
    await client.get("/users/me")
    response = await client.put("/users/me", json=payload)
    assert response.status_code == 200

    # Unknown fields are ignored rather than rejected, but they must not be applied: a
    # client must not be able to escalate its own role or hijack a phone number.
    async with session_factory_for(engine)() as session:
        user = (await session.execute(select(User))).scalar_one()
    assert user.role is Role.CUSTOMER
    assert user.status is UserStatus.ACTIVE
    assert user.id == customer.id
    assert user.phone is None


async def test_unauthenticated_requests_are_rejected(anonymous_client: AsyncClient) -> None:
    assert (await anonymous_client.get("/users/me")).status_code == 401
    assert (await anonymous_client.get("/users/me/addresses")).status_code == 401


async def test_suspended_user_cannot_update_their_profile(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await client.get("/users/me")
    async with session_factory_for(engine)() as session:
        user = (await session.execute(select(User))).scalar_one()
        user.status = UserStatus.SUSPENDED
        await session.commit()

    response = await client.put("/users/me", json={"name": "Layla"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_not_active"
