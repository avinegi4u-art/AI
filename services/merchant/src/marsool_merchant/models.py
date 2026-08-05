"""ORM models for the ``merchant`` schema."""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from enum import StrEnum

from geoalchemy2 import WKBElement
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
)

# The PostgreSQL-specific ARRAY exposes the containment and overlap operators the
# cuisine filter needs; the generic sqlalchemy.ARRAY does not.
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from marsool_core.db.base import TimestampMixin, metadata_for_schema
from marsool_core.db.types import (
    CurrencyCode,
    MinorUnits,
    Point4326,
    Polygon4326,
    StringEnum,
    enum_values,
)
from marsool_core.events.consumer import build_processed_events_model
from marsool_core.events.outbox import build_outbox_model
from marsool_core.ids import PREFIX_MERCHANT, PREFIX_SERVICE_AREA, new_id
from marsool_merchant import SCHEMA


class Base(DeclarativeBase):
    """Declarative base bound to the ``merchant`` schema."""

    metadata = metadata_for_schema(SCHEMA)


class MerchantStatus(StrEnum):
    """Lifecycle state, controlled by operations rather than by the merchant.

    Only ``ACTIVE`` merchants appear in customer search.
    """

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    SUSPENDED = "SUSPENDED"


class MerchantVertical(StrEnum):
    """Product vertical. The MVP launches with restaurants only."""

    RESTAURANT = "RESTAURANT"
    GROCERY = "GROCERY"
    PHARMACY = "PHARMACY"
    CONVENIENCE = "CONVENIENCE"


class Merchant(TimestampMixin, Base):
    """A merchant storefront."""

    __tablename__ = "merchants"
    __table_args__ = (
        CheckConstraint(f"status IN {enum_values(MerchantStatus)}", name="status_valid"),
        CheckConstraint(f"vertical IN {enum_values(MerchantVertical)}", name="vertical_valid"),
        CheckConstraint("rating BETWEEN 0 AND 5", name="rating_range"),
        CheckConstraint("price_tier BETWEEN 1 AND 4", name="price_tier_range"),
        CheckConstraint("prep_time_minutes BETWEEN 1 AND 180", name="prep_time_range"),
        CheckConstraint("commission_bps BETWEEN 0 AND 5000", name="commission_range"),
        CheckConstraint("delivery_radius_m BETWEEN 100 AND 50000", name="delivery_radius_range"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        # Supports the search path: filter to bookable merchants, then rank by distance.
        Index("ix_merchants_status_accepting", "status", "accepting_orders"),
        # GIN index makes the cuisine overlap filter an index scan rather than a seq scan.
        Index("ix_merchants_cuisines", "cuisines", postgresql_using="gin"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_MERCHANT)
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    vertical: Mapped[MerchantVertical] = mapped_column(
        StringEnum(MerchantVertical), nullable=False, default=MerchantVertical.RESTAURANT
    )
    status: Mapped[MerchantStatus] = mapped_column(
        StringEnum(MerchantStatus), nullable=False, default=MerchantStatus.DRAFT
    )
    # The merchant's own kill switch, independent of ``status``: a kitchen that is
    # overwhelmed pauses orders without operations having to deactivate the storefront.
    accepting_orders: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cuisines: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False, default=list)

    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)

    address_line1: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    community: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str] = mapped_column(String(80), nullable=False, default="Abu Dhabi")
    emirate: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="AE")
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    location: Mapped[WKBElement] = mapped_column(Point4326, nullable=False)

    # Opening hours are stored as local wall-clock times; the timezone makes "is it open
    # now?" answerable without assuming the whole platform lives in one offset.
    timezone: Mapped[str] = mapped_column(String(40), nullable=False, default="Asia/Dubai")
    prep_time_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=20)
    delivery_radius_m: Mapped[int] = mapped_column(Integer, nullable=False, default=5_000)

    rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("0.00"))
    rating_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)

    currency: Mapped[str] = mapped_column(CurrencyCode, nullable=False, default="AED")
    min_order_minor: Mapped[int] = mapped_column(MinorUnits, nullable=False, default=0)
    commission_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=1_500)

    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hero_image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    @property
    def is_bookable(self) -> bool:
        """Whether the storefront may take orders at all, ignoring opening hours."""
        return self.status is MerchantStatus.ACTIVE and self.accepting_orders


class MerchantHours(Base):
    """One opening window for one weekday.

    Multiple rows per day express split shifts (lunch and dinner service). A window whose
    ``closes_at`` is at or before ``opens_at`` runs past midnight, which is common for
    Abu Dhabi late-night service.
    """

    __tablename__ = "merchant_hours"
    __table_args__ = (
        # 0 = Monday through 6 = Sunday, matching Python's ``date.weekday()``.
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="day_of_week_range"),
        UniqueConstraint("merchant_id", "day_of_week", "opens_at", name="uq_merchant_hours_window"),
        Index("ix_merchant_hours_merchant_day", "merchant_id", "day_of_week"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("mhr"))
    merchant_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey(f"{SCHEMA}.merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    opens_at: Mapped[time] = mapped_column(Time, nullable=False)
    closes_at: Mapped[time] = mapped_column(Time, nullable=False)

    @property
    def spans_midnight(self) -> bool:
        return self.closes_at <= self.opens_at


class ServiceArea(TimestampMixin, Base):
    """A polygon the merchant delivers to.

    A merchant with no active service area falls back to ``delivery_radius_m``. Polygons
    exist because real coverage is not a circle: bridges, industrial zones and islands make
    a radius either exclude reachable customers or promise deliveries that cannot be made.
    """

    __tablename__ = "service_areas"
    __table_args__ = (
        Index("ix_service_areas_merchant_active", "merchant_id", "is_active"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_SERVICE_AREA)
    )
    merchant_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey(f"{SCHEMA}.merchants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    boundary: Mapped[WKBElement] = mapped_column(Polygon4326, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


EventOutbox = build_outbox_model(Base)
ProcessedEvent = build_processed_events_model(Base)
