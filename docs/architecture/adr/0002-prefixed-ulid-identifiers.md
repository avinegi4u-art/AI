# ADR 0002: Prefixed ULID identifiers

- **Status**: Accepted
- **Date**: 2026-08-05

## Context

Every aggregate needs a public identifier that appears in URLs, event payloads, logs and support
tickets. The choice affects index performance, debuggability, and how easy it is to confuse two
kinds of identifier.

Candidates: auto-incrementing integers, UUIDv4, UUIDv7, and prefixed ULIDs.

## Decision

Prefixed, time-sortable identifiers: `<prefix>_<ulid>`, for example
`ord_01J9Z8XQF3K7M2P4R6T8V0W1Y3`. Stored as `VARCHAR(40)` primary keys.

Prefixes are declared once in `marsool_core.ids` so two services cannot invent different
prefixes for the same aggregate.

## Rationale

**Prefixes make identifiers self-describing.** `ord_01J9Z...` in a log line, a stack trace or a
customer complaint needs no context to interpret. More importantly, passing a `usr_` where an
`ord_` is expected is visible immediately rather than producing a confusing "not found" — and
`has_prefix` makes it checkable in code.

**Time-sortability keeps index inserts append-only.** UUIDv4 primary keys scatter inserts
randomly across the B-tree, which fragments pages and pushes write amplification up as the table
grows. ULIDs are monotonic in the high bits, so inserts land at the right edge of the index.
This is the same property UUIDv7 provides.

**Time-sortability also makes keyset pagination meaningful.** `WHERE id > :cursor ORDER BY id`
returns rows in creation order, so a list endpoint needs no separate sort column.

**The creation timestamp is recoverable.** `timestamp_ms(order_id)` reads the creation time out
of the identifier, which is genuinely useful in support work when you have an id and nothing
else.

**No coordination is required.** Any service, any replica, can mint an identifier without a
round trip. That matters because auth mints a user id and the user service adopts it from an
event.

## Why not the alternatives

**Auto-incrementing integers.** Rejected outright. They leak business volume (a competitor
reading `order/48213` learns the order count), they are trivially enumerable, and they require a
database round trip before an identifier exists — which breaks the pattern where auth mints an
id and publishes it.

**UUIDv4.** Rejected for index locality and for being unreadable. `550e8400-e29b-41d4-a716-446655440000`
tells you nothing about what it identifies.

**UUIDv7.** Technically the closest alternative: time-ordered and standardised. Rejected only
because it has no prefix, and the prefix is doing real work in logs and in catching
type-confusion. A prefixed UUIDv7 (`ord_0190f2...`) would be an entirely reasonable variant of
this decision.

## Consequences

**Good**

- Self-describing identifiers in every log, URL and support ticket.
- Append-only index inserts.
- Keyset pagination on the primary key alone.
- No coordination or round trip to mint one.
- Creation time recoverable from the identifier.

**Bad, and accepted**

- `VARCHAR(40)` keys are larger than a 16-byte UUID or an 8-byte integer, so indexes and foreign
  keys use more space and comparisons are marginally slower. At the scale where that becomes
  measurable, the fix is to store the ULID payload as `BYTEA`/`UUID` and keep the prefix as
  presentation — a migration, not a redesign.
- ULID generation is 26 characters of Crockford base32, implemented in ~40 lines rather than
  taken from a dependency. Tested for uniqueness at 2,000 draws, time-ordering, round-tripping
  and malformed-input rejection.
- Identifiers expose creation time, which is a minor information leak. Acceptable: order
  creation time is not secret, and it is already in the payload.

## Implementation

```python
new_id("ord")                    # 'ord_01J9Z8XQF3K7M2P4R6T8V0W1Y3'
parse_id("ord_01J9Z8...")        # ('ord', '01J9Z8...'), raises on malformed input
timestamp_ms("ord_01J9Z8...")    # 1723000000123
has_prefix(value, "usr")         # total; False rather than raising on garbage
```

`parse_id` validates the payload length and alphabet, so a malformed identifier from a client
becomes a clean rejection rather than a lookup that mysteriously finds nothing.
