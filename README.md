# Marsool

A food and grocery delivery platform, built for Abu Dhabi and designed to scale globally.

`مرسول` — *the one who is sent*.

## Status

**Step 1 of the MVP is implemented and tested**: authentication, user profiles, merchant
discovery and the catalogue, behind an API gateway. A customer can browse merchants that
deliver to their location, sign in with a one-time password, manage their addresses and
delivery preferences, read a merchant's menu, and have a configured basket validated and
priced.

Ordering, pricing, payment, dispatch and tracking are designed but not yet built. See
[`docs/roadmap.md`](docs/roadmap.md) for what lands in each step and
[`docs/architecture/`](docs/architecture/) for the design they will be built against.

| Component | State | What it does |
|---|---|---|
| `libs/core` | Implemented | Shared platform library every service builds on |
| `api-gateway` | Implemented | Single entry point: auth, rate limiting, routing |
| `services/auth` | Implemented | OTP challenges, token issuance, refresh rotation |
| `services/user` | Implemented | Profiles, addresses, preferences, courier details |
| `services/merchant` | Implemented | Geospatial discovery, opening hours, service areas |
| `services/catalog` | Implemented | Menus, items, options, basket validation |
| `services/order` | Planned (Step 2) | Order lifecycle and state machine |
| `services/pricing` | Planned (Step 2) | Delivery fees, taxes, surge, courier pay |
| `services/payment` | Planned (Step 2) | Payment intents, capture, refunds, ledger |
| `services/dispatch` | Planned (Step 3) | Courier matching, batching, routing |
| `services/tracking` | Planned (Step 3) | Location ingestion, ETAs, WebSocket streams |
| `services/notification` | Planned (Step 4) | Push, SMS and email driven by events |
| `services/analytics` | Planned (Step 5) | Event-stream aggregation and dashboards |

## Quick start

Requires Python 3.11+, PostgreSQL 15+ with PostGIS, and Redis. Docker Compose brings all of
it up, or point the DSNs at your own instances.

```bash
# Full stack in Docker: Postgres + PostGIS, Redis, Kafka, MinIO, all services, gateway
make up

# Or run against local Postgres and Redis
make install          # create .venv and install every package in editable mode
make migrate          # apply every service's migrations
make seed             # load Abu Dhabi merchants, menus and a demo customer
make run-auth         # in separate shells: run-user, run-merchant, run-catalog, run-gateway
```

Then exercise the whole customer journey end to end:

```bash
python scripts/smoke_test.py --base-url http://localhost:8000
```

```
  [PASS]  2. Anonymous merchant search works — 2 merchants, nearest 217 m
  [PASS]  6. Login issued tokens — user usr_01KZ871A10HW17HK1G9FEZ28JW
  [PASS]  8. Profile read — Layla Al Mansouri (customer)
  [PASS] 12. Menu fetched via the gateway — 3 items in 2 categories
  [PASS] 14. Valid basket priced — 76.00 AED for 2 x Chicken Machboos
  [PASS] 18. Replayed refresh token is rejected — 401 token_reused
```

### Try it by hand

```bash
# Find merchants that deliver to Al Reem Island, open right now
curl 'http://localhost:8000/merchants?lat=24.49464&lng=54.39946&radius_m=10000&open_now=true'

# Sign in (outside production the OTP is returned in the response for convenience)
CODE=$(curl -s -X POST http://localhost:8000/auth/otp \
  -H 'content-type: application/json' \
  -d '{"phone":"+971501234567"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["debug_code"])')

curl -s -X POST http://localhost:8000/auth/login \
  -H 'content-type: application/json' \
  -d "{\"phone\":\"+971501234567\",\"otp\":\"$CODE\"}"
```

Interactive API documentation is served per service at `/docs` in non-production
environments; the generated OpenAPI documents are committed under
[`docs/openapi/`](docs/openapi/) so a contract change shows up in review.

## Development

```bash
make check        # lint, type-check and the full test suite — what CI runs
make test         # 340+ tests against real PostgreSQL/PostGIS and Redis
make test-unit    # only tests that need no external services
make openapi      # regenerate the committed OpenAPI documents
make migration service=auth rev=0002_auth msg="add something"
```

Tests run against real PostgreSQL, PostGIS and Redis rather than stubs. The behaviour that
matters most here — distance ranking, polygon containment, timezone-aware opening hours,
atomic rate limiting, transactional outbox semantics — *is* the datastore's behaviour, and a
stub would only verify the stub.

## Repository layout

```
libs/core/            Shared platform library (marsool_core)
api-gateway/          Single client entry point
services/<name>/      One bounded context each; own schema, own migrations
infra/                Dockerfiles, Compose stack, Kubernetes manifests
docs/                 Architecture, ADRs, roadmap, generated OpenAPI
scripts/              Migrations, seeding, OpenAPI export, smoke test
```

Every service follows the same internal shape, so moving between them is uneventful:

```
services/<name>/
├── alembic.ini
├── migrations/                  Alembic chain for this service's schema only
├── src/marsool_<name>/
│   ├── config.py                Settings (environment-driven)
│   ├── models.py                ORM models for one schema
│   ├── schemas.py               Request/response contracts
│   ├── repository.py            Queries
│   ├── service.py               Domain logic
│   ├── events.py                Events this service publishes
│   ├── consumers.py             Events this service consumes
│   ├── router.py                HTTP endpoints
│   ├── runtime.py               Long-lived resources
│   ├── deps.py                  FastAPI dependencies
│   ├── testing.py               Test doubles and sample data
│   └── main.py                  Application assembly
└── tests/
```

See [`docs/architecture/01-repo-structure.md`](docs/architecture/01-repo-structure.md) for
why it is laid out this way.

## Documentation

- [Architecture overview](docs/architecture/00-overview.md) — the shape of the system
- [Repository structure](docs/architecture/01-repo-structure.md) — layout and conventions
- [Service catalog](docs/architecture/02-service-catalog.md) — what each service owns
- [Event contracts](docs/architecture/03-event-contracts.md) — topics, envelope, delivery
- [Order lifecycle](docs/architecture/04-order-lifecycle.md) — the state machine to come
- [Data stores](docs/architecture/05-data-stores.md) — what lives where, and why
- [Security model](docs/architecture/06-security.md) — authn, authz, rate limits, secrets
- [Observability](docs/architecture/07-observability.md) — logs, metrics, traces, probes
- [Testing strategy](docs/architecture/08-testing.md) — what is tested at which level
- [Decision records](docs/architecture/adr/) — the choices worth writing down
- [Roadmap](docs/roadmap.md) — what lands in each step

## Licence

Proprietary. All rights reserved.
