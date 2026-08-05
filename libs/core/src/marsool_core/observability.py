"""Metrics and tracing.

Prometheus metrics are always available at ``/metrics``. OpenTelemetry tracing is
optional: if the OTel packages are not installed (local dev, unit tests) the setup call
degrades to a no-op rather than failing startup.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Final

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from marsool_core.logging import get_logger

logger = get_logger(__name__)

# Buckets tuned for API latencies: fine-grained under 1s, coarse above.
_LATENCY_BUCKETS: Final = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class ServiceMetrics:
    """Per-service Prometheus collectors.

    Each service owns its own registry so tests can create and discard metrics without
    the duplicate-registration errors that a global default registry causes.
    """

    def __init__(self, service_name: str, registry: CollectorRegistry | None = None) -> None:
        self.service_name = service_name
        self.registry = registry or CollectorRegistry()
        self.requests_total = Counter(
            "http_requests_total",
            "Total HTTP requests handled.",
            labelnames=("service", "method", "route", "status"),
            registry=self.registry,
        )
        self.request_duration = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds.",
            labelnames=("service", "method", "route"),
            buckets=_LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.events_published = Counter(
            "domain_events_published_total",
            "Domain events published to the event bus.",
            labelnames=("service", "event_type"),
            registry=self.registry,
        )
        self.events_consumed = Counter(
            "domain_events_consumed_total",
            "Domain events consumed from the event bus.",
            labelnames=("service", "event_type", "outcome"),
            registry=self.registry,
        )
        self.cache_operations = Counter(
            "cache_operations_total",
            "Cache lookups by outcome.",
            labelnames=("service", "cache", "outcome"),
            registry=self.registry,
        )

    def render(self) -> bytes:
        return generate_latest(self.registry)

    def observe_request(
        self, *, method: str, route: str, status_code: int, duration_seconds: float
    ) -> None:
        self.requests_total.labels(self.service_name, method, route, str(status_code)).inc()
        self.request_duration.labels(self.service_name, method, route).observe(duration_seconds)

    def observe_event_published(self, event_type: str) -> None:
        self.events_published.labels(self.service_name, event_type).inc()

    def observe_event_consumed(self, event_type: str, *, outcome: str) -> None:
        self.events_consumed.labels(self.service_name, event_type, outcome).inc()

    def observe_cache(self, cache: str, *, outcome: str) -> None:
        self.cache_operations.labels(self.service_name, cache, outcome).inc()


class MetricsMiddleware:
    """Records request counts and latency, labelled by route template."""

    def __init__(self, app: ASGIApp, metrics: ServiceMetrics) -> None:
        self._app = app
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            # Prefer the route template over the raw path: raw paths contain IDs and
            # would explode metric label cardinality.
            route = getattr(scope.get("route"), "path", None) or scope.get("path") or "unknown"
            self._metrics.observe_request(
                method=scope.get("method", "GET"),
                route=str(route),
                status_code=status_code,
                duration_seconds=time.perf_counter() - started,
            )


def metrics_endpoint(metrics: ServiceMetrics) -> Callable[[Request], Awaitable[Response]]:
    """Build the ``/metrics`` scrape endpoint for a service."""

    async def _endpoint(_request: Request) -> Response:
        return PlainTextResponse(metrics.render(), media_type=CONTENT_TYPE_LATEST)

    return _endpoint


def setup_tracing(
    *,
    service_name: str,
    otlp_endpoint: str | None,
    sample_ratio: float = 0.1,
) -> bool:
    """Configure OpenTelemetry tracing if the SDK and an endpoint are available.

    Returns:
        True when tracing was configured, False when it was skipped.
    """
    if not otlp_endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ImportError:
        logger.info("tracing_skipped", reason="opentelemetry_sdk_not_installed")
        return False

    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name}),
        sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    trace.set_tracer_provider(provider)
    logger.info("tracing_configured", endpoint=otlp_endpoint, sample_ratio=sample_ratio)
    return True
