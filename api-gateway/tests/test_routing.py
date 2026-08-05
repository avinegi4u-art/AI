"""Gateway routing, authentication, header handling and rate limiting."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import pytest
from httpx import AsyncClient

from marsool_core.context import CORRELATION_ID_HEADER, bind_context
from marsool_core.security.principal import Principal, Role
from marsool_core.testing import bearer_header, make_principal
from marsool_gateway.config import is_public, resolve_route
from marsool_gateway.proxy import forwardable_request_headers

from .conftest import UpstreamRecorder

ClientFactory = Callable[..., Awaitable[AsyncClient]]


# --------------------------------------------------------------------------- route table


@pytest.mark.parametrize(
    ("path", "method", "expected_upstream"),
    [
        ("/auth/login", "POST", "auth"),
        ("/auth/otp", "POST", "auth"),
        ("/users/me", "GET", "user"),
        ("/users/me/addresses", "POST", "user"),
        ("/merchants", "GET", "merchant"),
        ("/merchants/mch_1", "GET", "merchant"),
        ("/merchants/mch_1/hours", "GET", "merchant"),
        ("/merchants/mch_1/serviceability", "GET", "merchant"),
        ("/catalog/items/itm_1", "PUT", "catalog"),
        # Reads naturally as a merchant sub-resource, but the data lives in catalog.
        ("/merchants/mch_1/menu", "GET", "catalog"),
        ("/merchants/mch_1/basket/validate", "POST", "catalog"),
    ],
)
def test_routes_resolve_to_the_owning_service(
    path: str, method: str, expected_upstream: str
) -> None:
    route = resolve_route(path, method)
    assert route is not None
    assert route.upstream == expected_upstream


def test_unknown_paths_do_not_resolve() -> None:
    assert resolve_route("/orders", "POST") is None
    assert resolve_route("/", "GET") is None


@pytest.mark.parametrize(
    "path",
    ["/auth/login", "/merchants", "/merchants/mch_1", "/merchants/mch_1/menu"],
)
def test_public_read_paths(path: str) -> None:
    assert is_public(path, "GET") is True


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/users/me", "GET"),
        ("/catalog/items/itm_1", "PUT"),
        ("/merchants/mch_1/basket/validate", "POST"),
        # A write is never public, even on an otherwise-public prefix.
        ("/merchants/mch_1", "PUT"),
        ("/merchants/mch_1/hours", "PUT"),
    ],
)
def test_protected_paths(path: str, method: str) -> None:
    assert is_public(path, method) is False


# ------------------------------------------------------------------------------ proxying


async def test_public_request_is_forwarded_without_credentials(
    client: AsyncClient, upstream: UpstreamRecorder
) -> None:
    response = await client.get("/merchants", params={"lat": "24.49", "lng": "54.39"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    forwarded = upstream.last
    assert str(forwarded.url) == "http://merchant.internal/merchants?lat=24.49&lng=54.39"


async def test_query_parameters_are_preserved(
    client: AsyncClient, upstream: UpstreamRecorder
) -> None:
    await client.get("/merchants", params=[("cuisine", "japanese"), ("cuisine", "pizza")])
    # Repeated parameters must survive: the cuisine filter depends on them.
    assert upstream.last.url.params.get_list("cuisine") == ["japanese", "pizza"]


async def test_request_body_and_method_are_preserved(
    client: AsyncClient, upstream: UpstreamRecorder
) -> None:
    await client.post("/auth/login", json={"phone": "+971501234567", "otp": "123456"})

    forwarded = upstream.last
    assert forwarded.method == "POST"
    assert str(forwarded.url) == "http://auth.internal/auth/login"
    assert forwarded.read() == b'{"phone":"+971501234567","otp":"123456"}'


async def test_upstream_status_and_body_are_passed_through(
    client: AsyncClient, upstream: UpstreamRecorder
) -> None:
    upstream.status_code = 201
    upstream.payload = {"challenge_id": "otp_1"}

    response = await client.post("/auth/otp", json={"phone": "+971501234567"})

    assert response.status_code == 201
    assert response.json() == {"challenge_id": "otp_1"}


async def test_unknown_route_returns_the_platform_envelope(client: AsyncClient) -> None:
    response = await client.post("/orders", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "route_not_found"


# ------------------------------------------------------------------------ authentication


async def test_protected_route_requires_a_token(client: AsyncClient) -> None:
    response = await client.get("/users/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_credentials"


async def test_protected_route_rejects_a_bad_token(client: AsyncClient) -> None:
    response = await client.get("/users/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_invalid"


async def test_unauthenticated_request_is_not_forwarded(
    client: AsyncClient, upstream: UpstreamRecorder
) -> None:
    await client.get("/users/me")
    # Rejecting at the edge keeps unauthenticated load off the services entirely.
    assert upstream.forwarded_count == 0


async def test_authenticated_request_is_forwarded_with_identity_headers(
    client: AsyncClient, upstream: UpstreamRecorder, customer: Principal
) -> None:
    response = await client.get("/users/me", headers=bearer_header(customer))

    assert response.status_code == 200
    forwarded = upstream.last
    assert forwarded.headers["x-marsool-user-id"] == customer.id
    assert forwarded.headers["x-marsool-user-role"] == "customer"
    # The bearer token travels too: each service verifies it independently, so a bypassed
    # gateway cannot mint trust.
    assert forwarded.headers["authorization"].startswith("Bearer ")


async def test_clients_cannot_spoof_identity_headers(
    client: AsyncClient, upstream: UpstreamRecorder, customer: Principal
) -> None:
    await client.get(
        "/users/me",
        headers={
            **bearer_header(customer),
            "X-Marsool-User-Id": "usr_attacker",
            "X-Marsool-User-Role": "admin",
        },
    )

    forwarded = upstream.last
    # The gateway's own values win; a client-supplied copy is dropped.
    assert forwarded.headers["x-marsool-user-id"] == customer.id
    assert forwarded.headers["x-marsool-user-role"] == "customer"


async def test_identity_headers_are_absent_for_anonymous_requests(
    client: AsyncClient, upstream: UpstreamRecorder
) -> None:
    await client.get("/merchants", params={"lat": "24.49", "lng": "54.39"})
    assert "x-marsool-user-id" not in upstream.last.headers


async def test_admin_token_is_accepted_on_protected_routes(
    client: AsyncClient, upstream: UpstreamRecorder
) -> None:
    admin = make_principal(role=Role.ADMIN)
    response = await client.put(
        "/catalog/items/itm_1", json={"price": "10.00"}, headers=bearer_header(admin)
    )
    assert response.status_code == 200
    assert upstream.last.headers["x-marsool-user-role"] == "admin"


# ----------------------------------------------------------------------------- transport


@pytest.mark.parametrize(
    "header", ["Connection", "Transfer-Encoding", "Upgrade", "Host", "Content-Length"]
)
def test_hop_by_hop_headers_are_dropped(header: str) -> None:
    # Asserted on the function rather than end-to-end, because the outbound HTTP client
    # legitimately sets its own connection headers afterwards.
    forwarded = forwardable_request_headers({header: "value", "Accept": "*/*"}, principal=None)
    assert header.lower() not in {name.lower() for name in forwarded}
    assert forwarded["Accept"] == "*/*"


def test_correlation_header_is_not_duplicated() -> None:
    with bind_context(correlation_id="corr_ambient"):
        forwarded = forwardable_request_headers(
            {"x-correlation-id": "corr_ambient"}, principal=None
        )
    matching = [name for name in forwarded if name.lower() == CORRELATION_ID_HEADER.lower()]
    # Two entries would reach the upstream as a comma-joined value and break log joins.
    assert len(matching) == 1
    assert forwarded[matching[0]] == "corr_ambient"


async def test_correlation_id_is_propagated_upstream(
    client: AsyncClient, upstream: UpstreamRecorder, auth_headers: dict[str, str]
) -> None:
    await client.get(
        "/users/me", headers={**auth_headers, CORRELATION_ID_HEADER: "corr_from_client"}
    )
    assert upstream.last.headers[CORRELATION_ID_HEADER.lower()] == "corr_from_client"


async def test_correlation_id_is_generated_when_absent(
    client: AsyncClient, upstream: UpstreamRecorder, auth_headers: dict[str, str]
) -> None:
    response = await client.get("/users/me", headers=auth_headers)
    generated = upstream.last.headers[CORRELATION_ID_HEADER.lower()]
    assert generated
    # The same id comes back to the client, so support can join both sides.
    assert response.headers[CORRELATION_ID_HEADER] == generated


async def test_upstream_timeout_becomes_a_503(
    client: AsyncClient, upstream: UpstreamRecorder, auth_headers: dict[str, str]
) -> None:
    upstream.raise_error = httpx.ReadTimeout("upstream too slow")

    response = await client.get("/users/me", headers=auth_headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_timeout"


async def test_upstream_connection_failure_becomes_a_503(
    client: AsyncClient, upstream: UpstreamRecorder, auth_headers: dict[str, str]
) -> None:
    upstream.raise_error = httpx.ConnectError("connection refused")

    response = await client.get("/users/me", headers=auth_headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_unavailable"


# -------------------------------------------------------------------------- rate limiting


async def test_authenticated_requests_are_limited_per_user(
    client_factory: ClientFactory, customer: Principal
) -> None:
    client = await client_factory(rate_limit_requests=3, rate_limit_window_seconds=60)
    headers = bearer_header(customer)

    for _ in range(3):
        assert (await client.get("/users/me", headers=headers)).status_code == 200

    limited = await client.get("/users/me", headers=headers)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1


async def test_one_users_burst_does_not_limit_another(
    client_factory: ClientFactory, customer: Principal
) -> None:
    client = await client_factory(rate_limit_requests=2, rate_limit_window_seconds=60)
    other = make_principal(role=Role.CUSTOMER)

    for _ in range(2):
        await client.get("/users/me", headers=bearer_header(customer))
    assert (await client.get("/users/me", headers=bearer_header(customer))).status_code == 429

    assert (await client.get("/users/me", headers=bearer_header(other))).status_code == 200


async def test_anonymous_traffic_has_its_own_tighter_bucket(
    client_factory: ClientFactory,
) -> None:
    client = await client_factory(
        anonymous_rate_limit_requests=2, rate_limit_window_seconds=60
    )
    params = {"lat": "24.49", "lng": "54.39"}

    for _ in range(2):
        assert (await client.get("/merchants", params=params)).status_code == 200

    limited = await client.get("/merchants", params=params)
    assert limited.status_code == 429


async def test_rate_limited_request_is_not_forwarded(
    client_factory: ClientFactory, customer: Principal, upstream: UpstreamRecorder
) -> None:
    client = await client_factory(rate_limit_requests=1, rate_limit_window_seconds=60)
    headers = bearer_header(customer)

    await client.get("/users/me", headers=headers)
    await client.get("/users/me", headers=headers)

    # Shedding at the edge is the point: the upstream never sees the excess traffic.
    assert upstream.forwarded_count == 1


async def test_rate_limiting_can_be_disabled(
    client_factory: ClientFactory, customer: Principal
) -> None:
    client = await client_factory(rate_limit_enabled=False, rate_limit_requests=1)
    headers = bearer_header(customer)

    for _ in range(5):
        assert (await client.get("/users/me", headers=headers)).status_code == 200


# --------------------------------------------------------------- platform routes survive


async def test_health_probe_is_not_proxied(client: AsyncClient, upstream: UpstreamRecorder) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json()["service"] == "marsool-gateway"
    # The catch-all route must not shadow the platform's own endpoints.
    assert upstream.forwarded_count == 0


async def test_metrics_endpoint_is_not_proxied(
    client: AsyncClient, upstream: UpstreamRecorder
) -> None:
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert upstream.forwarded_count == 0
