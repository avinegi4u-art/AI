# Service catalog

Every service owns one bounded context and one schema. This document is the authority on who
owns what; if two services appear to need the same data, one of them is wrong.

## Implemented

### api-gateway

Single entry point for every client. Verifies bearer tokens, enforces rate limits, propagates
correlation IDs, and forwards to the owning service.

It holds no business logic and no database, and every downstream service verifies the token
again for itself, so a bypassed or compromised gateway cannot mint trust.

Public reads (browsing merchants and menus) pass through without a token; no write is ever
public. Authenticated callers are rate-limited per user id, anonymous traffic shares a tighter
bucket keyed by client IP.

Routing note: `/merchants/{id}/menu` and `/merchants/{id}/basket/validate` go to **catalog**,
not merchant. They read as merchant sub-resources for clients, which is the right client
experience, but the data belongs to the catalogue context.

### auth — schema `auth`

The only service that mints tokens.

| Endpoint | Purpose |
|---|---|
| `POST /auth/otp` | Issue a one-time password; rate limited per phone number |
| `POST /auth/login` | Exchange phone + OTP for a token pair; creates the account on first use |
| `POST /auth/refresh` | Rotate the refresh token |
| `POST /auth/logout` | Revoke one session, or all of them |
| `GET /auth/sessions` | List the caller's active sessions |

Owns `principals`, `otp_challenges`, `refresh_tokens`. Keeps a denormalised `display_name`,
maintained by consuming `identity.user_profile_updated`, so a login response can include the
user's name without a synchronous call into the user service.

Publishes `identity.user_registered`, `identity.user_logged_in`,
`identity.user_session_revoked`, `notification.otp_requested`.

### user — schema `identity`

System of record for who a user is.

| Endpoint | Purpose |
|---|---|
| `GET /users/me` | The caller's profile |
| `PUT /users/me` | Partial profile update |
| `GET/POST /users/me/addresses` | List and create delivery addresses |
| `PUT/DELETE /users/me/addresses/{id}` | Update and remove an address |
| `PUT /users/me/courier-profile` | Courier vehicle, availability, payout details |

Owns `users`, `addresses`, `courier_profiles`, `merchant_staff`. Addresses carry both scalar
coordinates and a PostGIS geography point, so serviceability, pricing and routing all read
the same authoritative location.

Every route is scoped to the authenticated caller. There is deliberately no
`GET /users/{id}`: making one user readable by identifier is how customer data leaks. The
internal callers that legitimately need it — support tooling, dispatch — will get a
purpose-built, scope-gated endpoint.

Publishes `identity.user_profile_updated` and the address lifecycle events. Consumes
`identity.user_registered` to provision the profile.

### merchant — schema `merchant`

Answers "what can I order from right now?" — the hottest read path in the platform.

| Endpoint | Purpose |
|---|---|
| `GET /merchants` | Geospatial search: proximity, coverage, cuisine, open-now |
| `GET /merchants/{id}` | Storefront and weekly schedule |
| `PUT /merchants/{id}` | Partial storefront update (merchant staff, admin) |
| `GET/PUT /merchants/{id}/hours` | Read and replace the weekly schedule |
| `PUT /merchants/{id}/accepting-orders` | The merchant's own order kill switch |
| `GET /merchants/{id}/serviceability` | Pre-checkout gate for order creation |

Owns `merchants`, `merchant_hours`, `service_areas`. Two axes of control are deliberately
separate: `status` is operations-owned lifecycle, `accepting_orders` is the merchant's own
switch for when the kitchen is overwhelmed. Commission and rating are not settable by a
merchant.

Publishes `merchant.updated`, `merchant.status_changed`, `merchant.hours_updated`.

### catalog — schema `catalog`

What a merchant sells, and what a valid basket line is.

| Endpoint | Purpose |
|---|---|
| `GET /merchants/{id}/menu` | Whole nested menu in one round trip; Redis-cached |
| `POST /merchants/{id}/basket/validate` | Validate and price a basket |
| `PUT /catalog/items/{id}` | Partial item update |
| `PUT /catalog/items/{id}/availability` | Mark an item in or out of stock |

Owns `menus`, `menu_categories`, `menu_items`, `option_groups`, `options`. Variants and
modifiers are one model: a `SINGLE` group is a variant (size, crust), a `MULTI` group is a
modifier set (toppings, sauces).

Basket validation lives here rather than in the order service, next to the data that defines
the rules, so the order service cannot accept a line the catalogue would reject.

Publishes `catalog.item_updated`, `catalog.item_availability_changed`.

## Planned

### order — schema `orders` (Step 2)

Owns the order lifecycle and its state machine: `orders`, `order_items`, `order_status_history`.
Composes catalogue validation, a pricing quote and a payment authorization into one order, then
drives the order through its states in response to merchant, courier and payment events.

`POST /orders`, `GET /orders/{id}`, `GET /orders`, `POST /orders/{id}/cancel`, plus
merchant-facing transitions. See [order lifecycle](04-order-lifecycle.md).

### pricing — schema `pricing` (Step 2)

`POST /pricing/quote` returns a signed, time-limited quote: item total, delivery fee, service
fee, tax, suggested tip, total. Quotes are persisted and referenced by id at order creation,
so the price a customer saw is the price they are charged even if surge moves in between.

Also owns courier pay rules, which are the same computation viewed from the other side.

### payment — schema `payments` (Step 2)

Payment intents with authorize/capture/refund, an internal double-entry ledger, and merchant
payouts. `Idempotency-Key` is mandatory on every mutating endpoint: a duplicated charge is the
single worst failure mode in the platform.

### dispatch — schema `dispatch` (Step 3)

Courier matching, batching and assignment state. Consumes `dispatch.assignment_requested`,
scores nearby available couriers, and assigns. Starts with a transparent weighted scoring
function; the plan is a VRP solver once batching density justifies it.

### tracking — schema `tracking` + time-series store (Step 3)

Ingests courier location at 1–5 second intervals, computes ETAs, and pushes updates over
WebSocket to subscribed customers. The high-volume location stream goes to a time-series
store, not PostgreSQL — see [data stores](05-data-stores.md).

### notification — schema `notifications` (Step 4)

Push, SMS and email, driven entirely by events. Owns templates, delivery attempts and user
channel preferences, and takes over real OTP delivery from the auth service's pluggable
sender.

### analytics — warehouse (Step 5)

Consumes every topic and builds the read models behind operational dashboards, merchant
reporting and ML feature extraction. Write-only from the platform's perspective; nothing in
the request path depends on it.

## Dependency rules

- No service reads another service's schema. Cross-context data arrives by event or by API
  call.
- No cross-schema foreign keys. `catalog.menu_items.merchant_id` has no constraint, on
  purpose.
- Synchronous calls between services are allowed only where the caller genuinely cannot
  proceed without a fresh answer — order creation checking serviceability and validating a
  basket. Everything else is asynchronous.
- The gateway calls services; services do not call the gateway.
