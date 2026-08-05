# ADR 0006: Money as integer minor units

- **Status**: Accepted
- **Date**: 2026-08-05

## Context

The platform handles item prices, option surcharges, delivery fees, service fees, VAT, tips,
merchant commission, courier pay and refunds. Every one of those is money, and every one is
either summed, multiplied by a quantity, or scaled by a rate.

Getting the representation wrong produces errors that are individually tiny and collectively
unreconcilable — the kind of bug found by a finance team six months later, in aggregate, with no
way to identify which orders were affected.

## Decision

**Integer minor units** in the database (fils for AED, cents for USD), **decimal strings** on the
wire, `Decimal` in application code. Never `float`, anywhere, for any monetary value.

```python
to_minor_units("24.50", "AED")      # 2450
from_minor_units(2450, "AED")       # Decimal('24.50')
format_amount(2450, "AED")          # '24.50'
apply_basis_points(10_000, 500)     # 500  (5% commission)
```

Currency exponents are declared explicitly:

```python
CURRENCY_EXPONENTS = {"AED": 2, "SAR": 2, "USD": 2, "KWD": 3, "BHD": 3, "OMR": 3, "JPY": 0}
```

An unregistered currency raises `UnsupportedCurrencyError` rather than defaulting to two decimal
places. Adding a market is a deliberate edit.

## Rationale

**Floats cannot represent money.** `0.1 + 0.2 == 0.30000000000000004`. Summing a basket of
floats accumulates error that shows up as a total that does not match its lines.

**Integers make arithmetic exact and cheap.** Summing, multiplying by a quantity, and splitting
between merchant, courier and platform are all exact integer operations. There is no rounding
until a rate is applied.

**Rounding is half-up, explicitly.** Python's `Decimal` defaults to banker's rounding
(`ROUND_HALF_EVEN`), under which `24.505` becomes `24.50`. Finance expects `24.51`. That
mismatch is small per transaction and systematic across millions, so it is pinned with an
explicit `ROUND_HALF_UP` and a test.

**Rates are basis points, not floats.** Commission is `1_500` (15.00%), not `0.15`. An integer
rate stored in an integer column has no representation error, and `apply_basis_points` does the
rounding in one audited place.

**Exponents must be explicit because they are not all two.** The Kuwaiti dinar, Bahraini dinar
and Omani rial use three decimal places, and Gulf expansion is on the roadmap. A hardcoded `100`
multiplier would under-charge by a factor of ten in Kuwait — and it would do so silently.

## Why decimal strings on the wire

`{"price": "38.00"}` rather than `{"price": 38.00}` or `{"price_minor": 3800}`.

A JSON number is a double in most parsers, including JavaScript's `JSON.parse`. Sending `38.00`
as a number invites a client to reintroduce float error the moment it does arithmetic. A string
forces a deliberate decision, and every sensible client library parses it into a decimal type.

Minor units on the wire were the other candidate and were rejected as a usability trap: `3800`
requires every client to know the currency's exponent to render it, and a client that assumes two
decimals gets Kuwait wrong in exactly the way this decision exists to prevent.

## Consequences

**Good**

- Exact arithmetic. Line totals always sum to the basket total.
- One place where rounding happens, with a stated convention and a test.
- Three-decimal currencies work without special cases.
- No float in any monetary path, enforceable by review because the helpers are the only way to
  convert.

**Bad, and accepted**

- Conversion at the boundary is boilerplate: `to_minor_units` on write, `from_minor_units` on
  read. Confined to the service layer, and the alternative is worse.
- Two representations of one value means two chances to forget a conversion. Mitigated by naming
  the columns `*_minor` so a raw `price_minor` reaching a response is visible in review.
- `Decimal` is slower than `float`. Irrelevant: these are tens of operations per request, not
  millions.

## What is not decided here

**Multi-currency orders.** A single order is priced in one currency, the merchant's. Cross-border
ordering is not a use case.

**Foreign-exchange conversion.** No conversion happens anywhere yet. When it does — most likely
for a courier paid in one currency for an order priced in another — the rate must be captured on
the transaction, not looked up at read time, or historical totals will change retroactively.

**Tax calculation.** UAE VAT at 5% is a basis-point rate, so the mechanism is in place. Which
components are taxable, and whether delivery fees are taxed at the same rate, is a pricing
question settled in Step 2.
