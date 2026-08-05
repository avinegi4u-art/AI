"""Request and response schemas for the user API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from marsool_core.security.principal import Role
from marsool_user.models import (
    AddressLabel,
    CourierStatus,
    CourierVehicle,
    UserStatus,
)

Latitude = Annotated[Decimal, Field(ge=-90, le=90, description="WGS84 latitude")]
Longitude = Annotated[Decimal, Field(ge=-180, le=180, description="WGS84 longitude")]

# Curated list rather than free text: these tags drive search filters and the AI
# assistant's reasoning, so they must be a closed vocabulary.
ALLOWED_DIETARY_TAGS = frozenset(
    {
        "halal",
        "vegetarian",
        "vegan",
        "gluten_free",
        "dairy_free",
        "nut_free",
        "keto",
        "low_carb",
        "high_protein",
        "organic",
    }
)


class AddressBase(BaseModel):
    """Fields shared by address create and update payloads."""

    label: AddressLabel = AddressLabel.HOME
    nickname: str | None = Field(default=None, max_length=60)
    line1: str = Field(min_length=3, max_length=200, description="Street address or building name")
    line2: str | None = Field(default=None, max_length=200)
    building: str | None = Field(default=None, max_length=120)
    apartment: str | None = Field(default=None, max_length=60)
    community: str | None = Field(
        default=None, max_length=120, description="Community or district, e.g. 'Al Reem Island'"
    )
    makani_number: str | None = Field(
        default=None,
        max_length=20,
        description="UAE Makani address number, when the customer knows it",
    )
    city: str = Field(default="Abu Dhabi", max_length=80)
    emirate: str | None = Field(default=None, max_length=80)
    country_code: str = Field(default="AE", min_length=2, max_length=2)
    latitude: Latitude
    longitude: Longitude
    delivery_notes: str | None = Field(
        default=None, max_length=500, description="Courier hand-off instructions"
    )

    @field_validator("country_code")
    @classmethod
    def _uppercase_country(cls, value: str) -> str:
        return value.upper()


class AddressCreate(AddressBase):
    """Create a delivery address."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "label": "HOME",
                    "line1": "Sky Tower, Shams Abu Dhabi",
                    "apartment": "1204",
                    "community": "Al Reem Island",
                    "city": "Abu Dhabi",
                    "emirate": "Abu Dhabi",
                    "country_code": "AE",
                    "latitude": "24.494640",
                    "longitude": "54.399460",
                    "delivery_notes": "Leave with the concierge",
                }
            ]
        }
    )

    set_as_default: bool = Field(
        default=False, description="Make this the user's default delivery address"
    )


class AddressUpdate(BaseModel):
    """Partially update a delivery address. Omitted fields are left unchanged."""

    label: AddressLabel | None = None
    nickname: str | None = Field(default=None, max_length=60)
    line1: str | None = Field(default=None, min_length=3, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    building: str | None = Field(default=None, max_length=120)
    apartment: str | None = Field(default=None, max_length=60)
    community: str | None = Field(default=None, max_length=120)
    makani_number: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=80)
    emirate: str | None = Field(default=None, max_length=80)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    latitude: Latitude | None = None
    longitude: Longitude | None = None
    delivery_notes: str | None = Field(default=None, max_length=500)
    set_as_default: bool | None = None


class AddressResponse(AddressBase):
    """A stored delivery address."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


class CourierProfileResponse(BaseModel):
    """Courier-specific details."""

    model_config = ConfigDict(from_attributes=True)

    vehicle: CourierVehicle
    status: CourierStatus
    rating: Decimal
    completed_deliveries: int
    max_concurrent_orders: int
    licence_expires_at: date | None = None


class CourierProfileUpdate(BaseModel):
    """Update courier-specific details.

    Rating and completed-delivery counts are derived from order outcomes, so they are not
    settable here.
    """

    vehicle: CourierVehicle | None = None
    status: CourierStatus | None = None
    licence_number: str | None = Field(default=None, max_length=40)
    licence_expires_at: date | None = None
    max_concurrent_orders: int | None = Field(default=None, ge=1, le=10)
    payout_iban: str | None = Field(default=None, min_length=15, max_length=34)


class UserResponse(BaseModel):
    """The authenticated user's profile."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "usr_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
                    "phone": "+971501234567",
                    "role": "customer",
                    "status": "ACTIVE",
                    "name": "Layla Al Mansouri",
                    "email": "layla@example.ae",
                    "locale": "en-AE",
                    "marketing_opt_in": False,
                    "dietary_tags": ["halal", "vegetarian"],
                    "default_address_id": "adr_01J9Z8XQF3K7M2P4R6T8V0W1Y4",
                }
            ]
        },
    )

    id: str
    phone: str | None = Field(
        default=None,
        description=(
            "E.164 phone number. Null only in the brief window between just-in-time "
            "provisioning and the arrival of the registration event."
        ),
    )
    role: Role
    status: UserStatus
    name: str | None = None
    email: str | None = None
    locale: str
    date_of_birth: date | None = None
    avatar_url: str | None = None
    marketing_opt_in: bool
    dietary_tags: list[str]
    default_address_id: str | None = None
    created_at: datetime
    courier_profile: CourierProfileResponse | None = None
    merchant_ids: list[str] = Field(
        default_factory=list, description="Merchants this user may administer"
    )


class UserUpdate(BaseModel):
    """Partially update the authenticated user's profile.

    Phone number and role are deliberately absent: phone changes go through the auth
    service (they require OTP verification of the new number) and role changes are an
    administrative action.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    locale: str | None = Field(default=None, min_length=2, max_length=10)
    date_of_birth: date | None = None
    avatar_url: str | None = Field(default=None, max_length=500)
    marketing_opt_in: bool | None = None
    dietary_tags: list[str] | None = Field(default=None, max_length=20)

    @field_validator("dietary_tags")
    @classmethod
    def _validate_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalised = [tag.strip().lower() for tag in value]
        unknown = sorted(set(normalised) - ALLOWED_DIETARY_TAGS)
        if unknown:
            raise ValueError(
                f"unknown dietary tags: {', '.join(unknown)}; "
                f"allowed: {', '.join(sorted(ALLOWED_DIETARY_TAGS))}"
            )
        # Deduplicate while preserving the order the client sent.
        return list(dict.fromkeys(normalised))
