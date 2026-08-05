"""Reusable FastAPI dependencies: authentication, authorization, pagination.

This module deliberately does *not* use ``from __future__ import annotations``. The
guard factories below build closures whose parameter annotations reference runtime
values (``self._verifier``); as strings those annotations cannot be evaluated by
FastAPI's dependency resolver, which would silently downgrade the principal parameter
to a query parameter.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from marsool_core.config import ServiceSettings
from marsool_core.context import set_actor_id
from marsool_core.errors import AuthenticationError, PermissionDeniedError
from marsool_core.security.jwt import decode_access_token
from marsool_core.security.principal import Principal, Role

# ``auto_error=False`` so a missing header produces our own error envelope rather than
# FastAPI's default ``{"detail": ...}`` shape.
_bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


def build_principal_dependency(
    settings: ServiceSettings,
) -> Callable[[Request, HTTPAuthorizationCredentials | None], Principal]:
    """Build a dependency that verifies the bearer token and returns the principal.

    Each service verifies the JWT itself rather than trusting gateway-injected headers:
    a compromised or bypassed gateway must not be able to impersonate a user.
    """

    def _dependency(request: Request, credentials: BearerCredentials) -> Principal:
        if credentials is None or not credentials.credentials:
            raise AuthenticationError("Missing bearer token", code="missing_credentials")
        claims = decode_access_token(
            credentials.credentials,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            algorithm=settings.jwt_algorithm,
            secret=settings.jwt_secret,
            public_key=settings.jwt_public_key,
        )
        principal = claims.to_principal()
        request.state.principal = principal
        set_actor_id(principal.id)
        return principal

    return _dependency


class AuthGuards:
    """Auth dependencies bound to one service's settings.

    A service instantiates this once and exposes the resulting annotated types:

        guards = AuthGuards(settings)
        CurrentPrincipal = guards.principal
        CustomerOnly = guards.roles(Role.CUSTOMER)

        @router.get("/users/me")
        async def read_me(principal: CurrentPrincipal) -> UserResponse: ...

    Tests override authentication with ``app.dependency_overrides[guards.verifier]``.
    """

    def __init__(self, settings: ServiceSettings) -> None:
        self._settings = settings
        self._verifier = build_principal_dependency(settings)

    @property
    def verifier(self) -> Callable[..., Principal]:
        """The underlying token verification callable (the override key for tests)."""
        return self._verifier

    @property
    def principal(self) -> Any:
        """Annotated type requiring any authenticated principal.

        Returns an ``Annotated[...]`` alias rather than a value, so the declared return
        type is ``Any``; the alias is meant to be used as a parameter annotation.
        """
        return Annotated[Principal, Depends(self._verifier)]

    @property
    def optional_principal(self) -> Any:
        """Annotated type allowing anonymous access; resolves to None when unauthenticated.

        Used by endpoints that serve both public browsing and personalised results.
        """

        def _optional(request: Request, credentials: BearerCredentials) -> Principal | None:
            if credentials is None or not credentials.credentials:
                return None
            return self._verifier(request, credentials)

        return Annotated[Principal | None, Depends(_optional)]

    def roles(self, *roles: Role) -> Any:
        """Annotated type requiring one of ``roles``.

        Admins always pass, so admin tooling need not be listed on every endpoint.
        """
        allowed = frozenset(roles) | {Role.ADMIN}

        def _guard(principal: Annotated[Principal, Depends(self._verifier)]) -> Principal:
            if principal.role not in allowed:
                raise PermissionDeniedError(
                    "This action requires a different role",
                    details={
                        "required_roles": sorted(role.value for role in roles),
                        "actual_role": principal.role.value,
                    },
                )
            return principal

        return Annotated[Principal, Depends(_guard)]

    def scopes(self, *scopes: str) -> Any:
        """Annotated type requiring all of ``scopes``.

        Scopes gate machine-to-machine callers (internal workers, AI agents) so an agent
        token can be limited to e.g. ``catalog:read`` without granting write access.
        """

        def _guard(principal: Annotated[Principal, Depends(self._verifier)]) -> Principal:
            missing = [scope for scope in scopes if not principal.has_scope(scope)]
            if missing:
                raise PermissionDeniedError(
                    "Token is missing required scopes",
                    details={"missing_scopes": missing},
                )
            return principal

        return Annotated[Principal, Depends(_guard)]


@dataclass(frozen=True, slots=True)
class PaginationParams:
    """Validated pagination query parameters."""

    limit: int
    cursor: str | None


def pagination_params(
    limit: Annotated[int, Query(ge=1, le=100, description="Maximum items to return")] = 20,
    cursor: Annotated[str | None, Query(description="Opaque cursor from a previous page")] = None,
) -> PaginationParams:
    """Standard pagination dependency shared by all list endpoints."""
    return PaginationParams(limit=limit, cursor=cursor)


Pagination = Annotated[PaginationParams, Depends(pagination_params)]

__all__ = [
    "AuthGuards",
    "BearerCredentials",
    "Pagination",
    "PaginationParams",
    "build_principal_dependency",
    "pagination_params",
]
