"""Create the catalog schema: menus, categories, items, option groups, options.

Revision ID: 0001_catalog
Revises:
Create Date: 2026-08-05 05:24:26.695579
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0001_catalog'
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
    schema='catalog'
    )
    op.create_index(op.f('ix_event_outbox_aggregate_id'), 'event_outbox', ['aggregate_id'], unique=False, schema='catalog')
    op.create_index(op.f('ix_event_outbox_created_at'), 'event_outbox', ['created_at'], unique=False, schema='catalog')
    op.create_index(op.f('ix_event_outbox_status'), 'event_outbox', ['status'], unique=False, schema='catalog')
    op.create_table('menus',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('merchant_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_menus')),
    schema='catalog'
    )
    op.create_index(op.f('ix_menus_created_at'), 'menus', ['created_at'], unique=False, schema='catalog')
    op.create_index('ix_menus_merchant_active', 'menus', ['merchant_id', 'is_active'], unique=False, schema='catalog')
    op.create_index(op.f('ix_menus_merchant_id'), 'menus', ['merchant_id'], unique=False, schema='catalog')
    op.create_table('processed_events',
    sa.Column('event_id', sa.String(length=40), nullable=False),
    sa.Column('consumer_group', sa.String(length=80), nullable=False),
    sa.Column('event_type', sa.String(length=80), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('event_id', 'consumer_group', name=op.f('pk_processed_events')),
    schema='catalog'
    )
    op.create_table('menu_categories',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('menu_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['menu_id'], ['catalog.menus.id'], name=op.f('fk_menu_categories_menu_id_menus'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_menu_categories')),
    sa.UniqueConstraint('menu_id', 'name', name='uq_menu_categories_menu_name'),
    schema='catalog'
    )
    op.create_index(op.f('ix_menu_categories_created_at'), 'menu_categories', ['created_at'], unique=False, schema='catalog')
    op.create_index(op.f('ix_menu_categories_menu_id'), 'menu_categories', ['menu_id'], unique=False, schema='catalog')
    op.create_index('ix_menu_categories_menu_position', 'menu_categories', ['menu_id', 'position'], unique=False, schema='catalog')
    op.create_table('menu_items',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('merchant_id', sa.String(length=40), nullable=False),
    sa.Column('category_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('price_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('is_available', sa.Boolean(), nullable=False),
    sa.Column('image_url', sa.String(length=500), nullable=True),
    sa.Column('tags', postgresql.ARRAY(sa.String(length=32)), nullable=False),
    sa.Column('calories', sa.Integer(), nullable=True),
    sa.Column('spice_level', sa.SmallInteger(), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('price_minor >= 0', name=op.f('ck_menu_items_price_non_negative')),
    sa.CheckConstraint('spice_level BETWEEN 0 AND 3', name=op.f('ck_menu_items_spice_level_range')),
    sa.ForeignKeyConstraint(['category_id'], ['catalog.menu_categories.id'], name=op.f('fk_menu_items_category_id_menu_categories'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_menu_items')),
    schema='catalog'
    )
    op.create_index(op.f('ix_menu_items_category_id'), 'menu_items', ['category_id'], unique=False, schema='catalog')
    op.create_index('ix_menu_items_category_position', 'menu_items', ['category_id', 'position'], unique=False, schema='catalog')
    op.create_index(op.f('ix_menu_items_created_at'), 'menu_items', ['created_at'], unique=False, schema='catalog')
    op.create_index('ix_menu_items_merchant_available', 'menu_items', ['merchant_id', 'is_available'], unique=False, schema='catalog')
    op.create_index(op.f('ix_menu_items_merchant_id'), 'menu_items', ['merchant_id'], unique=False, schema='catalog')
    op.create_index('ix_menu_items_tags', 'menu_items', ['tags'], unique=False, schema='catalog', postgresql_using='gin')
    op.create_table('option_groups',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('item_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('selection_type', sa.String(length=32), nullable=False),
    sa.Column('min_select', sa.SmallInteger(), nullable=False),
    sa.Column('max_select', sa.SmallInteger(), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("selection_type IN ('SINGLE', 'MULTI')", name=op.f('ck_option_groups_selection_valid')),
    sa.CheckConstraint('max_select >= 1', name=op.f('ck_option_groups_max_select_positive')),
    sa.CheckConstraint('max_select >= min_select', name=op.f('ck_option_groups_select_bounds_ordered')),
    sa.CheckConstraint('min_select >= 0', name=op.f('ck_option_groups_min_select_non_negative')),
    sa.ForeignKeyConstraint(['item_id'], ['catalog.menu_items.id'], name=op.f('fk_option_groups_item_id_menu_items'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_option_groups')),
    schema='catalog'
    )
    op.create_index(op.f('ix_option_groups_created_at'), 'option_groups', ['created_at'], unique=False, schema='catalog')
    op.create_index(op.f('ix_option_groups_item_id'), 'option_groups', ['item_id'], unique=False, schema='catalog')
    op.create_index('ix_option_groups_item_position', 'option_groups', ['item_id', 'position'], unique=False, schema='catalog')
    op.create_table('options',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('group_id', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('price_delta_minor', sa.BigInteger(), nullable=False),
    sa.Column('is_available', sa.Boolean(), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['catalog.option_groups.id'], name=op.f('fk_options_group_id_option_groups'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_options')),
    schema='catalog'
    )
    op.create_index(op.f('ix_options_created_at'), 'options', ['created_at'], unique=False, schema='catalog')
    op.create_index(op.f('ix_options_group_id'), 'options', ['group_id'], unique=False, schema='catalog')
    op.create_index('ix_options_group_position', 'options', ['group_id', 'position'], unique=False, schema='catalog')


def downgrade() -> None:
    op.drop_index('ix_options_group_position', table_name='options', schema='catalog')
    op.drop_index(op.f('ix_options_group_id'), table_name='options', schema='catalog')
    op.drop_index(op.f('ix_options_created_at'), table_name='options', schema='catalog')
    op.drop_table('options', schema='catalog')
    op.drop_index('ix_option_groups_item_position', table_name='option_groups', schema='catalog')
    op.drop_index(op.f('ix_option_groups_item_id'), table_name='option_groups', schema='catalog')
    op.drop_index(op.f('ix_option_groups_created_at'), table_name='option_groups', schema='catalog')
    op.drop_table('option_groups', schema='catalog')
    op.drop_index('ix_menu_items_tags', table_name='menu_items', schema='catalog', postgresql_using='gin')
    op.drop_index(op.f('ix_menu_items_merchant_id'), table_name='menu_items', schema='catalog')
    op.drop_index('ix_menu_items_merchant_available', table_name='menu_items', schema='catalog')
    op.drop_index(op.f('ix_menu_items_created_at'), table_name='menu_items', schema='catalog')
    op.drop_index('ix_menu_items_category_position', table_name='menu_items', schema='catalog')
    op.drop_index(op.f('ix_menu_items_category_id'), table_name='menu_items', schema='catalog')
    op.drop_table('menu_items', schema='catalog')
    op.drop_index('ix_menu_categories_menu_position', table_name='menu_categories', schema='catalog')
    op.drop_index(op.f('ix_menu_categories_menu_id'), table_name='menu_categories', schema='catalog')
    op.drop_index(op.f('ix_menu_categories_created_at'), table_name='menu_categories', schema='catalog')
    op.drop_table('menu_categories', schema='catalog')
    op.drop_table('processed_events', schema='catalog')
    op.drop_index(op.f('ix_menus_merchant_id'), table_name='menus', schema='catalog')
    op.drop_index('ix_menus_merchant_active', table_name='menus', schema='catalog')
    op.drop_index(op.f('ix_menus_created_at'), table_name='menus', schema='catalog')
    op.drop_table('menus', schema='catalog')
    op.drop_index(op.f('ix_event_outbox_status'), table_name='event_outbox', schema='catalog')
    op.drop_index(op.f('ix_event_outbox_created_at'), table_name='event_outbox', schema='catalog')
    op.drop_index(op.f('ix_event_outbox_aggregate_id'), table_name='event_outbox', schema='catalog')
    op.drop_table('event_outbox', schema='catalog')
