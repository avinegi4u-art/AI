"""Timezone-aware opening hours, including windows that run past midnight.

Every case pins the evaluation instant explicitly. An open-now test that depends on when
the suite happens to run is worse than no test: it passes for most of the day and fails
mysteriously at 2 a.m.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from marsool_core.testing import session_factory_for
from marsool_merchant import repository
from marsool_merchant.models import Merchant
from marsool_merchant.service import estimate_eta_minutes

MerchantFactory = Callable[..., Awaitable[Merchant]]

# Gulf Standard Time is UTC+4 with no daylight saving, so a UTC instant maps to a
# predictable Abu Dhabi wall-clock time.
# Monday 2026-08-03 08:00 UTC == Monday 12:00 in Abu Dhabi.
MONDAY_NOON_LOCAL = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)
# Monday 2026-08-03 04:00 UTC == Monday 08:00 local.
MONDAY_MORNING_LOCAL = datetime(2026, 8, 3, 4, 0, tzinfo=UTC)
# Monday 2026-08-03 20:00 UTC == Tuesday 00:00 local.
TUESDAY_MIDNIGHT_LOCAL = datetime(2026, 8, 3, 20, 0, tzinfo=UTC)
# Monday 2026-08-03 21:00 UTC == Tuesday 01:00 local.
TUESDAY_ONE_AM_LOCAL = datetime(2026, 8, 3, 21, 0, tzinfo=UTC)
# Monday 2026-08-03 23:00 UTC == Tuesday 03:00 local.
TUESDAY_THREE_AM_LOCAL = datetime(2026, 8, 3, 23, 0, tzinfo=UTC)

LUNCH_ONLY = [(day, time(11, 0), time(15, 0)) for day in range(7)]
LATE_NIGHT = [(day, time(18, 0), time(2, 0)) for day in range(7)]
MONDAY_ONLY = [(0, time(11, 0), time(23, 0))]
SPLIT_SHIFT = [(0, time(8, 0), time(11, 0)), (0, time(18, 0), time(23, 0))]


async def is_open(engine: AsyncEngine, merchant_id: str, at: datetime) -> bool:
    async with session_factory_for(engine)() as session:
        return await repository.is_open_now(session, merchant_id, now=at)


@pytest.fixture
async def venue(merchant_factory: MerchantFactory) -> Callable[..., Awaitable[Merchant]]:
    async def _create(**kwargs: object) -> Merchant:
        return await merchant_factory(
            name="Test Venue",
            slug=f"venue-{kwargs.get('slug_suffix', 'x')}",
            latitude="24.494640",
            longitude="54.399460",
            **{key: value for key, value in kwargs.items() if key != "slug_suffix"},
        )

    return _create


async def test_open_inside_a_same_day_window(
    venue: Callable[..., Awaitable[Merchant]], engine: AsyncEngine
) -> None:
    merchant = await venue(hours=LUNCH_ONLY)
    assert await is_open(engine, merchant.id, MONDAY_NOON_LOCAL) is True


async def test_closed_before_opening(
    venue: Callable[..., Awaitable[Merchant]], engine: AsyncEngine
) -> None:
    merchant = await venue(hours=LUNCH_ONLY)
    assert await is_open(engine, merchant.id, MONDAY_MORNING_LOCAL) is False


async def test_closed_after_closing(
    venue: Callable[..., Awaitable[Merchant]], engine: AsyncEngine
) -> None:
    merchant = await venue(hours=LUNCH_ONLY)
    assert await is_open(engine, merchant.id, TUESDAY_MIDNIGHT_LOCAL) is False


async def test_overnight_window_is_open_before_midnight(
    venue: Callable[..., Awaitable[Merchant]], engine: AsyncEngine
) -> None:
    merchant = await venue(hours=LATE_NIGHT)
    # 22:00 local on Monday, inside Monday's 18:00-02:00 window.
    assert await is_open(engine, merchant.id, datetime(2026, 8, 3, 18, 0, tzinfo=UTC)) is True


async def test_overnight_window_is_open_after_midnight(
    venue: Callable[..., Awaitable[Merchant]], engine: AsyncEngine
) -> None:
    merchant = await venue(hours=LATE_NIGHT)
    # 01:00 local on Tuesday is still inside *Monday's* window. This is the case naive
    # implementations get wrong: the matching row is stored against the previous day.
    assert await is_open(engine, merchant.id, TUESDAY_ONE_AM_LOCAL) is True


async def test_overnight_window_is_closed_after_it_ends(
    venue: Callable[..., Awaitable[Merchant]], engine: AsyncEngine
) -> None:
    merchant = await venue(hours=LATE_NIGHT)
    assert await is_open(engine, merchant.id, TUESDAY_THREE_AM_LOCAL) is False


async def test_day_of_week_is_respected(
    venue: Callable[..., Awaitable[Merchant]], engine: AsyncEngine
) -> None:
    merchant = await venue(hours=MONDAY_ONLY)
    assert await is_open(engine, merchant.id, MONDAY_NOON_LOCAL) is True
    # Tuesday noon local: no window exists for Tuesday.
    assert await is_open(engine, merchant.id, datetime(2026, 8, 4, 8, 0, tzinfo=UTC)) is False


async def test_split_shifts_close_between_services(
    venue: Callable[..., Awaitable[Merchant]], engine: AsyncEngine
) -> None:
    merchant = await venue(hours=SPLIT_SHIFT)
    # 09:00 local — inside the morning shift.
    assert await is_open(engine, merchant.id, datetime(2026, 8, 3, 5, 0, tzinfo=UTC)) is True
    # 12:00 local — between shifts.
    assert await is_open(engine, merchant.id, MONDAY_NOON_LOCAL) is False
    # 19:00 local — inside the evening shift.
    assert await is_open(engine, merchant.id, datetime(2026, 8, 3, 15, 0, tzinfo=UTC)) is True


async def test_merchant_without_hours_is_never_open(
    venue: Callable[..., Awaitable[Merchant]], engine: AsyncEngine
) -> None:
    merchant = await venue()
    # Absent hours means "unknown", and the safe reading of unknown is closed: promising an
    # order a kitchen will not receive is worse than hiding a storefront.
    assert await is_open(engine, merchant.id, MONDAY_NOON_LOCAL) is False


async def test_hours_are_evaluated_in_the_merchants_own_timezone(
    merchant_factory: MerchantFactory, engine: AsyncEngine
) -> None:
    # Same wall-clock window, two timezones. At 08:00 UTC it is noon in Abu Dhabi (open)
    # and 09:00 in London (closed), which is only correct if the comparison happens per
    # merchant rather than in one platform-wide offset.
    abu_dhabi = await merchant_factory(
        name="Abu Dhabi Venue",
        slug="abu-dhabi-venue",
        latitude="24.494640",
        longitude="54.399460",
        timezone="Asia/Dubai",
        hours=LUNCH_ONLY,
    )
    london = await merchant_factory(
        name="London Venue",
        slug="london-venue",
        latitude="24.494650",
        longitude="54.399470",
        timezone="Europe/London",
        hours=LUNCH_ONLY,
    )

    assert await is_open(engine, abu_dhabi.id, MONDAY_NOON_LOCAL) is True
    assert await is_open(engine, london.id, MONDAY_NOON_LOCAL) is False


@pytest.mark.parametrize(
    ("prep_minutes", "distance_m", "expected_minimum"),
    [(20, 0, 20), (20, 1_000, 22), (15, 5_000, 30)],
)
def test_eta_is_prep_plus_travel_and_rounds_up(
    prep_minutes: int, distance_m: float, expected_minimum: int
) -> None:
    eta = estimate_eta_minutes(prep_time_minutes=prep_minutes, distance_m=distance_m)
    assert eta >= expected_minimum
    assert eta >= prep_minutes, "ETA can never be shorter than preparation time"


def test_eta_is_monotonic_in_distance() -> None:
    etas = [
        estimate_eta_minutes(prep_time_minutes=20, distance_m=distance)
        for distance in (0, 500, 2_000, 10_000)
    ]
    assert etas == sorted(etas)
