"""User service settings."""

from __future__ import annotations

import functools

from pydantic import Field

from marsool_core.config import ServiceSettings


class UserSettings(ServiceSettings):
    """Settings for the user service."""

    service_name: str = "marsool-user"
    port: int = 8002

    max_addresses_per_user: int = Field(default=20, ge=1, le=100)
    default_market: str = "AE"
    default_locale: str = "en-AE"
    default_currency: str = "AED"


@functools.cache
def get_user_settings() -> UserSettings:
    """Return cached user settings."""
    return UserSettings()
