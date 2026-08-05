"""Create the merchant schema: merchants, opening hours, service areas, event outbox.

Revision ID: 0001_merchant
Revises:
Create Date: 2026-08-05 05:24:25.039090
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision: str = '0001_merchant'
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
    schema='merchant'
    )
    op.create_index(op.f('ix_event_outbox_aggregate_id'), 'event_outbox', ['aggregate_id'], unique=False, schema='merchant')
    op.create_index(op.f('ix_event_outbox_created_at'), 'event_outbox', ['created_at'], unique=False, schema='merchant')
    op.create_index(op.f('ix_event_outbox_status'), 'event_outbox', ['status'], unique=False, schema='merchant')
    op.create_geospatial_table('merchants',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('slug', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('vertical', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('accepting_orders', sa.Boolean(), nullable=False),
    sa.Column('cuisines', postgresql.ARRAY(sa.String(length=40)), nullable=False),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('address_line1', sa.String(length=200), nullable=False),
    sa.Column('address_line2', sa.String(length=200), nullable=True),
    sa.Column('community', sa.String(length=120), nullable=True),
    sa.Column('city', sa.String(length=80), nullable=False),
    sa.Column('emirate', sa.String(length=80), nullable=True),
    sa.Column('country_code', sa.String(length=2), nullable=False),
    sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('location', Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography', nullable=False), nullable=False),
    sa.Column('timezone', sa.String(length=40), nullable=False),
    sa.Column('prep_time_minutes', sa.SmallInteger(), nullable=False),
    sa.Column('delivery_radius_m', sa.Integer(), nullable=False),
    sa.Column('rating', sa.Numeric(precision=3, scale=2), nullable=False),
    sa.Column('rating_count', sa.Integer(), nullable=False),
    sa.Column('price_tier', sa.SmallInteger(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('min_order_minor', sa.BigInteger(), nullable=False),
    sa.Column('commission_bps', sa.Integer(), nullable=False),
    sa.Column('logo_url', sa.String(length=500), nullable=True),
    sa.Column('hero_image_url', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('DRAFT', 'ACTIVE', 'PAUSED', 'SUSPENDED')", name=op.f('ck_merchants_status_valid')),
    sa.CheckConstraint("vertical IN ('RESTAURANT', 'GROCERY', 'PHARMACY', 'CONVENIENCE')", name=op.f('ck_merchants_vertical_valid')),
    sa.CheckConstraint('commission_bps BETWEEN 0 AND 5000', name=op.f('ck_merchants_commission_range')),
    sa.CheckConstraint('delivery_radius_m BETWEEN 100 AND 50000', name=op.f('ck_merchants_delivery_radius_range')),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name=op.f('ck_merchants_latitude_range')),
    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name=op.f('ck_merchants_longitude_range')),
    sa.CheckConstraint('prep_time_minutes BETWEEN 1 AND 180', name=op.f('ck_merchants_prep_time_range')),
    sa.CheckConstraint('price_tier BETWEEN 1 AND 4', name=op.f('ck_merchants_price_tier_range')),
    sa.CheckConstraint('rating BETWEEN 0 AND 5', name=op.f('ck_merchants_rating_range')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_merchants')),
    schema='merchant'
    )
    op.create_geospatial_index('idx_merchants_location', 'merchants', ['location'], unique=False, schema='merchant', postgresql_using='gist', postgresql_ops={})
    op.create_index(op.f('ix_merchants_created_at'), 'merchants', ['created_at'], unique=False, schema='merchant')
    op.create_index('ix_merchants_cuisines', 'merchants', ['cuisines'], unique=False, schema='merchant', postgresql_using='gin')
    op.create_index(op.f('ix_merchants_slug'), 'merchants', ['slug'], unique=True, schema='merchant')
    op.create_index('ix_merchants_status_accepting', 'merchants', ['status', 'accepting_orders'], unique=False, schema='merchant')
    op.create_table('processed_events',
    sa.Column('event_id', sa.String(length=40), nullable=False),
    sa.Column('consumer_group', sa.String(length=80), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('event_id', 'consumer_group', name=op.f('pk_processed_events')),
    schema='merchant'
    )
    op.create_table('merchant_hours',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('merchant_id', sa.String(length=40), nullable=False),
    sa.Column('day_of_week', sa.SmallInteger(), nullable=False),
    sa.Column('opens_at', sa.Time(), nullable=False),
    sa.Column('closes_at', sa.Time(), nullable=False),
    sa.CheckConstraint('day_of_week BETWEEN 0 AND 6', name=op.f('ck_merchant_hours_day_of_week_range')),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchant.merchants.id'], name=op.f('fk_merchant_hours_merchant_id_merchants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_merchant_hours')),
    sa.UniqueConstraint('merchant_id', 'day_of_week', 'opens_at', name='uq_merchant_hours_window'),
    schema='merchant'
    )
    op.create_index('ix_merchant_hours_merchant_day', 'merchant_hours', ['merchant_id', 'day_of_week'], unique=False, schema='merchant')
    op.create_index(op.f('ix_merchant_hours_merchant_id'), 'merchant_hours', ['merchant_id'], unique=False, schema='merchant')
    op.create_geospatial_table('service_areas',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('merchant_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('boundary', Geography(geometry_type='POLYGON', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography', nullable=False), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['merchant_id'], ['merchant.merchants.id'], name=op.f('fk_service_areas_merchant_id_merchants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_service_areas')),
    schema='merchant'
    )
    op.create_geospatial_index('idx_service_areas_boundary', 'service_areas', ['boundary'], unique=False, schema='merchant', postgresql_using='gist', postgresql_ops={})
    op.create_index(op.f('ix_service_areas_created_at'), 'service_areas', ['created_at'], unique=False, schema='merchant')
    op.create_index('ix_service_areas_merchant_active', 'service_areas', ['merchant_id', 'is_active'], unique=False, schema='merchant')
    op.create_index(op.f('ix_service_areas_merchant_id'), 'service_areas', ['merchant_id'], unique=False, schema='merchant')


def downgrade() -> None:
    op.drop_index(op.f('ix_service_areas_merchant_id'), table_name='service_areas', schema='merchant')
    op.drop_index('ix_service_areas_merchant_active', table_name='service_areas', schema='merchant')
    op.drop_index(op.f('ix_service_areas_created_at'), table_name='service_areas', schema='merchant')
    op.drop_geospatial_index('idx_service_areas_boundary', table_name='service_areas', schema='merchant', postgresql_using='gist', column_name='boundary')
    op.drop_geospatial_table('service_areas', schema='merchant')
    op.drop_index(op.f('ix_merchant_hours_merchant_id'), table_name='merchant_hours', schema='merchant')
    op.drop_index('ix_merchant_hours_merchant_day', table_name='merchant_hours', schema='merchant')
    op.drop_table('merchant_hours', schema='merchant')
    op.drop_table('processed_events', schema='merchant')
    op.drop_index('ix_merchants_status_accepting', table_name='merchants', schema='merchant')
    op.drop_index(op.f('ix_merchants_slug'), table_name='merchants', schema='merchant')
    op.drop_index('ix_merchants_cuisines', table_name='merchants', schema='merchant', postgresql_using='gin')
    op.drop_index(op.f('ix_merchants_created_at'), table_name='merchants', schema='merchant')
    op.drop_geospatial_index('idx_merchants_location', table_name='merchants', schema='merchant', postgresql_using='gist', column_name='location')
    op.drop_geospatial_table('merchants', schema='merchant')
    op.drop_index(op.f('ix_event_outbox_status'), table_name='event_outbox', schema='merchant')
    op.drop_index(op.f('ix_event_outbox_created_at'), table_name='event_outbox', schema='merchant')
    op.drop_index(op.f('ix_event_outbox_aggregate_id'), table_name='event_outbox', schema='merchant')
    op.drop_table('event_outbox', schema='merchant')
