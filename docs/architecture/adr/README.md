# Architecture decision records

Decisions that were not obvious, that have real trade-offs, and that would be expensive to
reverse. Each one states what was decided, why, what it costs, and what was rejected.

Decisions that were obvious are not recorded here. "Use PostgreSQL" needs no defence; "store
money as integer minor units and reject unregistered currencies" does.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-monorepo-with-schema-per-service.md) | Monorepo with a schema per service | Accepted |
| [0002](0002-prefixed-ulid-identifiers.md) | Prefixed ULID identifiers | Accepted |
| [0003](0003-transactional-outbox.md) | Transactional outbox for event publishing | Accepted |
| [0004](0004-otp-attempt-counter-in-redis.md) | OTP attempt counter in Redis, not a column | Accepted |
| [0005](0005-postgis-for-discovery.md) | PostGIS for discovery, polygons over radii | Accepted |
| [0006](0006-money-as-minor-units.md) | Money as integer minor units | Accepted |
| [0007](0007-basket-validation-in-catalog.md) | Basket validation belongs to catalog | Accepted |

## Writing one

A record is worth adding when a future engineer would otherwise reasonably ask "why on earth is
it done this way?", or when a decision was close and the losing option deserves to be on record.

Structure: **Context** (the forces, including the naive approach and why it fails) →
**Decision** (what, concretely) → **Rationale** (why this over the alternatives) →
**Consequences** (both directions, honestly) → **Alternatives considered** (with the reason each
was rejected).

Two conventions worth keeping:

- **Name the bug if there was one.** ADR 0004 exists because the obvious implementation shipped
  and was wrong. Recording that is more useful than presenting the fix as if it were foresight.
- **Never write a consequences section that is all upside.** A decision with no cost was not a
  decision.

Records are immutable once accepted. A reversal is a new record that supersedes the old one, and
the old one is marked `Superseded by ADR NNNN` rather than edited — the history of what was
believed and why is the point.
