"""Request and response schemas for the merchant API."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marsool_merchant.models import MerchantStatus, MerchantVertical

Latitude = Annotated[Decimal, Field(ge=-90, le=90, description="WGS84 latitude")]
Longitude = Annotated[Decimal, Field(ge=-180, le=180, description="WGS84 longitude")]

#: Cuisine vocabulary for the Abu Dhabi launch. Closed set so filters and the AI assistant
#: agree on the same tags.
CUISINES = (
    "emirati",
    "lebanese",
    "egyptian",
    "syrian",
    "indian",
    "pakistani",
    "filipino",
    "japanese",
    "chinese",
    "thai",
    "italian",
    "american",
    "burgers",
    "pizza",
    "seafood",
    "grills",
    "breakfast",
    "bakery",
    "desserts",
    "coffee",
    "healthy",
    "vegan",
)


class OpeningHours(BaseModel):
    """One opening window on one weekday."""

    model_config = ConfigDict(from_attributes=True)

    day_of_week: int = Field(ge=0, le=6, description="0 = Monday through 6 = Sunday")
    opens_at: time
    closes_at: time
    spans_midnight: bool = Field(
        default=False,
        description="True when the window runs past midnight into the following day",
    )

    @model_validator(mode="after")
    def _derive_spans_midnight(self) -> Self:
        object.__setattr__(self, "spans_midnight", self.closes_at <= self.opens_at)
        return self


class OpeningHoursUpdate(BaseModel):
    """Replace a merchant's full weekly schedule.

    A whole-schedule replacement rather than per-window edits: partial edits make it easy
    to leave a stale window behind, and an incorrect open window means orders a kitchen
    cannot fulfil.
    """

    windows: list[OpeningHours] = Field(max_length=42, description="At most 6 windows per day")

    @model_validator(mode="after")
    def _reject_duplicate_windows(self) -> Self:
        seen = {(window.day_of_week, window.opens_at) for window in self.windows}
        if len(seen) != len(self.windows):
            raise ValueError("duplicate opening window for the same day and opening time")
        return self


class MerchantSummary(BaseModel):
    """A merchant as it appears in search results."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "mch_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
                    "name": "Al Mrzab Restaurant",
                    "slug": "al-mrzab-restaurant",
                    "vertical": "RESTAURANT",
                    "cuisines": ["emirati", "grills"],
                    "latitude": "24.481200",
                    "longitude": "54.365500",
                    "rating": "4.60",
                    "rating_count": 1284,
                    "price_tier": 2,
                    "prep_time_minutes": 25,
                    "currency": "AED",
                    "min_order_amount": "20.00",
                    "distance_m": 1843.2,
                    "is_open_now": True,
                    "accepting_orders": True,
                    "eta_min": 31,
                }
            ]
        },
    )

    id: str
    name: str
    slug: str
    vertical: MerchantVertical
    description: str | None = None
    cuisines: list[str]
    city: str
    community: str | None = None
    latitude: Decimal
    longitude: Decimal
    rating: Decimal
    rating_count: int
    price_tier: int = Field(ge=1, le=4, description="1 = budget through 4 = premium")
    prep_time_minutes: int
    currency: str
    min_order_amount: Decimal
    logo_url: str | None = None
    hero_image_url: str | None = None
    accepting_orders: bool
    distance_m: float = Field(description="Straight-line distance from the search point, metres")
    is_open_now: bool
    eta_min: int = Field(
        description="Estimated minutes until delivery: preparation plus travel to the search point"
    )


class MerchantDetail(MerchantSummary):
    """A merchant's full storefront, including this week's schedule."""

    status: MerchantStatus
    address_line1: str
    address_line2: str | None = None
    emirate: str | None = None
    country_code: str
    phone: str | None = None
    timezone: str
    delivery_radius_m: int
    opening_hours: list[OpeningHours] = Field(default_factory=list)


class MerchantUpdate(BaseModel):
    """Partially update a merchant storefront.

    Commission, status and rating are absent by design: they are operations- and
    outcome-owned, and a merchant must not be able to change its own economics.
    """

    name: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    cuisines: list[str] | None = Field(default=None, max_length=10)
    phone: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=254)
    prep_time_minutes: int | None = Field(default=None, ge=1, le=180)
    delivery_radius_m: int | None = Field(default=None, ge=100, le=50_000)
    min_order_amount: Decimal | None = Field(default=None, ge=0, le=10_000)
    logo_url: str | None = Field(default=None, max_length=500)
    hero_image_url: str | None = Field(default=None, max_length=500)
    address_line1: str | None = Field(default=None, min_length=3, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    community: str | None = Field(default=None, max_length=120)
    latitude: Latitude | None = None
    longitude: Longitude | None = None

    @model_validator(mode="after")
    def _validate_cuisines(self) -> Self:
        if self.cuisines is None:
            return self
        normalised = [cuisine.strip().lower() for cuisine in self.cuisines]
        unknown = sorted(set(normalised) - set(CUISINES))
        if unknown:
            raise ValueError(f"unknown cuisines: {', '.join(unknown)}")
        object.__setattr__(self, "cuisines", list(dict.fromkeys(normalised)))
        return self


class AcceptingOrdersUpdate(BaseModel):
    """Toggle the merchant's own order kill switch."""

    accepting_orders: bool
    reason: str | None = Field(
        default=None, max_length=200, description="Recorded for operations follow-up"
    )


class ServiceabilityResponse(BaseModel):
    """Whether a merchant delivers to a given point."""

    merchant_id: str
    latitude: Decimal
    longitude: Decimal
    deliverable: bool
    distance_m: float
    reason: str | None = Field(
        default=None, description="Why delivery is unavailable, when it is not"
    )
