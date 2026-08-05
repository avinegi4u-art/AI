"""Gateway settings and the upstream routing table."""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Final

from pydantic import Field

from marsool_core.config import ServiceSettings


@dataclass(frozen=True, slots=True)
class Route:
    """A path prefix mapped to an upstream service.

    Attributes:
        prefix: Path prefix that selects this route, e.g. ``/auth``.
        upstream: Name of the upstream, resolved through ``GatewaySettings.upstreams``.
        public: When True, the route is reachable without a bearer token. Public routes
            still pass through rate limiting, keyed by client IP instead of user id.
        methods: Methods the route accepts; empty means all.
    """

    prefix: str
    upstream: str
    public: bool = False
    methods: frozenset[str] = frozenset()

    def matches(self, path: str, method: str) -> bool:
        if not (path == self.prefix or path.startswith(f"{self.prefix}/")):
            return False
        return not self.methods or method.upper() in self.methods


#: Paths that are public even under an otherwise-authenticated prefix. Ordered patterns:
#: the first match wins, so a specific public exception can precede a protected prefix.
PUBLIC_PATH_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    # Browsing merchants and menus must work before a customer signs in.
    re.compile(r"^/merchants(/[^/]+)?$"),
    re.compile(r"^/merchants/[^/]+/menu$"),
    re.compile(r"^/merchants/[^/]+/hours$"),
    re.compile(r"^/merchants/[^/]+/serviceability$"),
)

#: Longest prefix first, so ``/merchants/{id}/menu`` reaches catalog rather than merchant.
ROUTES: Final[tuple[Route, ...]] = (
    Route(prefix="/auth", upstream="auth", public=True),
    Route(prefix="/users", upstream="user"),
    Route(prefix="/catalog", upstream="catalog"),
    Route(prefix="/merchants", upstream="merchant"),
)

#: Path suffixes that belong to catalog even though they sit under ``/merchants``.
CATALOG_MERCHANT_SUFFIXES: Final[tuple[str, ...]] = ("/menu", "/basket/validate")


class GatewaySettings(ServiceSettings):
    """Settings for the API gateway."""

    service_name: str = "marsool-gateway"
    port: int = 8000

    auth_service_url: str = "http://localhost:8001"
    user_service_url: str = "http://localhost:8002"
    merchant_service_url: str = "http://localhost:8003"
    catalog_service_url: str = "http://localhost:8004"

    # Upstream timeouts. Deliberately short: a client waiting 30 seconds for a menu is a
    # worse outcome than a fast 504 it can retry.
    upstream_connect_timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    upstream_read_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    upstream_max_connections: int = Field(default=200, ge=1)

    # Anonymous callers share an IP-keyed bucket, so the limit is lower than the
    # per-user limit inherited from ServiceSettings.
    anonymous_rate_limit_requests: int = Field(default=60, ge=1)

    @property
    def upstreams(self) -> dict[str, str]:
        """Upstream base URLs by name."""
        return {
            "auth": self.auth_service_url.rstrip("/"),
            "user": self.user_service_url.rstrip("/"),
            "merchant": self.merchant_service_url.rstrip("/"),
            "catalog": self.catalog_service_url.rstrip("/"),
        }


@functools.cache
def get_gateway_settings() -> GatewaySettings:
    """Return cached gateway settings."""
    return GatewaySettings()


def resolve_route(path: str, method: str) -> Route | None:
    """Return the route that owns ``path``, or None when nothing matches.

    ``/merchants/{id}/menu`` and ``/merchants/{id}/basket/validate`` are special-cased to
    the catalog service: they read naturally as merchant sub-resources for clients, but the
    data belongs to the catalog bounded context.
    """
    if path.startswith("/merchants/") and any(
        path.endswith(suffix) for suffix in CATALOG_MERCHANT_SUFFIXES
    ):
        return Route(prefix="/merchants", upstream="catalog", public=path.endswith("/menu"))
    for route in ROUTES:
        if route.matches(path, method):
            return route
    return None


def is_public(path: str, method: str) -> bool:
    """Whether ``path`` may be called without authentication."""
    route = resolve_route(path, method)
    if route is not None and route.public:
        return True
    if method.upper() not in {"GET", "HEAD"}:
        # Only reads are ever public: a write always needs an identity to attribute it to.
        return False
    return any(pattern.match(path) for pattern in PUBLIC_PATH_PATTERNS)
