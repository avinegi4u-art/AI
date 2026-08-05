# Event contracts

## The envelope

Every event on every topic uses one envelope. Consumers can route, log, deduplicate and
dead-letter messages without knowing anything about the payload; `data` is the only
event-type-specific part.

```json
{
  "event_id": "evt_01J9Z8XQF3K7M2P4R6T8V0W1Y3",
  "event_type": "order.created",
  "occurred_at": "2026-08-05T19:00:00Z",
  "aggregate_id": "ord_777",
  "aggregate_type": "Order",
  "version": 1,
  "correlation_id": "01J9Z8XQF3K7M2P4R6T8V0W1Y4",
  "causation_id": "evt_01J9Z8XQF3K7M2P4R6T8V0W1Y0",
  "data": { }
}
```

| Field | Why it exists |
|---|---|
| `event_id` | Consumer-side idempotency key. Recorded in `processed_events`. |
| `event_type` | Dotted type; its prefix determines the topic. |
| `occurred_at` | When the state change happened, not when it was published. Timezone-aware; naive timestamps are rejected. |
| `aggregate_id` | Also the Kafka partition key, which is what gives per-aggregate ordering. |
| `aggregate_type` | Lets a consumer filter without parsing the payload. |
| `version` | Schema version of `data`. Bump on a breaking payload change. |
| `correlation_id` | Propagated from the originating request, so one customer action can be followed across every service and event it caused. |
| `causation_id` | The `event_id` that caused this one, forming a causal chain for debugging. |

The envelope rejects unknown top-level fields, so a typo in event construction fails at the
boundary rather than becoming a silently-dropped field downstream.

## Topics

One topic per bounded context. Ordering guarantees are per-partition, and partitions are keyed
by `aggregate_id`, so all events for one order are ordered relative to each other — which the
order state machine and dispatch assignment both depend on. Events for different orders are
not ordered relative to each other, and nothing requires them to be.

| Topic | Owner | Event types |
|---|---|---|
| `identity.events` | auth, user | `user_registered`, `user_profile_updated`, `user_address_added`, `user_address_updated`, `user_address_deleted`, `user_logged_in`, `user_session_revoked` |
| `merchant.events` | merchant | `created`, `updated`, `status_changed`, `hours_updated` |
| `catalog.events` | catalog | `menu_published`, `item_created`, `item_updated`, `item_availability_changed` |
| `order.events` | order | `created`, `confirmed`, `preparing`, `ready_for_pickup`, `picked_up`, `in_transit`, `delivered`, `cancelled`, `failed`, `refunded` |
| `pricing.events` | pricing | `quote_issued`, `surge_updated` |
| `payment.events` | payment | `authorized`, `captured`, `failed`, `refunded` |
| `dispatch.events` | dispatch | `assignment_requested`, `assigned`, `reassigned`, `assignment_rejected`, `unassigned` |
| `tracking.events` | tracking | `location_updated`, `eta_updated` |
| `notification.events` | notification | `otp_requested`, `order_confirmed`, `courier_assigned`, `order_delivered`, `order_cancelled` |

Each topic has a `<topic>.dlq` companion for messages that exhausted their retry budget.

The mapping from event type to topic lives in one module (`marsool_core.events.topics`) and is
asserted by a contract test that walks every registered event type. Adding an event type
without registering its topic prefix fails in CI, not in production.

`notification.events` carries OTP codes in `notification.otp_requested`, which makes it
sensitive: short retention, restricted ACLs, and explicitly excluded from mirroring into the
analytics lake.

## Publishing: the transactional outbox

Publishing to a broker inside a database transaction is not atomic. The broker call can succeed
while the transaction rolls back, producing an event for a state change that never happened;
or the transaction can commit while the broker call fails, losing the event entirely. Both are
observable in production as data that disagrees with itself.

So services never publish directly. They write to an `event_outbox` table in the **same
transaction** as the state change:

```python
# Inside the request's transaction
order.status = OrderStatus.CONFIRMED
outbox.enqueue(session, order_confirmed(order_id=order.id))
# Both land, or neither does.
```

A relay then polls the outbox and publishes. It claims rows with
`FOR UPDATE SKIP LOCKED`, so multiple replicas can run concurrently without publishing the
same event twice. Failures increment an attempt counter with the error recorded; after eight
attempts the row is marked `DEAD_LETTERED` and an operator can requeue it after fixing the
cause.

In the MVP the relay runs in-process alongside the API. It moves to its own deployment once
event volume justifies scaling publication independently of request handling.

## Consuming: at-least-once, therefore idempotent

The outbox guarantees at-least-once delivery, so every consumer must tolerate seeing an event
twice. Two mechanisms, used together:

1. **Naturally idempotent handlers.** Upserts rather than inserts; state-machine transitions
   guarded by the current state, so applying `order.delivered` to an already-delivered order is
   a no-op.
2. **A deduplication table.** `processed_events(event_id, consumer_group)` records what a
   consumer group has applied. The claim is written **in the handler's own transaction**, which
   is what makes it safe: an event cannot be recorded as processed if its handler failed.

```python
async with session_factory() as session:
    if await idempotency.already_processed(session, envelope.event_id):
        return
    await handler(session, envelope)
    idempotency.mark_processed(session, envelope)
    await session.commit()
```

Handlers that keep failing are retried with bounded exponential backoff and then dead-lettered,
rather than blocking their partition forever. An unregistered event type is ignored, not an
error: a service subscribed to a topic will legitimately see events it does not care about.

## Versioning

Additive changes — a new optional field in `data` — do not bump `version`. Consumers must
tolerate unknown fields.

Breaking changes bump `version`, and the producer emits both versions until every consumer has
migrated. Consumers branch on `version` explicitly rather than inferring shape from the
presence of fields.

Removing an event type requires confirming no consumer group is still subscribed, which the
consumer-group metadata in Kafka answers directly.

## Local development

The `memory` event-bus backend is the default outside staging and production. It records
published events and invokes local subscribers synchronously, so the whole stack runs without
Kafka and tests can assert on exactly what was published:

```python
await runtime.outbox_relay.drain_once()
assert event_bus.events_of_type("identity.user_registered")[0].data["phone"] == "+971501234567"
```

Set `MARSOOL_EVENT_BUS_BACKEND=kafka` to exercise real cross-service event flow against the
Kafka container in the Compose stack.
