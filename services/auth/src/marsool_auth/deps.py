"""FastAPI dependencies for the auth service.

No ``from __future__ import annotations`` here: FastAPI must be able to resolve the
``Annotated`` dependency aliases below at import time.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from marsool_auth.config import get_auth_settings
from marsool_auth.runtime import AuthRuntime
from marsool_auth.service import AuthService, ClientContext
from marsool_core.http.deps import AuthGuards

guards = AuthGuards(get_auth_settings())


def get_runtime(request: Request) -> AuthRuntime:
    """Return the runtime bound to this application."""
    runtime: AuthRuntime = request.app.state.runtime
    return runtime


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped transactional session.

    The transaction commits when the handler returns and rolls back on any exception, so
    a failed request cannot leave a half-applied state change or a stray outbox row.
    """
    async with get_runtime(request).database.transaction() as session:
        yield session


def get_auth_service(request: Request) -> AuthService:
    return get_runtime(request).auth_service


def get_client_context(request: Request) -> ClientContext:
    """Capture client attribution for session records.

    ``X-Forwarded-For`` is trusted only because the gateway is the sole ingress and
    rewrites it; the left-most entry is the original client.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    ip_address = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )
    user_agent = request.headers.get("User-Agent")
    return ClientContext(
        user_agent=user_agent[:200] if user_agent else None,
        ip_address=ip_address,
    )


SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
ClientContextDep = Annotated[ClientContext, Depends(get_client_context)]
CurrentPrincipal = guards.principal
