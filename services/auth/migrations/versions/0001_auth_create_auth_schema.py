"""Create the auth schema: principals, OTP challenges, refresh tokens, event outbox.

Revision ID: 0001_auth
Revises:
Create Date: 2026-08-05 05:24:21.621071
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001_auth'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('event_outbox',
    sa.Column('event_id', sa.String(length=40), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('aggregate_id', sa.String(length=40), nullable=False),
    sa.Column('aggregate_type', sa.String(length=40), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('correlation_id', sa.String(length=64), nullable=True),
    sa.Column('causation_id', sa.String(length=40), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('event_id', name=op.f('pk_event_outbox')),
    schema='auth'
    )
    op.create_index(op.f('ix_event_outbox_aggregate_id'), 'event_outbox', ['aggregate_id'], unique=False, schema='auth')
    op.create_index(op.f('ix_event_outbox_created_at'), 'event_outbox', ['created_at'], unique=False, schema='auth')
    op.create_index(op.f('ix_event_outbox_status'), 'event_outbox', ['status'], unique=False, schema='auth')
    op.create_table('otp_challenges',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('code_hash', sa.String(length=160), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_otp_challenges')),
    schema='auth'
    )
    op.create_index(op.f('ix_otp_challenges_created_at'), 'otp_challenges', ['created_at'], unique=False, schema='auth')
    op.create_index(op.f('ix_otp_challenges_phone'), 'otp_challenges', ['phone'], unique=False, schema='auth')
    op.create_index('ix_otp_challenges_phone_active', 'otp_challenges', ['phone', 'consumed_at', 'expires_at'], unique=False, schema='auth')
    op.create_table('principals',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=False),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('customer', 'merchant', 'courier', 'admin', 'service')", name=op.f('ck_principals_role_valid')),
    sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'DELETED')", name=op.f('ck_principals_status_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_principals')),
    schema='auth'
    )
    op.create_index(op.f('ix_principals_created_at'), 'principals', ['created_at'], unique=False, schema='auth')
    op.create_index(op.f('ix_principals_phone'), 'principals', ['phone'], unique=True, schema='auth')
    op.create_table('processed_events',
    sa.Column('event_id', sa.String(length=40), nullable=False),
    sa.Column('consumer_group', sa.String(length=80), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('event_id', 'consumer_group', name=op.f('pk_processed_events')),
    schema='auth'
    )
    op.create_table('refresh_tokens',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('principal_id', sa.String(length=40), nullable=False),
    sa.Column('session_id', sa.String(length=40), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_reason', sa.String(length=40), nullable=True),
    sa.Column('replaced_by_id', sa.String(length=40), nullable=True),
    sa.Column('user_agent', sa.String(length=200), nullable=True),
    sa.Column('ip_address', sa.String(length=45), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['principal_id'], ['auth.principals.id'], name=op.f('fk_refresh_tokens_principal_id_principals'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('token_hash', name='uq_refresh_tokens_token_hash'),
    schema='auth'
    )
    op.create_index(op.f('ix_refresh_tokens_created_at'), 'refresh_tokens', ['created_at'], unique=False, schema='auth')
    op.create_index('ix_refresh_tokens_session_active', 'refresh_tokens', ['session_id', 'revoked_at'], unique=False, schema='auth')
    op.create_index(op.f('ix_refresh_tokens_session_id'), 'refresh_tokens', ['session_id'], unique=False, schema='auth')


def downgrade() -> None:
    op.drop_index(op.f('ix_refresh_tokens_session_id'), table_name='refresh_tokens', schema='auth')
    op.drop_index('ix_refresh_tokens_session_active', table_name='refresh_tokens', schema='auth')
    op.drop_index(op.f('ix_refresh_tokens_created_at'), table_name='refresh_tokens', schema='auth')
    op.drop_table('refresh_tokens', schema='auth')
    op.drop_table('processed_events', schema='auth')
    op.drop_index(op.f('ix_principals_phone'), table_name='principals', schema='auth')
    op.drop_index(op.f('ix_principals_created_at'), table_name='principals', schema='auth')
    op.drop_table('principals', schema='auth')
    op.drop_index('ix_otp_challenges_phone_active', table_name='otp_challenges', schema='auth')
    op.drop_index(op.f('ix_otp_challenges_phone'), table_name='otp_challenges', schema='auth')
    op.drop_index(op.f('ix_otp_challenges_created_at'), table_name='otp_challenges', schema='auth')
    op.drop_table('otp_challenges', schema='auth')
    op.drop_index(op.f('ix_event_outbox_status'), table_name='event_outbox', schema='auth')
    op.drop_index(op.f('ix_event_outbox_created_at'), table_name='event_outbox', schema='auth')
    op.drop_index(op.f('ix_event_outbox_aggregate_id'), table_name='event_outbox', schema='auth')
    op.drop_table('event_outbox', schema='auth')
