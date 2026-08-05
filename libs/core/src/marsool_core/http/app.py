"""FastAPI application factory.

Every service builds its app through :func:`create_app` so that logging, error
envelopes, correlation IDs, security headers, metrics and health probes are identical
across the platform and cannot drift service by service.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from marsool_core.config import Environment, ServiceSettings
from marsool_core.http.errors import install_exception_handlers
from marsool_core.http.health import ReadinessCheck, build_health_router
from marsool_core.http.middleware import (
    AccessLogMiddleware,
    CorrelationIdMiddleware,
    SecurityHeadersMiddleware,
)
from marsool_core.http.schemas import ErrorEnvelope
from marsool_core.logging import configure_logging, get_logger
from marsool_core.observability import MetricsMiddleware, ServiceMetrics, metrics_endpoint

logger = get_logger(__name__)

#: Documented on every route so generated clients handle failures uniformly.
COMMON_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorEnvelope, "description": "Malformed request"},
    401: {"model": ErrorEnvelope, "description": "Missing or invalid credentials"},
    403: {"model": ErrorEnvelope, "description": "Caller lacks permission"},
    404: {"model": ErrorEnvelope, "description": "Resource not found"},
    422: {"model": ErrorEnvelope, "description": "Request validation failed"},
    429: {"model": ErrorEnvelope, "description": "Rate limit exceeded"},
    500: {"model": ErrorEnvelope, "description": "Unexpected internal error"},
}

LifespanHook = Callable[[FastAPI], AsyncIterator[None]]


@dataclass(slots=True)
class ServiceApp:
    """A configured service application and its observability handles."""

    app: FastAPI
    settings: ServiceSettings
    metrics: ServiceMetrics
    readiness_checks: dict[str, ReadinessCheck] = field(default_factory=dict)


def _operation_id_from_route_name(route: APIRoute) -> str:
    """Use the route's function name as its OpenAPI operation ID.

    FastAPI's default operation IDs embed the path and method, producing generated
    client methods like ``get_merchant_menu_merchants__merchant_id__menu_get``.
    """
    return route.name


def _assert_unique_route_names(routers: list[Any]) -> None:
    """Reject duplicate route names before they become duplicate operation IDs.

    Duplicate operation IDs do not fail OpenAPI generation — they silently produce a
    generated client where one endpoint overwrites another.
    """
    seen: set[str] = set()
    for router in routers:
        for route in getattr(router, "routes", []):
            if not isinstance(route, APIRoute):
                continue
            if route.name in seen:
                raise ValueError(
                    f"duplicate route name {route.name!r}; operation IDs must be unique"
                )
            seen.add(route.name)


def create_app(
    settings: ServiceSettings,
    *,
    title: str,
    description: str,
    routers: list[Any] | None = None,
    readiness_checks: dict[str, ReadinessCheck] | None = None,
    lifespan_hook: LifespanHook | None = None,
    tags_metadata: list[dict[str, Any]] | None = None,
) -> ServiceApp:
    """Build a fully configured FastAPI application.

    Args:
        settings: Service settings.
        title: Human-readable API title used in OpenAPI.
        description: Markdown description used in OpenAPI.
        routers: Routers to include.
        readiness_checks: Named dependency checks for ``/health/ready``.
        lifespan_hook: Async generator for startup/shutdown resource management.
        tags_metadata: OpenAPI tag descriptions.
    """
    configure_logging(
        service_name=settings.service_name,
        level=settings.log_level,
        log_format=settings.log_format,
    )
    metrics = ServiceMetrics(settings.service_name)
    # Shared by reference with the health router and the returned ServiceApp, so a service
    # can register dependency checks after construction (they need lifespan resources).
    checks: dict[str, ReadinessCheck] = dict(readiness_checks) if readiness_checks else {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "service_starting",
            service=settings.service_name,
            version=settings.version,
            environment=settings.environment.value,
        )
        if lifespan_hook is None:
            yield
        else:
            async for _ in lifespan_hook(app):
                yield
        logger.info("service_stopped", service=settings.service_name)

    _assert_unique_route_names(routers or [])

    app = FastAPI(
        title=title,
        description=description,
        version=settings.version,
        root_path=settings.root_path,
        lifespan=lifespan,
        openapi_tags=tags_metadata,
        generate_unique_id_function=_operation_id_from_route_name,
        docs_url="/docs" if settings.is_debug else None,
        redoc_url=None,
        openapi_url="/openapi.json",
        responses=COMMON_ERROR_RESPONSES,
        # Hide null optional fields so clients see a stable, compact payload shape.
        response_model_exclude_none=True,
    )
    app.state.settings = settings
    app.state.metrics = metrics
    app.state.readiness_checks = checks

    # Middleware is applied bottom-up: the last one added runs first. Correlation IDs
    # must be bound before anything logs, so it is added last.
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    app.add_middleware(
        SecurityHeadersMiddleware, hsts=settings.environment is Environment.PRODUCTION
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-Id", "Idempotency-Key"],
        expose_headers=["X-Correlation-Id", "X-Request-Id", "X-Response-Time-Ms"],
        max_age=600,
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    install_exception_handlers(app, include_debug_detail=settings.is_debug)

    # Platform routes are registered before service routers so that a service with a
    # catch-all route (the gateway) cannot shadow health probes or the metrics endpoint.
    app.include_router(build_health_router(settings, readiness_checks=checks))
    if settings.metrics_enabled:
        app.add_route(
            "/metrics",
            metrics_endpoint(metrics),
            methods=["GET"],
            include_in_schema=False,
        )

    for router in routers or []:
        app.include_router(router)

    return ServiceApp(app=app, settings=settings, metrics=metrics, readiness_checks=checks)
