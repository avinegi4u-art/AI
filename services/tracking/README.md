# Tracking service

**Status:** planned for Step 3. Not yet implemented.

**Bounded context:** the `tracking` schema.

Courier location ingestion, ETA computation and WebSocket streams to customers.

Last-known position in Redis for the live map; full history in TimescaleDB, not the
transactional database — see [data stores](../../docs/architecture/05-data-stores.md).

This directory exists so the service catalog in the documentation matches the repository, and so
the eventual implementation has an obvious home. It will follow the same internal shape as every
implemented service — see
[repository structure](../../docs/architecture/01-repo-structure.md#inside-a-service).
