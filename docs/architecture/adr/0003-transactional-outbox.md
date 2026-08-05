# ADR 0003: Transactional outbox for event publishing

- **Status**: Accepted
- **Date**: 2026-08-05

## Context

Services publish domain events when state changes: `order.confirmed` when a merchant accepts,
`identity.user_registered` when an account is created. Consumers act on those events — dispatch
starts looking for a courier, the user service provisions a profile.

The naive implementation writes the state change and publishes the event in the same handler:

```python
async with session.begin():
    order.status = OrderStatus.CONFIRMED
    await kafka.publish(order_confirmed(order))   # wrong
```

This is not atomic, and it fails in both directions:

- **Phantom event.** The publish succeeds, then the transaction rolls back — a deadlock, a
  constraint violation on a later write, a connection drop. Dispatch now hunts for a courier for
  an order that was never confirmed.
- **Lost event.** The transaction commits, then the publish fails. The order is confirmed and
  nobody is looking for a courier. The order sits there until a customer complains.

Both are silent. Neither shows up in an error rate. They show up as data that disagrees with
itself, days later, and are miserable to diagnose.

## Decision

Every service writes events to an `event_outbox` table **in the same transaction as the state
change**. A relay process polls the table and publishes.

```python
# One transaction, both writes
order.status = OrderStatus.CONFIRMED
outbox.enqueue(session, order_confirmed(order_id=order.id))
```

`enqueue` is deliberately not `async`: it only stages an ORM object. The write happens when the
caller's transaction commits, and that is precisely what makes it atomic with the state change.

The relay claims rows with `FOR UPDATE SKIP LOCKED`, so multiple replicas can run concurrently
without publishing the same event twice. Failures increment an attempt counter and record the
error; after eight attempts the row is marked `DEAD_LETTERED`, and `requeue_dead_letters` lets an
operator retry once the cause is fixed.

## Consequences

**The good**

- No phantom events. A rolled-back transaction takes its events with it. Directly tested:
  `test_rollback_discards_the_event`.
- No lost events. A committed event is durable in PostgreSQL and will be published eventually.
- Broker outages become latency, not data loss. Events queue in the outbox and drain when Kafka
  returns.
- The outbox is an audit log. Every event a service has ever emitted, with its payload and
  publication status, is queryable.

**The cost, and how it is paid**

*Delivery is at-least-once, so every consumer must be idempotent.* This is the real price. Two
mechanisms pay it:

1. Handlers are written to be naturally idempotent — upserts rather than inserts,
   state-machine transitions guarded by the current state.
2. A `processed_events(event_id, consumer_group)` table records what has been applied. The claim
   is written **in the handler's own transaction**, so an event cannot be marked processed if its
   handler failed.

*Publication latency is now polling latency.* Roughly one second at the default interval. Fine
for everything in the platform; if a path ever needs sub-100ms fan-out, the relay switches to
PostgreSQL `LISTEN/NOTIFY` to wake immediately on insert, keeping the poll as a safety net.

*Ordering is per-aggregate, not global.* The relay publishes in `created_at` order and Kafka
partitions by `aggregate_id`, so all events for one order stay ordered. Events for different
orders are not ordered relative to each other, and nothing requires them to be.

*Every service carries an extra table and a background task.* Both come from the shared library,
so the per-service cost is two lines in `models.py` and one in `runtime.py`.

## Alternatives considered

**Publish after commit.** Cheap and wrong. It removes phantom events but not lost ones: the
process can die between commit and publish. That window is small, which makes it worse — it will
happen rarely enough to be dismissed as a fluke.

**Change data capture (Debezium on the WAL).** Genuinely good, and how this would look at large
scale: no application-side table, no relay, exactly-once-ish semantics from the log. Rejected for
now because it makes Kafka Connect and the replication slot part of the critical path, and it
couples event payloads to table shapes rather than to explicit domain events. The outbox keeps
the event contract in application code where it can be versioned deliberately.

**Two-phase commit across PostgreSQL and Kafka.** Rejected. Kafka's transaction support does not
compose with a database transaction, and XA coordinators are an operational burden with a poor
failure story.

**Event sourcing.** Rejected as too large a commitment for an MVP. The outbox gives most of the
auditability benefit without making every read a projection.

## Implementation notes

The relay runs in-process alongside each service's API in the MVP, started as an asyncio task in
the lifespan. It moves to its own deployment when event volume justifies scaling publication
independently of request handling — the class takes a session factory and an event bus, so that
is a change of entrypoint, not of code.

Tests cover the properties that matter, not the mechanism: rollback discards, commit persists,
a second drain is a no-op, repeated failure dead-letters after exactly the retry budget, and a
requeued dead letter publishes successfully.
