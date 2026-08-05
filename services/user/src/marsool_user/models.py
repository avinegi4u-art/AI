"""ORM models for the ``identity`` schema."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from geoalchemy2 import WKBElement
from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from marsool_core.db.base import TimestampMixin, metadata_for_schema
from marsool_core.db.types import Point4326, StringEnum, enum_values
from marsool_core.events.consumer import build_processed_events_model
from marsool_core.events.outbox import build_outbox_model
from marsool_core.ids import PREFIX_ADDRESS, PREFIX_USER, new_id
from marsool_core.security.principal import Role
from marsool_user import SCHEMA


class Base(DeclarativeBase):
    """Declarative base bound to the ``identity`` schema."""

    metadata = metadata_for_schema(SCHEMA)


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class AddressLabel(StrEnum):
    """Where a delivery address is. Drives courier hand-off instructions."""

    HOME = "HOME"
    WORK = "WORK"
    OTHER = "OTHER"


class CourierVehicle(StrEnum):
    """Vehicle class. Determines batching capacity and routing speed in dispatch."""

    BICYCLE = "BICYCLE"
    MOTORCYCLE = "MOTORCYCLE"
    CAR = "CAR"
    VAN = "VAN"
    ON_FOOT = "ON_FOOT"


class CourierStatus(StrEnum):
    """Courier availability, as reported to dispatch."""

    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    ON_BREAK = "ON_BREAK"


class User(TimestampMixin, Base):
    """A platform user: customer, courier, merchant staff member or admin."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(f"role IN {enum_values(Role)}", name="role_valid"),
        CheckConstraint(f"status IN {enum_values(UserStatus)}", name="status_valid"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_USER)
    )
    # Set by the auth service; the user service never changes a user's phone or role.
    # Nullable for exactly one reason: a profile provisioned just-in-time from a JWT has
    # no phone claim, so the number is unknown until ``identity.user_registered`` arrives.
    # PostgreSQL unique indexes permit multiple NULLs, so the constraint still holds.
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, unique=True, index=True)
    role: Mapped[Role] = mapped_column(StringEnum(Role), nullable=False, default=Role.CUSTOMER)
    status: Mapped[UserStatus] = mapped_column(
        StringEnum(UserStatus), nullable=False, default=UserStatus.ACTIVE
    )
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True)
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="en-AE")
    date_of_birth: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    marketing_opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Dietary and cuisine tags, used by search ranking and the AI assistant.
    dietary_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, default=list
    )
    default_address_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # ``lazy="raise"`` on purpose: under asyncio an implicit lazy load raises an opaque
    # MissingGreenlet error deep inside response serialisation. Failing loudly at the
    # access site forces every read to go through an explicit repository query.
    addresses: Mapped[list[Address]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="Address.created_at",
    )
    courier_profile: Mapped[CourierProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False, lazy="raise"
    )

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE


class Address(TimestampMixin, Base):
    """A delivery address.

    Both the raw ``latitude``/``longitude`` and a PostGIS ``geography`` point are stored:
    the scalars are what clients send and read, while the geography column is what
    supports containment and distance queries (serviceability, courier proximity)
    without per-query projection.
    """

    __tablename__ = "addresses"
    __table_args__ = (
        CheckConstraint(f"label IN {enum_values(AddressLabel)}", name="label_valid"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        Index("ix_addresses_user_created", "user_id", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_ADDRESS)
    )
    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[AddressLabel] = mapped_column(
        StringEnum(AddressLabel), nullable=False, default=AddressLabel.HOME
    )
    nickname: Mapped[str | None] = mapped_column(String(60), nullable=True)
    line1: Mapped[str] = mapped_column(String(200), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    building: Mapped[str | None] = mapped_column(String(120), nullable=True)
    apartment: Mapped[str | None] = mapped_column(String(60), nullable=True)
    # Abu Dhabi addresses are commonly given as a community plus a Makani address rather
    # than a street number, so both are first-class fields.
    community: Mapped[str | None] = mapped_column(String(120), nullable=True)
    makani_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    city: Mapped[str] = mapped_column(String(80), nullable=False, default="Abu Dhabi")
    emirate: Mapped[str | None] = mapped_column(String(80), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="AE")
    latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    longitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    location: Mapped[WKBElement] = mapped_column(Point4326, nullable=False)
    delivery_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="addresses")


class CourierProfile(TimestampMixin, Base):
    """Courier-specific details used by dispatch scoring and payouts."""

    __tablename__ = "courier_profiles"
    __table_args__ = (
        CheckConstraint(f"vehicle IN {enum_values(CourierVehicle)}", name="vehicle_valid"),
        CheckConstraint(f"status IN {enum_values(CourierStatus)}", name="status_valid"),
        CheckConstraint("rating BETWEEN 0 AND 5", name="rating_range"),
        CheckConstraint("max_concurrent_orders BETWEEN 1 AND 10", name="capacity_range"),
        {"schema": SCHEMA},
    )

    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), primary_key=True
    )
    vehicle: Mapped[CourierVehicle] = mapped_column(
        StringEnum(CourierVehicle), nullable=False, default=CourierVehicle.MOTORCYCLE
    )
    status: Mapped[CourierStatus] = mapped_column(
        StringEnum(CourierStatus), nullable=False, default=CourierStatus.OFFLINE, index=True
    )
    licence_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    licence_expires_at: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    # Reliability inputs to the dispatch scoring function.
    rating: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("5.00"))
    completed_deliveries: Mapped[int] = mapped_column(nullable=False, default=0)
    max_concurrent_orders: Mapped[int] = mapped_column(nullable=False, default=3)
    payout_iban: Mapped[str | None] = mapped_column(String(34), nullable=True)

    user: Mapped[User] = relationship(back_populates="courier_profile")


class MerchantStaff(TimestampMixin, Base):
    """Links a user to a merchant they may administer.

    Authorization reads this to populate ``merchant_ids`` on the principal, which is what
    stops one merchant's staff from editing another merchant's catalogue.
    """

    __tablename__ = "merchant_staff"
    __table_args__ = (
        UniqueConstraint("user_id", "merchant_id", name="uq_merchant_staff_user_merchant"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("mst"))
    user_id: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    merchant_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    position: Mapped[str | None] = mapped_column(String(60), nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


EventOutbox = build_outbox_model(Base)
ProcessedEvent = build_processed_events_model(Base)
