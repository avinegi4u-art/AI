"""Liveness and readiness probes.

Liveness answers "is the process healthy enough to keep running"; readiness answers
"can it serve traffic right now". Kubernetes must not restart a pod because a
downstream dependency blipped, so dependency checks belong to readiness only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from marsool_core.config import ServiceSettings
from marsool_core.http.schemas import HealthStatus
from marsool_core.logging import get_logger

logger = get_logger(__name__)

#: A readiness check returns None when healthy or a short failure reason.
ReadinessCheck = Callable[[], Awaitable[str | None]]

_CHECK_TIMEOUT_SECONDS = 2.0


def build_health_router(
    settings: ServiceSettings,
    *,
    readiness_checks: dict[str, ReadinessCheck] | None = None,
) -> APIRouter:
    """Build the health router exposing ``/health``, ``/health/live``, ``/health/ready``.

    The passed dict is held by reference, not copied, so a service can register checks after
    the app is built — which it must, because the checks need resources that only exist once
    the lifespan has run. Note the explicit ``is not None``: ``readiness_checks or {}`` would
    substitute a fresh dict for an empty one and silently discard every later registration.
    """
    checks = readiness_checks if readiness_checks is not None else {}
    router = APIRouter(tags=["health"])

    def _snapshot(status_value: str, results: dict[str, str]) -> HealthStatus:
        return HealthStatus(
            status=status_value,
            service=settings.service_name,
            version=settings.version,
            environment=settings.environment.value,
            checks=results,
        )

    async def _run_checks() -> tuple[bool, dict[str, str]]:
        if not checks:
            return True, {}
        async def _run(name: str, check: ReadinessCheck) -> tuple[str, str]:
            try:
                reason = await asyncio.wait_for(check(), timeout=_CHECK_TIMEOUT_SECONDS)
            except TimeoutError:
                return name, "timeout"
            except Exception as exc:  # noqa: BLE001 - a probe must never raise
                return name, f"error: {type(exc).__name__}"
            return name, reason or "ok"

        results = dict(await asyncio.gather(*(_run(name, c) for name, c in checks.items())))
        return all(value == "ok" for value in results.values()), results

    @router.get(
        "/health",
        response_model=HealthStatus,
        summary="Service health snapshot",
    )
    async def health() -> HealthStatus:
        _, results = await _run_checks()
        return _snapshot("ok", results)

    @router.get(
        "/health/live",
        response_model=HealthStatus,
        summary="Liveness probe (process is running)",
    )
    async def live() -> HealthStatus:
        return _snapshot("ok", {})

    @router.get(
        "/health/ready",
        response_model=HealthStatus,
        summary="Readiness probe (dependencies reachable)",
    )
    async def ready(response: Response) -> HealthStatus:
        healthy, results = await _run_checks()
        if not healthy:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            logger.warning("readiness_check_failed", checks=results)
        return _snapshot("ok" if healthy else "degraded", results)

    return router


def database_readiness_check(engine_provider: Callable[[], object]) -> ReadinessCheck:
    """Build a readiness check that issues ``SELECT 1`` against the service database."""

    async def _check() -> str | None:
        engine = engine_provider()
        async with engine.connect() as connection:  # type: ignore[attr-defined]
            await connection.execute(text("SELECT 1"))
        return None

    return _check


def redis_readiness_check(client_provider: Callable[[], object]) -> ReadinessCheck:
    """Build a readiness check that pings Redis."""

    async def _check() -> str | None:
        client = client_provider()
        await client.ping()  # type: ignore[attr-defined]
        return None

    return _check
