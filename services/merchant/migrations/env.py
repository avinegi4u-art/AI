"""Alembic environment for the merchant service."""

from marsool_core.db.migrations import run_migrations
from marsool_merchant import SCHEMA
from marsool_merchant.config import MerchantSettings
from marsool_merchant.models import Base

run_migrations(
    metadata=Base.metadata,
    settings=MerchantSettings(),
    schema=SCHEMA,
    # Merchant locations and service-area polygons are PostGIS geography columns.
    create_postgis=True,
)
