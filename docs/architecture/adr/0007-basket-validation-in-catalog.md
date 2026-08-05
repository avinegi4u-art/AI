# ADR 0007: Basket validation belongs to the catalog service

- **Status**: Accepted
- **Date**: 2026-08-05

## Context

Before an order can be created, a basket has to be checked. Does every item exist and is it in
stock? Do all items belong to one merchant? Are the selected options real, available, and within
the group's selection bounds? Has a required group been left out? And what does the configured
line actually cost?

The order service is the natural-looking home for this: it is creating the order, so it should
validate what it is creating.

## Decision

Basket validation lives in the **catalog** service, exposed as
`POST /merchants/{merchant_id}/basket/validate`. The order service calls it and refuses to create
an order the catalogue rejected.

## Rationale

**The rules are catalogue data.** `min_select`, `max_select`, `selection_type`, `is_available`
and `price_delta_minor` are all columns the catalog service owns. Validating against them from
the order service means either reading another service's tables — which the platform prohibits —
or replicating the rules and keeping two copies in step. Both are worse than a call.

**One implementation, one place to be wrong.** If the order service reimplemented the rules, a
change to how option groups work would need coordinated edits in two services, and a merchant
whose menu the order service misunderstands accepts orders the kitchen cannot make.

**Pricing needs the same computation.** A pricing quote needs the item subtotal, which is
exactly what basket validation produces. Having pricing call the same endpoint means the quote
and the order agree by construction rather than by coincidence.

**The logic is pure and therefore exhaustively testable.** `validate_basket` takes a loaded
`CatalogSnapshot` and the requested lines and returns the result. No I/O, no session, no
framework. Every rule has its own test.

## Design choices within the decision

**Every line is validated independently, and all issues are returned together.** Stopping at the
first problem would make a customer with three bad lines discover them one request at a time.

**Valid lines are still priced when others fail.** The response carries both `lines` and
`issues`, so a client can show correct prices for the good lines while flagging the bad ones.

**Issues are machine-readable codes with structured details**, not prose:

```json
{
  "line_index": 0,
  "item_id": "itm_01J9Z...",
  "code": "required_group_missing",
  "message": "'Portion' requires a selection",
  "details": {"group_id": "opg_01J9Z...", "min_select": 1}
}
```

A client can highlight the exact group in the exact line. A message string could not.

**A `SINGLE` group's ceiling is one, whatever `max_select` says.** The effective maximum is the
stricter of the two, so bad data cannot let a customer pick two sizes.

**A cross-merchant item is rejected outright, not merely flagged.** A basket spanning two
merchants is unfulfillable and unroutable, so it fails fast with its own code rather than being
priced.

**Validation is authenticated.** Menu browsing is public; validating a basket is part of
checkout, and leaving it open would expose a free pricing oracle for scraping.

## Consequences

**Good**

- Rules live with the data that defines them, in one implementation.
- Order and pricing agree on the item subtotal by construction.
- Pure logic, exhaustively tested: nine distinct failure modes, each with its own test.
- Adding an option-group feature is a catalog-only change.

**Bad, and accepted**

- Order creation makes a synchronous call to catalog, so catalog is on the critical path for
  ordering and its latency is added to checkout. Accepted because there is no correct
  alternative: the answer must be fresh. An item that sold out thirty seconds ago must not be
  orderable, which rules out caching this response.
- A catalog outage blocks order creation. Correct behaviour rather than a flaw — accepting an
  order without knowing the basket is valid is worse than refusing it — but it means catalog
  needs the same availability posture as order.
- The response duplicates prices the client already fetched with the menu. Deliberate: the
  server's number is authoritative, and a client comparing them is a feature, since a mismatch
  means the menu changed mid-session.

## Alternatives considered

**Validate in the order service, reading catalog tables directly.** Rejected: it breaks schema
ownership and couples the two services at the database.

**Replicate the rules into the order service via events.** Rejected. The order service would need
a full read model of the catalogue kept eventually consistent, and "eventually" here means
accepting orders against a menu that is thirty seconds stale, which is precisely the failure mode
this endpoint exists to prevent.

**Validate on the client.** Rejected as a security boundary — a client is not one — but the
client *should* also validate, for immediate feedback. Both, with the server authoritative.

**A shared library holding the rules, imported by both.** Tempting, and would remove the network
call. Rejected because the rules need the data, so the order service would still need the rows;
a shared library would only move the coupling from HTTP to a package version, and reintroduce the
version-skew problem across services.
