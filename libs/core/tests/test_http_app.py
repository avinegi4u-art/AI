"""App factory: error envelopes, health probes, metrics, auth guards, pagination.

The fixture app's router is defined at module level, mirroring how real services declare
routers: ``from __future__ import annotations`` turns annotations into strings, and
FastAPI can only resolve dependency annotations that live in the module namespace.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from marsool_core.config import Environment, ServiceSettings
from marsool_core.context import CORRELATION_ID_HEADER
from marsool_core.errors import ConflictError, NotFoundError, RateLimitedError
from marsool_core.http import AuthGuards, Page, create_app
from marsool_core.http.deps import Pagination
from marsool_core.http.pagination import decode_cursor, encode_cursor
from marsool_core.security.principal import Principal, Role
from marsool_core.testing import TEST_JWT_SECRET, bearer_header, make_principal

SETTINGS = ServiceSettings(
    service_name="marsool-test",
    environment=Environment.TEST,
    jwt_secret=TEST_JWT_SECRET,
    log_level="WARNING",
    log_format="console",
)
GUARDS = AuthGuards(SETTINGS)

CurrentPrincipal = GUARDS.principal
OptionalPrincipal = GUARDS.optional_principal
AdminOnly = GUARDS.roles(Role.ADMIN)
CourierOnly = GUARDS.roles(Role.COURIER)
CatalogWriter = GUARDS.scopes("catalog:write")

router = APIRouter()


class _Item(BaseModel):
    id: str


@router.get("/boom/not-found")
async def boom_not_found() -> None:
    raise NotFoundError("Merchant not found", details={"merchant_id": "mch_1"})


@router.get("/boom/conflict")
async def boom_conflict() -> None:
    raise ConflictError("Order already cancelled")


@router.get("/boom/rate-limited")
async def boom_rate_limited() -> None:
    raise RateLimitedError("Slow down", retry_after_seconds=30)


@router.get("/boom/unexpected")
async def boom_unexpected() -> None:
    raise RuntimeError("kaboom")


@router.get("/echo/{item_id}")
async def echo(item_id: str, quantity: int) -> dict[str, object]:
    return {"item_id": item_id, "quantity": quantity}


@router.get("/me")
async def me(principal: CurrentPrincipal) -> dict[str, str]:
    return {"id": principal.id, "role": principal.role.value}


@router.get("/maybe-me")
async def maybe_me(principal: OptionalPrincipal) -> dict[str, str | None]:
    return {"id": principal.id if principal else None}


@router.get("/admin-only")
async def admin_only(principal: AdminOnly) -> dict[str, str]:
    return {"id": principal.id}


@router.get("/courier-only")
async def courier_only(principal: CourierOnly) -> dict[str, str]:
    return {"id": principal.id}


@router.get("/catalog-write")
async def catalog_write(principal: CatalogWriter) -> dict[str, str]:
    return {"id": principal.id}


@router.get("/items", response_model=Page[_Item])
async def list_items(page: Pagination) -> Page[_Item]:
    items = [_Item(id=f"itm_{index}") for index in range(page.limit)]
    return Page.of(items, limit=page.limit, next_cursor=encode_cursor({"after": items[-1].id}))


async def _ok_check() -> str | None:
    return None


async def _bad_check() -> str | None:
    return "unreachable"


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    service = create_app(
        SETTINGS,
        title="Test Service",
        description="Fixture app for core HTTP tests.",
        routers=[router],
        readiness_checks={"always_ok": _ok_check, "always_bad": _bad_check},
    )
    transport = ASGITransport(app=service.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def test_domain_error_uses_the_platform_envelope(client: AsyncClient) -> None:
    response = await client.get("/boom/not-found")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"] == "Merchant not found"
    assert body["error"]["details"] == {"merchant_id": "mch_1"}
    assert body["error"]["correlation_id"] == response.headers[CORRELATION_ID_HEADER]


async def test_conflict_maps_to_409(client: AsyncClient) -> None:
    response = await client.get("/boom/conflict")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_rate_limited_sets_retry_after(client: AsyncClient) -> None:
    response = await client.get("/boom/rate-limited")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"
    assert response.json()["error"]["details"]["retry_after_seconds"] == 30


async def test_unexpected_errors_are_wrapped_not_leaked(client: AsyncClient) -> None:
    response = await client.get("/boom/unexpected")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


async def test_validation_errors_report_field_paths(client: AsyncClient) -> None:
    response = await client.get("/echo/itm_1", params={"quantity": "not-a-number"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["field_errors"][0]["field"] == "quantity"


async def test_unknown_route_uses_the_envelope(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_correlation_id_is_echoed_when_supplied(client: AsyncClient) -> None:
    response = await client.get("/health", headers={CORRELATION_ID_HEADER: "corr_from_gateway"})
    assert response.headers[CORRELATION_ID_HEADER] == "corr_from_gateway"


async def test_correlation_id_is_generated_when_absent(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers[CORRELATION_ID_HEADER]
    assert response.headers["X-Request-Id"]


async def test_security_headers_present(client: AsyncClient) -> None:
    headers = (await client.get("/health")).headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"


async def test_liveness_ignores_dependencies(client: AsyncClient) -> None:
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_readiness_reports_failing_dependency(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"] == {"always_ok": "ok", "always_bad": "unreachable"}


async def test_readiness_checks_registered_after_construction_are_used() -> None:
    # Services register their dependency checks after ``create_app`` returns, because the
    # checks need resources that only exist once the lifespan has run. An earlier version
    # dropped them: an empty dict is falsy, so the health router substituted a fresh one.
    service = create_app(SETTINGS, title="t", description="d")
    service.readiness_checks["late_dependency"] = _bad_check

    async with AsyncClient(
        transport=ASGITransport(app=service.app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"] == {"late_dependency": "unreachable"}


async def test_metrics_endpoint_exposes_request_counters(client: AsyncClient) -> None:
    await client.get("/boom/conflict")
    body = (await client.get("/metrics")).text
    assert "http_requests_total" in body
    assert 'service="marsool-test"' in body


async def test_authenticated_endpoint_rejects_missing_token(client: AsyncClient) -> None:
    response = await client.get("/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_credentials"


async def test_authenticated_endpoint_rejects_garbage_token(client: AsyncClient) -> None:
    response = await client.get("/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_invalid"


async def test_authenticated_endpoint_accepts_valid_token(client: AsyncClient) -> None:
    principal = make_principal(role=Role.CUSTOMER)
    response = await client.get("/me", headers=bearer_header(principal))
    assert response.status_code == 200
    assert response.json() == {"id": principal.id, "role": "customer"}


async def test_optional_authentication_allows_anonymous(client: AsyncClient) -> None:
    assert (await client.get("/maybe-me")).json() == {"id": None}
    principal = make_principal()
    assert (await client.get("/maybe-me", headers=bearer_header(principal))).json() == {
        "id": principal.id
    }


async def test_role_guard_blocks_wrong_role(client: AsyncClient) -> None:
    response = await client.get("/courier-only", headers=bearer_header(make_principal()))
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "permission_denied"
    assert body["error"]["details"] == {
        "required_roles": ["courier"],
        "actual_role": "customer",
    }


async def test_admin_passes_any_role_guard(client: AsyncClient) -> None:
    admin = make_principal(role=Role.ADMIN)
    assert (await client.get("/courier-only", headers=bearer_header(admin))).status_code == 200
    assert (await client.get("/admin-only", headers=bearer_header(admin))).status_code == 200


async def test_scope_guard_enforces_missing_scopes(client: AsyncClient) -> None:
    agent = Principal(id="svc_1", role=Role.SERVICE, scopes=frozenset({"catalog:read"}))
    response = await client.get("/catalog-write", headers=bearer_header(agent))
    assert response.status_code == 403
    assert response.json()["error"]["details"]["missing_scopes"] == ["catalog:write"]


async def test_scope_guard_allows_scoped_agent(client: AsyncClient) -> None:
    agent = Principal(id="svc_1", role=Role.SERVICE, scopes=frozenset({"catalog:write"}))
    assert (await client.get("/catalog-write", headers=bearer_header(agent))).status_code == 200


async def test_dependency_override_replaces_authentication() -> None:
    service = create_app(SETTINGS, title="t", description="d", routers=[router])
    stub = make_principal(user_id="usr_stub")
    service.app.dependency_overrides[GUARDS.verifier] = lambda: stub

    async with AsyncClient(
        transport=ASGITransport(app=service.app), base_url="http://testserver"
    ) as client:
        assert (await client.get("/me")).json()["id"] == "usr_stub"


async def test_pagination_defaults_and_bounds(client: AsyncClient) -> None:
    default_page = (await client.get("/items")).json()
    assert default_page["meta"]["limit"] == 20
    assert len(default_page["items"]) == 20
    assert default_page["meta"]["has_more"] is True
    assert decode_cursor(default_page["meta"]["next_cursor"]) == {"after": "itm_19"}

    assert (await client.get("/items", params={"limit": 0})).status_code == 422
    assert (await client.get("/items", params={"limit": 101})).status_code == 422


async def test_openapi_documents_error_envelope_and_clean_operation_ids(
    client: AsyncClient,
) -> None:
    schema = (await client.get("/openapi.json")).json()
    operation = schema["paths"]["/me"]["get"]
    assert operation["operationId"] == "me"
    unauthorized = operation["responses"]["401"]["content"]["application/json"]["schema"]
    assert "ErrorEnvelope" in unauthorized["$ref"]


def test_duplicate_route_names_are_rejected() -> None:
    duplicate_router = APIRouter()

    @duplicate_router.get("/a", name="same")
    async def route_a() -> dict[str, str]:
        return {}

    @duplicate_router.get("/b", name="same")
    async def route_b() -> dict[str, str]:
        return {}

    with pytest.raises(ValueError, match="duplicate route name"):
        create_app(SETTINGS, title="t", description="d", routers=[duplicate_router])


def test_docs_hidden_in_production() -> None:
    production = SETTINGS.model_copy(update={"environment": Environment.PRODUCTION})
    service = create_app(production, title="t", description="d")
    assert service.app.docs_url is None


def test_cors_origins_accept_comma_separated_string() -> None:
    parsed = ServiceSettings(cors_allow_origins="https://a.example,https://b.example")  # type: ignore[arg-type]
    assert parsed.cors_allow_origins == ["https://a.example", "https://b.example"]


def test_sync_dsn_uses_psycopg_driver() -> None:
    assert SETTINGS.sync_database_dsn.startswith("postgresql+psycopg2://")
