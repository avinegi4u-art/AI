"""Catalog service settings."""

from __future__ import annotations

import functools

from pydantic import Field

from marsool_core.config import ServiceSettings


class CatalogSettings(ServiceSettings):
    """Settings for the catalog service."""

    service_name: str = "marsool-catalog"
    port: int = 8004

    # Menus are read on nearly every session and change rarely, so they are the single
    # highest-leverage cache in the platform. The TTL is a ceiling: availability changes
    # invalidate the entry immediately, so a sold-out item disappears without waiting.
    menu_cache_ttl_seconds: int = Field(default=300, ge=0, le=3600)

    max_items_per_basket_line: int = Field(default=50, ge=1, le=200)
    max_basket_lines: int = Field(default=50, ge=1, le=200)


@functools.cache
def get_catalog_settings() -> CatalogSettings:
    """Return cached catalog settings."""
    return CatalogSettings()
