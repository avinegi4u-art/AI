"""Sample merchants shipped with the merchant service.

Real Abu Dhabi venues and coordinates, shared by tests and the local seed script so both
exercise genuine geography rather than a synthetic grid.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Any, Final

from marsool_merchant.models import Merchant, MerchantHours, MerchantStatus, MerchantVertical
from marsool_merchant.repository import geography_point

#: Weekday-long trading hours: open 11:00-23:00 every day.
ALL_WEEK_DAYTIME: Final[list[tuple[int, time, time]]] = [
    (day, time(11, 0), time(23, 0)) for day in range(7)
]

#: Late-night trading that runs past midnight: 18:00-02:00 every day.
ALL_WEEK_OVERNIGHT: Final[list[tuple[int, time, time]]] = [
    (day, time(18, 0), time(2, 0)) for day in range(7)
]


def make_merchant(
    *,
    name: str,
    slug: str,
    latitude: str,
    longitude: str,
    cuisines: list[str] | None = None,
    status: MerchantStatus = MerchantStatus.ACTIVE,
    accepting_orders: bool = True,
    delivery_radius_m: int = 5_000,
    prep_time_minutes: int = 20,
    timezone: str = "Asia/Dubai",
    **overrides: Any,
) -> Merchant:
    """Build a merchant with the PostGIS point derived from its coordinates."""
    return Merchant(
        name=name,
        slug=slug,
        vertical=MerchantVertical.RESTAURANT,
        status=status,
        accepting_orders=accepting_orders,
        cuisines=cuisines or ["emirati"],
        address_line1=f"{name}, Abu Dhabi",
        city="Abu Dhabi",
        emirate="Abu Dhabi",
        country_code="AE",
        latitude=Decimal(latitude),
        longitude=Decimal(longitude),
        location=geography_point(Decimal(latitude), Decimal(longitude)),
        timezone=timezone,
        prep_time_minutes=prep_time_minutes,
        delivery_radius_m=delivery_radius_m,
        currency="AED",
        **overrides,
    )


def make_hours(merchant_id: str, windows: list[tuple[int, time, time]]) -> list[MerchantHours]:
    """Build opening-hour rows from ``(day_of_week, opens_at, closes_at)`` tuples."""
    return [
        MerchantHours(
            merchant_id=merchant_id, day_of_week=day, opens_at=opens_at, closes_at=closes_at
        )
        for day, opens_at, closes_at in windows
    ]


#: A polygon roughly covering Al Reem Island, as WKT.
AL_REEM_ISLAND_POLYGON: Final = (
    "POLYGON((54.380 24.480, 54.420 24.480, 54.420 24.510, 54.380 24.510, 54.380 24.480))"
)
