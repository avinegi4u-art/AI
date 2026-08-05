# Order lifecycle

The order state machine is the spine of the platform: pricing, payment, dispatch, tracking and
notifications all hang off its transitions. This document specifies it before it is built, so
Step 2 has a target rather than a discovery exercise.

## States

```
                    ┌─────────┐
                    │ CREATED │  basket validated, payment authorized, merchant not yet accepted
                    └────┬────┘
           ┌─────────────┼─────────────┐
           ▼             ▼             ▼
     ┌───────────┐  ┌─────────┐  ┌────────┐
     │ CONFIRMED │  │CANCELLED│  │ FAILED │
     └─────┬─────┘  └────┬────┘  └────────┘
           ▼             │            payment authorization failed, or
     ┌───────────┐       │            no courier could be assigned
     │ PREPARING │       │
     └─────┬─────┘       │
           ▼             │
  ┌──────────────────┐   │
  │ READY_FOR_PICKUP │   │
  └────────┬─────────┘   │
           ▼             │
     ┌───────────┐       │  cancellation is possible until PICKED_UP;
     │ PICKED_UP │       │  after that the food is en route and the
     └─────┬─────┘       │  resolution is a refund, not a cancellation
           ▼             │
    ┌────────────┐       │
    │ IN_TRANSIT │       │
    └─────┬──────┘       │
          ▼              ▼
   ┌───────────┐    ┌──────────┐
   │ DELIVERED │───▶│ REFUNDED │  full or partial, after the fact
   └───────────┘    └──────────┘
```

## Transition table

| From | To | Trigger | Side effects |
|---|---|---|---|
| — | `CREATED` | `POST /orders` | Basket validated against catalog, quote consumed, payment authorized. Emits `order.created`. |
| `CREATED` | `CONFIRMED` | Merchant accepts | Emits `order.confirmed` and `dispatch.assignment_requested`; notification to customer. |
| `CREATED` | `CANCELLED` | Customer cancels, or merchant rejects | Authorization voided (never captured). Emits `order.cancelled`. |
| `CREATED` | `FAILED` | Authorization declined, or merchant acceptance times out | Emits `order.failed`. |
| `CONFIRMED` | `PREPARING` | Merchant starts cooking | Emits `order.preparing`. Prep-time estimate refined. |
| `PREPARING` | `READY_FOR_PICKUP` | Merchant marks ready | Emits `order.ready_for_pickup`; courier notified. |
| `READY_FOR_PICKUP` | `PICKED_UP` | Courier confirms pickup | Payment captured. Emits `order.picked_up`. |
| `PICKED_UP` | `IN_TRANSIT` | First location update after pickup | Emits `order.in_transit`; live tracking opens for the customer. |
| `IN_TRANSIT` | `DELIVERED` | Courier confirms delivery | Emits `order.delivered`; courier payout accrued; rating prompt. |
| `IN_TRANSIT` | `FAILED` | Undeliverable (customer unreachable, address wrong) | Emits `order.failed`; support workflow opened. |
| `CONFIRMED`…`READY_FOR_PICKUP` | `CANCELLED` | Customer or operations cancels | Refund if captured; merchant compensation rules apply. Emits `order.cancelled`. |
| `DELIVERED` or `CANCELLED` | `REFUNDED` | Refund issued | Emits `order.refunded`. |

Terminal states: `DELIVERED`, `CANCELLED`, `FAILED`, `REFUNDED`.

## Design commitments

**The machine is explicit and total.** Allowed transitions live in one table, and any
transition not in it raises a `409 Conflict` with the current state in the error details. No
`if status == ...` scattered across handlers.

**Transitions are idempotent.** Applying `DELIVERED` to an already-delivered order succeeds
without side effects. This is required, not merely convenient: events are delivered
at-least-once, and a courier's phone will retry on a flaky connection.

**Every transition is recorded.** An append-only `order_status_history` row captures the
from-state, to-state, actor, reason and timestamp. Disputes are about who changed what and
when, and reconstructing that from mutable columns is not possible.

**Money follows the food.** Authorize at creation, capture at pickup. Capturing earlier means
refunding every merchant rejection; capturing later means delivering food that was never paid
for. Pickup is the point at which the merchant has incurred real cost.

**Cancellation and refund are different things.** Before pickup, an authorization is voided
and no money moves. After pickup, the resolution is a refund with its own accounting. Modelling
both as "cancelled" would make the ledger unreconcilable.

## Concurrency

Two updates to one order can race — a merchant marking ready while a customer cancels. The
transition is performed with a conditional update:

```sql
UPDATE orders.orders
   SET status = :to_status, updated_at = now()
 WHERE id = :order_id AND status = :expected_from_status
```

Zero rows affected means someone else moved first; the caller re-reads and either accepts the
new state as equivalent or returns a conflict. This is optimistic concurrency without a version
column, using the state itself as the version — which works precisely because transitions are
constrained.

Because Kafka partitions are keyed by `aggregate_id`, events for one order are consumed in
order, so a consumer never sees `delivered` before `picked_up`.

## Timeouts

Several transitions are driven by a clock rather than an actor, and each needs an owner:

| Timeout | Duration | Action |
|---|---|---|
| Merchant acceptance | 3 minutes from `CREATED` | Auto-cancel, void authorization, notify customer, flag the merchant |
| Courier assignment | 5 minutes from `CONFIRMED` | Escalate: widen the search radius, raise courier incentive, then alert operations |
| Pickup | 15 minutes past `READY_FOR_PICKUP` | Reassign the courier |
| Delivery | 2x the promised ETA | Open a support case proactively, before the customer complains |

These are scheduled jobs reading order state, not in-memory timers: an in-memory timer dies
with its process, and an order stuck in `CREATED` because a pod restarted is a customer whose
money is held and whose food never comes.

## What Step 2 builds

1. `services/order` — the schema, the state machine as a tested pure function, and the
   endpoints (`POST /orders`, `GET /orders/{id}`, `GET /orders`, `POST /orders/{id}/cancel`,
   plus merchant transitions).
2. `services/pricing` — `POST /pricing/quote`, persisted quotes referenced by id so the price
   a customer saw is the price they are charged.
3. `services/payment` — payment intents with authorize/capture/refund, an internal
   double-entry ledger, and mandatory `Idempotency-Key` on every mutating endpoint.
4. The integration test that matters: create → pay → confirm → prepare → ready → pick up →
   deliver, asserting the state history and every emitted event.
