"""Menu assembly, caching and item management."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from httpx import AsyncClient
from redis.asyncio import Redis

from marsool_catalog.runtime import CatalogRuntime
from marsool_core.cache import cache_key
from marsool_core.events.bus import InMemoryEventBus
from marsool_core.events.topics import EventType
from marsool_core.security.principal import Principal, Role
from marsool_core.testing import make_principal

from .conftest import MERCHANT_ID, OTHER_MERCHANT_ID

SeedMenu = Callable[..., Awaitable[dict[str, str]]]
ClientAs = Callable[[Principal], Awaitable[AsyncClient]]


async def test_menu_returns_the_nested_structure(client: AsyncClient, seed_menu: SeedMenu) -> None:
    await seed_menu()

    response = await client.get(f"/merchants/{MERCHANT_ID}/menu")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["merchant_id"] == MERCHANT_ID
    assert body["currency"] == "AED"
    assert [category["name"] for category in body["categories"]] == ["Mains", "Drinks"]

    mains = body["categories"][0]
    assert [item["name"] for item in mains["items"]] == ["Chicken Machboos", "Grilled Hammour"]
    machboos = mains["items"][0]
    assert machboos["price"] == "38.00"
    assert machboos["tags"] == ["halal", "house_special"]
    assert [group["name"] for group in machboos["option_groups"]] == ["Portion", "Add-ons"]


async def test_option_groups_expose_their_selection_rules(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    await seed_menu()

    body = (await client.get(f"/merchants/{MERCHANT_ID}/menu")).json()
    groups = {
        group["name"]: group
        for group in body["categories"][0]["items"][0]["option_groups"]
    }

    assert groups["Portion"]["selection_type"] == "SINGLE"
    assert groups["Portion"]["is_required"] is True
    assert groups["Add-ons"]["selection_type"] == "MULTI"
    assert groups["Add-ons"]["is_required"] is False
    assert groups["Add-ons"]["max_select"] == 3


async def test_option_prices_and_availability_are_exposed(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    await seed_menu()

    body = (await client.get(f"/merchants/{MERCHANT_ID}/menu")).json()
    add_ons = body["categories"][0]["items"][0]["option_groups"][1]
    options = {option["name"]: option for option in add_ons["options"]}

    assert options["Large" if "Large" in options else "Extra chicken"]["price_delta"] == "15.00"
    assert options["Sold-out side"]["is_available"] is False


async def test_item_counts_are_reported(client: AsyncClient, seed_menu: SeedMenu) -> None:
    await seed_menu()
    body = (await client.get(f"/merchants/{MERCHANT_ID}/menu")).json()
    assert body["item_count"] == 3
    assert body["available_item_count"] == 3


async def test_merchant_without_a_menu_returns_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/merchants/{OTHER_MERCHANT_ID}/menu")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "menu_not_found"


async def test_menu_is_cached_after_the_first_read(
    client: AsyncClient, seed_menu: SeedMenu, redis_client: Redis
) -> None:
    await seed_menu()
    key = cache_key("catalog", "menu", MERCHANT_ID)
    assert await redis_client.get(key) is None

    await client.get(f"/merchants/{MERCHANT_ID}/menu")

    assert await redis_client.get(key) is not None


async def test_cached_and_uncached_reads_agree(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    await seed_menu()
    first = (await client.get(f"/merchants/{MERCHANT_ID}/menu")).json()
    second = (await client.get(f"/merchants/{MERCHANT_ID}/menu")).json()
    # The second read is served from Redis; a serialisation mismatch here would show up as
    # subtly different prices or missing fields for most users.
    assert first == second


async def test_availability_change_invalidates_the_cache(
    client: AsyncClient,
    client_as: ClientAs,
    seed_menu: SeedMenu,
    merchant_staff: Principal,
) -> None:
    ids = await seed_menu()
    machboos_id = ids["item:Chicken Machboos"]

    # Warm the cache, then take the item out of stock.
    assert (await client.get(f"/merchants/{MERCHANT_ID}/menu")).json()["available_item_count"] == 3
    staff_client = await client_as(merchant_staff)
    response = await staff_client.put(
        f"/catalog/items/{machboos_id}/availability",
        json={"is_available": False, "reason": "sold out"},
    )
    assert response.status_code == 200, response.text

    refreshed = (await client.get(f"/merchants/{MERCHANT_ID}/menu")).json()
    # A stale cache here would keep selling an item the kitchen cannot make.
    assert refreshed["available_item_count"] == 2
    machboos = refreshed["categories"][0]["items"][0]
    assert machboos["is_available"] is False


async def test_price_change_invalidates_the_cache(
    client: AsyncClient,
    client_as: ClientAs,
    seed_menu: SeedMenu,
    merchant_staff: Principal,
) -> None:
    ids = await seed_menu()
    await client.get(f"/merchants/{MERCHANT_ID}/menu")

    staff_client = await client_as(merchant_staff)
    await staff_client.put(
        f"/catalog/items/{ids['item:Karak Chai']}", json={"price": "11.50"}
    )

    body = (await client.get(f"/merchants/{MERCHANT_ID}/menu")).json()
    drinks = body["categories"][1]["items"]
    assert drinks[0]["price"] == "11.50"


async def test_availability_change_publishes_an_event(
    client_as: ClientAs,
    seed_menu: SeedMenu,
    merchant_staff: Principal,
    runtime: CatalogRuntime,
    event_bus: InMemoryEventBus,
) -> None:
    ids = await seed_menu()
    staff_client = await client_as(merchant_staff)

    await staff_client.put(
        f"/catalog/items/{ids['item:Karak Chai']}/availability",
        json={"is_available": False, "reason": "milk delivery late"},
    )
    await runtime.outbox_relay.drain_once()

    events = event_bus.events_of_type(EventType.MENU_ITEM_AVAILABILITY_CHANGED.value)
    assert events[0].data["is_available"] is False
    assert events[0].data["reason"] == "milk delivery late"
    assert events[0].data["merchant_id"] == MERCHANT_ID


async def test_no_op_availability_change_publishes_nothing(
    client_as: ClientAs,
    seed_menu: SeedMenu,
    merchant_staff: Principal,
    runtime: CatalogRuntime,
    event_bus: InMemoryEventBus,
) -> None:
    ids = await seed_menu()
    staff_client = await client_as(merchant_staff)

    await staff_client.put(
        f"/catalog/items/{ids['item:Karak Chai']}/availability", json={"is_available": True}
    )
    await runtime.outbox_relay.drain_once()

    assert event_bus.events_of_type(EventType.MENU_ITEM_AVAILABILITY_CHANGED.value) == []


async def test_staff_cannot_edit_another_merchants_item(
    client_as: ClientAs, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()
    outsider = make_principal(role=Role.MERCHANT, merchant_ids=(OTHER_MERCHANT_ID,))
    staff_client = await client_as(outsider)

    response = await staff_client.put(
        f"/catalog/items/{ids['item:Karak Chai']}", json={"price": "1.00"}
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "merchant_access_denied"


async def test_customer_cannot_edit_items(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    response = await client.put(
        f"/catalog/items/{ids['item:Karak Chai']}", json={"price": "1.00"}
    )

    assert response.status_code == 403


async def test_admin_can_edit_any_item(
    client_as: ClientAs, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()
    admin_client = await client_as(make_principal(role=Role.ADMIN))

    response = await admin_client.put(
        f"/catalog/items/{ids['item:Karak Chai']}", json={"name": "Karak Tea"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Karak Tea"


async def test_unknown_item_returns_not_found(
    client_as: ClientAs, merchant_staff: Principal
) -> None:
    staff_client = await client_as(merchant_staff)
    response = await staff_client.put(
        "/catalog/items/itm_01J9Z8XQF3K7M2P4R6T8V0W1Y3", json={"name": "Ghost"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "item_not_found"


async def test_negative_price_is_rejected(
    client_as: ClientAs, seed_menu: SeedMenu, merchant_staff: Principal
) -> None:
    ids = await seed_menu()
    staff_client = await client_as(merchant_staff)

    response = await staff_client.put(
        f"/catalog/items/{ids['item:Karak Chai']}", json={"price": "-1.00"}
    )

    assert response.status_code == 422


async def test_menus_are_isolated_per_merchant(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    await seed_menu(MERCHANT_ID)
    await seed_menu(OTHER_MERCHANT_ID)

    first = (await client.get(f"/merchants/{MERCHANT_ID}/menu")).json()
    second = (await client.get(f"/merchants/{OTHER_MERCHANT_ID}/menu")).json()

    assert first["menu_id"] != second["menu_id"]
    first_item_ids = {
        item["id"] for category in first["categories"] for item in category["items"]
    }
    second_item_ids = {
        item["id"] for category in second["categories"] for item in category["items"]
    }
    assert first_item_ids.isdisjoint(second_item_ids)
