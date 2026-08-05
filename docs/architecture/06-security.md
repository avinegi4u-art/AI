# Security model

## Authentication

Phone plus one-time password. No passwords anywhere, which removes credential stuffing,
password reuse and reset flows as attack surface in a market where phone-based login is the
norm.

### The OTP flow, and what protects it

A six-digit code is only 10^6 possibilities. It is safe because three limits apply at once, and
each one alone would be insufficient:

1. **A short expiry.** Five minutes, so a captured code has a small window.
2. **A per-challenge attempt budget.** Five wrong guesses burn the challenge.
3. **A per-phone issuance rate limit.** Five codes an hour, so an attacker cannot simply
   request a fresh challenge to reset their attempt budget.

Codes are stored only as salted scrypt digests. scrypt rather than a single hash pass because
the input space is small enough to enumerate: a plain SHA-256 of a six-digit code is
effectively plaintext against a rainbow table.

**Failures are indistinguishable.** No pending challenge, an expired challenge, and a wrong
code all return `401 otp_invalid` with the same message. Distinguishing them would turn the
login endpoint into an account-enumeration oracle. Requesting an OTP likewise succeeds whether
or not the number is registered.

**The attempt counter is in Redis, not a database column.** A failed login rolls its
transaction back, which would discard a column increment and leave the budget permanently
unused — the counter would read zero forever and the cap would never fire. See
[ADR 0004](adr/0004-otp-attempt-counter-in-redis.md).

### Tokens

| | Access token | Refresh token |
|---|---|---|
| Lifetime | 15 minutes | 30 days |
| Verified by | Gateway **and** every service | Auth service only |
| Revocation | Expiry only | Immediate, database-backed |
| Reuse | Unlimited within its lifetime | Single-use; rotated on every use |

Access tokens are short-lived precisely because they are not revocable: the window between a
revocation and its taking effect is the token's remaining lifetime.

**Every service verifies the token itself.** The gateway also does, but a service never trusts
gateway-injected identity headers as authoritative. If the gateway is bypassed — a
misconfigured ingress, a pod reachable inside the cluster — no service becomes impersonatable.
The `X-Marsool-User-*` headers exist so logs and traces carry the identity without re-parsing
the JWT, and inbound copies of them are stripped so a client cannot claim to be someone else.

**Refresh rotation with reuse detection.** Each refresh revokes the presented token and issues
a successor, linked through `replaced_by_id`. Presenting a token that was already rotated
revokes the **entire session**, logging out both the attacker and the legitimate user. That
turns a stolen refresh token from silent long-term access into a detected incident with a
forced re-authentication. Revocation happens in its own transaction, because the request that
detects reuse raises, and its transaction is rolled back.

Refresh tokens are stored only as SHA-256 digests: a database leak must not yield usable
session credentials.

**Algorithms.** HS256 with a shared secret locally, because key distribution for local
development is friction with no benefit. RS256 in production: the auth service holds the
private key, verifiers hold only the public key, and a compromised service cannot mint tokens.
Both are implemented; the switch is configuration. `alg: none` is rejected — a test asserts a
forged unsigned token claiming `role: admin` is refused.

## Authorization

Four human roles — `customer`, `merchant`, `courier`, `admin` — plus `service` for
machine-to-machine callers.

Roles are coarse. Two finer mechanisms carry the real weight:

**Merchant scoping.** A merchant principal carries `merchant_ids`, and `can_act_for_merchant`
gates every storefront and catalogue mutation. Without it, any merchant user could edit any
merchant's prices. Tested from the attacker's side: a merchant user attempting to edit another
merchant's item gets `403`.

**Scopes for non-human callers.** A service principal holds an explicit scope list, so an AI
agent token can be limited to `catalog:read` without gaining write access. This is what makes
the Phase 2 agents safe to point at internal APIs: they get a narrowly-scoped service
principal, not an admin token.

Admins bypass role checks but not audit: every mutation is attributed to the acting principal.

Ownership is enforced in the query, not after it. `get_address` is scoped by `user_id`, so a
caller guessing another user's address identifier gets `404`, not `403` — which also avoids
confirming that the identifier exists.

## Rate limiting

Token-bucket, per identity, implemented as an atomic Lua script. Separate `GET`/`SET` calls
would let concurrent bursts slip through, which is exactly when a limiter matters.

| Scope | Default | Rationale |
|---|---|---|
| Authenticated (per user) | 120 per minute | Comfortable for a real client, useless for scraping |
| Anonymous (per IP) | 60 per minute | Coarser key, so a tighter bucket |
| OTP issuance (per phone) | 5 per hour | The main brute-force defence |
| OTP verification (per challenge) | 5 attempts | Caps a single challenge |

Limits are enforced at the gateway, so shed traffic never reaches a service. Rejections carry
`Retry-After`, because a client that cannot tell how long to wait will retry immediately and
make it worse.

## Input validation

Validation happens at the boundary, in Pydantic models, so no downstream code can receive
malformed data.

- Coordinates are range-checked in the schema *and* by a database `CHECK` constraint.
- Phone numbers are normalised to E.164 in the schema, so `0501234567` and `+971501234567`
  cannot become two accounts.
- Closed vocabularies for cuisines and dietary tags, rather than free text, so filters and the
  AI assistant agree on the same values.
- Every list field has a maximum length, so a request cannot ask the server to allocate
  unbounded memory.
- Pagination limits are bounded, so `limit=1000000` is a `422` rather than a slow query.

Protected fields are absent from update schemas rather than filtered afterwards: `role`,
`status`, `phone`, `commission_bps` and `rating` simply do not exist on the models a client can
submit. Tests confirm that submitting them changes nothing.

## Transport and headers

Baseline security headers on every response: `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, `Cross-Origin-Opener-Policy`, `Permissions-Policy`, plus HSTS in production.
CORS is an explicit origin allowlist, never a wildcard, because credentialed requests are
allowed.

## Secrets

From the environment, never from the repository. The one committed secret is the local
development JWT secret, which is inert by construction: production deployments use RS256 with
keys from a secrets manager, and the settings model rejects a secret shorter than 16
characters.

Logs redact by key name — `otp`, `token`, `password`, `secret`, `authorization`, `card_number`,
`cvv` and others are replaced before reaching the log sink, so a well-meaning
`logger.info("login", otp=code)` cannot leak a credential. Phone numbers are masked to
`+9715****4567` in logs.

## What is deliberately not built yet

Named so they are decisions rather than oversights:

- **Device binding and refresh-token pinning.** Would strengthen theft detection; needs a
  device-identity story on the mobile clients first.
- **Anomaly-based lockout.** Impossible-travel and velocity checks need the traffic history
  the analytics service will produce.
- **Field-level encryption of payout IBANs.** Required before couriers are onboarded at scale;
  the column exists and is empty.
- **PDPL erasure workflow.** Specified in [data stores](05-data-stores.md); implemented in
  Step 2 when there are orders to redact.
- **WAF and bot mitigation.** An edge concern, configured at the ingress rather than in
  application code.
