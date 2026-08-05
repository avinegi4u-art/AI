"""Events the auth service consumes.

Auth keeps a small read model of profile data (``principals.display_name``) so the login
response can include the user's name without a synchronous call into the user service.
That copy is maintained here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marsool_auth.models import AuthPrincipal
from marsool_core.events.consumer import EventRouter, IdempotentConsumption
from marsool_core.events.envelope import EventEnvelope
from marsool_core.events.topics import EventType
from marsool_core.logging import get_logger

logger = get_logger(__name__)


def build_router(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    idempotency: IdempotentConsumption,
    consumer_group: str,
    metrics: object | None = None,
) -> EventRouter:
    """Wire the auth service's event handlers."""
    router = EventRouter(consumer_group=consumer_group, metrics=metrics)
    router.on(
        EventType.USER_PROFILE_UPDATED,
        _transactional(session_factory, idempotency, handle_profile_updated),
    )
    return router


def _transactional(
    session_factory: async_sessionmaker[AsyncSession],
    idempotency: IdempotentConsumption,
    handler: Callable[[AsyncSession, EventEnvelope], Awaitable[None]],
) -> Callable[[EventEnvelope], Awaitable[None]]:
    """Run ``handler`` and its idempotency claim in one transaction.

    Claiming in the same transaction is what makes at-least-once delivery safe: the event
    is only recorded as processed if the handler's writes committed.
    """

    async def _run(envelope: EventEnvelope) -> None:
        async with session_factory() as session:
            try:
                if await idempotency.already_processed(session, envelope.event_id):
                    logger.debug("event_already_processed", event_id=envelope.event_id)
                    return
                await handler(session, envelope)
                idempotency.mark_processed(session, envelope)
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    return _run


async def handle_profile_updated(session: AsyncSession, envelope: EventEnvelope) -> None:
    """Refresh the denormalised display name after a profile change."""
    user_id = envelope.data.get("user_id")
    name = envelope.data.get("name")
    if not user_id:
        logger.warning("profile_updated_event_missing_user_id", event_id=envelope.event_id)
        return
    await session.execute(
        update(AuthPrincipal).where(AuthPrincipal.id == user_id).values(display_name=name)
    )
