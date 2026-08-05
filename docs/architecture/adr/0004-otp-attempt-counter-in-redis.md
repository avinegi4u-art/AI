# ADR 0004: OTP attempt counter in Redis, not a database column

- **Status**: Accepted
- **Date**: 2026-08-05

## Context

A six-digit OTP has 10^6 possible values. Without a cap on verification attempts, an attacker who
can trigger a code for a known phone number can enumerate the space. Capping attempts per
challenge is therefore not a nicety; it is the control that makes a short code safe.

The obvious implementation is a column:

```python
class OtpChallenge(Base):
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
```

```python
if not verify_otp(otp, challenge.code_hash):
    challenge.attempts += 1
    raise AuthenticationError("The code is invalid or has expired")
```

That is what this repository shipped first, and it does not work.

## The bug

The platform gives every request a transactional session that commits on success and **rolls
back on any exception**. That invariant is valuable: a failed request cannot leave a
half-applied state change or a stray outbox row.

But a wrong OTP raises. So the transaction rolls back, and the increment is discarded. The
counter reads zero forever, and the cap never fires.

Discovered by a test that made six wrong guesses and expected the sixth to be refused:

```
FAILED test_attempts_are_capped_per_challenge
    assert 'otp_invalid' == 'otp_attempts_exhausted'
```

Worth noting: the code looked correct in review, the test suite would have been green without
that specific test, and the resulting hole — unlimited guesses against a six-digit code — is
severe.

## Decision

The verification attempt counter lives in Redis, incremented outside the request's transaction:

```python
if await self._attempts.is_exhausted(challenge.id):
    raise AuthenticationError("Too many incorrect attempts; request a new code",
                              code="otp_attempts_exhausted")
if not verify_otp(otp, challenge.code_hash):
    attempts = await self._attempts.register_failure(challenge.id)
    raise AuthenticationError(_GENERIC_OTP_FAILURE, code="otp_invalid")
```

`AttemptCounter` is a fixed-window counter (`INCR` plus `EXPIRE`, in a pipeline) keyed by
challenge id, with a TTL equal to the challenge lifetime.

The `attempts` column was removed. `max_attempts` stays, so the policy in force when a challenge
was issued is recorded even if configuration changes later.

## It fails closed, unlike the rest of the cache layer

Caching and rate limiting in this platform fail **open**: if Redis is unreachable, a cache read
misses and a rate-limit check allows. A Redis outage must not become a platform outage.

`AttemptCounter` fails **closed**. If Redis is unreachable, the budget is reported as exhausted
and logins are refused.

That is the opposite trade-off, and it is intentional. Failing open here means unlimited OTP
guesses for the duration of the outage, which is a credential-stuffing window. Refusing logins
is a visible, bounded, recoverable degradation; an account takeover is not.

The consequence is that Redis becomes a hard dependency for login. That is named explicitly
rather than discovered later: Redis needs the same availability posture as PostgreSQL.

## Consequences

**Good**

- The cap actually works. The correct code is refused too once the budget is spent, so a
  challenge cannot be salvaged after being attacked.
- A high-churn counter is out of the transactional database, where it would have generated row
  updates and WAL for every wrong guess.
- The TTL expires the counter automatically; no cleanup job.

**Bad, and accepted**

- Redis is a hard dependency for login.
- The counter is not durable. A Redis restart resets budgets. Bounded by the per-phone issuance
  limit (five codes an hour), which is still enforced, so the practical attempt ceiling survives.
- Two stores now hold parts of one challenge's state. Mitigated by the counter being keyed by
  challenge id and expiring with the challenge, so there is nothing to reconcile.

## The general rule this establishes

**A write that must survive the failure of its own request cannot live in that request's
transaction.**

Two places in this codebase are subject to that rule, and both are documented at the call site:

1. This counter — moved to Redis.
2. Refresh-token reuse detection, which must revoke a session while returning `401`. That one
   stays in PostgreSQL because the revocation must be durable, so it uses an explicit autonomous
   transaction (`_revoke_session_out_of_band`) and takes care not to hold a lock that would
   deadlock against itself.

A tempting third option was rejected: making the session dependency commit on `AppError` and roll
back only on unexpected exceptions. It would have fixed both cases in one line, and it would have
quietly weakened the "a failed request changes nothing" invariant for every future service. Two
explicit, documented exceptions are better than one implicit rule nobody remembers.
