"""Merchant domain logic: discovery, storefront management, serviceability."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marsool_core.errors import BadRequestError, NotFoundError, PermissionDeniedError
from marsool_core.events.outbox import OutboxRepository
from marsool_core.geo import DEFAULT_URBAN_SPEED_KMH, ROAD_DISTANCE_FACTOR, travel_time_minutes
from marsool_core.http.pagination import decode_cursor, encode_cursor
from marsool_core.http.schemas import Page
from marsool_core.logging import get_logger
from marsool_core.money import from_minor_units, to_minor_units
from marsool_core.security.principal import Principal, Role
from marsool_merchant import repository
from marsool_merchant.config import MerchantSettings
from marsool_merchant.events import (
    merchant_hours_updated,
    merchant_status_changed,
    merchant_updated,
)
from marsool_merchant.models import Merchant, MerchantHours
from marsool_merchant.repository import geography_point
from marsool_merchant.schemas import (
    AcceptingOrdersUpdate,
    MerchantDetail,
    MerchantSummary,
    MerchantUpdate,
    OpeningHours,
    OpeningHoursUpdate,
    ServiceabilityResponse,
)

logger = get_logger(__name__)

# Fields on MerchantUpdate that map straight onto a column of the same name.
_DIRECT_UPDATE_FIELDS = (
    "name",
    "description",
    "cuisines",
    "phone",
    "email",
    "prep_time_minutes",
    "delivery_radius_m",
    "logo_url",
    "hero_image_url",
    "address_line1",
    "address_line2",
    "community",
)


class MerchantService:
    """Discovery and storefront operations."""

    def __init__(self, *, settings: MerchantSettings, outbox: OutboxRepository) -> None:
        self._settings = settings
        self._outbox = outbox

    # ----------------------------------------------------------------------- discovery

    async def search(
        self,
        session: AsyncSession,
        *,
        latitude: Decimal,
        longitude: Decimal,
        radius_m: int | None = None,
        cuisines: list[str] | None = None,
        vertical: str | None = None,
        query: str | None = None,
        open_now: bool | None = None,
        limit: int = 20,
        cursor: str | None = None,
        now: datetime | None = None,
    ) -> Page[MerchantSummary]:
        """Find merchants that can deliver to a point, nearest first."""
        effective_radius = min(
            radius_m or self._settings.default_search_radius_m,
            self._settings.max_search_radius_m,
        )
        after_distance, after_id = _decode_search_cursor(cursor)

        # One extra row tells us whether another page exists without a second count query.
        rows = await repository.search(
            session,
            latitude=latitude,
            longitude=longitude,
            radius_m=effective_radius,
            cuisines=cuisines,
            vertical=vertical,
            query=query,
            open_now=open_now,
            limit=limit + 1,
            after_distance_m=after_distance,
            after_id=after_id,
            now=now,
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]

        items = [
            self._to_summary(merchant, distance_m=distance_m, is_open_now=is_open_now)
            for merchant, distance_m, is_open_now in page_rows
        ]
        next_cursor = (
            encode_cursor({"d": page_rows[-1][1], "id": page_rows[-1][0].id})
            if has_more and page_rows
            else None
        )
        return Page.of(items, limit=limit, next_cursor=next_cursor)

    async def get_detail(
        self,
        session: AsyncSession,
        *,
        merchant_id: str,
        latitude: Decimal | None = None,
        longitude: Decimal | None = None,
        now: datetime | None = None,
    ) -> MerchantDetail:
        """Return one merchant's storefront and weekly schedule.

        Coordinates are optional: supplying them makes ``distance_m`` and ``eta_min``
        meaningful for the requesting customer instead of zero.
        """
        merchant = await self._require_merchant(session, merchant_id)
        is_open = await repository.is_open_now(session, merchant_id, now=now)
        hours = await repository.list_hours(session, merchant_id)
        distance_m = (
            await self._distance_to(session, merchant, latitude, longitude)
            if latitude is not None and longitude is not None
            else 0.0
        )
        summary = self._to_summary(merchant, distance_m=distance_m, is_open_now=is_open)
        return MerchantDetail(
            **summary.model_dump(),
            status=merchant.status,
            address_line1=merchant.address_line1,
            address_line2=merchant.address_line2,
            emirate=merchant.emirate,
            country_code=merchant.country_code,
            phone=merchant.phone,
            timezone=merchant.timezone,
            delivery_radius_m=merchant.delivery_radius_m,
            opening_hours=[OpeningHours.model_validate(window) for window in hours],
        )

    async def check_serviceability(
        self,
        session: AsyncSession,
        *,
        merchant_id: str,
        latitude: Decimal,
        longitude: Decimal,
        now: datetime | None = None,
    ) -> ServiceabilityResponse:
        """Whether a merchant can deliver to a point right now, and if not, why.

        Order creation calls this before accepting a basket, so the customer learns about
        an out-of-range address at checkout rather than after paying.
        """
        merchant = await self._require_merchant(session, merchant_id)
        distance_m = await self._distance_to(session, merchant, latitude, longitude)
        in_area = await repository.delivers_to(
            session, merchant_id, latitude=latitude, longitude=longitude
        )
        is_open = await repository.is_open_now(session, merchant_id, now=now)

        reason: str | None = None
        if not merchant.is_bookable:
            reason = "merchant_not_accepting_orders"
        elif not in_area:
            reason = "outside_delivery_area"
        elif not is_open:
            reason = "merchant_closed"

        return ServiceabilityResponse(
            merchant_id=merchant_id,
            latitude=latitude,
            longitude=longitude,
            deliverable=reason is None,
            distance_m=distance_m,
            reason=reason,
        )

    # ------------------------------------------------------------ storefront management

    async def update_merchant(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        merchant_id: str,
        payload: MerchantUpdate,
    ) -> MerchantDetail:
        """Apply a partial storefront update."""
        merchant = await self._require_merchant(session, merchant_id)
        self._require_merchant_access(principal, merchant_id)

        changes = payload.model_dump(exclude_unset=True)
        for field in _DIRECT_UPDATE_FIELDS:
            if field in changes:
                setattr(merchant, field, changes[field])

        if "min_order_amount" in changes:
            merchant.min_order_minor = to_minor_units(
                changes["min_order_amount"], merchant.currency
            )
        if "latitude" in changes or "longitude" in changes:
            # Write the scalars first: the geography column is derived from them, and
            # recomputing it from stale values would leave search pointing at the old site.
            merchant.latitude = changes.get("latitude", merchant.latitude)
            merchant.longitude = changes.get("longitude", merchant.longitude)
            merchant.location = geography_point(merchant.latitude, merchant.longitude)

        if changes:
            await session.flush()
            await session.refresh(merchant)
            self._outbox.enqueue(
                session,
                merchant_updated(merchant_id=merchant_id, changed_fields=sorted(changes)),
            )
            logger.info("merchant_updated", merchant_id=merchant_id, fields=sorted(changes))
        return await self.get_detail(session, merchant_id=merchant_id)

    async def set_accepting_orders(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        merchant_id: str,
        payload: AcceptingOrdersUpdate,
    ) -> MerchantDetail:
        """Flip the merchant's order kill switch."""
        merchant = await self._require_merchant(session, merchant_id)
        self._require_merchant_access(principal, merchant_id)

        if merchant.accepting_orders != payload.accepting_orders:
            merchant.accepting_orders = payload.accepting_orders
            await session.flush()
            self._outbox.enqueue(
                session,
                merchant_status_changed(
                    merchant_id=merchant_id,
                    accepting_orders=merchant.accepting_orders,
                    status=merchant.status.value,
                    reason=payload.reason,
                ),
            )
            logger.info(
                "merchant_accepting_orders_changed",
                merchant_id=merchant_id,
                accepting_orders=merchant.accepting_orders,
                reason=payload.reason,
            )
        return await self.get_detail(session, merchant_id=merchant_id)

    async def replace_hours(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        merchant_id: str,
        payload: OpeningHoursUpdate,
    ) -> list[OpeningHours]:
        """Replace a merchant's whole weekly schedule."""
        await self._require_merchant(session, merchant_id)
        self._require_merchant_access(principal, merchant_id)

        await session.execute(
            delete(MerchantHours).where(MerchantHours.merchant_id == merchant_id)
        )
        for window in payload.windows:
            session.add(
                MerchantHours(
                    merchant_id=merchant_id,
                    day_of_week=window.day_of_week,
                    opens_at=window.opens_at,
                    closes_at=window.closes_at,
                )
            )
        await session.flush()
        self._outbox.enqueue(
            session,
            merchant_hours_updated(merchant_id=merchant_id, window_count=len(payload.windows)),
        )
        return [
            OpeningHours.model_validate(window)
            for window in await repository.list_hours(session, merchant_id)
        ]

    # ------------------------------------------------------------------------ helpers

    async def _require_merchant(self, session: AsyncSession, merchant_id: str) -> Merchant:
        merchant = await repository.get_by_id(session, merchant_id)
        if merchant is None:
            raise NotFoundError("Merchant not found", code="merchant_not_found")
        return merchant

    @staticmethod
    def _require_merchant_access(principal: Principal, merchant_id: str) -> None:
        """Reject a merchant user acting on a storefront that is not theirs."""
        if principal.role is Role.ADMIN:
            return
        if not principal.can_act_for_merchant(merchant_id):
            raise PermissionDeniedError(
                "You may not administer this merchant",
                code="merchant_access_denied",
                details={"merchant_id": merchant_id},
            )

    @staticmethod
    async def _distance_to(
        session: AsyncSession,
        merchant: Merchant,
        latitude: Decimal | None,
        longitude: Decimal | None,
    ) -> float:
        if latitude is None or longitude is None:
            return 0.0
        point = geography_point(latitude, longitude)
        result = await session.execute(
            select(func.ST_Distance(Merchant.location, point)).where(Merchant.id == merchant.id)
        )
        return float(result.scalar_one())

    def _to_summary(
        self, merchant: Merchant, *, distance_m: float, is_open_now: bool
    ) -> MerchantSummary:
        return MerchantSummary(
            id=merchant.id,
            name=merchant.name,
            slug=merchant.slug,
            vertical=merchant.vertical,
            description=merchant.description,
            cuisines=list(merchant.cuisines),
            city=merchant.city,
            community=merchant.community,
            latitude=merchant.latitude,
            longitude=merchant.longitude,
            rating=merchant.rating,
            rating_count=merchant.rating_count,
            price_tier=merchant.price_tier,
            prep_time_minutes=merchant.prep_time_minutes,
            currency=merchant.currency,
            min_order_amount=from_minor_units(merchant.min_order_minor, merchant.currency),
            logo_url=merchant.logo_url,
            hero_image_url=merchant.hero_image_url,
            accepting_orders=merchant.accepting_orders,
            distance_m=round(distance_m, 1),
            is_open_now=is_open_now,
            eta_min=estimate_eta_minutes(
                prep_time_minutes=merchant.prep_time_minutes, distance_m=distance_m
            ),
        )


def estimate_eta_minutes(*, prep_time_minutes: int, distance_m: float) -> int:
    """Estimate delivery time as preparation plus travel.

    A deliberately simple, explainable baseline: straight-line distance inflated to a road
    estimate, divided by an average urban speed, plus the merchant's stated prep time. It
    is replaced by a learned model in a later phase, but everything downstream (search
    ranking, pricing, promises to customers) needs *an* ETA now, and rounding up means the
    platform under-promises rather than breaks its word.
    """
    travel_minutes = travel_time_minutes(
        distance_m * ROAD_DISTANCE_FACTOR, speed_kmh=DEFAULT_URBAN_SPEED_KMH
    )
    return max(1, math.ceil(prep_time_minutes + travel_minutes))


def _decode_search_cursor(cursor: str | None) -> tuple[float | None, str | None]:
    """Decode a search cursor into ``(distance_m, merchant_id)``."""
    if not cursor:
        return None, None
    payload = decode_cursor(cursor)
    distance = payload.get("d")
    merchant_id = payload.get("id")
    if not isinstance(distance, (int, float)) or not isinstance(merchant_id, str):
        raise BadRequestError("Malformed pagination cursor", code="invalid_cursor")
    return float(distance), merchant_id
