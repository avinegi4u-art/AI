"""ORM models for the ``auth`` schema."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from marsool_auth import SCHEMA
from marsool_core.db.base import TimestampMixin, metadata_for_schema
from marsool_core.db.types import StringEnum, enum_values
from marsool_core.events.consumer import build_processed_events_model
from marsool_core.events.outbox import build_outbox_model
from marsool_core.ids import (
    PREFIX_OTP,
    PREFIX_REFRESH_TOKEN,
    PREFIX_USER,
    new_id,
)
from marsool_core.security.principal import Role


class Base(DeclarativeBase):
    """Declarative base bound to the ``auth`` schema."""

    metadata = metadata_for_schema(SCHEMA)


class PrincipalStatus(StrEnum):
    """Account status. Only ACTIVE principals may obtain tokens."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class AuthPrincipal(Base, TimestampMixin):
    """An authenticatable account, keyed by phone number.

    ``display_name`` is a read model maintained from ``identity.user_profile_updated``
    events so that a login response can include the user's name without a synchronous
    call into the user service.
    """

    __tablename__ = "principals"
    __table_args__ = (
        CheckConstraint(f"role IN {enum_values(Role)}", name="role_valid"),
        CheckConstraint(f"status IN {enum_values(PrincipalStatus)}", name="status_valid"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_USER)
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    role: Mapped[Role] = mapped_column(StringEnum(Role), nullable=False, default=Role.CUSTOMER)
    status: Mapped[PrincipalStatus] = mapped_column(
        StringEnum(PrincipalStatus), nullable=False, default=PrincipalStatus.ACTIVE
    )
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_active(self) -> bool:
        return self.status is PrincipalStatus.ACTIVE


class OtpChallenge(Base):
    """A pending one-time-password challenge.

    The code is stored only as a salted scrypt digest. The verification attempt counter
    deliberately lives in Redis, not here: a failed login rolls its transaction back,
    which would discard a column increment (see
    :class:`marsool_core.cache.AttemptCounter`). ``max_attempts`` is persisted so the
    policy in force when the challenge was issued is recorded even if config changes.
    """

    __tablename__ = "otp_challenges"
    __table_args__ = (
        Index("ix_otp_challenges_phone_active", "phone", "consumed_at", "expires_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_OTP)
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(160), nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    def is_expired(self, *, now: datetime) -> bool:
        return self.expires_at <= now

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None


class RefreshToken(Base):
    """A refresh token in a session's rotation chain.

    Rotation semantics: each refresh revokes the presented token and issues a successor,
    linked through ``replaced_by_id``. Presenting an already-revoked token indicates
    either replay or theft, and revokes the entire session (see
    ``AuthService.refresh``).
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        Index("ix_refresh_tokens_session_active", "session_id", "revoked_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True, default=lambda: new_id(PREFIX_REFRESH_TOKEN)
    )
    principal_id: Mapped[str] = mapped_column(
        String(40), ForeignKey(f"{SCHEMA}.principals.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # SHA-256 digest: a database leak must not yield usable session credentials.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    replaced_by_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    def is_expired(self, *, now: datetime) -> bool:
        return self.expires_at <= now

    def is_usable(self, *, now: datetime) -> bool:
        return not self.is_revoked and not self.is_expired(now=now)


EventOutbox = build_outbox_model(Base)
ProcessedEvent = build_processed_events_model(Base)
