"""Test helpers shared by all service test suites.

Kept in the shipped package (not a test-only module) so every service imports the same
harness: identical settings overrides, identical schema lifecycle, identical auth stubs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from marsool_core.ids import new_id
from marsool_core.security.jwt import encode_access_token
from marsool_core.security.principal import Principal, Role

TEST_DATABASE_DSN = "postgresql+asyncpg://marsool:marsool@localhost:5432/marsool_test"
TEST_REDIS_DSN = "redis://localhost:6379/15"
# At least 32 bytes: PyJWT warns about shorter HMAC keys for SHA-256.
TEST_JWT_SECRET = "marsool-test-secret-key-0123456789abcdef"  # noqa: S105 - test fixture


def test_env(service_name: str, **overrides: str) -> dict[str, str]:
    """Return environment variables that point a service at the test infrastructure."""
    env = {
        "MARSOOL_SERVICE_NAME": service_name,
        "MARSOOL_ENVIRONMENT": "test",
        "MARSOOL_LOG_LEVEL": "WARNING",
        "MARSOOL_LOG_FORMAT": "console",
        "MARSOOL_DATABASE_DSN": TEST_DATABASE_DSN,
        "MARSOOL_REDIS_DSN": TEST_REDIS_DSN,
        "MARSOOL_JWT_SECRET": TEST_JWT_SECRET,
        "MARSOOL_EVENT_BUS_BACKEND": "memory",
        "MARSOOL_METRICS_ENABLED": "true",
    }
    env.update(overrides)
    return env


def build_test_engine(dsn: str = TEST_DATABASE_DSN) -> AsyncEngine:
    """Create an engine for tests with pooling disabled.

    ``NullPool`` avoids cross-event-loop connection reuse, which is the most common
    source of flaky "attached to a different loop" failures in async test suites.
    """
    from sqlalchemy.pool import NullPool

    return create_async_engine(dsn, poolclass=NullPool, future=True)


@asynccontextmanager
async def temporary_schema(
    engine: AsyncEngine, *, metadata: MetaData, schema: str, create_postgis: bool = True
) -> AsyncIterator[None]:
    """Create the service's schema and tables, dropping them on exit.

    Tables are created from metadata rather than by running Alembic: it is much faster,
    and a dedicated migration test asserts that the migrations produce the same schema.
    """
    async with engine.begin() as connection:
        if create_postgis:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await connection.run_sync(metadata.create_all)
    try:
        yield
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


async def truncate_all(engine: AsyncEngine, *, metadata: MetaData) -> None:
    """Truncate every table in ``metadata``, for per-test isolation."""
    table_names = ", ".join(
        f'"{table.schema}"."{table.name}"' for table in reversed(metadata.sorted_tables)
    )
    if not table_names:
        return
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))


def session_factory_for(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to a test engine."""
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def make_principal(
    *,
    role: Role = Role.CUSTOMER,
    user_id: str | None = None,
    scopes: frozenset[str] = frozenset(),
    merchant_ids: tuple[str, ...] = (),
) -> Principal:
    """Build a principal for tests."""
    return Principal(
        id=user_id or new_id("usr"),
        role=role,
        scopes=scopes,
        merchant_ids=merchant_ids,
        session_id=new_id("ses"),
    )


def bearer_header(
    principal: Principal,
    *,
    issuer: str = "marsool.auth",
    audience: str = "marsool.api",
    secret: str = TEST_JWT_SECRET,
    ttl_seconds: int = 900,
) -> dict[str, str]:
    """Return an ``Authorization`` header carrying a valid access token."""
    token, _ = encode_access_token(
        principal,
        jwt_id=new_id("jti"),
        ttl_seconds=ttl_seconds,
        issuer=issuer,
        audience=audience,
        secret=secret,
    )
    return {"Authorization": f"Bearer {token}"}


def override_principal(
    app: Any, guards: Any, principal: Principal
) -> Callable[[], None]:
    """Override a service's auth dependency to return ``principal``.

    Returns a callable that removes the override, for use in fixture teardown.
    """
    app.dependency_overrides[guards.verifier] = lambda: principal

    def _reset() -> None:
        app.dependency_overrides.pop(guards.verifier, None)

    return _reset
