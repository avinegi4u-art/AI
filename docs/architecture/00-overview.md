# Architecture overview

## What the platform has to do

A delivery marketplace has to keep three sides happy at once, and their interests conflict.
Customers want food fast, cheap and exactly as promised. Merchants want a steady flow of
orders they can actually cook. Couriers want short, well-paid runs with little waiting. The
platform's job is to clear that market continuously, under a load pattern that is almost all
peak: in Abu Dhabi, roughly half of a day's orders land in two windows.

Two properties follow from that and shape most of the design:

1. **Reads dominate, and they are geospatial.** For every order placed, there are dozens of
   "what can I get here, right now?" queries. That path must be indexed, cached and cheap.
2. **Writes are long-lived, multi-party workflows.** An order touches pricing, payment,
   dispatch, tracking and notifications over 30–45 minutes, with participants going offline
   mid-flight. That is a saga, not a transaction.

## Shape of the system

```
                        ┌──────────────┐
   Customer / Merchant  │              │   Every service verifies the JWT itself:
   / Courier / Admin ───▶ API Gateway  │   the gateway is a policy layer, never
                        │              │   the only guard.
                        └──────┬───────┘
                               │  HTTP, correlation ID propagated
   ┌───────────┬───────────┬───┴───────┬───────────┬───────────┐
   ▼           ▼           ▼           ▼           ▼           ▼
 auth        user      merchant     catalog      order ⟂     pricing ⟂
   │           │           │           │           │           │
   └───────────┴───────────┴─────┬─────┴───────────┴───────────┘
                                 │  domain events via transactional outbox
                        ┌────────▼─────────┐
                        │   Kafka topics    │  order.events, dispatch.events,
                        │                   │  payment.events, tracking.events,
                        └────────┬─────────┘  notification.events, ...
   ┌───────────┬───────────┬─────┴─────┬───────────┐
   ▼           ▼           ▼           ▼           ▼
 payment ⟂  dispatch ⟂  tracking ⟂  notification ⟂  analytics ⟂

   ⟂ = designed, not yet built (Steps 2–5)

 Data: PostgreSQL 16 + PostGIS (schema per service) · Redis (cache, limits, idempotency)
       Kafka (events) · S3-compatible object storage (images) · time-series store (tracking)
```

## The decisions that matter

### Microservices, but along ownership lines

Each service owns one bounded context and one PostgreSQL schema, and no service reads
another's tables. In the MVP all schemas live in one database instance — splitting them
across clusters before there is load to justify it buys operational cost and no benefit —
but the *logical* separation is enforced from day one: no cross-schema foreign keys, no
cross-schema joins. Splitting later becomes an infrastructure change rather than a rewrite.

The catalog service references `merchant_id` without a foreign key for exactly this reason.
Referential integrity across contexts is maintained by events and by validation at the
boundary, not by the database.

### Event-driven, with a transactional outbox

Services publish domain events on state changes and consume them to update read models,
trigger notifications and drive dispatch. Publishing to a broker inside a database
transaction is not atomic: the broker call can succeed while the transaction rolls back
(a phantom event) or the reverse (a lost event). So every service writes events to an
`event_outbox` table in the *same* transaction as the state change, and a relay publishes
them afterwards.

That makes delivery at-least-once, so consumers must be idempotent. Two mechanisms provide
it: handlers are written to be naturally idempotent (upserts, state-machine guards), and a
`processed_events` table records every `(event_id, consumer_group)` pair so a redelivery is
skipped. The claim is written in the handler's own transaction, so an event cannot be marked
processed if its handler failed.

### Geospatial work belongs in PostGIS

Merchant discovery answers three questions at once — is this merchant close enough, does it
deliver to this exact point, and is it open right now — and all three are done in one
indexed SQL query:

- `ST_DWithin` against a GiST-indexed `geography` column, ranked by distance.
- Service-area polygons take precedence over a delivery radius, because real coverage is not
  a circle: bridges, industrial zones and islands mean a radius either excludes reachable
  customers or promises deliveries that cannot be made.
- Opening hours are stored as local wall-clock times and compared in each merchant's own
  timezone, including windows that run past midnight.

Doing any of this in Python would mean loading every merchant on every request.

### Money is never a float

Amounts are stored as integer minor units (fils, cents) and exposed as decimal strings.
Rounding is half-up, the convention finance expects, and currency exponents are declared
explicitly so a three-decimal currency such as the Kuwaiti dinar cannot be silently
mis-scaled.

### Failure paths get the same attention as happy paths

Several behaviours only make sense once you think about what happens when a request fails:

- **Rate limiting and caching fail open.** A Redis outage must not become a platform
  outage.
- **Idempotency and OTP attempt counting fail closed.** Replaying a payment or brute-forcing
  a six-digit code are worse outcomes than a 503.
- **The OTP attempt counter lives in Redis, not in a database column.** A failed login rolls
  its transaction back, which would discard a column increment and leave the retry budget
  permanently unused.
- **Refresh-token reuse revokes the whole session, in its own transaction.** The revocation
  has to survive the rollback of the request that raised.

## Non-functional posture

| Concern | Approach |
|---|---|
| Scalability | Stateless services, horizontal replicas, indexed hot paths, Redis caching, keyset pagination |
| Reliability | Transactional outbox, at-least-once delivery with consumer-side deduplication, bounded retries, dead-letter queues |
| Security | Short-lived JWTs verified independently per service, single-use rotating refresh tokens, per-identity rate limits, role and scope guards, secrets from the environment |
| Observability | Structured JSON logs with correlation IDs, Prometheus metrics, OpenTelemetry-compatible tracing, liveness and readiness probes |
| Correctness | 340+ tests against real PostgreSQL/PostGIS and Redis; migration-parity tests; contract tests over the event registry |

## Where to read next

- [Repository structure](01-repo-structure.md) — how the code is laid out and why
- [Service catalog](02-service-catalog.md) — what each service owns, endpoint by endpoint
- [Event contracts](03-event-contracts.md) — the envelope, the topics, delivery semantics
- [Order lifecycle](04-order-lifecycle.md) — the state machine Step 2 is built around
- [Decision records](adr/) — the choices worth writing down, with their trade-offs
