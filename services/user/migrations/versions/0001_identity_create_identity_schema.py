"""Create the identity schema: users, addresses, courier profiles, merchant staff.

Revision ID: 0001_identity
Revises:
Create Date: 2026-08-05 05:04:36.103566
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision: str = '0001_identity'
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
    schema='identity'
    )
    op.create_index(op.f('ix_event_outbox_aggregate_id'), 'event_outbox', ['aggregate_id'], unique=False, schema='identity')
    op.create_index(op.f('ix_event_outbox_created_at'), 'event_outbox', ['created_at'], unique=False, schema='identity')
    op.create_index(op.f('ix_event_outbox_status'), 'event_outbox', ['status'], unique=False, schema='identity')
    op.create_table('processed_events',
    sa.Column('event_id', sa.String(length=40), nullable=False),
    sa.Column('consumer_group', sa.String(length=80), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('event_id', 'consumer_group', name=op.f('pk_processed_events')),
    schema='identity'
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('role', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=True),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('locale', sa.String(length=10), nullable=False),
    sa.Column('date_of_birth', sa.Date(), nullable=True),
    sa.Column('avatar_url', sa.String(length=500), nullable=True),
    sa.Column('marketing_opt_in', sa.Boolean(), nullable=False),
    sa.Column('dietary_tags', sa.ARRAY(sa.String(length=32)), nullable=False),
    sa.Column('default_address_id', sa.String(length=40), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('customer', 'merchant', 'courier', 'admin', 'service')", name=op.f('ck_users_role_valid')),
    sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED', 'DELETED')", name=op.f('ck_users_status_valid')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    schema='identity'
    )
    op.create_index(op.f('ix_users_created_at'), 'users', ['created_at'], unique=False, schema='identity')
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=False, schema='identity')
    op.create_index(op.f('ix_users_phone'), 'users', ['phone'], unique=True, schema='identity')
    op.create_geospatial_table('addresses',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('user_id', sa.String(length=40), nullable=False),
    sa.Column('label', sa.String(length=32), nullable=False),
    sa.Column('nickname', sa.String(length=60), nullable=True),
    sa.Column('line1', sa.String(length=200), nullable=False),
    sa.Column('line2', sa.String(length=200), nullable=True),
    sa.Column('building', sa.String(length=120), nullable=True),
    sa.Column('apartment', sa.String(length=60), nullable=True),
    sa.Column('community', sa.String(length=120), nullable=True),
    sa.Column('makani_number', sa.String(length=20), nullable=True),
    sa.Column('city', sa.String(length=80), nullable=False),
    sa.Column('emirate', sa.String(length=80), nullable=True),
    sa.Column('country_code', sa.String(length=2), nullable=False),
    sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('location', Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography', nullable=False), nullable=False),
    sa.Column('delivery_notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("label IN ('HOME', 'WORK', 'OTHER')", name=op.f('ck_addresses_label_valid')),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name=op.f('ck_addresses_latitude_range')),
    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name=op.f('ck_addresses_longitude_range')),
    sa.ForeignKeyConstraint(['user_id'], ['identity.users.id'], name=op.f('fk_addresses_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_addresses')),
    schema='identity'
    )
    op.create_geospatial_index('idx_addresses_location', 'addresses', ['location'], unique=False, schema='identity', postgresql_using='gist', postgresql_ops={})
    op.create_index(op.f('ix_addresses_created_at'), 'addresses', ['created_at'], unique=False, schema='identity')
    op.create_index('ix_addresses_user_created', 'addresses', ['user_id', 'created_at'], unique=False, schema='identity')
    op.create_index(op.f('ix_addresses_user_id'), 'addresses', ['user_id'], unique=False, schema='identity')
    op.create_table('courier_profiles',
    sa.Column('user_id', sa.String(length=40), nullable=False),
    sa.Column('vehicle', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('licence_number', sa.String(length=40), nullable=True),
    sa.Column('licence_expires_at', sa.Date(), nullable=True),
    sa.Column('rating', sa.Numeric(precision=3, scale=2), nullable=False),
    sa.Column('completed_deliveries', sa.Integer(), nullable=False),
    sa.Column('max_concurrent_orders', sa.Integer(), nullable=False),
    sa.Column('payout_iban', sa.String(length=34), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('OFFLINE', 'ONLINE', 'BUSY', 'ON_BREAK')", name=op.f('ck_courier_profiles_status_valid')),
    sa.CheckConstraint("vehicle IN ('BICYCLE', 'MOTORCYCLE', 'CAR', 'VAN', 'ON_FOOT')", name=op.f('ck_courier_profiles_vehicle_valid')),
    sa.CheckConstraint('max_concurrent_orders BETWEEN 1 AND 10', name=op.f('ck_courier_profiles_capacity_range')),
    sa.CheckConstraint('rating BETWEEN 0 AND 5', name=op.f('ck_courier_profiles_rating_range')),
    sa.ForeignKeyConstraint(['user_id'], ['identity.users.id'], name=op.f('fk_courier_profiles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_courier_profiles')),
    schema='identity'
    )
    op.create_index(op.f('ix_courier_profiles_created_at'), 'courier_profiles', ['created_at'], unique=False, schema='identity')
    op.create_index(op.f('ix_courier_profiles_status'), 'courier_profiles', ['status'], unique=False, schema='identity')
    op.create_table('merchant_staff',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('user_id', sa.String(length=40), nullable=False),
    sa.Column('merchant_id', sa.String(length=40), nullable=False),
    sa.Column('position', sa.String(length=60), nullable=True),
    sa.Column('is_owner', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['identity.users.id'], name=op.f('fk_merchant_staff_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_merchant_staff')),
    sa.UniqueConstraint('user_id', 'merchant_id', name='uq_merchant_staff_user_merchant'),
    schema='identity'
    )
    op.create_index(op.f('ix_merchant_staff_created_at'), 'merchant_staff', ['created_at'], unique=False, schema='identity')
    op.create_index(op.f('ix_merchant_staff_merchant_id'), 'merchant_staff', ['merchant_id'], unique=False, schema='identity')
    op.create_index(op.f('ix_merchant_staff_user_id'), 'merchant_staff', ['user_id'], unique=False, schema='identity')


def downgrade() -> None:
    op.drop_index(op.f('ix_merchant_staff_user_id'), table_name='merchant_staff', schema='identity')
    op.drop_index(op.f('ix_merchant_staff_merchant_id'), table_name='merchant_staff', schema='identity')
    op.drop_index(op.f('ix_merchant_staff_created_at'), table_name='merchant_staff', schema='identity')
    op.drop_table('merchant_staff', schema='identity')
    op.drop_index(op.f('ix_courier_profiles_status'), table_name='courier_profiles', schema='identity')
    op.drop_index(op.f('ix_courier_profiles_created_at'), table_name='courier_profiles', schema='identity')
    op.drop_table('courier_profiles', schema='identity')
    op.drop_index(op.f('ix_addresses_user_id'), table_name='addresses', schema='identity')
    op.drop_index('ix_addresses_user_created', table_name='addresses', schema='identity')
    op.drop_index(op.f('ix_addresses_created_at'), table_name='addresses', schema='identity')
    op.drop_geospatial_index('idx_addresses_location', table_name='addresses', schema='identity', postgresql_using='gist', column_name='location')
    op.drop_geospatial_table('addresses', schema='identity')
    op.drop_index(op.f('ix_users_phone'), table_name='users', schema='identity')
    op.drop_index(op.f('ix_users_email'), table_name='users', schema='identity')
    op.drop_index(op.f('ix_users_created_at'), table_name='users', schema='identity')
    op.drop_table('users', schema='identity')
    op.drop_table('processed_events', schema='identity')
    op.drop_index(op.f('ix_event_outbox_status'), table_name='event_outbox', schema='identity')
    op.drop_index(op.f('ix_event_outbox_created_at'), table_name='event_outbox', schema='identity')
    op.drop_index(op.f('ix_event_outbox_aggregate_id'), table_name='event_outbox', schema='identity')
    op.drop_table('event_outbox', schema='identity')
