# Repository structure

## Monorepo, and why

A monorepo, with independently deployable services inside it.

The alternative — a repository per service — is the conventional choice for microservices,
and it is the wrong one here. The reason is the shared platform library. Eleven services
need identical behaviour for error envelopes, correlation IDs, JWT verification, event
envelopes and outbox semantics. In a multi-repo layout that library is a versioned package,
and every change to it becomes a release plus eleven dependency bumps. Services then sit on
different versions, and "the error format is consistent across the platform" quietly stops
being true.

In a monorepo, a change to the shared library is one commit that updates every consumer, and
CI proves all of them still work. Cross-service contract changes — a new event type, a
changed payload — likewise land atomically with the producer and every consumer in one
reviewable diff.

The cost is real and worth naming: CI runs more than it strictly needs to, and the repository
will get large. Both are solvable with path-filtered pipelines when they start to hurt.
Splitting a service out later is mechanical, because nothing in the layout depends on
co-location — each service has its own `pyproject.toml`, its own migration chain and its own
container image.

## Top level

```
├── libs/core/               Shared platform library (marsool_core)
├── api-gateway/             Single client entry point
├── services/                One directory per bounded context
│   ├── auth/                Implemented
│   ├── user/                Implemented
│   ├── merchant/            Implemented
│   ├── catalog/             Implemented
│   ├── order/               Step 2
│   ├── pricing/             Step 2
│   ├── payment/             Step 2
│   ├── dispatch/            Step 3
│   ├── tracking/            Step 3
│   ├── notification/        Step 4
│   └── analytics/           Step 5
├── web/                     Next.js customer and admin web app (Step 4)
├── mobile/                  Customer, courier and merchant apps (Step 4)
├── infra/
│   ├── docker/              One shared service Dockerfile plus a migration runner
│   ├── compose/             Local development stack
│   ├── postgres/init/       Extensions and schemas for a fresh local database
│   └── k8s/                 Kubernetes manifests
├── docs/
│   ├── architecture/        This directory, plus decision records
│   ├── openapi/             Generated, committed API contracts
│   └── roadmap.md
├── scripts/                 Migrations, seeding, OpenAPI export, smoke test
├── Makefile                 The task surface for humans and CI
└── pyproject.toml           Repo-wide tooling config (ruff, mypy, pytest)
```

## Inside a service

Every service has the same internal shape. Uniformity is the point: an engineer who has read
one service can navigate any of them, and a reviewer knows where to look for a given kind of
change.

```
services/<name>/
├── pyproject.toml                  Its own dependencies; installable on its own
├── alembic.ini                     Its own migration chain
├── migrations/versions/            Sequential revision ids: 0001_auth, 0002_auth, ...
├── src/marsool_<name>/
│   ├── __init__.py                 Bounded context statement, SCHEMA, CONSUMER_GROUP
│   ├── config.py                   Settings subclass; environment-driven
│   ├── models.py                   ORM models, one schema
│   ├── schemas.py                  Pydantic request/response contracts
│   ├── repository.py               Queries; keeps SQL out of the domain layer
│   ├── service.py                  Domain logic; no HTTP, no framework types
│   ├── events.py                   Events published, as typed builders
│   ├── consumers.py                Events consumed, with idempotent transactions
│   ├── router.py                   HTTP endpoints; thin, delegates to service
│   ├── runtime.py                  Long-lived resources (db, redis, bus, relay)
│   ├── deps.py                     FastAPI dependencies and auth guards
│   ├── testing.py                  Test doubles and sample data, shipped
│   └── main.py                     Application assembly
└── tests/
```

### Why the layers are split this way

**`router.py` is thin.** Endpoints parse, delegate, and return. Business rules in a request
handler cannot be reused by a worker, a support tool or an AI agent, and can only be tested
through HTTP.

**`service.py` takes a session, never opens one.** Transaction boundaries belong to the
caller. That is what lets one request compose several service calls atomically, and it makes
the deliberate exceptions — the writes that must survive a rollback — visible as explicit
autonomous transactions rather than an accident of where a commit landed.

**`repository.py` exists to isolate SQL.** In the merchant service it holds the PostGIS
expressions; keeping them out of `service.py` means the domain logic reads as domain logic.

**`runtime.py` owns resources, and there is no module-level mutable state.** The runtime is
created during startup and attached to `app.state`; dependencies read it from the request.
Tests construct a runtime bound to a test engine, so there is nothing global to reset.

**`testing.py` ships in the package.** Test doubles and realistic sample data are useful
beyond a service's own tests — the seed script uses the same Abu Dhabi merchants and the same
Emirati menu that the tests do, so a developer's local database looks like the launch market.

## Conventions

**Identifiers** are prefixed ULIDs: `usr_01J9Z8XQF3K7M2P4R6T8V0W1Y3`. Prefixed so a value is
self-describing in a log line or a support ticket; ULID so it is time-sortable, which keeps
index inserts append-only and makes keyset pagination on the primary key meaningful. See
[ADR 0002](adr/0002-prefixed-ulid-identifiers.md).

**Schemas** are one per service, named for the context rather than the service:
`auth`, `identity`, `merchant`, `catalog`. Migrations are restricted to their own schema —
autogenerate would otherwise emit DDL dropping other services' tables, which it did once
before that filter existed.

**Money** is integer minor units in the database (`price_minor`), decimal strings on the
wire (`"38.00"`), never a float anywhere.

**Enums** are `StrEnum` persisted as `VARCHAR` with a `CHECK` constraint, not PostgreSQL
enum types. Adding a variant is then a code change rather than a locking `ALTER TYPE`, and
the stored value stays readable.

**Settings** come from the environment with a `MARSOOL_` prefix, so a shared container
environment cannot collide with unrelated variables.

**No inline imports**, with two documented exceptions: genuinely optional dependencies
(`aiokafka`, the OpenTelemetry SDK) are imported lazily so services that do not use them need
not install them. Every such import carries a `noqa` and a reason.

## Frontend

Not yet built; the recommendation, for confirmation:

- **Web** — Next.js with TypeScript and Tailwind. App Router for server-side rendering of
  merchant and menu pages, which are the SEO surface, and the same stack for the admin
  console.
- **Mobile** — React Native with Expo, over Flutter. Not because it is technically superior,
  but because it shares TypeScript, the generated API client and the domain vocabulary with
  the web app, and three apps (customer, courier, merchant) are needed. One language across
  four surfaces is a hiring and velocity argument. Flutter would win on raw rendering
  performance for the courier map; that is worth revisiting if map performance becomes the
  binding constraint.

API clients for both are generated from the committed OpenAPI documents rather than
hand-written, so a contract change breaks the build instead of production.
