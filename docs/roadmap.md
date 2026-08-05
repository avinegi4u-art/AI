# Roadmap

Sequenced by dependency, not by calendar. Each step ends with something demonstrable, and each
step's tests are the entry criteria for the next.

## Step 1 — Identity, discovery and catalogue ✅ Complete

**Demonstrable:** a customer can browse merchants that deliver to their location, sign in, manage
addresses, read a menu, and have a basket validated and priced.

- `libs/core` — settings, structured logging, correlation IDs, error envelope, app factory,
  auth guards, PostGIS column types, event envelope and topic registry, transactional outbox,
  consumer runtime, JWT, Redis cache/rate limiting/idempotency, money, geo, phone, ULIDs.
- `api-gateway` — auth, rate limiting, routing, correlation propagation.
- `services/auth` — OTP challenges, login, refresh rotation with reuse detection, sessions.
- `services/user` — profiles, addresses with PostGIS points, preferences, courier details.
- `services/merchant` — geospatial discovery, service-area polygons, timezone-aware hours,
  serviceability.
- `services/catalog` — nested cached menus, item management, basket validation and pricing.
- Infrastructure — Compose stack, shared service Dockerfile, migration runner, seed data,
  OpenAPI export, 19-step end-to-end smoke test.

340+ tests against real PostgreSQL/PostGIS and Redis. `ruff` and `mypy` clean.

## Step 2 — Ordering, pricing and payment

**Demonstrable:** a customer completes checkout and pays; the merchant accepts and cooks.

**Entry criteria:** Step 1 green.

1. **`services/pricing`** — `POST /pricing/quote` returning items total, delivery fee, service
   fee, tax, suggested tip and total, with `eta_min` and `valid_until`. Quotes are persisted and
   referenced by id, so the price a customer saw is the price they are charged even if surge moves
   in between. Distance-banded delivery fees, 5% UAE VAT, a surge multiplier driven by the
   courier-to-order ratio, and courier pay rules as the same computation from the other side.
2. **`services/order`** — the state machine from
   [the lifecycle document](architecture/04-order-lifecycle.md) as a tested pure function, plus
   `orders`, `order_items` and append-only `order_status_history`. Creation composes catalogue
   validation, quote consumption and payment authorization. Optimistic concurrency via
   conditional status updates.
3. **`services/payment`** — payment intents with authorize/capture/refund, an internal
   double-entry ledger, merchant payouts, and mandatory `Idempotency-Key` on every mutating
   endpoint. A provider adapter interface with a deterministic fake, so the whole flow is testable
   without a sandbox account.
4. **Scheduled transitions** — merchant acceptance and courier assignment timeouts as jobs
   reading order state, not in-memory timers.
5. **The integration test that matters** — create → pay → confirm → prepare → ready → pick up →
   deliver, asserting the state history and every emitted event.

Also in this step: a k6 load profile against seeded data, so the claims made about the search
path become measurements; and the PDPL erasure workflow, now that there are orders to redact.

## Step 3 — Dispatch and tracking

**Demonstrable:** an order is assigned to a courier automatically, and the customer watches it
arrive.

1. **`services/dispatch`** — consumes `dispatch.assignment_requested`, finds nearby on-duty
   couriers with capacity, and scores them:

   ```
   score = w_distance x normalised_distance_to_pickup
         + w_time     x normalised_time_to_pickup
         + w_load     x current_active_orders
         - w_rating   x courier_rating
         - w_batch    x batching_compatibility
   ```

   Deliberately a transparent weighted sum to begin with: when a courier asks why they did not
   get an order, there has to be an answer. Weights are configuration, and the function is a pure
   tested unit. Then batching (2–3 orders on compatible routes), reassignment on courier
   timeout or going offline, and `POST /dispatch/assign` as a manual operations override.
2. **`services/tracking`** — WebSocket ingestion of courier location, last-known position in
   Redis for the live map, full history in TimescaleDB, ETA recomputation on each update, and
   `WS /tracking/courier-stream` plus `GET /tracking/orders/{id}/eta`.
3. **Routing engine** — replace the straight-line-times-a-factor estimate with real road
   distances and durations.

## Step 4 — Notifications, web and mobile

**Demonstrable:** the customer-facing product, on a phone, with push notifications.

1. **`services/notification`** — templates, channel preferences, delivery attempts, provider
   adapters for push/SMS/email, driven entirely by events. Takes over real OTP delivery.
2. **`web/`** — Next.js customer app (server-rendered merchant and menu pages for SEO) and the
   admin console for operations: live order board, merchant management, courier oversight,
   manual dispatch override.
3. **`mobile/`** — React Native customer, courier and merchant apps. Recommendation and rationale
   in [repo structure](architecture/01-repo-structure.md#frontend); awaiting confirmation.
4. **Clients generated from the committed OpenAPI documents**, so a contract change breaks the
   build rather than production.

## Step 5 — Analytics

**Demonstrable:** operations can see marketplace health and act on it.

1. **`services/analytics`** — consumes every topic into a warehouse.
2. **The four dashboards** named in [observability](architecture/07-observability.md#dashboards-to-build).
3. **Feature extraction** for the ML work in Phase 2: prep-time accuracy, ETA error, courier
   utilisation, demand by zone and hour.

## Phase 2 — AI and agentic features

Deferred until the core loop is stable, and until there is enough real data for a model to beat
the heuristics. The platform is built so these can be added without redesign: internal APIs are
scope-gated, so an agent gets a narrowly-scoped service principal rather than an admin token, and
every action it takes is attributed and rate-limited.

| Feature | What it does | What it needs first |
|---|---|---|
| **Customer assistant** | "Something healthy under 25 AED arriving in under 25 minutes" — an LLM over search, filter, pricing and ETA tools | Steps 1–3; the closed dietary-tag vocabulary already exists for this |
| **ML-based ETA** | Replaces the heuristic with a model over historical prep and travel times | Step 5 feature extraction |
| **Merchant ops agent** | Auto-adjusts prep-time estimates, flags items that keep selling out, suggests menu changes during a rush | `catalog.item_availability_changed` and prep-time accuracy data |
| **Dispatch control agent** | Watches marketplace health and proposes parameter changes — radius, incentives, batching aggressiveness | Step 3 plus the marketplace-health dashboard |
| **Dynamic pricing** | Reinforcement learning over surge and courier incentives, bounded by hard fairness limits | A long baseline; a bad pricing model is worse than a simple one |
| **Support triage** | Classifies issues, proposes refunds within policy limits, drafts responses | Step 2 payments and refund policy |

Two rules for all of it. **Agents propose, humans approve**, for anything touching money or a
customer's order, until the proposal quality is measured. And **every agent action is attributed
to a service principal**, so an agent's decisions are as auditable as a human operator's.

## Cross-cutting, ongoing

- **Load testing** from Step 2, so performance claims are measurements.
- **Chaos testing** of the fail-open and fail-closed paths against real dependency failures.
- **Property-based tests** for money arithmetic and the order state machine.
- **Mutation testing** to find out whether the test suite would actually catch a regression.
- **Kubernetes manifests** progressing from the current skeleton to HPA, PodDisruptionBudgets,
  network policies and a service mesh if mutual TLS becomes a requirement.
