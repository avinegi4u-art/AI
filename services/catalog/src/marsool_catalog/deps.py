"""FastAPI dependencies for the catalog service.

No ``from __future__ import annotations``: FastAPI must resolve the ``Annotated``
dependency aliases below at import time.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from marsool_catalog.config import get_catalog_settings
from marsool_catalog.runtime import CatalogRuntime
from marsool_catalog.service import CatalogService
from marsool_core.http.deps import AuthGuards
from marsool_core.security.principal import Role

guards = AuthGuards(get_catalog_settings())


def get_runtime(request: Request) -> CatalogRuntime:
    runtime: CatalogRuntime = request.app.state.runtime
    return runtime


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped transactional session."""
    async with get_runtime(request).database.transaction() as session:
        yield session


def get_catalog_service(request: Request) -> CatalogService:
    return get_runtime(request).catalog_service


SessionDep = Annotated[AsyncSession, Depends(get_session)]
CatalogServiceDep = Annotated[CatalogService, Depends(get_catalog_service)]
# Menu browsing is public; basket validation is not, since it is part of checkout.
OptionalPrincipal = guards.optional_principal
CurrentPrincipal = guards.principal
MerchantStaffOnly = guards.roles(Role.MERCHANT)
