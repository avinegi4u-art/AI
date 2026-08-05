"""Merchant HTTP endpoints."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query

from marsool_core.http.schemas import Page
from marsool_merchant.deps import (
    MerchantServiceDep,
    MerchantStaffOnly,
    OptionalPrincipal,
    SessionDep,
)
from marsool_merchant.schemas import (
    AcceptingOrdersUpdate,
    MerchantDetail,
    MerchantSummary,
    MerchantUpdate,
    OpeningHours,
    OpeningHoursUpdate,
    ServiceabilityResponse,
)

router = APIRouter(prefix="/merchants", tags=["merchants"])

LatQuery = Annotated[Decimal, Query(ge=-90, le=90, description="Customer latitude (WGS84)")]
LngQuery = Annotated[Decimal, Query(ge=-180, le=180, description="Customer longitude (WGS84)")]


@router.get(
    "",
    response_model=Page[MerchantSummary],
    summary="Find merchants that deliver to a location",
    description=(
        "Returns merchants able to deliver to the given point, nearest first.\n\n"
        "A merchant is included when it is `ACTIVE`, within `radius_m`, and the point falls "
        "inside one of its service-area polygons — or within its delivery radius if it has "
        "no polygons. `open_now` additionally requires the merchant to be accepting orders "
        "and inside an opening window evaluated in the merchant's own timezone, including "
        "windows that run past midnight.\n\n"
        "Results are cursor-paginated by distance; pass `meta.next_cursor` back as `cursor`."
    ),
    name="search_merchants",
)
async def search_merchants(
    session: SessionDep,
    merchant_service: MerchantServiceDep,
    principal: OptionalPrincipal,
    lat: LatQuery,
    lng: LngQuery,
    radius_m: Annotated[
        int | None, Query(ge=100, le=100_000, description="Search radius in metres")
    ] = None,
    cuisine: Annotated[
        list[str] | None, Query(description="Repeatable; matches any of the given cuisines")
    ] = None,
    vertical: Annotated[str | None, Query(description="e.g. RESTAURANT")] = None,
    q: Annotated[str | None, Query(max_length=80, description="Name search")] = None,
    open_now: Annotated[bool | None, Query(description="Filter by current open state")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a previous page")] = None,
) -> Page[MerchantSummary]:
    return await merchant_service.search(
        session,
        latitude=lat,
        longitude=lng,
        radius_m=radius_m,
        cuisines=cuisine,
        vertical=vertical,
        query=q,
        open_now=open_now,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/{merchant_id}",
    response_model=MerchantDetail,
    summary="Get a merchant storefront",
    description=(
        "Returns the storefront and this week's schedule. Supply `lat` and `lng` to get a "
        "meaningful `distance_m` and `eta_min` for the requesting customer."
    ),
    name="get_merchant",
)
async def get_merchant(
    merchant_id: str,
    session: SessionDep,
    merchant_service: MerchantServiceDep,
    principal: OptionalPrincipal,
    lat: Annotated[Decimal | None, Query(ge=-90, le=90)] = None,
    lng: Annotated[Decimal | None, Query(ge=-180, le=180)] = None,
) -> MerchantDetail:
    return await merchant_service.get_detail(
        session, merchant_id=merchant_id, latitude=lat, longitude=lng
    )


@router.put(
    "/{merchant_id}",
    response_model=MerchantDetail,
    summary="Update a merchant storefront",
    description=(
        "Partial update. Commission, lifecycle status and rating are not settable: they are "
        "operations- and outcome-owned, so a merchant cannot change its own economics. "
        "Merchant staff may only edit merchants they are attached to."
    ),
    name="update_merchant",
)
async def update_merchant(
    merchant_id: str,
    payload: MerchantUpdate,
    principal: MerchantStaffOnly,
    session: SessionDep,
    merchant_service: MerchantServiceDep,
) -> MerchantDetail:
    return await merchant_service.update_merchant(
        session, principal=principal, merchant_id=merchant_id, payload=payload
    )


@router.get(
    "/{merchant_id}/serviceability",
    response_model=ServiceabilityResponse,
    summary="Check whether a merchant delivers to a point",
    description=(
        "Used by order creation before a basket is accepted, so a customer learns about an "
        "out-of-range address or a closed kitchen at checkout rather than after paying. "
        "`reason` explains a negative result: `merchant_not_accepting_orders`, "
        "`outside_delivery_area` or `merchant_closed`."
    ),
    name="check_serviceability",
)
async def check_serviceability(
    merchant_id: str,
    session: SessionDep,
    merchant_service: MerchantServiceDep,
    principal: OptionalPrincipal,
    lat: LatQuery,
    lng: LngQuery,
) -> ServiceabilityResponse:
    return await merchant_service.check_serviceability(
        session, merchant_id=merchant_id, latitude=lat, longitude=lng
    )


@router.get(
    "/{merchant_id}/hours",
    response_model=list[OpeningHours],
    summary="Get a merchant's weekly schedule",
    name="get_merchant_hours",
)
async def get_merchant_hours(
    merchant_id: str,
    session: SessionDep,
    merchant_service: MerchantServiceDep,
    principal: OptionalPrincipal,
) -> list[OpeningHours]:
    detail = await merchant_service.get_detail(session, merchant_id=merchant_id)
    return detail.opening_hours


@router.put(
    "/{merchant_id}/hours",
    response_model=list[OpeningHours],
    summary="Replace a merchant's weekly schedule",
    description=(
        "Replaces the whole schedule rather than editing individual windows: partial edits "
        "make it easy to leave a stale window behind, and a wrong open window means orders "
        "the kitchen cannot fulfil. Merchant staff may only edit their own merchants."
    ),
    name="replace_merchant_hours",
)
async def replace_merchant_hours(
    merchant_id: str,
    payload: OpeningHoursUpdate,
    principal: MerchantStaffOnly,
    session: SessionDep,
    merchant_service: MerchantServiceDep,
) -> list[OpeningHours]:
    return await merchant_service.replace_hours(
        session, principal=principal, merchant_id=merchant_id, payload=payload
    )


@router.put(
    "/{merchant_id}/accepting-orders",
    response_model=MerchantDetail,
    summary="Start or stop accepting orders",
    description=(
        "The merchant's own kill switch, independent of the operations-owned `status`. A "
        "kitchen that is overwhelmed pauses here without its storefront being deactivated."
    ),
    name="set_accepting_orders",
)
async def set_accepting_orders(
    merchant_id: str,
    payload: AcceptingOrdersUpdate,
    principal: MerchantStaffOnly,
    session: SessionDep,
    merchant_service: MerchantServiceDep,
) -> MerchantDetail:
    return await merchant_service.set_accepting_orders(
        session, principal=principal, merchant_id=merchant_id, payload=payload
    )
