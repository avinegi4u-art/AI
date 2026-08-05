"""Async engine and session lifecycle.

Every service owns a single :class:`Database` instance created at startup and disposed
at shutdown. Request handlers receive a session through a FastAPI dependency that
commits on success and rolls back on any exception, so handlers never manage
transactions by hand.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from marsool_core.config import ServiceSettings
from marsool_core.logging import get_logger

logger = get_logger(__name__)


def build_async_engine(settings: ServiceSettings) -> AsyncEngine:
    """Create the async engine for a service.

    ``pool_pre_ping`` guards against connections killed by a failover or an idle
    timeout, and ``statement_timeout`` caps runaway queries at the server so a slow
    query cannot exhaust the pool.
    """
    return create_async_engine(
        str(settings.database_dsn),
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "server_settings": {
                "application_name": settings.service_name,
                "statement_timeout": str(settings.database_statement_timeout_ms),
                "jit": "off",
            }
        },
    )


class Database:
    """Owns the engine and session factory for one service."""

    def __init__(self, settings: ServiceSettings, *, engine: AsyncEngine | None = None) -> None:
        self._settings = settings
        self._engine = engine or build_async_engine(settings)
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Yield a session inside a transaction, committing on clean exit.

        Use this outside the request cycle (workers, event consumers, scripts).
        """
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        """Close all pooled connections."""
        await self._engine.dispose()
        logger.info("database_engine_disposed", service=self._settings.service_name)


def session_dependency(
    get_database: Callable[[], Database],
) -> Callable[[], AsyncIterator[AsyncSession]]:
    """Build a FastAPI dependency yielding a request-scoped transactional session.

    Args:
        get_database: Callable returning the service's :class:`Database`. Passed as a
            callable (not the instance) so the dependency can be declared at import
            time while the database itself is created during startup.
    """

    async def _dependency() -> AsyncIterator[AsyncSession]:
        async with get_database().transaction() as session:
            yield session

    return _dependency
