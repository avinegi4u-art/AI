"""Auth domain logic.

All authentication rules live here rather than in the router, so they can be tested
directly and reused by non-HTTP entrypoints (admin tooling, support workflows).

Security properties this module is responsible for:

- OTP codes are never stored or logged in plaintext.
- OTP verification is bounded per challenge (attempt counter) *and* per phone number
  (rate limiter), so neither a single challenge nor a stream of new challenges can be
  brute-forced.
- Failure responses do not reveal whether a phone number is registered.
- Refresh tokens are single-use; presenting a revoked one revokes the whole session,
  which converts a stolen token into a detected incident rather than silent access.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marsool_auth.config import AuthSettings
from marsool_auth.events import otp_requested, session_revoked, user_logged_in, user_registered
from marsool_auth.models import (
    AuthPrincipal,
    OtpChallenge,
    PrincipalStatus,
    RefreshToken,
)
from marsool_auth.otp import OtpSender, generate_otp
from marsool_auth.schemas import (
    AuthenticatedUser,
    OtpChallengeResponse,
    SessionSummary,
    TokenPair,
)
from marsool_core.cache import AttemptCounter, RateLimiter
from marsool_core.config import Environment
from marsool_core.db.session import rowcount
from marsool_core.errors import AuthenticationError, BadRequestError, PermissionDeniedError
from marsool_core.events.outbox import OutboxRepository
from marsool_core.ids import PREFIX_REFRESH_TOKEN, new_id
from marsool_core.logging import get_logger
from marsool_core.phone import mask_phone
from marsool_core.security.hashing import hash_opaque_token, hash_otp, verify_otp
from marsool_core.security.jwt import (
    decode_refresh_token,
    encode_access_token,
    encode_refresh_token,
)
from marsool_core.security.principal import Principal, Role

logger = get_logger(__name__)

PREFIX_SESSION = "ses"
PREFIX_JTI = "jti"

# Deliberately identical for "no challenge", "expired" and "wrong code": a distinct
# message per case would let an attacker enumerate which phone numbers have pending
# challenges.
_GENERIC_OTP_FAILURE = "The code is invalid or has expired"


@dataclass(frozen=True, slots=True)
class ClientContext:
    """Best-effort client attribution recorded on a session."""

    user_agent: str | None = None
    ip_address: str | None = None


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A freshly minted token pair plus the internal identifiers behind it.

    The identifiers stay out of :class:`TokenPair` (clients have no use for them) but
    callers need them to emit events and to link the rotation chain.
    """

    pair: TokenPair
    session_id: str
    refresh_token_id: str


