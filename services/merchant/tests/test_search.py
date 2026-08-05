"""Geospatial merchant discovery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient

from marsool_merchant.models import Merchant, MerchantStatus
from marsool_merchant.testing import AL_REEM_ISLAND_POLYGON, ALL_WEEK_DAYTIME

MerchantFactory = Callable[..., Awaitable[Merchant]]

# Search origin: Sky Tower, Al Reem Island.
ORIGIN = {"lat": "24.494640", "lng": "54.399460"}

# Real Abu Dhabi venues at increasing distance from the origin.
NEAR = {
    "name": "Reem Grill",
    "slug": "reem-grill",
    "latitude": "24.496000",
    "longitude": "54.401000",
}
MID = {
    "name": "Corniche Cafe",
    "slug": "corniche-cafe",
    "latitude": "24.475900",
    "longitude": "54.322000",
}
FAR = {
    "name": "Yas Kitchen",
    "slug": "yas-kitchen",
    "latitude": "24.488600",
    "longitude": "54.607000",
}


async def search(client: AsyncClient, **params: object) -> dict:
    response = await client.get("/merchants", params={**ORIGIN, **params})
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_search_returns_nearby_merchants_ordered_by_distance(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    await merchant_factory(**FAR, delivery_radius_m=50_000)
    await merchant_factory(**NEAR, delivery_radius_m=50_000)
    await merchant_factory(**MID, delivery_radius_m=50_000)

    body = await search(client, radius_m=30_000)

    names = [item["name"] for item in body["items"]]
    assert names == ["Reem Grill", "Corniche Cafe", "Yas Kitchen"]
    distances = [item["distance_m"] for item in body["items"]]
    assert distances == sorted(distances)
    # Nearest venue is a couple of hundred metres away; the far one is over 20 km.
    assert distances[0] < 300
    assert distances[2] > 20_000


async def test_radius_excludes_distant_merchants(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    await merchant_factory(**NEAR, delivery_radius_m=50_000)
    await merchant_factory(**FAR, delivery_radius_m=50_000)

    body = await search(client, radius_m=1_000)

    assert [item["name"] for item in body["items"]] == ["Reem Grill"]


async def test_only_active_merchants_are_returned(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    await merchant_factory(**NEAR)
    await merchant_factory(
        name="Draft Kitchen",
        slug="draft-kitchen",
        latitude="24.496100",
        longitude="54.401100",
        status=MerchantStatus.DRAFT,
    )
    await merchant_factory(
        name="Suspended Kitchen",
        slug="suspended-kitchen",
        latitude="24.496200",
        longitude="54.401200",
        status=MerchantStatus.SUSPENDED,
    )

    body = await search(client)

    assert [item["name"] for item in body["items"]] == ["Reem Grill"]


async def test_delivery_radius_gates_inclusion(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    # Yas Kitchen is ~23 km away and only delivers within 3 km, so a wide search radius
    # must still exclude it: the customer is outside its coverage.
    await merchant_factory(**FAR, delivery_radius_m=3_000)
    await merchant_factory(**NEAR, delivery_radius_m=3_000)

    body = await search(client, radius_m=30_000)

    assert [item["name"] for item in body["items"]] == ["Reem Grill"]


async def test_service_area_polygon_overrides_the_radius(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    # A tiny delivery radius would exclude this merchant, but its polygon covers the
    # origin — polygons are authoritative because real coverage is not a circle.
    await merchant_factory(
        name="Polygon Grill",
        slug="polygon-grill",
        latitude="24.481000",
        longitude="54.383000",
        delivery_radius_m=100,
        service_area_wkt=AL_REEM_ISLAND_POLYGON,
    )

    body = await search(client, radius_m=10_000)

    assert [item["name"] for item in body["items"]] == ["Polygon Grill"]


async def test_point_outside_the_service_area_is_excluded(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    # Same merchant and polygon, but the customer is on Yas Island, outside it. A generous
    # delivery radius must not override the polygon.
    await merchant_factory(
        name="Polygon Grill",
        slug="polygon-grill",
        latitude="24.481000",
        longitude="54.383000",
        delivery_radius_m=50_000,
        service_area_wkt=AL_REEM_ISLAND_POLYGON,
    )

    response = await client.get(
        "/merchants", params={"lat": "24.488600", "lng": "54.607000", "radius_m": 30_000}
    )

    assert response.json()["items"] == []


async def test_cuisine_filter_matches_any_requested_cuisine(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    await merchant_factory(
        name="Sushi Reem", slug="sushi-reem", latitude="24.4950", longitude="54.3995",
        cuisines=["japanese", "seafood"],
    )
    await merchant_factory(
        name="Pizza Reem", slug="pizza-reem", latitude="24.4951", longitude="54.3996",
        cuisines=["pizza", "italian"],
    )
    await merchant_factory(
        name="Grill Reem", slug="grill-reem", latitude="24.4952", longitude="54.3997",
        cuisines=["grills"],
    )

    japanese = await search(client, cuisine=["japanese"])
    assert [item["name"] for item in japanese["items"]] == ["Sushi Reem"]

    either = await search(client, cuisine=["japanese", "pizza"])
    assert {item["name"] for item in either["items"]} == {"Sushi Reem", "Pizza Reem"}


async def test_name_search_is_case_insensitive(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    await merchant_factory(**NEAR)
    await merchant_factory(**MID)

    body = await search(client, q="reem", radius_m=30_000)

    assert [item["name"] for item in body["items"]] == ["Reem Grill"]


async def test_paused_merchant_is_listed_but_flagged(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    # A kitchen that has paused stays visible so customers can browse and see why, rather
    # than the storefront vanishing mid-session.
    await merchant_factory(**NEAR, accepting_orders=False, hours=ALL_WEEK_DAYTIME)

    body = await search(client)

    assert body["items"][0]["accepting_orders"] is False


async def test_open_now_filter_requires_accepting_orders(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    await merchant_factory(**NEAR, accepting_orders=False, hours=ALL_WEEK_DAYTIME)

    assert (await search(client, open_now=True))["items"] == []


async def test_eta_grows_with_distance(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    await merchant_factory(**NEAR, prep_time_minutes=20, delivery_radius_m=50_000)
    await merchant_factory(**FAR, prep_time_minutes=20, delivery_radius_m=50_000)

    body = await search(client, radius_m=30_000)

    near_eta, far_eta = (item["eta_min"] for item in body["items"])
    # Prep time is identical, so the whole difference is travel.
    assert near_eta < far_eta
    assert near_eta >= 20


async def test_pagination_walks_every_merchant_exactly_once(
    client: AsyncClient, merchant_factory: MerchantFactory
) -> None:
    for index in range(7):
        await merchant_factory(
            name=f"Venue {index}",
            slug=f"venue-{index}",
            # Spread eastwards so distances are distinct and ordering is stable.
            latitude="24.494640",
            longitude=f"54.{400 + index * 3:03d}000",
            delivery_radius_m=50_000,
        )

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):
        params = {"radius_m": 30_000, "limit": 3}
        if cursor:
            params["cursor"] = cursor
        page = await search(client, **params)
        seen.extend(item["name"] for item in page["items"])
        cursor = page["meta"]["next_cursor"]
        if cursor is None:
            break

    assert cursor is None
    assert len(seen) == 7
    assert len(set(seen)) == 7, "pagination must not repeat or skip merchants"


async def test_malformed_cursor_is_a_client_error(client: AsyncClient) -> None:
    response = await client.get("/merchants", params={**ORIGIN, "cursor": "!!!not-base64!!!"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_cursor"


@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"lat": "91", "lng": "54.4"}, "latitude out of range"),
        ({"lat": "24.4", "lng": "181"}, "longitude out of range"),
        ({"lat": "24.4", "lng": "54.4", "radius_m": 10}, "radius below minimum"),
        ({"lat": "24.4", "lng": "54.4", "limit": 500}, "limit above maximum"),
    ],
)
async def test_invalid_search_parameters_are_rejected(
    client: AsyncClient, params: dict, reason: str
) -> None:
    response = await client.get("/merchants", params=params)
    assert response.status_code == 422, reason


async def test_missing_coordinates_are_rejected(client: AsyncClient) -> None:
    assert (await client.get("/merchants")).status_code == 422


async def test_radius_is_capped_at_the_configured_maximum(
    client: AsyncClient, merchant_factory: MerchantFactory, settings
) -> None:
    # A merchant beyond the configured cap must not be reachable by asking for a huge radius.
    await merchant_factory(
        name="Dubai Kitchen",
        slug="dubai-kitchen",
        latitude="25.204800",
        longitude="55.270800",
        delivery_radius_m=50_000,
    )

    body = await search(client, radius_m=100_000)

    assert settings.max_search_radius_m < 100_000
    assert body["items"] == []
