"""Alembic environment for the catalog service."""

from marsool_catalog import SCHEMA
from marsool_catalog.config import CatalogSettings
from marsool_catalog.models import Base
from marsool_core.db.migrations import run_migrations

run_migrations(
    metadata=Base.metadata,
    settings=CatalogSettings(),
    schema=SCHEMA,
    create_postgis=False,
)
