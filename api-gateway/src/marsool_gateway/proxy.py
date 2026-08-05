"""Upstream forwarding.

One shared ``httpx.AsyncClient`` with a bounded connection pool: creating a client per
request would defeat connection reuse and exhaust ephemeral ports under load.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

import httpx
from starlette.responses import Response

from marsool_core.context import CORRELATION_ID_HEADER, current_context
from marsool_core.errors import DependencyUnavailableError
from marsool_core.logging import get_logger
from marsool_core.security.principal import Principal

logger = get_logger(__name__)

# Hop-by-hop headers must not be forwarded: they describe this connection, not the request.
_HOP_BY_HOP_HEADERS: Final = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)

#: Headers the gateway sets itself; an inbound copy is dropped so a client cannot spoof
#: its own identity by sending them.
GATEWAY_PRINCIPAL_HEADERS: Final = (
    "x-marsool-user-id",
    "x-marsool-user-role",
    "x-marsool-session-id",
)


def build_upstream_client(
    *,
    connect_timeout: float,
    read_timeout: float,
    max_connections: int,
) -> httpx.AsyncClient:
    """Create the shared upstream HTTP client."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
        limits=httpx.Limits(
            max_connections=max_connections, max_keepalive_connections=max_connections // 2
        ),
        follow_redirects=False,
    )


def forwardable_request_headers(
    inbound: Mapping[str, str], *, principal: Principal | None
) -> dict[str, str]:
    """Build the header set to send upstream.

    Three classes of inbound header are dropped: hop-by-hop headers (they describe this
    connection, not the request), the gateway's own identity headers (so a client cannot
    claim to be someone else), and the correlation header (re-added below from the ambient
    context, which already reflects the inbound value — copying it as well would send the
    header twice and produce a comma-joined value downstream).
    """
    dropped = _HOP_BY_HOP_HEADERS | set(GATEWAY_PRINCIPAL_HEADERS) | {CORRELATION_ID_HEADER.lower()}
    headers = {
        name: value for name, value in inbound.items() if name.lower() not in dropped
    }
    correlation_id = current_context().correlation_id
    if correlation_id:
        headers[CORRELATION_ID_HEADER] = correlation_id
    if principal is not None:
        # Advisory only. Services verify the bearer token themselves; these headers exist so
        # logs and traces carry the identity without every service re-parsing the JWT first.
        headers["X-Marsool-User-Id"] = principal.id
        headers["X-Marsool-User-Role"] = principal.role.value
        if principal.session_id:
            headers["X-Marsool-Session-Id"] = principal.session_id
    return headers


def forwardable_response_headers(inbound: Mapping[str, str]) -> dict[str, str]:
    """Build the header set to return to the client."""
    return {
        name: value
        for name, value in inbound.items()
        if name.lower() not in _HOP_BY_HOP_HEADERS
    }


async def forward(
    client: httpx.AsyncClient,
    *,
    method: str,
    upstream_url: str,
    headers: dict[str, str],
    query_string: str,
    body: bytes,
) -> Response:
    """Forward one request upstream and adapt the reply.

    Upstream failures are translated into a 503 with the platform error envelope rather
    than leaking an httpx exception as a 500.
    """
    try:
        upstream_response = await client.request(
            method,
            upstream_url,
            headers=headers,
            params=httpx.QueryParams(query_string),
            content=body or None,
        )
    except httpx.TimeoutException as exc:
        logger.warning("upstream_timeout", upstream_url=upstream_url, error=str(exc))
        raise DependencyUnavailableError(
            "The upstream service did not respond in time",
            code="upstream_timeout",
            details={"upstream": upstream_url},
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning("upstream_unreachable", upstream_url=upstream_url, error=str(exc))
        raise DependencyUnavailableError(
            "The upstream service is unavailable",
            code="upstream_unavailable",
            details={"upstream": upstream_url},
        ) from exc

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=forwardable_response_headers(upstream_response.headers),
        media_type=upstream_response.headers.get("content-type"),
    )
