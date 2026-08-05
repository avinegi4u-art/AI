"""ORM models for the ``catalog`` schema."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from marsool_catalog import SCHEMA
from marsool_core.db.base import TimestampMixin, metadata_for_schema
from marsool_core.db.types import CurrencyCode, MinorUnits, StringEnum, enum_values
from marsool_core.events.consumer import build_processed_events_model
from marsool_core.events.outbox import build_outbox_model
from marsool_core.ids import (
    PREFIX_CATEGORY,
    PREFIX_MENU,
    PREFIX_MENU_ITEM,
    PREFIX_OPTION,
    PREFIX_OPTION_GROUP,
    new_id,
)


class Base(DeclarativeBase):
    """Declarative base bound to the ``catalog`` schema."""

    metadata = metadata_for_schema(SCHEMA)


class SelectionType(StrEnum):
    """How many options a customer may pick from a group.

    A single unified model covers both concepts the brief calls out separately: a
    ``SINGLE`` group is a variant (size, crust), a ``MULTI`` group is a modifier set
    (extra toppings, sauces). Two near-identical tables would double the validation logic
    for no gain.
    """

    SINGLE = "SINGLE"
    MULTI = "MULTI"


class Menu(TimestampMixin, Base):
    """A merchant's menu. Multiple menus support day-parting (breakfast, dinner)."""

    __tablename__ = "menus"
    __table_args__ = (
        Index("ix_menus_merchant_active", "merchant_id", "is_active"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_MENU)
    )
    # No foreign key: merchants live in another service's schema. Referential integrity
    # across bounded contexts is maintained by events, not by database constraints, so the
    # schemas can be split onto separate clusters without rewriting anything.
    merchant_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class MenuCategory(TimestampMixin, Base):
    """A section of a menu, e.g. "Starters"."""

    __tablename__ = "menu_categories"
    __table_args__ = (
        UniqueConstraint("menu_id", "name", name="uq_menu_categories_menu_name"),
        Index("ix_menu_categories_menu_position", "menu_id", "position"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_CATEGORY)
    )
    menu_id: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.menus.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MenuItem(TimestampMixin, Base):
    """A sellable item."""

    __tablename__ = "menu_items"
    __table_args__ = (
        CheckConstraint("price_minor >= 0", name="price_non_negative"),
        CheckConstraint("spice_level BETWEEN 0 AND 3", name="spice_level_range"),
        Index("ix_menu_items_category_position", "category_id", "position"),
        Index("ix_menu_items_merchant_available", "merchant_id", "is_available"),
        Index("ix_menu_items_tags", "tags", postgresql_using="gin"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_MENU_ITEM)
    )
    # Denormalised from the parent category so the hot paths — merchant menu fetch and
    # basket validation — never need a join back through categories and menus.
    merchant_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    category_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey(f"{SCHEMA}.menu_categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    price_minor: Mapped[int] = mapped_column(MinorUnits, nullable=False)
    currency: Mapped[str] = mapped_column(CurrencyCode, nullable=False, default="AED")
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False, default=list)
    calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spice_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class OptionGroup(TimestampMixin, Base):
    """A set of choices attached to an item."""

    __tablename__ = "option_groups"
    __table_args__ = (
        CheckConstraint(f"selection_type IN {enum_values(SelectionType)}", name="selection_valid"),
        CheckConstraint("min_select >= 0", name="min_select_non_negative"),
        CheckConstraint("max_select >= 1", name="max_select_positive"),
        CheckConstraint("max_select >= min_select", name="select_bounds_ordered"),
        Index("ix_option_groups_item_position", "item_id", "position"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_OPTION_GROUP)
    )
    item_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey(f"{SCHEMA}.menu_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    selection_type: Mapped[SelectionType] = mapped_column(
        StringEnum(SelectionType), nullable=False, default=SelectionType.SINGLE
    )
    min_select: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    max_select: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    @property
    def is_required(self) -> bool:
        return self.min_select > 0


class Option(TimestampMixin, Base):
    """One choice within an option group."""

    __tablename__ = "options"
    __table_args__ = (
        Index("ix_options_group_position", "group_id", "position"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_OPTION)
    )
    group_id: Mapped[str] = mapped_column(
        String(40),
        ForeignKey(f"{SCHEMA}.option_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Signed: an option may be a discount (smaller portion) as well as a surcharge.
    price_delta_minor: Mapped[int] = mapped_column(MinorUnits, nullable=False, default=0)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


EventOutbox = build_outbox_model(Base)
ProcessedEvent = build_processed_events_model(Base)
