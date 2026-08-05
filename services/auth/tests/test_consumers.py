"""Auth's consumer-side read model and idempotent consumption."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_auth import CONSUMER_GROUP
from marsool_auth.models import AuthPrincipal, ProcessedEvent
from marsool_auth.runtime import AuthRuntime
from marsool_core.events.envelope import new_envelope
from marsool_core.events.topics import EventType
from marsool_core.testing import session_factory_for

pytestmark = pytest.mark.integration


def profile_updated(user_id: str, name: str) -> object:
    return new_envelope(
        event_type=EventType.USER_PROFILE_UPDATED,
        aggregate_id=user_id,
        aggregate_type="User",
        data={"user_id": user_id, "name": name},
    )


async def _create_principal(engine: AsyncEngine, phone: str = "+971501234567") -> str:
    async with session_factory_for(engine)() as session:
        principal_record = AuthPrincipal(phone=phone)
        session.add(principal_record)
        await session.commit()
        return principal_record.id


async def test_profile_update_refreshes_the_denormalised_name(
    runtime: AuthRuntime, engine: AsyncEngine
) -> None:
    user_id = await _create_principal(engine)

    await runtime.event_router.dispatch(profile_updated(user_id, "Layla Al Mansouri"))

    async with session_factory_for(engine)() as session:
        stored = (await session.execute(select(AuthPrincipal))).scalar_one()
    assert stored.display_name == "Layla Al Mansouri"


async def test_updated_name_appears_in_the_next_login(
    runtime: AuthRuntime, login, engine: AsyncEngine
) -> None:
    first = await login()
    await runtime.event_router.dispatch(profile_updated(first["user"]["id"], "Layla"))

    second = await login()
    assert second["user"]["name"] == "Layla"


async def test_redelivery_is_recorded_once(runtime: AuthRuntime, engine: AsyncEngine) -> None:
    user_id = await _create_principal(engine)
    envelope = profile_updated(user_id, "Layla")

    await runtime.event_router.dispatch(envelope)
    await runtime.event_router.dispatch(envelope)

    async with session_factory_for(engine)() as session:
        processed = (await session.execute(select(ProcessedEvent))).scalars().all()
    assert len(processed) == 1
    assert processed[0].consumer_group == CONSUMER_GROUP


async def test_unknown_event_types_are_ignored(runtime: AuthRuntime) -> None:
    # A service subscribed to a topic sees events it does not care about; that is normal.
    unrelated = new_envelope(
        event_type=EventType.ORDER_CREATED,
        aggregate_id="ord_1",
        aggregate_type="Order",
        data={},
    )
    assert await runtime.event_router.dispatch(unrelated) is False


async def test_event_without_a_user_id_is_skipped(runtime: AuthRuntime) -> None:
    malformed = new_envelope(
        event_type=EventType.USER_PROFILE_UPDATED,
        aggregate_id="usr_unknown",
        aggregate_type="User",
        data={"name": "No Id"},
    )
    # A malformed payload from an upstream producer must not crash the consumer loop.
    assert await runtime.event_router.dispatch(malformed) is True
