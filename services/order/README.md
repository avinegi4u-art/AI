# Order service

**Status:** planned for Step 2. Not yet implemented.

**Bounded context:** the `orders` schema.

Order lifecycle and state machine. Composes catalogue validation, a pricing quote and a
payment authorization into an order, then drives it through its states in response to
merchant, courier and payment events.

See [order lifecycle](../../docs/architecture/04-order-lifecycle.md) for the state
machine, transition table, concurrency approach and timeouts this service implements.

This directory exists so the service catalog in the documentation matches the repository, and so
the eventual implementation has an obvious home. It will follow the same internal shape as every
implemented service — see
[repository structure](../../docs/architecture/01-repo-structure.md#inside-a-service).
