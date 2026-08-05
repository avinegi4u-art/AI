"""Request and response schemas for the auth API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from marsool_core.phone import InvalidPhoneNumberError, normalize_phone
from marsool_core.security.principal import Role

PhoneField = Annotated[
    str,
    Field(
        min_length=6,
        max_length=24,
        description="Phone number in E.164 or local format, e.g. '+971501234567' or '0501234567'",
        examples=["+971501234567"],
    ),
]


class _NormalisedPhoneMixin(BaseModel):
    """Normalises ``phone`` to E.164 at the API boundary.

    Doing this in the schema means no downstream code can accidentally work with an
    un-normalised number.
    """

    phone: PhoneField

    @field_validator("phone")
    @classmethod
    def _normalise(cls, value: str) -> str:
        try:
            return normalize_phone(value)
        except InvalidPhoneNumberError as exc:
            raise ValueError(str(exc)) from exc


class OtpRequest(_NormalisedPhoneMixin):
    """Request a one-time password for a phone number."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"phone": "+971501234567"}]})


class OtpChallengeResponse(BaseModel):
    """Details of an issued OTP challenge."""

    challenge_id: str = Field(description="Identifier of the challenge, for support lookups")
    phone: str = Field(description="Normalised E.164 phone the code was sent to")
    expires_at: datetime
    attempts_allowed: int
    delivery_channel: str = Field(description="Channel used to deliver the code, e.g. 'sms'")
    debug_code: str | None = Field(
        default=None,
        description="The OTP itself. Only populated outside production, for local testing.",
    )


class LoginRequest(_NormalisedPhoneMixin):
    """Exchange a phone number and OTP for tokens."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"phone": "+971501234567", "otp": "123456"}]}
    )

    otp: Annotated[
        str,
        Field(min_length=4, max_length=8, pattern=r"^\d+$", description="The numeric OTP code"),
    ]


class RefreshRequest(BaseModel):
    """Exchange a refresh token for a new token pair."""

    refresh_token: Annotated[str, Field(min_length=20, description="A valid refresh token")]


class LogoutRequest(BaseModel):
    """Revoke a session.

    Supply either the refresh token to revoke a single session, or ``all_sessions`` to
    revoke every session belonging to the authenticated principal.
    """

    refresh_token: str | None = Field(default=None, min_length=20)
    all_sessions: bool = False

    @model_validator(mode="after")
    def _require_a_target(self) -> Self:
        if not self.refresh_token and not self.all_sessions:
            raise ValueError("provide refresh_token or set all_sessions=true")
        return self


class AuthenticatedUser(BaseModel):
    """The authenticated user summary returned alongside tokens."""

    id: str
    role: Role
    name: str | None = None
    phone: str


class TokenPair(BaseModel):
    """The token pair issued on login and refresh."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    "token_type": "Bearer",
                    "expires_in": 900,
                    "user": {
                        "id": "usr_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
                        "role": "customer",
                        "name": "Layla",
                        "phone": "+971501234567",
                    },
                }
            ]
        }
    )

    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 - a scheme name, not a credential
    expires_in: int = Field(description="Access token lifetime in seconds")
    user: AuthenticatedUser


class SessionSummary(BaseModel):
    """An active session belonging to the authenticated principal."""

    session_id: str
    created_at: datetime
    expires_at: datetime
    user_agent: str | None = None
    ip_address: str | None = None
    is_current: bool = False
