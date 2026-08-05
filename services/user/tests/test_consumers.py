"""Consuming registration events from the auth service."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_core.events.envelope import EventEnvelope, new_envelope
from marsool_core.events.topics import EventType
from marsool_core.security.principal import Principal, Role
from marsool_core.testing import session_factory_for
from marsool_user.models import CourierProfile, ProcessedEvent, User
from marsool_user.runtime import UserRuntime

pytestmark = pytest.mark.integration

PHONE = "+971501234567"


def user_registered(
    user_id: str, *, phone: str = PHONE, role: Role = Role.CUSTOMER
) -> EventEnvelope:
    return new_envelope(
        event_type=EventType.USER_REGISTERED,
        aggregate_id=user_id,
        aggregate_type="User",
        data={
            "user_id": user_id,
            "phone": phone,
            "role": role.value,
            "registered_at": "2026-08-05T19:00:00+00:00",
        },
    )


async def test_registration_event_provisions_the_profile(
    runtime: UserRuntime, engine: AsyncEngine, customer: Principal
) -> None:
    await runtime.event_router.dispatch(user_registered(customer.id))

    async with session_factory_for(engine)() as session:
        user = (await session.execute(select(User))).scalar_one()
    assert user.id == customer.id
    assert user.phone == PHONE
    assert user.role is Role.CUSTOMER


async def test_courier_registration_creates_a_courier_profile(
    runtime: UserRuntime, engine: AsyncEngine, customer: Principal
) -> None:
    await runtime.event_router.dispatch(user_registered(customer.id, role=Role.COURIER))

    async with session_factory_for(engine)() as session:
        profile = (await session.execute(select(CourierProfile))).scalar_one()
    assert profile.user_id == customer.id


async def test_redelivery_does_not_duplicate_the_user(
    runtime: UserRuntime, engine: AsyncEngine, customer: Principal
) -> None:
    envelope = user_registered(customer.id)
    await runtime.event_router.dispatch(envelope)
    await runtime.event_router.dispatch(envelope)

    async with session_factory_for(engine)() as session:
        users = (await session.execute(select(User))).scalars().all()
        processed = (await session.execute(select(ProcessedEvent))).scalars().all()
    assert len(users) == 1
    assert len(processed) == 1


async def test_event_after_jit_provisioning_fills_in_the_real_phone(
    client: AsyncClient, runtime: UserRuntime, engine: AsyncEngine, customer: Principal
) -> None:
    # The client raced ahead of the consumer, so the row exists with a placeholder phone.
    await client.get("/users/me")
    async with session_factory_for(engine)() as session:
        provisional = (await session.execute(select(User))).scalar_one()
    assert provisional.phone is None

    await runtime.event_router.dispatch(user_registered(customer.id))

    async with session_factory_for(engine)() as session:
        users = (await session.execute(select(User))).scalars().all()
    assert len(users) == 1, "reconciliation must not create a second user"
    assert users[0].phone == PHONE


async def test_jit_provisioning_after_the_event_keeps_the_real_phone(
    client: AsyncClient, runtime: UserRuntime, engine: AsyncEngine, customer: Principal
) -> None:
    # The other ordering: the consumer wins the race.
    await runtime.event_router.dispatch(user_registered(customer.id))

    body = (await client.get("/users/me")).json()

    assert body["phone"] == PHONE
    async with session_factory_for(engine)() as session:
        users = (await session.execute(select(User))).scalars().all()
    assert len(users) == 1


async def test_incomplete_registration_event_is_skipped(
    runtime: UserRuntime, engine: AsyncEngine
) -> None:
    malformed = new_envelope(
        event_type=EventType.USER_REGISTERED,
        aggregate_id="usr_unknown",
        aggregate_type="User",
        data={"phone": PHONE},
    )
    # A producer bug must not crash the consumer loop.
    assert await runtime.event_router.dispatch(malformed) is True

    async with session_factory_for(engine)() as session:
        assert (await session.execute(select(User))).scalars().all() == []
