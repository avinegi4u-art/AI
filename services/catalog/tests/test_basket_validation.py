"""Basket validation and pricing.

These rules are the contract the order service depends on, so each rule gets its own test
rather than being covered incidentally by a happy-path case.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from httpx import AsyncClient

from marsool_core.security.principal import Principal

from .conftest import MERCHANT_ID, OTHER_MERCHANT_ID

SeedMenu = Callable[..., Awaitable[dict[str, str]]]
ClientAs = Callable[[Principal], Awaitable[AsyncClient]]


async def validate(client: AsyncClient, lines: list[dict], merchant_id: str = MERCHANT_ID) -> dict:
    response = await client.post(
        f"/merchants/{merchant_id}/basket/validate", json={"lines": lines}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def issue_codes(body: dict) -> list[str]:
    return [issue["code"] for issue in body["issues"]]


async def test_valid_basket_is_priced(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    body = await validate(
        client,
        [
            {
                "item_id": ids["item:Chicken Machboos"],
                "quantity": 2,
                "selections": [
                    {"group_id": ids["group:Portion"], "option_ids": [ids["option:Large"]]},
                    {
                        "group_id": ids["group:Add-ons"],
                        "option_ids": [ids["option:Salad"], ids["option:Laban"]],
                    },
                ],
                "notes": "No coriander",
            }
        ],
    )

    assert body["valid"] is True
    assert body["issues"] == []
    line = body["lines"][0]
    assert line["unit_base_price"] == "38.00"
    # Large (+12.00) plus Salad (+7.00) plus Laban (+5.00).
    assert line["unit_options_price"] == "24.00"
    assert line["unit_price"] == "62.00"
    assert line["line_total"] == "124.00"
    assert body["items_amount"] == "124.00"
    assert line["notes"] == "No coriander"
    assert {option["name"] for option in line["selected_options"]} == {"Large", "Salad", "Laban"}


async def test_multiple_lines_are_summed(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    body = await validate(
        client,
        [
            {
                "item_id": ids["item:Chicken Machboos"],
                "quantity": 1,
                "selections": [
                    {"group_id": ids["group:Portion"], "option_ids": [ids["option:Regular"]]}
                ],
            },
            {"item_id": ids["item:Karak Chai"], "quantity": 3},
        ],
    )

    assert body["valid"] is True
    # 38.00 + (3 x 9.00)
    assert body["items_amount"] == "65.00"


async def test_item_without_option_groups_needs_no_selections(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    body = await validate(client, [{"item_id": ids["item:Grilled Hammour"], "quantity": 1}])

    assert body["valid"] is True
    assert body["items_amount"] == "65.00"


async def test_unknown_item_is_reported(client: AsyncClient, seed_menu: SeedMenu) -> None:
    await seed_menu()

    body = await validate(client, [{"item_id": "itm_01J9Z8XQF3K7M2P4R6T8V0W1Y3", "quantity": 1}])

    assert body["valid"] is False
    assert issue_codes(body) == ["item_not_found"]
    assert body["items_amount"] == "0.00"


async def test_item_from_another_merchant_is_rejected(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    await seed_menu(MERCHANT_ID)
    other_ids = await seed_menu(OTHER_MERCHANT_ID)


    # Ordering another merchant's item would make the order unfulfillable and the delivery
    # unroutable, so it is rejected rather than silently priced.
    body = await validate(
        client, [{"item_id": other_ids["item:Karak Chai"], "quantity": 1}], MERCHANT_ID
    )

    assert body["valid"] is False
    assert issue_codes(body) == ["item_not_in_merchant"]


async def test_unavailable_item_is_rejected(
    client: AsyncClient,
    client_as: ClientAs,
    seed_menu: SeedMenu,
    merchant_staff: Principal,
) -> None:
    ids = await seed_menu()
    staff_client = await client_as(merchant_staff)
    await staff_client.put(
        f"/catalog/items/{ids['item:Karak Chai']}/availability", json={"is_available": False}
    )

    body = await validate(client, [{"item_id": ids["item:Karak Chai"], "quantity": 1}])

    assert body["valid"] is False
    assert issue_codes(body) == ["item_unavailable"]


async def test_missing_required_group_is_reported(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    # Portion is required (min_select = 1) and omitted entirely.
    body = await validate(client, [{"item_id": ids["item:Chicken Machboos"], "quantity": 1}])

    assert body["valid"] is False
    assert issue_codes(body) == ["required_group_missing"]
    assert body["issues"][0]["details"]["group_id"] == ids["group:Portion"]


async def test_too_many_options_in_a_single_select_group(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    body = await validate(
        client,
        [
            {
                "item_id": ids["item:Chicken Machboos"],
                "quantity": 1,
                "selections": [
                    {
                        "group_id": ids["group:Portion"],
                        "option_ids": [ids["option:Regular"], ids["option:Large"]],
                    }
                ],
            }
        ],
    )

    assert body["valid"] is False
    assert "too_many_options" in issue_codes(body)


async def test_too_many_options_in_a_multi_select_group(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    # Add-ons allows at most 3; four are chosen.
    body = await validate(
        client,
        [
            {
                "item_id": ids["item:Chicken Machboos"],
                "quantity": 1,
                "selections": [
                    {"group_id": ids["group:Portion"], "option_ids": [ids["option:Regular"]]},
                    {
                        "group_id": ids["group:Add-ons"],
                        "option_ids": [
                            ids["option:Extra chicken"],
                            ids["option:Salad"],
                            ids["option:Laban"],
                            ids["option:Sold-out side"],
                        ],
                    },
                ],
            }
        ],
    )

    assert body["valid"] is False
    codes = issue_codes(body)
    assert "too_many_options" in codes
    # The unavailable option is reported too, so the customer sees both problems at once.
    assert "option_unavailable" in codes


async def test_unavailable_option_is_rejected(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    body = await validate(
        client,
        [
            {
                "item_id": ids["item:Chicken Machboos"],
                "quantity": 1,
                "selections": [
                    {"group_id": ids["group:Portion"], "option_ids": [ids["option:Regular"]]},
                    {
                        "group_id": ids["group:Add-ons"],
                        "option_ids": [ids["option:Sold-out side"]],
                    },
                ],
            }
        ],
    )

    assert body["valid"] is False
    assert issue_codes(body) == ["option_unavailable"]


async def test_option_from_another_group_is_rejected(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    body = await validate(
        client,
        [
            {
                "item_id": ids["item:Chicken Machboos"],
                "quantity": 1,
                "selections": [
                    # 'Salad' belongs to Add-ons, not to Portion.
                    {"group_id": ids["group:Portion"], "option_ids": [ids["option:Salad"]]}
                ],
            }
        ],
    )

    assert body["valid"] is False
    assert "option_not_found" in issue_codes(body)


async def test_option_group_from_another_item_is_rejected(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    body = await validate(
        client,
        [
            {
                "item_id": ids["item:Karak Chai"],
                "quantity": 1,
                "selections": [
                    {"group_id": ids["group:Portion"], "option_ids": [ids["option:Regular"]]}
                ],
            }
        ],
    )

    assert body["valid"] is False
    assert issue_codes(body) == ["option_group_not_found"]


async def test_all_problems_across_lines_are_reported_together(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    body = await validate(
        client,
        [
            {"item_id": "itm_01J9Z8XQF3K7M2P4R6T8V0W1Y3", "quantity": 1},
            {"item_id": ids["item:Chicken Machboos"], "quantity": 1},
            {"item_id": ids["item:Karak Chai"], "quantity": 1},
        ],
    )

    assert body["valid"] is False
    # One issue per broken line, and each carries the index so a client can highlight it.
    assert {issue["line_index"] for issue in body["issues"]} == {0, 1}
    assert set(issue_codes(body)) == {"item_not_found", "required_group_missing"}
    # The healthy line is still priced, so the client can show correct prices for the rest.
    assert [line["line_index"] for line in body["lines"]] == [2]


async def test_duplicate_option_in_one_group_is_rejected_at_the_boundary(
    client: AsyncClient, seed_menu: SeedMenu
) -> None:
    ids = await seed_menu()

    response = await client.post(
        f"/merchants/{MERCHANT_ID}/basket/validate",
        json={
            "lines": [
                {
                    "item_id": ids["item:Chicken Machboos"],
                    "quantity": 1,
                    "selections": [
                        {
                            "group_id": ids["group:Portion"],
                            "option_ids": [ids["option:Regular"], ids["option:Regular"]],
                        }
                    ],
                }
            ]
        },
    )

    # Selecting the same option twice is a malformed request, not a business rule failure.
    assert response.status_code == 422


async def test_empty_basket_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        f"/merchants/{MERCHANT_ID}/basket/validate", json={"lines": []}
    )
    assert response.status_code == 422


async def test_zero_quantity_is_rejected(client: AsyncClient, seed_menu: SeedMenu) -> None:
    response = await client.post(
        f"/merchants/{MERCHANT_ID}/basket/validate",
        json={"lines": [{"item_id": "itm_x", "quantity": 0}]},
    )
    assert response.status_code == 422


async def test_menu_is_public_but_basket_validation_is_not(
    anonymous_client: AsyncClient, seed_menu: SeedMenu
) -> None:
    await seed_menu()

    assert (await anonymous_client.get(f"/merchants/{MERCHANT_ID}/menu")).status_code == 200
    response = await anonymous_client.post(
        f"/merchants/{MERCHANT_ID}/basket/validate",
        json={"lines": [{"item_id": "itm_x", "quantity": 1}]},
    )
    assert response.status_code == 401
