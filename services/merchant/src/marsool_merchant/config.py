"""Merchant service settings."""

from __future__ import annotations

import functools

from pydantic import Field

from marsool_core.config import ServiceSettings


class MerchantSettings(ServiceSettings):
    """Settings for the merchant service."""

    service_name: str = "marsool-merchant"
    port: int = 8003

    # Search defaults, tuned for Abu Dhabi: the island is compact, so 5 km reaches most
    # of the city centre while keeping result sets and courier legs sensible.
    default_search_radius_m: int = Field(default=5_000, ge=100, le=50_000)
    max_search_radius_m: int = Field(default=25_000, ge=1_000, le=100_000)
    default_timezone: str = "Asia/Dubai"

    # Search results are cached briefly. Even a few seconds absorbs the thundering herd on
    # the home screen, and the window is short enough that a merchant pausing orders is
    # reflected almost immediately.
    search_cache_ttl_seconds: int = Field(default=15, ge=0, le=300)
    merchant_cache_ttl_seconds: int = Field(default=60, ge=0, le=3600)


@functools.cache
def get_merchant_settings() -> MerchantSettings:
    """Return cached merchant settings."""
    return MerchantSettings()
