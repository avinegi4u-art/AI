# Analytics service

**Status:** planned for Step 5. Not yet implemented.

**Bounded context:** the `warehouse` schema.

Consumes every topic and builds the read models behind operational dashboards, merchant
reporting and ML feature extraction.

Write-only from the platform's perspective: nothing in the request path depends on it.

This directory exists so the service catalog in the documentation matches the repository, and so
the eventual implementation has an obvious home. It will follow the same internal shape as every
implemented service — see
[repository structure](../../docs/architecture/01-repo-structure.md#inside-a-service).
