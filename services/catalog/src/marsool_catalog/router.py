"""Catalog HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from marsool_catalog.deps import (
    CatalogServiceDep,
    CurrentPrincipal,
    MerchantStaffOnly,
    OptionalPrincipal,
    SessionDep,
)
from marsool_catalog.schemas import (
    BasketValidationRequest,
    BasketValidationResponse,
    ItemAvailabilityUpdate,
    MenuItemResponse,
    MenuItemUpdate,
    MenuResponse,
)

# Menu reads are nested under the merchant, matching how clients navigate.
merchant_router = APIRouter(prefix="/merchants", tags=["catalog"])
catalog_router = APIRouter(prefix="/catalog", tags=["catalog"])


@merchant_router.get(
    "/{merchant_id}/menu",
    response_model=MenuResponse,
    summary="Get a merchant's active menu",
    description=(
        "Returns the whole menu — categories, items and option groups — in one nested "
        "response, so a client needs a single round trip to render a storefront.\n\n"
        "Cached in Redis. Any item change invalidates the entry, so a sold-out item "
        "disappears immediately rather than lingering until the TTL expires."
    ),
    name="get_merchant_menu",
)
async def get_merchant_menu(
    merchant_id: str,
    session: SessionDep,
    catalog_service: CatalogServiceDep,
    principal: OptionalPrincipal,
) -> MenuResponse:
    return await catalog_service.get_menu(session, merchant_id=merchant_id)


@merchant_router.post(
    "/{merchant_id}/basket/validate",
    response_model=BasketValidationResponse,
    summary="Validate and price a basket",
    description=(
        "Checks every line against the merchant's catalogue and returns the priced basket.\n\n"
        "Validation covers item existence and availability, that all items belong to the "
        "same merchant, that selected options exist in their group and are available, and "
        "that each group's selection count is within its bounds — including required groups "
        "the client omitted entirely.\n\n"
        "All lines are checked independently and every issue is returned together, so a "
        "customer sees all problems at once. Order creation calls this before accepting a "
        "basket, which is why the rules live next to the data that defines them."
    ),
    name="validate_basket",
)
async def validate_basket(
    merchant_id: str,
    payload: BasketValidationRequest,
    principal: CurrentPrincipal,
    session: SessionDep,
    catalog_service: CatalogServiceDep,
) -> BasketValidationResponse:
    return await catalog_service.validate_basket(
        session, merchant_id=merchant_id, payload=payload
    )


@catalog_router.put(
    "/items/{item_id}/availability",
    response_model=MenuItemResponse,
    summary="Mark an item in or out of stock",
    description=(
        "The most frequently used merchant operation during service. Invalidates the cached "
        "menu so customers stop seeing the item immediately."
    ),
    name="set_item_availability",
)
async def set_item_availability(
    item_id: str,
    payload: ItemAvailabilityUpdate,
    principal: MerchantStaffOnly,
    session: SessionDep,
    catalog_service: CatalogServiceDep,
) -> MenuItemResponse:
    return await catalog_service.set_item_availability(
        session, principal=principal, item_id=item_id, payload=payload
    )


@catalog_router.put(
    "/items/{item_id}",
    response_model=MenuItemResponse,
    summary="Update an item",
    description="Partial update. Merchant staff may only edit their own merchants' items.",
    name="update_item",
)
async def update_item(
    item_id: str,
    payload: MenuItemUpdate,
    principal: MerchantStaffOnly,
    session: SessionDep,
    catalog_service: CatalogServiceDep,
) -> MenuItemResponse:
    return await catalog_service.update_item(
        session, principal=principal, item_id=item_id, payload=payload
    )
