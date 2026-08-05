# Payment service

**Status:** planned for Step 2. Not yet implemented.

**Bounded context:** the `payments` schema.

Payment intents with authorize/capture/refund, an internal double-entry ledger, and merchant
payouts.

`Idempotency-Key` is mandatory on every mutating endpoint: a duplicated charge is the single
worst failure mode in the platform.

This directory exists so the service catalog in the documentation matches the repository, and so
the eventual implementation has an obvious home. It will follow the same internal shape as every
implemented service — see
[repository structure](../../docs/architecture/01-repo-structure.md#inside-a-service).
