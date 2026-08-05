# Pricing service

**Status:** planned for Step 2. Not yet implemented.

**Bounded context:** the `pricing` schema.

Delivery fees, service fees, tax, suggested tips and surge, plus courier pay rules as the
same computation from the other side.

Quotes are persisted and referenced by id at order creation, so the price a customer saw is
the price they are charged even if surge moves in between.

This directory exists so the service catalog in the documentation matches the repository, and so
the eventual implementation has an obvious home. It will follow the same internal shape as every
implemented service — see
[repository structure](../../docs/architecture/01-repo-structure.md#inside-a-service).
