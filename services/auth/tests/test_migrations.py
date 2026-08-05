"""The migration chain must produce exactly the schema the models declare."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from marsool_auth import SCHEMA
from marsool_auth.models import Base
from marsool_core.testing import drop_schema, migration_diffs, provision_schema

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

pytestmark = pytest.mark.integration


@pytest.fixture
def pristine_schema() -> Iterator[None]:
    """Hand the schema to Alembic, then restore the metadata-built one.

    Restoring keeps this test independent of ordering: the rest of the suite builds
    tables from metadata and expects them to exist.
    """
    drop_schema(schema=SCHEMA)
    try:
        yield
    finally:
        drop_schema(schema=SCHEMA)
        provision_schema(metadata=Base.metadata, schema=SCHEMA, create_postgis=False)


def test_migrations_match_the_models(pristine_schema: None) -> None:
    diffs = migration_diffs(alembic_ini=ALEMBIC_INI, metadata=Base.metadata, schema=SCHEMA)
    assert diffs == [], (
        "auth models and migrations have drifted; run "
        "`alembic revision --autogenerate` in services/auth"
    )
