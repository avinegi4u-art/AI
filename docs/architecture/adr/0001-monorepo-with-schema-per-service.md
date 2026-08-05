# ADR 0001: Monorepo with a schema per service

- **Status**: Accepted
- **Date**: 2026-08-05

## Context

Eleven services are planned. Two structural questions had to be answered before any code was
written, and both are expensive to reverse:

1. One repository or eleven?
2. One database or eleven?

The two are related but not the same question.

## Decision

**A monorepo containing independently deployable services**, and **one PostgreSQL instance with
one logical schema per service**, with no cross-schema reads or foreign keys.

## Why a monorepo

The deciding factor is the shared platform library. Eleven services need identical behaviour
for error envelopes, correlation IDs, JWT verification, event envelopes and outbox semantics.

In a multi-repo layout, that library is a versioned package. Every change to it is a release
followed by eleven dependency bumps, so services inevitably sit on different versions, and
"the error format is consistent across the platform" stops being true — quietly, and only
discovered by a client that hits the older shape.

In a monorepo, a change to the shared library is one commit that updates every consumer, and CI
proves all of them still work. Cross-service contract changes — a new event type, a changed
payload — land atomically with the producer and every consumer in one reviewable diff.

Services stay independently deployable: each has its own `pyproject.toml`, its own Alembic
chain, and its own container image built from a shared Dockerfile with the service path as a
build argument.

## Why a schema per service in one database

The distributed-systems-textbook answer is a database per service. It is the right answer at
scale and the wrong answer now.

Eleven database instances means eleven backup configurations, eleven failover plans, eleven sets
of connection limits to tune, and eleven things to provision before anyone can run the system
locally. None of that buys anything until there is load that one instance cannot serve.

What actually matters about "database per service" is the *logical* discipline, and that is
enforced from day one:

- Each service owns exactly one schema and reads no other.
- No cross-schema foreign keys. `catalog.menu_items.merchant_id` deliberately has no
  constraint.
- Each service has its own Alembic chain with its own `alembic_version` table inside its own
  schema.
- Autogenerate is restricted to the owning schema.

Because no query and no constraint crosses a schema boundary, moving a schema to its own cluster
later is a connection-string change. Nothing in the application code has to be found and
rewritten.

## Consequences

**Good**

- Shared-library changes are atomic and verified across every consumer.
- One database to provision, back up and reason about while the platform is small.
- Refactoring across service boundaries is a normal code change.
- A new engineer runs the whole platform with `make up`.

**Bad, and accepted**

- CI runs more than it strictly needs to. Solvable with path-filtered pipelines when it hurts.
- The repository will grow large. Sparse checkouts exist.
- One database instance is a shared blast radius: a runaway query in one service can affect
  another. Mitigated with per-service connection pools, a server-side `statement_timeout`, and
  per-service database roles.
- The discipline is a convention, not a mechanism. A determined engineer can write a
  cross-schema join. The code review guard is that any `JOIN` across schemas is a design
  discussion, and the practical guard is that no service's models even define the other's
  tables.

## Alternatives considered

**Repository per service.** Rejected because of shared-library version skew, which is the
specific failure mode that would erode platform-wide consistency.

**Database per service now.** Rejected as operational cost with no current benefit. Revisit when
one instance's write throughput or connection count becomes the binding constraint — most likely
for tracking, which is why that store is planned as separate from the start.

**A modular monolith.** Genuinely tempting for an MVP, and would ship faster. Rejected because
dispatch and tracking have fundamentally different scaling and latency profiles from the
transactional services — tracking ingests hundreds of writes per second continuously — and
retrofitting service boundaries after they are load-bearing is far more expensive than starting
with them.
