"""Events the user service consumes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marsool_core.events.consumer import EventRouter, IdempotentConsumption
from marsool_core.events.envelope import EventEnvelope
from marsool_core.events.topics import EventType
from marsool_core.logging import get_logger
from marsool_core.security.principal import Role
from marsool_user.service import UserService

logger = get_logger(__name__)


def build_router(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    idempotency: IdempotentConsumption,
    consumer_group: str,
    user_service: UserService,
    metrics: object | None = None,
) -> EventRouter:
    """Wire the user service's event handlers."""
    router = EventRouter(consumer_group=consumer_group, metrics=metrics)

    async def on_user_registered(session: AsyncSession, envelope: EventEnvelope) -> None:
        """Provision the profile for a newly registered account."""
        user_id = envelope.data.get("user_id")
        phone = envelope.data.get("phone")
        if not user_id or not phone:
            logger.warning("user_registered_event_incomplete", event_id=envelope.event_id)
            return
        await user_service.reconcile_registration(
            session,
            user_id=str(user_id),
            phone=str(phone),
            role=Role(envelope.data.get("role", Role.CUSTOMER.value)),
        )

    router.on(
        EventType.USER_REGISTERED,
        _transactional(session_factory, idempotency, on_user_registered),
    )
    return router


def _transactional(
    session_factory: async_sessionmaker[AsyncSession],
    idempotency: IdempotentConsumption,
    handler: Callable[[AsyncSession, EventEnvelope], Awaitable[None]],
) -> Callable[[EventEnvelope], Awaitable[None]]:
    """Run ``handler`` and its idempotency claim in one transaction."""

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
