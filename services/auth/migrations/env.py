"""Alembic environment for the auth service."""

from marsool_auth import SCHEMA
from marsool_auth.config import AuthSettings
from marsool_auth.models import Base
from marsool_core.db.migrations import run_migrations

run_migrations(
    metadata=Base.metadata,
    settings=AuthSettings(),
    schema=SCHEMA,
    create_postgis=False,
)
