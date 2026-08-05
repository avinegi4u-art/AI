"""The gateway's catch-all proxy route."""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.responses import Response

from marsool_core.errors import AuthenticationError, NotFoundError
from marsool_core.logging import get_logger
from marsool_core.security.jwt import decode_access_token
from marsool_core.security.principal import Principal
from marsool_gateway.config import GatewaySettings, is_public, resolve_route
from marsool_gateway.proxy import forward, forwardable_request_headers
from marsool_gateway.runtime import GatewayRuntime

logger = get_logger(__name__)

router = APIRouter()

PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


def _authenticate(request: Request, settings: GatewaySettings) -> Principal | None:
    """Extract and verify the bearer token, if one was supplied."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    claims = decode_access_token(
        token,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        algorithm=settings.jwt_algorithm,
        secret=settings.jwt_secret,
        public_key=settings.jwt_public_key,
    )
    return claims.to_principal()


def _client_ip(request: Request) -> str:
    """Best-effort client IP for anonymous rate limiting."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.api_route(
    "/{path:path}",
    methods=PROXY_METHODS,
    include_in_schema=False,
    name="proxy",
)
async def proxy(request: Request, path: str) -> Response:
    """Authenticate, rate limit, and forward the request to the owning service."""
    runtime: GatewayRuntime = request.app.state.runtime
    settings = runtime.settings
    normalised_path = f"/{path}" if not path.startswith("/") else path

    route = resolve_route(normalised_path, request.method)
    if route is None:
        raise NotFoundError(
            "No route matches this path",
            code="route_not_found",
            details={"path": normalised_path},
        )

    principal = _authenticate(request, settings)
    if principal is None and not is_public(normalised_path, request.method):
        raise AuthenticationError(
            "This endpoint requires authentication", code="missing_credentials"
        )

    if settings.rate_limit_enabled:
        # An authenticated caller is limited by identity; anonymous traffic shares an
        # IP-keyed bucket so one client cannot exhaust the allowance for everyone.
        if principal is not None:
            await runtime.user_rate_limiter.enforce(f"user:{principal.id}")
        else:
            await runtime.anonymous_rate_limiter.enforce(f"ip:{_client_ip(request)}")

    upstream_base = settings.upstreams[route.upstream]
    return await forward(
        runtime.upstream_client,
        method=request.method,
        upstream_url=f"{upstream_base}{normalised_path}",
        headers=forwardable_request_headers(request.headers, principal=principal),
        query_string=request.url.query,
        body=await request.body(),
    )