class AuthService:
    """Phone/OTP authentication and token lifecycle."""

    def __init__(
        self,
        *,
        settings: AuthSettings,
        otp_sender: OtpSender,
        outbox: OutboxRepository,
        session_factory: async_sessionmaker[AsyncSession],
        attempt_counter: AttemptCounter,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._settings = settings
        self._otp_sender = otp_sender
        self._outbox = outbox
        # Used only for writes that must survive the rollback of a failing request; see
        # ``_revoke_session_out_of_band``.
        self._session_factory = session_factory
        self._attempts = attempt_counter
        self._rate_limiter = rate_limiter

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    # ------------------------------------------------------------------ OTP challenges

    async def request_otp(self, session: AsyncSession, *, phone: str) -> OtpChallengeResponse:
        """Issue an OTP challenge for ``phone``.

        Succeeds whether or not the phone is registered: the response must not reveal
        account existence. Registration happens implicitly on first successful login.
        """
        if self._rate_limiter is not None:
            await self._rate_limiter.enforce(f"otp:{phone}")

        now = self._now()
        # Invalidate any outstanding challenge so only the newest code works. Without
        # this, requesting several codes would multiply the attacker's attempt budget.
        await session.execute(
            update(OtpChallenge)
            .where(OtpChallenge.phone == phone, OtpChallenge.consumed_at.is_(None))
            .values(consumed_at=now)
        )

        code = generate_otp(self._settings.otp_length)
        challenge = OtpChallenge(
            phone=phone,
            code_hash=hash_otp(code),
            max_attempts=self._settings.otp_max_attempts,
            expires_at=now + timedelta(seconds=self._settings.otp_ttl_seconds),
            created_at=now,
        )
        session.add(challenge)
        await session.flush()

        await self._otp_sender.send(
            phone=phone, code=code, ttl_seconds=self._settings.otp_ttl_seconds
        )
        self._outbox.enqueue(
            session,
            otp_requested(
                phone=phone,
                challenge_id=challenge.id,
                code=code,
                ttl_seconds=self._settings.otp_ttl_seconds,
                channel=self._otp_sender.channel,
            ),
        )
        logger.info("otp_challenge_issued", phone=mask_phone(phone), challenge_id=challenge.id)

        return OtpChallengeResponse(
            challenge_id=challenge.id,
            phone=phone,
            expires_at=challenge.expires_at,
            attempts_allowed=challenge.max_attempts,
            delivery_channel=self._otp_sender.channel,
            debug_code=code if self._may_echo_otp() else None,
        )

    def _may_echo_otp(self) -> bool:
        """Whether the OTP may be returned in the API response.

        Never in production, regardless of configuration: a misapplied environment
        variable must not be able to turn OTP into a no-op.
        """
        if self._settings.environment is Environment.PRODUCTION:
            return False
        return self._settings.otp_echo_in_response

    # -------------------------------------------------------------------------- login

    async def login(
        self,
        session: AsyncSession,
        *,
        phone: str,
        otp: str,
        client: ClientContext | None = None,
    ) -> TokenPair:
        """Verify an OTP and issue a token pair, registering the user if new."""
        now = self._now()
        challenge = (
            await session.execute(
                select(OtpChallenge)
                .where(OtpChallenge.phone == phone, OtpChallenge.consumed_at.is_(None))
                .order_by(OtpChallenge.created_at.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()

        if challenge is None or challenge.is_expired(now=now):
            raise AuthenticationError(_GENERIC_OTP_FAILURE, code="otp_invalid")

        if await self._attempts.is_exhausted(challenge.id):
            # The budget is spent, so even the correct code is refused: a new code must be
            # requested, and those requests are themselves rate limited per phone number.
            raise AuthenticationError(
                "Too many incorrect attempts; request a new code",
                code="otp_attempts_exhausted",
            )

        if not verify_otp(otp, challenge.code_hash):
            attempts = await self._attempts.register_failure(challenge.id)
            logger.info(
                "otp_verification_failed",
                phone=mask_phone(phone),
                challenge_id=challenge.id,
                attempts=attempts,
            )
            raise AuthenticationError(_GENERIC_OTP_FAILURE, code="otp_invalid")

        challenge.consumed_at = now
        await self._attempts.reset(challenge.id)
        principal_record = await self._get_or_register_principal(session, phone=phone, now=now)

        if not principal_record.is_active:
            raise PermissionDeniedError(
                "This account is not permitted to sign in",
                code="account_not_active",
                details={"status": principal_record.status.value},
            )

        principal_record.last_login_at = now
        await self._enforce_session_ceiling(session, principal_id=principal_record.id, now=now)

        issued = await self._issue_session(
            session, principal_record=principal_record, now=now, client=client
        )
        self._outbox.enqueue(
            session,
            user_logged_in(
                principal_id=principal_record.id,
                session_id=issued.session_id,
                role=principal_record.role,
                logged_in_at=now,
            ),
        )
        return issued.pair

    async def _get_or_register_principal(
        self, session: AsyncSession, *, phone: str, now: datetime
    ) -> AuthPrincipal:
        """Return the principal for ``phone``, creating it on first login."""
        existing = (
            await session.execute(select(AuthPrincipal).where(AuthPrincipal.phone == phone))
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        principal_record = AuthPrincipal(
            phone=phone,
            role=Role.CUSTOMER,
            status=PrincipalStatus.ACTIVE,
        )
        session.add(principal_record)
        await session.flush()
        self._outbox.enqueue(
            session,
            user_registered(
                principal_id=principal_record.id,
                phone=phone,
                role=principal_record.role,
                registered_at=now,
            ),
        )
        logger.info(
            "principal_registered", user_id=principal_record.id, phone=mask_phone(phone)
        )
        return principal_record

    # ------------------------------------------------------------------------ refresh

    async def refresh(
        self,
        session: AsyncSession,
        *,
        refresh_token: str,
        client: ClientContext | None = None,
    ) -> TokenPair:
        """Rotate a refresh token, returning a fresh pair.

        Presenting a token that was already rotated or revoked is treated as compromise:
        the entire session chain is revoked so the attacker and the legitimate user are
        both logged out, forcing re-authentication.
        """
        settings = self._settings
        claims = decode_refresh_token(
            refresh_token,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            algorithm=settings.jwt_algorithm,
            secret=settings.jwt_secret,
            public_key=settings.jwt_public_key,
        )
        now = self._now()
        token_hash = hash_opaque_token(refresh_token)

        # Deliberately unlocked: reuse detection has to revoke the session in its own
        # transaction (the request's transaction rolls back when this method raises), and
        # holding a row lock here would deadlock against that write.
        stored = (
            await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
        ).scalar_one_or_none()

        if stored is None:
            raise AuthenticationError("Refresh token is not recognised", code="token_invalid")

        if stored.is_revoked:
            await self._revoke_session_out_of_band(
                principal_id=stored.principal_id,
                session_id=stored.session_id,
                reason="reuse_detected",
            )
            raise AuthenticationError(
                "Session has been revoked; please sign in again", code="token_reused"
            )

        if stored.is_expired(now=now):
            raise AuthenticationError("Refresh token has expired", code="token_expired")

        # Re-read under a row lock and re-check: two concurrent refreshes with the same
        # token must not both rotate it. The loser is rejected without escalating to a
        # session revocation, since a concurrent double-submit is usually a client retry
        # rather than theft — and a genuinely stolen token trips the branch above on its
        # next use.
        stored = (
            await session.execute(
                select(RefreshToken)
                .where(RefreshToken.id == stored.id, RefreshToken.revoked_at.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if stored is None:
            raise AuthenticationError(
                "Session has been revoked; please sign in again", code="token_reused"
            )

        principal_record = (
            await session.execute(
                select(AuthPrincipal).where(AuthPrincipal.id == stored.principal_id)
            )
        ).scalar_one_or_none()
        if principal_record is None or not principal_record.is_active:
            raise PermissionDeniedError(
                "This account is not permitted to sign in", code="account_not_active"
            )

        issued = await self._issue_session(
            session,
            principal_record=principal_record,
            now=now,
            client=client,
            session_id=claims.session_id,
        )
        stored.revoked_at = now
        stored.revoked_reason = "rotated"
        stored.replaced_by_id = issued.refresh_token_id
        return issued.pair

    # ------------------------------------------------------------------------- logout

    async def logout(
        self,
        session: AsyncSession,
        *,
        principal: Principal,
        refresh_token: str | None,
        all_sessions: bool,
    ) -> int:
        """Revoke one session or every session for the principal.

        Returns the number of refresh tokens revoked.
        """
        now = self._now()
        if all_sessions:
            revoked = await self._revoke_all_sessions(
                session, principal_id=principal.id, reason="logout_all", now=now
            )
            return revoked

        if refresh_token is None:
            raise BadRequestError(
                "Provide refresh_token or set all_sessions=true", code="logout_target_missing"
            )
        stored = (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == hash_opaque_token(refresh_token)
                )
            )
        ).scalar_one_or_none()
        if stored is None:
            # Idempotent: logging out an unknown or already-revoked token is a success.
            return 0
        if stored.principal_id != principal.id:
            raise PermissionDeniedError(
                "Refresh token does not belong to the authenticated user",
                code="token_owner_mismatch",
            )
        return await self._revoke_session(
            session,
            principal_id=stored.principal_id,
            session_id=stored.session_id,
            reason="logout",
            now=now,
        )

    async def list_sessions(
        self, session: AsyncSession, *, principal: Principal
    ) -> list[SessionSummary]:
        """List the principal's active sessions, newest first."""
        now = self._now()
        rows = (
            (
                await session.execute(
                    select(RefreshToken)
                    .where(
                        RefreshToken.principal_id == principal.id,
                        RefreshToken.revoked_at.is_(None),
                        RefreshToken.expires_at > now,
                    )
                    .order_by(RefreshToken.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [
            SessionSummary(
                session_id=row.session_id,
                created_at=row.created_at,
                expires_at=row.expires_at,
                user_agent=row.user_agent,
                ip_address=row.ip_address,
                is_current=row.session_id == principal.session_id,
            )
            for row in rows
        ]

    # ------------------------------------------------------------------------ helpers

    async def _issue_session(
        self,
        session: AsyncSession,
        *,
        principal_record: AuthPrincipal,
        now: datetime,
        client: ClientContext | None,
        session_id: str | None = None,
    ) -> IssuedSession:
        """Mint an access/refresh pair and persist the refresh token."""
        settings = self._settings
        resolved_session_id = session_id or new_id(PREFIX_SESSION)
        principal = Principal(
            id=principal_record.id,
            role=principal_record.role,
            session_id=resolved_session_id,
        )

        access_token, access_expires_at = encode_access_token(
            principal,
            jwt_id=new_id(PREFIX_JTI),
            ttl_seconds=settings.access_token_ttl_seconds,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            algorithm=settings.jwt_algorithm,
            secret=settings.jwt_secret,
            private_key=settings.jwt_private_key,
            now=int(now.timestamp()),
        )
        refresh_token_id = new_id(PREFIX_REFRESH_TOKEN)
        refresh_token, refresh_expires_at = encode_refresh_token(
            subject=principal_record.id,
            session_id=resolved_session_id,
            jwt_id=refresh_token_id,
            ttl_seconds=settings.refresh_token_ttl_seconds,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            algorithm=settings.jwt_algorithm,
            secret=settings.jwt_secret,
            private_key=settings.jwt_private_key,
            now=int(now.timestamp()),
        )

        session.add(
            RefreshToken(
                id=refresh_token_id,
                principal_id=principal_record.id,
                session_id=resolved_session_id,
                token_hash=hash_opaque_token(refresh_token),
                expires_at=datetime.fromtimestamp(refresh_expires_at, tz=UTC),
                user_agent=(client.user_agent if client else None),
                ip_address=(client.ip_address if client else None),
                created_at=now,
            )
        )
        await session.flush()

        return IssuedSession(
            pair=TokenPair(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=access_expires_at - int(now.timestamp()),
                user=AuthenticatedUser(
                    id=principal_record.id,
                    role=principal_record.role,
                    name=principal_record.display_name,
                    phone=principal_record.phone,
                ),
            ),
            session_id=resolved_session_id,
            refresh_token_id=refresh_token_id,
        )

    async def _revoke_session(
        self,
        session: AsyncSession,
        *,
        principal_id: str,
        session_id: str,
        reason: str,
        now: datetime,
    ) -> int:
        result = await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.session_id == session_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_reason=reason)
        )
        revoked = rowcount(result)
        if revoked:
            self._outbox.enqueue(
                session,
                session_revoked(
                    principal_id=principal_id,
                    session_ids=[session_id],
                    reason=reason,
                    revoked_at=now,
                ),
            )
        return revoked

    async def _revoke_session_out_of_band(
        self, *, principal_id: str, session_id: str, reason: str
    ) -> int:
        """Revoke a session in its own committed transaction.

        Used on failure paths: the caller is about to raise, which rolls the request's
        transaction back, so a revocation written there would be silently discarded.
        """
        now = self._now()
        async with self._session_factory() as autonomous:
            try:
                revoked = await self._revoke_session(
                    autonomous,
                    principal_id=principal_id,
                    session_id=session_id,
                    reason=reason,
                    now=now,
                )
                await autonomous.commit()
            except Exception:
                await autonomous.rollback()
                raise
        logger.warning(
            "refresh_token_reuse_detected",
            user_id=principal_id,
            session_id=session_id,
            revoked_tokens=revoked,
        )
        return revoked

    async def _revoke_all_sessions(
        self, session: AsyncSession, *, principal_id: str, reason: str, now: datetime
    ) -> int:
        session_ids = list(
            (
                await session.execute(
                    select(RefreshToken.session_id)
                    .where(
                        RefreshToken.principal_id == principal_id,
                        RefreshToken.revoked_at.is_(None),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if not session_ids:
            return 0
        result = await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.principal_id == principal_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now, revoked_reason=reason)
        )
        self._outbox.enqueue(
            session,
            session_revoked(
                principal_id=principal_id,
                session_ids=session_ids,
                reason=reason,
                revoked_at=now,
            ),
        )
        return rowcount(result)

    async def _enforce_session_ceiling(
        self, session: AsyncSession, *, principal_id: str, now: datetime
    ) -> None:
        """Revoke the oldest sessions once the per-user ceiling is reached.

        Bounds the blast radius of credential sharing and keeps the token table from
        growing without limit for a single account.
        """
        active = list(
            (
                await session.execute(
                    select(RefreshToken)
                    .where(
                        RefreshToken.principal_id == principal_id,
                        RefreshToken.revoked_at.is_(None),
                        RefreshToken.expires_at > now,
                    )
                    .order_by(RefreshToken.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        # One slot is reserved for the session about to be created.
        overflow = active[self._settings.max_active_sessions - 1 :]
        for token in overflow:
            token.revoked_at = now
            token.revoked_reason = "session_limit"
