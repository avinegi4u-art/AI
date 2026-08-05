"""FastAPI dependencies for the user service.

No ``from __future__ import annotations``: FastAPI must resolve the ``Annotated``
dependency aliases below at import time.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from marsool_core.http.deps import AuthGuards
from marsool_core.security.principal import Role
from marsool_user.config import get_user_settings
from marsool_user.runtime import UserRuntime
from marsool_user.service import UserService

guards = AuthGuards(get_user_settings())


def get_runtime(request: Request) -> UserRuntime:
    runtime: UserRuntime = request.app.state.runtime
    return runtime


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped transactional session."""
    async with get_runtime(request).database.transaction() as session:
        yield session


def get_user_service(request: Request) -> UserService:
    return get_runtime(request).user_service


SessionDep = Annotated[AsyncSession, Depends(get_session)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
CurrentPrincipal = guards.principal
CourierOnly = guards.roles(Role.COURIER)
