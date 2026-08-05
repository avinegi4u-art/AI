"""Auth service settings."""

from __future__ import annotations

import functools

from pydantic import Field

from marsool_core.config import ServiceSettings


class AuthSettings(ServiceSettings):
    """Settings for the auth service."""

    service_name: str = "marsool-auth"
    port: int = 8001

    # OTP policy. Short codes are usable but only when paired with a tight expiry, a low
    # attempt ceiling and aggressive per-phone rate limiting.
    otp_length: int = Field(default=6, ge=4, le=8)
    otp_ttl_seconds: int = Field(default=300, ge=30, le=900)
    otp_max_attempts: int = Field(default=5, ge=1, le=10)
    otp_requests_per_hour: int = Field(default=5, ge=1, le=50)

    # Returning the OTP in the API response is a local-development affordance. It is
    # refused in production regardless of this flag (see AuthService.request_otp).
    otp_echo_in_response: bool = True

    # Default market used to normalise local-format phone numbers.
    default_market: str = "AE"

    # Maximum concurrent sessions per user; the oldest is revoked beyond this.
    max_active_sessions: int = Field(default=10, ge=1, le=50)


@functools.cache
def get_auth_settings() -> AuthSettings:
    """Return cached auth settings."""
    return AuthSettings()
