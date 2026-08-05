# Web

**Status:** planned for Step 4. Not yet implemented.

Next.js with TypeScript and Tailwind, covering two surfaces:

- **Customer app** — App Router with server-side rendering for merchant and menu pages, which are
  the SEO surface.
- **Admin console** — live order board, merchant management, courier oversight, manual dispatch
  override.

The API client is generated from the committed OpenAPI documents in
[`docs/openapi/`](../docs/openapi/) rather than hand-written, so a contract change breaks the
build instead of production.
