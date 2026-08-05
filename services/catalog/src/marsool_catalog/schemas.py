"""Request and response schemas for the catalog API."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from marsool_catalog.models import SelectionType

Quantity = Annotated[int, Field(ge=1, le=50, description="Number of units")]


class OptionResponse(BaseModel):
    """One choice within an option group."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    price_delta: Decimal = Field(
        description="Signed price adjustment; negative for a smaller portion"
    )
    is_available: bool
    is_default: bool


class OptionGroupResponse(BaseModel):
    """A set of choices attached to an item."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    selection_type: SelectionType
    min_select: int
    max_select: int
    is_required: bool
    options: list[OptionResponse]


class MenuItemResponse(BaseModel):
    """A sellable item with its configurable options."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "itm_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
                    "name": "Chicken Machboos",
                    "description": "Slow-cooked spiced rice with chicken",
                    "price": "38.00",
                    "currency": "AED",
                    "is_available": True,
                    "tags": ["halal", "house_special"],
                    "calories": 720,
                    "spice_level": 1,
                    "option_groups": [
                        {
                            "id": "opg_01J9Z8XQF3K7M2P4R6T8V0W1Y4",
                            "name": "Portion",
                            "selection_type": "SINGLE",
                            "min_select": 1,
                            "max_select": 1,
                            "is_required": True,
                            "options": [
                                {
                                    "id": "opt_01J9Z8XQF3K7M2P4R6T8V0W1Y5",
                                    "name": "Regular",
                                    "price_delta": "0.00",
                                    "is_available": True,
                                    "is_default": True,
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )

    id: str
    name: str
    description: str | None = None
    price: Decimal
    currency: str
    is_available: bool
    image_url: str | None = None
    tags: list[str]
    calories: int | None = None
    spice_level: int
    option_groups: list[OptionGroupResponse] = Field(default_factory=list)


class MenuCategoryResponse(BaseModel):
    """A section of a menu."""

    id: str
    name: str
    description: str | None = None
    items: list[MenuItemResponse]


class MenuResponse(BaseModel):
    """A merchant's full menu, nested for a single client round trip."""

    merchant_id: str
    menu_id: str
    name: str
    currency: str
    categories: list[MenuCategoryResponse]
    item_count: int
    available_item_count: int


class ItemAvailabilityUpdate(BaseModel):
    """Mark an item in or out of stock."""

    is_available: bool
    reason: str | None = Field(
        default=None, max_length=200, description="Recorded for operations follow-up"
    )


class MenuItemUpdate(BaseModel):
    """Partially update an item."""

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    price: Decimal | None = Field(default=None, ge=0, le=100_000)
    image_url: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = Field(default=None, max_length=20)
    calories: int | None = Field(default=None, ge=0, le=20_000)
    spice_level: int | None = Field(default=None, ge=0, le=3)
    is_available: bool | None = None


class BasketSelection(BaseModel):
    """The options chosen for one basket line."""

    group_id: str
    option_ids: list[str] = Field(max_length=20)

    @model_validator(mode="after")
    def _reject_duplicate_options(self) -> Self:
        if len(set(self.option_ids)) != len(self.option_ids):
            raise ValueError("the same option cannot be selected twice in one group")
        return self


class BasketLineRequest(BaseModel):
    """One line of a basket to validate and price."""

    item_id: str
    quantity: Quantity = 1
    selections: list[BasketSelection] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=280)


class BasketValidationRequest(BaseModel):
    """A basket to validate against a merchant's catalogue."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "lines": [
                        {
                            "item_id": "itm_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
                            "quantity": 2,
                            "selections": [
                                {
                                    "group_id": "opg_01J9Z8XQF3K7M2P4R6T8V0W1Y4",
                                    "option_ids": ["opt_01J9Z8XQF3K7M2P4R6T8V0W1Y5"],
                                }
                            ],
                            "notes": "No coriander",
                        }
                    ]
                }
            ]
        }
    )

    lines: list[BasketLineRequest] = Field(min_length=1, max_length=50)


class BasketLineIssue(BaseModel):
    """Why a basket line was rejected."""

    line_index: int = Field(description="Zero-based index of the offending line")
    item_id: str
    code: str = Field(
        description=(
            "One of: item_not_found, item_not_in_merchant, item_unavailable, "
            "option_group_not_found, option_not_found, option_unavailable, "
            "too_few_options, too_many_options, required_group_missing"
        )
    )
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class PricedOption(BaseModel):
    """A selected option, with the price it contributed."""

    option_id: str
    group_id: str
    name: str
    price_delta: Decimal


class PricedBasketLine(BaseModel):
    """A validated, priced basket line."""

    line_index: int
    item_id: str
    name: str
    quantity: int
    unit_base_price: Decimal = Field(description="Item price before options")
    unit_options_price: Decimal = Field(description="Sum of selected option deltas per unit")
    unit_price: Decimal = Field(description="Base plus options, per unit")
    line_total: Decimal = Field(description="Unit price multiplied by quantity")
    selected_options: list[PricedOption] = Field(default_factory=list)
    notes: str | None = None


class BasketValidationResponse(BaseModel):
    """The result of validating a basket.

    ``valid`` is false when any line has an issue. Lines are reported independently so a
    client can show every problem at once rather than one per round trip.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "merchant_id": "mch_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
                    "valid": True,
                    "currency": "AED",
                    "items_amount": "82.00",
                    "lines": [],
                    "issues": [],
                }
            ]
        }
    )

    merchant_id: str
    valid: bool
    currency: str
    items_amount: Decimal = Field(
        description="Sum of all validated line totals; zero when the basket is invalid"
    )
    lines: list[PricedBasketLine]
    issues: list[BasketLineIssue]
