# Observability

A delivery order touches five or more services over 30–45 minutes. When a customer asks why
their food is late, the answer has to be reconstructable from telemetry alone — nobody is going
to reproduce it.

## Structured logs

Single-line JSON, one schema across every service, so one query works platform-wide.

```json
{
  "event": "http_request",
  "level": "info",
  "timestamp": "2026-08-05T19:00:00.123456Z",
  "service": "marsool-merchant",
  "correlation_id": "01J9Z8XQF3K7M2P4R6T8V0W1Y3",
  "request_id": "01J9Z8XQF3K7M2P4R6T8V0W1Y4",
  "actor_id": "usr_01J9Z8XQF3K7M2P4R6T8V0W1Y5",
  "method": "GET",
  "route": "/merchants",
  "status_code": 200,
  "duration_ms": 18.4
}
```

`event` is a stable snake_case identifier, never an interpolated sentence. Searching for
`event:otp_verification_failed` finds every occurrence; searching for
`"OTP verification failed for +9715..."` finds the ones whose phrasing has not drifted.

The access log records the **route template**, not the raw path. `/merchants/{merchant_id}`
rather than `/merchants/mch_01J9Z...` keeps log and metric cardinality bounded — unbounded
label cardinality is the standard way to take down a metrics backend.

Health probes and metrics scrapes are excluded from the access log. At a 15-second probe
interval they would otherwise dominate the volume without adding signal.

**Redaction is by key name, in the logging pipeline.** `otp`, `token`, `access_token`,
`refresh_token`, `password`, `secret`, `authorization`, `card_number`, `cvv` and others are
replaced with `[redacted]` before reaching the sink. A well-meaning `logger.info("login",
otp=code)` cannot leak a credential. Phone numbers go through `mask_phone`.

## Correlation IDs

The single most valuable piece of telemetry in a distributed system, and the cheapest.

The gateway generates a correlation ID for external callers (or reuses an inbound
`X-Correlation-Id`), and it flows through every hop: propagated to upstreams, carried in the log
context, embedded in every domain event, and echoed back to the client. So:

- A customer support ticket with a correlation ID resolves to every log line across every
  service for that request.
- An event consumed hours later still carries the correlation ID of the customer action that
  caused it, plus `causation_id` for the causal chain.
- A failing request's `X-Correlation-Id` response header is the only thing a user needs to
  report.

Each hop also gets its own `request_id`, so a single logical request can be broken down per
service.

## Metrics

Prometheus at `/metrics` on every service, one registry per service so tests can create and
discard metrics without duplicate-registration errors.

| Metric | Type | Labels | Answers |
|---|---|---|---|
| `http_requests_total` | counter | service, method, route, status | Traffic and error rate |
| `http_request_duration_seconds` | histogram | service, method, route | Latency distribution |
| `domain_events_published_total` | counter | service, event_type | Is the outbox draining |
| `domain_events_consumed_total` | counter | service, event_type, outcome | Consumer health and failure rate |
| `cache_operations_total` | counter | service, cache, outcome | Hit ratio and cache availability |

Latency buckets are fine-grained below one second and coarse above, because that is where API
latency actually lives and where the decisions are made.

Service-level indicators worth alerting on, once there is traffic to measure:

| Indicator | Target |
|---|---|
| Merchant search p95 | < 200 ms |
| Menu fetch p95 (cached) | < 50 ms |
| Login p95 | < 400 ms |
| 5xx rate | < 0.1% |
| Outbox lag p99 | < 5 s |
| Dead-lettered events | 0 (page on any) |

## Tracing

OpenTelemetry-compatible, exported over OTLP, with parent-based ratio sampling (10% by
default; errors are always sampled once the collector is wired).

Tracing degrades to a no-op when the SDK is not installed or no endpoint is configured, so
local development and unit tests need neither. That is deliberate: an observability dependency
that breaks the test suite gets removed by the next person in a hurry.

Spans of interest span service boundaries: order creation fanning out to catalogue validation,
pricing and payment authorization is exactly where a p99 regression hides, and it is invisible
in per-service metrics.

## Health probes

Three endpoints, and the distinction between them matters operationally:

| Endpoint | Checks | Used by |
|---|---|---|
| `/health/live` | Process is running. No dependency checks. | Kubernetes liveness |
| `/health/ready` | Database and Redis reachable, with a 2-second timeout each | Kubernetes readiness, load-balancer membership |
| `/health` | Same checks, always 200 | Dashboards and humans |

Liveness deliberately ignores dependencies. If a readiness-style check drove liveness, a brief
database blip would restart every pod simultaneously, turning a recoverable degradation into an
outage. Readiness removes a pod from rotation; liveness kills it. Those are different remedies
for different problems.

A failing check reports a reason rather than a bare boolean:

```json
{
  "status": "degraded",
  "service": "marsool-merchant",
  "checks": {"database": "ok", "redis": "timeout"}
}
```

Probes never raise. Every check is wrapped, and an exception becomes `error: ExceptionName` in
the response, because a probe that 500s tells an operator nothing about *which* dependency
failed.

## Dashboards to build

Named now so the metrics above are known to be sufficient:

1. **Marketplace health** — orders per minute, acceptance rate, assignment time, delivery time
   against promise, active couriers against open orders. The one operations watches.
2. **Service health** — per-service traffic, latency percentiles, error rate, saturation.
3. **Event pipeline** — outbox depth and age, consumer lag per group, dead-letter count.
4. **Merchant operations** — pause rate, prep-time accuracy, item stock-outs. Feeds the
   merchant ops agent in Phase 2.
