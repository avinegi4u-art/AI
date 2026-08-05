"""Geospatial and temporal queries for merchant discovery.

The two hard parts of "what can I order from right now?" both belong in SQL:

- **Where**: PostGIS answers proximity and polygon containment against indexed geography
  columns. Doing it in Python would mean loading every merchant on every request.
- **When**: opening hours are local wall-clock times per merchant, so the comparison has
  to happen in the merchant's own timezone, including windows that run past midnight.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Integer,
    Time,
    and_,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from marsool_merchant.models import Merchant, MerchantHours, MerchantStatus, ServiceArea

#: Weekday numbering used by ``merchant_hours``: 0 = Monday ... 6 = Sunday, matching
#: Python's ``date.weekday()``. PostgreSQL's ``isodow`` is 1-based, hence the offset.
_ISODOW_OFFSET = 1


def geography_point(latitude: Decimal | float, longitude: Decimal | float) -> ColumnElement[Any]:
    """Build a WGS84 geography point.

    ``ST_MakePoint`` takes longitude first. Getting that wrong is silent and puts the
    point in the wrong hemisphere, so coordinates are never passed through positionally
    anywhere else in the codebase.
    """
    return func.ST_SetSRID(
        func.ST_MakePoint(float(longitude), float(latitude)), 4326
    ).cast(Merchant.location.type)


def _local_timestamp(now: datetime | None) -> ColumnElement[Any]:
    """The current time expressed in each merchant's own timezone.

    Args:
        now: Fixed instant to evaluate against. Tests pass one so results do not depend on
            when the suite happens to run; production passes None and uses ``now()``.
    """
    instant = literal(now) if now is not None else func.now()
    return func.timezone(Merchant.timezone, instant)


def open_now_expression(now: datetime | None = None) -> ColumnElement[bool]:
    """Build a boolean expression that is true when a merchant is currently open.

    Three cases have to be covered, and the third is the one that is usually missed:

    1. A same-day window (``opens_at`` < ``closes_at``) containing the local time.
    2. An overnight window that opened today and has not closed yet.
    3. An overnight window that opened *yesterday* and is still running past midnight --
       23:00-02:00 must report open at 01:00, and that row is stored against yesterday.
    """
    local_timestamp = _local_timestamp(now)
    local_time = cast(local_timestamp, Time)
    today = cast(func.extract("isodow", local_timestamp), Integer) - _ISODOW_OFFSET
    yesterday = (today + 6) % 7

    same_day_window = and_(
        MerchantHours.closes_at > MerchantHours.opens_at,
        MerchantHours.day_of_week == today,
        local_time >= MerchantHours.opens_at,
        local_time < MerchantHours.closes_at,
    )
    overnight_window_opened_today = and_(
        MerchantHours.closes_at <= MerchantHours.opens_at,
        MerchantHours.day_of_week == today,
        local_time >= MerchantHours.opens_at,
    )
    overnight_window_opened_yesterday = and_(
        MerchantHours.closes_at <= MerchantHours.opens_at,
        MerchantHours.day_of_week == yesterday,
        local_time < MerchantHours.closes_at,
    )

    return exists(
        select(MerchantHours.id).where(
            MerchantHours.merchant_id == Merchant.id,
            or_(
                same_day_window,
                overnight_window_opened_today,
                overnight_window_opened_yesterday,
            ),
        )
    )


def delivers_to_expression(point: ColumnElement[Any]) -> ColumnElement[bool]:
    """Build a boolean expression that is true when a merchant delivers to ``point``.

    Service-area polygons win when present, because real coverage is not a circle: a radius
    either excludes reachable customers or promises deliveries across water. Merchants
    without polygons fall back to their delivery radius.
    """
    active_areas = select(ServiceArea.id).where(
        ServiceArea.merchant_id == Merchant.id, ServiceArea.is_active.is_(True)
    )
    has_service_area = exists(active_areas)
    point_is_covered = exists(
        active_areas.where(func.ST_Covers(ServiceArea.boundary, point))
    )
    return or_(
        and_(has_service_area, point_is_covered),
        and_(
            ~has_service_area,
            func.ST_DWithin(Merchant.location, point, Merchant.delivery_radius_m),
        ),
    )


async def search(
    session: AsyncSession,
    *,
    latitude: Decimal,
    longitude: Decimal,
    radius_m: int,
    cuisines: list[str] | None = None,
    vertical: str | None = None,
    query: str | None = None,
    open_now: bool | None = None,
    limit: int = 20,
    after_distance_m: float | None = None,
    after_id: str | None = None,
    now: datetime | None = None,
) -> list[tuple[Merchant, float, bool]]:
    """Find merchants near a point.

    Returns:
        Up to ``limit`` tuples of ``(merchant, distance_m, is_open_now)``, nearest first.
    """
    point = geography_point(latitude, longitude)
    distance = func.ST_Distance(Merchant.location, point)
    is_open = open_now_expression(now)

    statement = (
        select(Merchant, distance.label("distance_m"), is_open.label("is_open_now"))
        .where(
            Merchant.status == MerchantStatus.ACTIVE,
            # ``ST_DWithin`` on geography is index-assisted, so the GiST index does the
            # filtering before any distance is computed.
            func.ST_DWithin(Merchant.location, point, radius_m),
            delivers_to_expression(point),
        )
        # Ties broken by id so keyset pagination is deterministic; without a unique
        # tiebreaker two merchants at the same distance can repeat or vanish across pages.
        .order_by(distance.asc(), Merchant.id.asc())
        .limit(limit)
    )

    if cuisines:
        # Array overlap: a merchant matches if it serves any requested cuisine.
        statement = statement.where(Merchant.cuisines.overlap(cuisines))
    if vertical:
        statement = statement.where(Merchant.vertical == vertical)
    if query:
        statement = statement.where(Merchant.name.ilike(f"%{query}%"))
    if open_now is True:
        statement = statement.where(Merchant.accepting_orders.is_(True)).where(is_open)
    elif open_now is False:
        statement = statement.where(or_(Merchant.accepting_orders.is_(False), ~is_open))

    if after_distance_m is not None and after_id is not None:
        statement = statement.where(
            or_(
                distance > after_distance_m,
                and_(distance == after_distance_m, Merchant.id > after_id),
            )
        )

    rows = (await session.execute(statement)).all()
    return [(row[0], float(row[1]), bool(row[2])) for row in rows]


async def get_by_id(session: AsyncSession, merchant_id: str) -> Merchant | None:
    return (
        await session.execute(select(Merchant).where(Merchant.id == merchant_id))
    ).scalar_one_or_none()


async def get_by_slug(session: AsyncSession, slug: str) -> Merchant | None:
    return (
        await session.execute(select(Merchant).where(Merchant.slug == slug))
    ).scalar_one_or_none()


async def is_open_now(
    session: AsyncSession, merchant_id: str, *, now: datetime | None = None
) -> bool:
    """Evaluate the opening-hours predicate for one merchant."""
    result = await session.execute(
        select(open_now_expression(now)).where(Merchant.id == merchant_id)
    )
    return bool(result.scalar_one_or_none())


async def list_hours(session: AsyncSession, merchant_id: str) -> list[MerchantHours]:
    return list(
        (
            await session.execute(
                select(MerchantHours)
                .where(MerchantHours.merchant_id == merchant_id)
                .order_by(MerchantHours.day_of_week, MerchantHours.opens_at)
            )
        )
        .scalars()
        .all()
    )


async def list_service_areas(session: AsyncSession, merchant_id: str) -> list[ServiceArea]:
    return list(
        (
            await session.execute(
                select(ServiceArea)
                .where(ServiceArea.merchant_id == merchant_id)
                .order_by(ServiceArea.created_at)
            )
        )
        .scalars()
        .all()
    )


async def delivers_to(
    session: AsyncSession, merchant_id: str, *, latitude: Decimal, longitude: Decimal
) -> bool:
    """Whether a merchant delivers to a specific point."""
    point = geography_point(latitude, longitude)
    result = await session.execute(
        select(delivers_to_expression(point)).where(Merchant.id == merchant_id)
    )
    return bool(result.scalar_one_or_none())
