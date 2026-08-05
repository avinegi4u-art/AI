# Dispatch service

**Status:** planned for Step 3. Not yet implemented.

**Bounded context:** the `dispatch` schema.

Courier matching, batching and assignment state.

Starts with a transparent weighted scoring function rather than a black box: when a courier
asks why they did not get an order, there has to be an answer. A VRP solver follows once
batching density justifies it.

This directory exists so the service catalog in the documentation matches the repository, and so
the eventual implementation has an obvious home. It will follow the same internal shape as every
implemented service — see
[repository structure](../../docs/architecture/01-repo-structure.md#inside-a-service).
