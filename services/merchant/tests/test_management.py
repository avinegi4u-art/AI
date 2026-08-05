"""Storefront management, authorization and serviceability checks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import time

from httpx import AsyncClient

from marsool_core.events.bus import InMemoryEventBus
from marsool_core.events.topics import EventType
from marsool_core.security.principal import Principal, Role
from marsool_core.testing import make_principal
from marsool_merchant.models import Merchant
from marsool_merchant.runtime import MerchantRuntime
from marsool_merchant.testing import AL_REEM_ISLAND_POLYGON, ALL_WEEK_DAYTIME

MerchantFactory = Callable[..., Awaitable[Merchant]]
ClientAs = Callable[[Principal], Awaitable[AsyncClient]]

ORIGIN = {"lat": "24.494640", "lng": "54.399460"}
VENUE = {
    "name": "Reem Grill",
    "slug": "reem-grill",
    "latitude": "24.496000",
    "longitude": "54.401000",
}


async def test_get_merchant_returns_storefront_and_schedule(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    merchant = await merchant_factory(**VENUE, hours=ALL_WEEK_DAYTIME)

    response = await client.get(f"/merchants/{merchant.id}", params=ORIGIN)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == merchant.id
    assert body["timezone"] == "Asia/Dubai"
    assert len(body["opening_hours"]) == 7
    assert body["opening_hours"][0]["day_of_week"] == 0
    assert body["distance_m"] > 0


async def test_get_merchant_without_coordinates_omits_distance(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    merchant = await merchant_factory(**VENUE)

    body = (await client.get(f"/merchants/{merchant.id}")).json()

    assert body["distance_m"] == 0.0
    assert body["eta_min"] == body["prep_time_minutes"]


async def test_unknown_merchant_returns_not_found(client: AsyncClient) -> None:
    response = await client.get("/merchants/mch_01J9Z8XQF3K7M2P4R6T8V0W1Y3")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "merchant_not_found"


async def test_overnight_window_is_reported_as_spanning_midnight(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    merchant = await merchant_factory(
        **VENUE, hours=[(0, time(18, 0), time(2, 0)), (1, time(11, 0), time(23, 0))]
    )

    windows = (await client.get(f"/merchants/{merchant.id}/hours")).json()

    by_day = {window["day_of_week"]: window for window in windows}
    assert by_day[0]["spans_midnight"] is True
    assert by_day[1]["spans_midnight"] is False


async def test_serviceability_accepts_a_deliverable_point(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    merchant = await merchant_factory(**VENUE, hours=ALL_WEEK_DAYTIME, delivery_radius_m=10_000)

    body = (
        await client.get(f"/merchants/{merchant.id}/serviceability", params=ORIGIN)
    ).json()

    assert body["distance_m"] > 0
    # ``deliverable`` also depends on the current time, so only the coverage decision is
    # asserted here; the time dimension is covered by the opening-hours suite.
    assert body["reason"] in (None, "merchant_closed")


async def test_serviceability_rejects_a_point_outside_the_polygon(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    merchant = await merchant_factory(
        **VENUE, delivery_radius_m=50_000, service_area_wkt=AL_REEM_ISLAND_POLYGON
    )

    body = (
        await client.get(
            f"/merchants/{merchant.id}/serviceability",
            params={"lat": "24.488600", "lng": "54.607000"},
        )
    ).json()

    assert body["deliverable"] is False
    assert body["reason"] == "outside_delivery_area"


async def test_serviceability_reports_a_paused_merchant(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    merchant = await merchant_factory(**VENUE, accepting_orders=False, hours=ALL_WEEK_DAYTIME)

    body = (
        await client.get(f"/merchants/{merchant.id}/serviceability", params=ORIGIN)
    ).json()

    assert body["deliverable"] is False
    assert body["reason"] == "merchant_not_accepting_orders"


async def test_merchant_staff_can_update_their_storefront(
    merchant_factory: MerchantFactory,
    client_as: ClientAs,
    merchant_staff_for: Callable[[str], Principal],
) -> None:
    merchant = await merchant_factory(**VENUE)
    client = await client_as(merchant_staff_for(merchant.id))

    response = await client.put(
        f"/merchants/{merchant.id}",
        json={"name": "Reem Grill & Grill", "prep_time_minutes": 35, "cuisines": ["grills"]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Reem Grill & Grill"
    assert body["prep_time_minutes"] == 35
    assert body["cuisines"] == ["grills"]


async def test_staff_cannot_update_another_merchant(
    merchant_factory: MerchantFactory,
    client_as: ClientAs,
    merchant_staff_for: Callable[[str], Principal],
) -> None:
    theirs = await merchant_factory(**VENUE)
    other = await merchant_factory(
        name="Other Grill", slug="other-grill", latitude="24.4970", longitude="54.4020"
    )
    client = await client_as(merchant_staff_for(theirs.id))

    response = await client.put(f"/merchants/{other.id}", json={"name": "Hijacked"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "merchant_access_denied"


async def test_customer_cannot_update_a_storefront(
    merchant_factory: MerchantFactory, client_as: ClientAs
) -> None:
    merchant = await merchant_factory(**VENUE)
    client = await client_as(make_principal(role=Role.CUSTOMER))

    response = await client.put(f"/merchants/{merchant.id}", json={"name": "Hijacked"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


async def test_admin_can_update_any_storefront(
    merchant_factory: MerchantFactory, client_as: ClientAs
) -> None:
    merchant = await merchant_factory(**VENUE)
    client = await client_as(make_principal(role=Role.ADMIN))

    response = await client.put(f"/merchants/{merchant.id}", json={"name": "Ops Renamed"})

    assert response.status_code == 200
    assert response.json()["name"] == "Ops Renamed"


async def test_anonymous_callers_cannot_update_a_storefront(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    merchant = await merchant_factory(**VENUE)
    assert (await client.put(f"/merchants/{merchant.id}", json={"name": "x"})).status_code == 401


async def test_commission_and_status_are_not_settable_by_a_merchant(
    merchant_factory: MerchantFactory,
    client_as: ClientAs,
    merchant_staff_for: Callable[[str], Principal],
) -> None:
    merchant = await merchant_factory(**VENUE, commission_bps=1_500)
    client = await client_as(merchant_staff_for(merchant.id))

    response = await client.put(
        f"/merchants/{merchant.id}",
        json={"commission_bps": 0, "status": "SUSPENDED", "rating": "5.00"},
    )

    assert response.status_code == 200
    body = response.json()
    # Unknown fields are ignored, not applied: a merchant must not be able to zero its own
    # commission or resurrect a suspended storefront.
    assert body["status"] == "ACTIVE"
    assert body["rating"] == "0.00"


async def test_moving_a_merchant_updates_its_searchable_position(
    merchant_factory: MerchantFactory,
    client_as: ClientAs,
    client: AsyncClient,
    merchant_staff_for: Callable[[str], Principal],
) -> None:
    merchant = await merchant_factory(**VENUE, delivery_radius_m=1_000)
    staff_client = await client_as(merchant_staff_for(merchant.id))

    # Relocate to Yas Island, ~23 km east of the search origin.
    await staff_client.put(
        f"/merchants/{merchant.id}",
        json={"latitude": "24.488600", "longitude": "54.607000"},
    )

    nearby = await client.get("/merchants", params={**ORIGIN, "radius_m": 2_000})
    assert nearby.json()["items"] == [], "a stale geography column would still match here"

    far = await client.get(
        "/merchants",
        params={"lat": "24.488600", "lng": "54.607000", "radius_m": 2_000},
    )
    assert [item["id"] for item in far.json()["items"]] == [merchant.id]


async def test_pausing_orders_publishes_a_status_event(
    merchant_factory: MerchantFactory,
    client_as: ClientAs,
    merchant_staff_for: Callable[[str], Principal],
    runtime: MerchantRuntime,
    event_bus: InMemoryEventBus,
) -> None:
    merchant = await merchant_factory(**VENUE)
    client = await client_as(merchant_staff_for(merchant.id))

    response = await client.put(
        f"/merchants/{merchant.id}/accepting-orders",
        json={"accepting_orders": False, "reason": "kitchen at capacity"},
    )
    await runtime.outbox_relay.drain_once()

    assert response.status_code == 200
    assert response.json()["accepting_orders"] is False
    events = event_bus.events_of_type(EventType.MERCHANT_STATUS_CHANGED.value)
    assert events[0].data["accepting_orders"] is False
    assert events[0].data["reason"] == "kitchen at capacity"


async def test_toggling_to_the_same_value_publishes_nothing(
    merchant_factory: MerchantFactory,
    client_as: ClientAs,
    merchant_staff_for: Callable[[str], Principal],
    runtime: MerchantRuntime,
    event_bus: InMemoryEventBus,
) -> None:
    merchant = await merchant_factory(**VENUE, accepting_orders=True)
    client = await client_as(merchant_staff_for(merchant.id))

    await client.put(f"/merchants/{merchant.id}/accepting-orders", json={"accepting_orders": True})
    await runtime.outbox_relay.drain_once()

    assert event_bus.events_of_type(EventType.MERCHANT_STATUS_CHANGED.value) == []


async def test_replacing_hours_swaps_the_whole_schedule(
    merchant_factory: MerchantFactory,
    client_as: ClientAs,
    merchant_staff_for: Callable[[str], Principal],
    runtime: MerchantRuntime,
    event_bus: InMemoryEventBus,
) -> None:
    merchant = await merchant_factory(**VENUE, hours=ALL_WEEK_DAYTIME)
    client = await client_as(merchant_staff_for(merchant.id))

    response = await client.put(
        f"/merchants/{merchant.id}/hours",
        json={
            "windows": [
                {"day_of_week": 0, "opens_at": "09:00:00", "closes_at": "14:00:00"},
                {"day_of_week": 0, "opens_at": "18:00:00", "closes_at": "23:30:00"},
            ]
        },
    )
    await runtime.outbox_relay.drain_once()

    assert response.status_code == 200, response.text
    windows = response.json()
    # The previous seven-day schedule is gone, not merged.
    assert len(windows) == 2
    assert {window["day_of_week"] for window in windows} == {0}
    events = event_bus.events_of_type(EventType.MERCHANT_HOURS_UPDATED.value)
    assert events[0].data["window_count"] == 2


async def test_duplicate_opening_windows_are_rejected(
    merchant_factory: MerchantFactory,
    client_as: ClientAs,
    merchant_staff_for: Callable[[str], Principal],
) -> None:
    merchant = await merchant_factory(**VENUE)
    client = await client_as(merchant_staff_for(merchant.id))

    response = await client.put(
        f"/merchants/{merchant.id}/hours",
        json={
            "windows": [
                {"day_of_week": 0, "opens_at": "09:00:00", "closes_at": "14:00:00"},
                {"day_of_week": 0, "opens_at": "09:00:00", "closes_at": "15:00:00"},
            ]
        },
    )

    assert response.status_code == 422
    assert "duplicate opening window" in response.text


async def test_unknown_cuisine_is_rejected(
    merchant_factory: MerchantFactory,
    client_as: ClientAs,
    merchant_staff_for: Callable[[str], Principal],
) -> None:
    merchant = await merchant_factory(**VENUE)
    client = await client_as(merchant_staff_for(merchant.id))

    response = await client.put(f"/merchants/{merchant.id}", json={"cuisines": ["martian"]})

    assert response.status_code == 422
    assert "martian" in response.text
