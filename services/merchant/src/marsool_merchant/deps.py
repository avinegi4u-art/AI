"""FastAPI dependencies for the merchant service.

No ``from __future__ import annotations``: FastAPI must resolve the ``Annotated``
dependency aliases below at import time.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from marsool_core.http.deps import AuthGuards
from marsool_core.security.principal import Role
from marsool_merchant.config import get_merchant_settings
from marsool_merchant.runtime import MerchantRuntime
from marsool_merchant.service import MerchantService

guards = AuthGuards(get_merchant_settings())


def get_runtime(request: Request) -> MerchantRuntime:
    runtime: MerchantRuntime = request.app.state.runtime
    return runtime


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped transactional session."""
    async with get_runtime(request).database.transaction() as session:
        yield session


def get_merchant_service(request: Request) -> MerchantService:
    return get_runtime(request).merchant_service


SessionDep = Annotated[AsyncSession, Depends(get_session)]
MerchantServiceDep = Annotated[MerchantService, Depends(get_merchant_service)]
# Browsing is public: a customer must be able to see what is available before signing in.
OptionalPrincipal = guards.optional_principal
MerchantStaffOnly = guards.roles(Role.MERCHANT)
