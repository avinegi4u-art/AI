"""Delivery address CRUD, default handling, ownership and geospatial storage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_core.events.bus import InMemoryEventBus
from marsool_core.events.topics import EventType
from marsool_core.security.principal import Principal
from marsool_core.testing import make_principal, session_factory_for
from marsool_user.models import Address
from marsool_user.runtime import UserRuntime
from marsool_user.testing import AL_REEM_ISLAND, YAS_ISLAND

ClientAs = Callable[[Principal], Awaitable[AsyncClient]]


async def create_address(client: AsyncClient, payload: dict[str, Any] | None = None) -> dict:
    response = await client.post("/users/me/addresses", json=payload or AL_REEM_ISLAND)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_create_address_returns_the_stored_record(client: AsyncClient) -> None:
    await client.get("/users/me")
    body = await create_address(client)

    assert body["id"].startswith("adr_")
    assert body["line1"] == "Sky Tower, Shams Abu Dhabi"
    assert body["community"] == "Al Reem Island"
    assert body["latitude"] == "24.494640"
    assert body["longitude"] == "54.399460"
    assert body["delivery_notes"] == "Leave with the concierge"


async def test_first_address_becomes_the_default(client: AsyncClient) -> None:
    await client.get("/users/me")
    first = await create_address(client)
    assert first["is_default"] is True

    second = await create_address(client, YAS_ISLAND)
    # Later additions do not silently steal the default.
    assert second["is_default"] is False
    assert (await client.get("/users/me")).json()["default_address_id"] == first["id"]


async def test_set_as_default_switches_the_default(client: AsyncClient) -> None:
    await client.get("/users/me")
    first = await create_address(client)
    second = await create_address(client, {**YAS_ISLAND, "set_as_default": True})

    assert second["is_default"] is True
    profile = (await client.get("/users/me")).json()
    assert profile["default_address_id"] == second["id"]

    addresses = (await client.get("/users/me/addresses")).json()
    by_id = {item["id"]: item for item in addresses}
    assert by_id[first["id"]]["is_default"] is False
    assert by_id[second["id"]]["is_default"] is True


async def test_coordinates_are_stored_as_a_postgis_point(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await client.get("/users/me")
    await create_address(client)

    async with session_factory_for(engine)() as session:
        # Read the geography column back through PostGIS. ST_MakePoint takes longitude
        # first, so a swapped pair is silent at write time and puts the address in the
        # wrong hemisphere; this asserts the order is right.
        stored = (
            await session.execute(select(func.ST_AsText(Address.location)))
        ).scalar_one()
    assert stored == "POINT(54.39946 24.49464)"


async def test_distance_query_uses_the_geography_column(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await client.get("/users/me")
    await create_address(client)
    await create_address(client, YAS_ISLAND)

    origin = "SRID=4326;POINT(54.399460 24.494640)"
    async with session_factory_for(engine)() as session:
        # ST_Distance on the geography type returns metres directly, which is what makes
        # this column usable for serviceability radii without any projection step.
        distances = (
            (
                await session.execute(
                    select(func.ST_Distance(Address.location, func.ST_GeogFromText(origin)))
                    .order_by(Address.created_at)
                )
            )
            .scalars()
            .all()
        )
    assert round(distances[0]) == 0
    assert 20_000 < distances[1] < 26_000


async def test_update_address_applies_partial_changes(client: AsyncClient) -> None:
    await client.get("/users/me")
    address = await create_address(client)

    response = await client.put(
        f"/users/me/addresses/{address['id']}",
        json={"apartment": "2801", "delivery_notes": "Call on arrival"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["apartment"] == "2801"
    assert body["delivery_notes"] == "Call on arrival"
    assert body["line1"] == address["line1"]


async def test_moving_an_address_updates_the_geography_column(
    client: AsyncClient, engine: AsyncEngine
) -> None:
    await client.get("/users/me")
    address = await create_address(client)

    await client.put(
        f"/users/me/addresses/{address['id']}",
        json={"latitude": "24.488600", "longitude": "54.607000"},
    )

    async with session_factory_for(engine)() as session:
        row = (
            await session.execute(
                select(
                    func.ST_Y(Address.location.ST_AsBinary()),
                    func.ST_X(Address.location.ST_AsBinary()),
                )
            )
        ).one()
    # A stale geography column would leave routing and serviceability using the old spot.
    assert round(float(row[0]), 4) == 24.4886
    assert round(float(row[1]), 4) == 54.607


async def test_delete_address_promotes_a_new_default(client: AsyncClient) -> None:
    await client.get("/users/me")
    first = await create_address(client)
    second = await create_address(client, YAS_ISLAND)

    assert (await client.delete(f"/users/me/addresses/{first['id']}")).status_code == 204

    profile = (await client.get("/users/me")).json()
    # A dangling default would break checkout, so the remaining address is promoted.
    assert profile["default_address_id"] == second["id"]


async def test_deleting_the_last_address_clears_the_default(client: AsyncClient) -> None:
    await client.get("/users/me")
    address = await create_address(client)

    await client.delete(f"/users/me/addresses/{address['id']}")

    assert (await client.get("/users/me")).json()["default_address_id"] is None
    assert (await client.get("/users/me/addresses")).json() == []


async def test_another_user_cannot_read_update_or_delete_an_address(
    client: AsyncClient, client_as: ClientAs
) -> None:
    await client.get("/users/me")
    victim_address = await create_address(client)

    attacker = await client_as(make_principal())
    await attacker.get("/users/me")

    assert (await attacker.get("/users/me/addresses")).json() == []
    update = await attacker.put(
        f"/users/me/addresses/{victim_address['id']}", json={"apartment": "hijacked"}
    )
    assert update.status_code == 404
    assert update.json()["error"]["code"] == "address_not_found"
    assert (await attacker.delete(f"/users/me/addresses/{victim_address['id']}")).status_code == 404


async def test_unknown_address_returns_not_found(client: AsyncClient) -> None:
    await client.get("/users/me")
    response = await client.put(
        "/users/me/addresses/adr_01J9Z8XQF3K7M2P4R6T8V0W1Y3", json={"apartment": "1"}
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latitude", "91.0"),
        ("latitude", "-91.0"),
        ("longitude", "181.0"),
        ("longitude", "-181.0"),
        ("line1", "ab"),
        ("country_code", "UAE"),
        ("label", "SPACESHIP"),
    ],
)
async def test_invalid_address_payloads_are_rejected(
    client: AsyncClient, field: str, value: str
) -> None:
    await client.get("/users/me")
    response = await client.post(
        "/users/me/addresses", json={**AL_REEM_ISLAND, field: value}
    )
    assert response.status_code == 422
    assert response.json()["error"]["field_errors"][0]["field"] == field


async def test_missing_coordinates_are_rejected(client: AsyncClient) -> None:
    await client.get("/users/me")
    payload = {key: value for key, value in AL_REEM_ISLAND.items() if key != "latitude"}
    response = await client.post("/users/me/addresses", json=payload)
    assert response.status_code == 422


async def test_country_code_is_upper_cased(client: AsyncClient) -> None:
    await client.get("/users/me")
    body = await create_address(client, {**AL_REEM_ISLAND, "country_code": "ae"})
    assert body["country_code"] == "AE"


async def test_address_limit_is_enforced(client: AsyncClient, settings) -> None:
    await client.get("/users/me")
    for index in range(settings.max_addresses_per_user):
        await create_address(client, {**AL_REEM_ISLAND, "nickname": f"address-{index}"})

    response = await client.post("/users/me/addresses", json=AL_REEM_ISLAND)
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "address_limit_reached"
    assert body["error"]["details"]["limit"] == settings.max_addresses_per_user


async def test_address_lifecycle_publishes_events(
    client: AsyncClient, runtime: UserRuntime, event_bus: InMemoryEventBus
) -> None:
    await client.get("/users/me")
    address = await create_address(client)
    await client.put(f"/users/me/addresses/{address['id']}", json={"apartment": "2801"})
    await client.delete(f"/users/me/addresses/{address['id']}")
    await runtime.outbox_relay.drain_once()

    added = event_bus.events_of_type(EventType.USER_ADDRESS_ADDED.value)
    updated = event_bus.events_of_type(EventType.USER_ADDRESS_UPDATED.value)
    deleted = event_bus.events_of_type(EventType.USER_ADDRESS_DELETED.value)

    assert added[0].data["address_id"] == address["id"]
    assert added[0].data["is_default"] is True
    assert updated[0].data["changed_fields"] == ["apartment"]
    # The update event carries field names only: an address is personal data and the
    # topic is consumed by analytics.
    assert "line1" not in updated[0].data
    assert deleted[0].data["address_id"] == address["id"]
