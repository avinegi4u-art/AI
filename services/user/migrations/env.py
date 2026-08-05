"""Alembic environment for the user service."""

from marsool_core.db.migrations import run_migrations
from marsool_user import SCHEMA
from marsool_user.config import UserSettings
from marsool_user.models import Base

run_migrations(
    metadata=Base.metadata,
    settings=UserSettings(),
    schema=SCHEMA,
    # Addresses use a PostGIS geography column for serviceability and proximity queries.
    create_postgis=True,
)
